"""Minimal Google Calendar helper for auto-reminder events.

Uses the same stored Google service token as Classroom to create calendar events.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict

import requests
from requests import exceptions as request_exceptions
from django.utils import timezone

from auth_app.auth_service import refresh_access_token, GoogleAuthError
from auth_app.models import GoogleServiceToken
logger = logging.getLogger(__name__)


class CalendarAPIError(Exception):
    """Raised when Calendar API calls fail."""

    def __init__(self, message: str, code: str = "calendar_error", user_message: str | None = None):
        super().__init__(message)
        self.code = code
        self.user_message = user_message or message


class CalendarAPI:
    def __init__(self, faculty_user):
        self.faculty = faculty_user
        self.token = GoogleServiceToken.objects.filter(user=faculty_user).first()

    def _refresh_if_needed(self) -> None:
        if not self.token:
            raise CalendarAPIError(
                "Google services not connected",
                code="auth_missing",
                user_message="Google services not connected. Please connect Google services.",
            )
        if self.token.token_expiry and self.token.token_expiry > timezone.now():
            return
        if not self.token.refresh_token:
            raise CalendarAPIError(
                "Missing refresh token; reconnect Google services",
                code="auth_refresh_missing",
                user_message="Google authorization expired. Please reconnect Google services.",
            )
        try:
            refreshed = refresh_access_token(self.token.refresh_token)
        except GoogleAuthError as exc:
            msg = str(exc)
            if "invalid_grant" in msg or "revoked" in msg:
                raise CalendarAPIError(
                    msg,
                    code="auth_revoked",
                    user_message="Google permissions were revoked. Please reconnect Google services.",
                ) from exc
            raise CalendarAPIError(
                msg,
                code="auth_refresh_failed",
                user_message="Google token refresh failed. Please reconnect Google services.",
            ) from exc
        self.token.access_token = refreshed.get("access_token", self.token.access_token)
        self.token.token_expiry = timezone.now() + timezone.timedelta(
            seconds=refreshed.get("expires_in", 3600)
        )
        self.token.scopes = refreshed.get("scope") or self.token.scopes
        self.token.save(update_fields=["access_token", "token_expiry", "scopes", "updated_at"])
        logger.info(
            "[%s] [AutoReminder.Calendar] [auth_refresh_ok] Token refreshed for %s",
            timezone.now().isoformat(),
            getattr(self.faculty, "email", "unknown"),
        )

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        self._refresh_if_needed()
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {self.token.access_token}"

        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                response = requests.request(method, url, headers=headers, timeout=10, **kwargs)
            except KeyboardInterrupt as exc:  # keep the scheduler alive
                raise CalendarAPIError("Request interrupted", code="interrupt", user_message="Request interrupted") from exc
            except (request_exceptions.Timeout, request_exceptions.ConnectionError) as exc:
                last_exc = exc
                if attempt == 0:
                    continue
                raise CalendarAPIError(
                    str(exc),
                    code="network_timeout",
                    user_message="Network timeout while contacting Google Calendar. Please try again.",
                ) from exc
            except request_exceptions.RequestException as exc:
                raise CalendarAPIError(
                    str(exc),
                    code="network_error",
                    user_message="Network error contacting Google Calendar.",
                ) from exc

            if response.status_code == 401 and self.token and self.token.refresh_token:
                self._refresh_if_needed()
                headers["Authorization"] = f"Bearer {self.token.access_token}"
                if attempt == 0:
                    continue

            if response.ok:
                return response

            if response.status_code in {500, 503} and attempt == 0:
                continue

            try:
                body = response.json()
                error = body.get("error", {}) if isinstance(body, dict) else {}
                message = error.get("message") or response.text
                reasons = [item.get("reason") for item in error.get("errors", []) if isinstance(item, dict)]
            except ValueError:
                message = response.text
                reasons = []

            code = "calendar_error"
            user_message = message or f"Calendar API failed with {response.status_code}"
            if response.status_code == 401:
                code = "auth_invalid"
                user_message = "Google authorization expired. Please reconnect Google services."
            elif response.status_code == 403:
                if "accessNotConfigured" in reasons:
                    code = "api_disabled"
                    user_message = "Google Calendar API is disabled for this project."
                elif "insufficientPermissions" in reasons or "forbidden" in reasons:
                    code = "auth_scope"
                    user_message = "Missing Calendar permission. Please reconnect Google services."
                elif "quotaExceeded" in reasons or "userRateLimitExceeded" in reasons:
                    code = "quota_exceeded"
                    user_message = "Google Calendar quota exceeded. Please try again later."
                else:
                    code = "permission_denied"
                    user_message = "Google Calendar permission denied."
            elif response.status_code == 429:
                code = "quota_exceeded"
                user_message = "Google Calendar quota exceeded. Please try again later."
            elif response.status_code == 400:
                code = "invalid_payload"
                user_message = "Invalid calendar event data."
            elif response.status_code in {404, 410}:
                code = "not_found"
                user_message = "Calendar event not found. It will be recreated."
            elif "Resource has been deleted" in (message or ""):
                code = "not_found"
                user_message = "Calendar event not found. It will be recreated."

            logger.warning(
                "[%s] [AutoReminder.Calendar] [%s] %s",
                timezone.now().isoformat(),
                code,
                message,
            )
            raise CalendarAPIError(message, code=code, user_message=user_message)

        if last_exc:
            raise CalendarAPIError(
                str(last_exc),
                code="network_timeout",
                user_message="Network timeout while contacting Google Calendar. Please try again.",
            ) from last_exc
        raise CalendarAPIError("Unknown Calendar API error")

    def create_all_day_event(
        self,
        summary: str,
        description: str,
        start_date: date,
        end_date: date,
        timezone_str: str = "Asia/Karachi",
    ) -> Dict[str, Any]:
        url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        body = {
            "summary": summary,
            "description": description,
            "start": {"date": start_date.isoformat(), "timeZone": timezone_str},
            "end": {"date": end_date.isoformat(), "timeZone": timezone_str},
            "reminders": {"useDefault": True},
        }
        resp = self._request(
            "POST",
            url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
        )
        return resp.json()

    def update_event(self, event_id: str, **fields) -> Dict[str, Any]:
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}"
        resp = self._request(
            "PATCH",
            url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(fields),
        )
        return resp.json()

    def delete_event(self, event_id: str) -> None:
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}"
        self._request("DELETE", url)
