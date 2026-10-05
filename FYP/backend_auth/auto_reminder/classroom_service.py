"""Lightweight Classroom client for auto-reminder alerts.

Reuses stored Google tokens and refresh logic from auth_app.auth_service.
This client is intentionally minimal: only the APIs needed for alerts.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

import requests
from requests import exceptions as request_exceptions
from django.utils import timezone

from auth_app.auth_service import refresh_access_token, GoogleAuthError
from auth_app.models import GoogleServiceToken


ASSIGNMENT_STATES_SUBMITTED = {"TURNED_IN", "RETURNED", "SUBMITTED"}


logger = logging.getLogger(__name__)


class ClassroomAPIError(Exception):
    """Raised when Classroom API call fails after retry."""

    def __init__(self, message: str, code: str = "classroom_error", user_message: str | None = None):
        super().__init__(message)
        self.code = code
        self.user_message = user_message or message


class ClassroomAPI:
    def __init__(self, faculty_user):
        self.faculty = faculty_user
        self.token = GoogleServiceToken.objects.filter(user=faculty_user).first()

    def _refresh_if_needed(self) -> None:
        if not self.token:
            raise ClassroomAPIError(
                "Google services not connected",
                code="auth_missing",
                user_message="Google services not connected. Please connect Google services.",
            )
        if self.token.token_expiry and self.token.token_expiry > timezone.now():
            return
        if not self.token.refresh_token:
            raise ClassroomAPIError(
                "Missing refresh token; reconnect Google services",
                code="auth_refresh_missing",
                user_message="Google authorization expired. Please reconnect Google services.",
            )
        try:
            refreshed = refresh_access_token(self.token.refresh_token)
        except GoogleAuthError as exc:
            msg = str(exc)
            if "invalid_grant" in msg or "revoked" in msg:
                raise ClassroomAPIError(
                    msg,
                    code="auth_revoked",
                    user_message="Google permissions were revoked. Please reconnect Google services.",
                ) from exc
            raise ClassroomAPIError(
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
            "[%s] [AutoReminder.Classroom] [auth_refresh_ok] Token refreshed for %s",
            timezone.now().isoformat(),
            getattr(self.faculty, "email", "unknown"),
        )

    def _parse_error(self, response: requests.Response) -> tuple[str, str, str]:
        """Return (code, user_message, raw_message) from Classroom response."""
        try:
            body = response.json()
        except ValueError:
            body = {"error": response.text}

        error_obj = body.get("error", {}) if isinstance(body, dict) else {}
        raw_message = error_obj.get("message") or response.text
        reasons = [item.get("reason") for item in error_obj.get("errors", []) if isinstance(item, dict)]

        code = "classroom_error"
        user_message = raw_message or f"Classroom API failed with {response.status_code}"
        if response.status_code == 401:
            code = "auth_invalid"
            user_message = "Google authorization expired. Please reconnect Google services."
        elif response.status_code == 403:
            if "insufficientPermissions" in reasons:
                code = "auth_scope"
                user_message = "Missing Classroom permission. Please reconnect Google services."
            elif "quotaExceeded" in reasons or "userRateLimitExceeded" in reasons:
                code = "quota_exceeded"
                user_message = "Google Classroom quota exceeded. Please try again later."
            else:
                code = "permission_denied"
                user_message = "Google Classroom permission denied."
        elif response.status_code == 404:
            code = "not_found"
            user_message = "Requested Classroom resource was not found."
        elif response.status_code == 409:
            code = "resource_exists"
            user_message = "Classroom resource already exists."
        elif response.status_code == 429:
            code = "quota_exceeded"
            user_message = "Google Classroom quota exceeded. Please try again later."
        elif response.status_code in {500, 503}:
            code = "service_unavailable"
            user_message = "Google Classroom service is temporarily unavailable."
        return code, user_message, raw_message

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        self._refresh_if_needed()
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {self.token.access_token}"
        allow_retry = method.upper() == "GET"
        last_response = None
        for attempt in range(2):
            try:
                response = requests.request(method, url, headers=headers, timeout=10, **kwargs)
                last_response = response
            except KeyboardInterrupt as exc:  # keep the job running even if a request is interrupted
                raise ClassroomAPIError("Request interrupted", code="interrupt", user_message="Request interrupted") from exc
            except request_exceptions.Timeout as exc:
                raise ClassroomAPIError(
                    str(exc),
                    code="network_timeout",
                    user_message="Network timeout while contacting Google Classroom. Please try again.",
                ) from exc
            except request_exceptions.ConnectionError as exc:
                raise ClassroomAPIError(
                    str(exc),
                    code="network_error",
                    user_message="Network error contacting Google Classroom.",
                ) from exc
            except request_exceptions.RequestException as exc:
                raise ClassroomAPIError(
                    str(exc),
                    code="network_error",
                    user_message="Network error contacting Google Classroom.",
                ) from exc

            # Retry on 401 with refresh
            if response.status_code == 401 and self.token and self.token.refresh_token:
                self._refresh_if_needed()
                headers["Authorization"] = f"Bearer {self.token.access_token}"
                continue

            if response.ok:
                return response

            if allow_retry and response.status_code in {429, 500, 503} and attempt == 0:
                continue

            code, user_message, raw_message = self._parse_error(response)
            logger.warning(
                "[%s] [AutoReminder.Classroom] [%s] %s",
                timezone.now().isoformat(),
                code,
                raw_message,
            )
            raise ClassroomAPIError(raw_message or f"Classroom API failed with {response.status_code}", code=code, user_message=user_message)

        if last_response is not None:
            code, user_message, raw_message = self._parse_error(last_response)
            raise ClassroomAPIError(raw_message, code=code, user_message=user_message)
        raise ClassroomAPIError("Unknown Classroom API error", code="classroom_error")

    def list_coursework(self, course_id: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page_token: str | None = None
        while True:
            url = (
                f"https://classroom.googleapis.com/v1/courses/{course_id}/courseWork"
                "?pageSize=100&orderBy=updateTime desc"
            )
            if page_token:
                url = f"{url}&pageToken={page_token}"
            resp = self._request("GET", url)
            payload = resp.json()
            items.extend(payload.get("courseWork", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return items

    def list_courses(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page_token: str | None = None
        while True:
            url = (
                "https://classroom.googleapis.com/v1/courses"
                "?teacherId=me&pageSize=100&courseStates=ACTIVE"
            )
            if page_token:
                url = f"{url}&pageToken={page_token}"
            resp = self._request("GET", url)
            payload = resp.json()
            items.extend(payload.get("courses", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return items

    def get_submission_summary(self, course_id: str, coursework_id: str) -> Dict[str, int]:
        url = (
            f"https://classroom.googleapis.com/v1/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions"
        )
        resp = self._request("GET", url)
        submissions = resp.json().get("studentSubmissions", [])
        total = len(submissions)
        submitted = 0
        for sub in submissions:
            state = sub.get("state", "").upper()
            if state in ASSIGNMENT_STATES_SUBMITTED:
                submitted += 1
        return {
            "total": total,
            "submitted": submitted,
            "not_submitted": max(total - submitted, 0),
        }

    def post_announcement(self, course_id: str, text: str) -> Dict[str, Any]:
        url = f"https://classroom.googleapis.com/v1/courses/{course_id}/announcements"
        body = {"text": text, "state": "PUBLISHED"}
        resp = self._request(
            "POST",
            url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
        )
        return resp.json()


def format_assignment_announcement(title: str, summary: Dict[str, int]) -> str:
    return (
        f"New assignment posted: {title}\n"
        f"Submissions: {summary.get('submitted', 0)} / {summary.get('total', 0)} submitted.\n"
        f"Pending: {summary.get('not_submitted', 0)}.\n"
        "This is an automated alert from Faculty Tasks Automation."
    )


def format_deadline_announcement(label: str, deadline_date: str) -> str:
    return (
        f"Reminder: {label} is due on {deadline_date}.\n"
        "Please submit marks before the deadline.\n"
        "This is an automated alert from Faculty Tasks Automation."
    )
