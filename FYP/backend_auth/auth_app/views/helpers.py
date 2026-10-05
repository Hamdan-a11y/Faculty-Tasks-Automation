"""Shared helpers and common utilities for auth_app views."""

import os
import socket
import json
import re
import time
import shutil
import subprocess
import requests
from functools import lru_cache
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponseBadRequest, JsonResponse, HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)
from webdriver_manager.chrome import ChromeDriverManager
from requests import exceptions as requests_exceptions

from auth_app.auth_service import (
    GoogleAuthError,
    refresh_access_token,
)
from auth_app.models import (
    FacultyUser,
    GoogleServiceToken,
)

STATIC_WHITELIST = {
    "2212239@szabist-isb.pk",
    "muhammadhumdan43@gmail.com",
    "muhammadhumdan91@gmail.com",
}


ALLOWED_DOMAIN_SUFFIXES = [
    suffix.strip().lower()
    for suffix in os.getenv("ALLOWED_DOMAIN_SUFFIXES", "@szabist-isb.pk,@gmail.com").split(",")
    if suffix.strip()
]


env_whitelist = {
    email.strip().lower()
    for email in os.getenv("WHITELISTED_EMAILS", ",").split(",")
    if email.strip()
}


WHITELISTED_EMAILS = (env_whitelist or STATIC_WHITELIST).union(STATIC_WHITELIST)


SESSION_KEY = "faculty_user_id"


CHROME_PROFILE_PATH = os.path.join(settings.BASE_DIR, "chrome_automation_profile")


PORTAL_URL = "http://127.0.0.1:5500/faculty-portal-dummy/course-outline.html?courseId=1"


LOGIN_URL = "https://hamdan-a11y.github.io/ZABDESK-Dummy-Portal/"


def log_auth(message: str):
    """Lightweight auth debug logger for console visibility."""
    print(f"[AUTH] {message}", flush=True)


def log_google(message: str, category: str = "info"):
    """Structured Google services logger for demo visibility and traceability."""
    timestamp = timezone.now().isoformat()
    print(f"[{timestamp}] [GoogleServices] [{category}] {message}", flush=True)


def _parse_google_error(response: requests.Response):
    """Extract a Google API error reason/message for user-safe feedback."""
    body = None
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - handle non-JSON errors safely
        body = {"error": response.text}

    error_obj = body.get("error") if isinstance(body, dict) else None
    message = None
    reason = None
    status_code = response.status_code

    if isinstance(error_obj, dict):
        message = error_obj.get("message")
        errors = error_obj.get("errors") or []
        if errors and isinstance(errors, list):
            reason = errors[0].get("reason")
    elif isinstance(error_obj, str):
        message = error_obj

    message = message or (body.get("error") if isinstance(body, dict) else None) or "Google API error"

    # Categorize common Google API failures for consistent UX.
    category = "google_api_error"
    user_message = "Google request failed. Please try again."
    if status_code == 401:
        category = "auth_expired"
        user_message = "Google access expired. Please reconnect Google services."
    elif status_code == 403:
        category = "permission_denied"
        user_message = "Permission denied by Google. Reconnect services with required scopes."
    elif status_code == 429:
        category = "quota_exceeded"
        user_message = "Google API quota exceeded. Try again later."
    elif status_code == 409:
        category = "resource_exists"
        user_message = "This resource already exists in Google."
    elif status_code == 404:
        category = "resource_not_found"
        user_message = "The requested Google resource was not found."
    elif status_code >= 500:
        category = "google_unavailable"
        user_message = "Google service is temporarily unavailable. Please retry later."

    # Override based on reason/message hints.
    msg_lower = (message or "").lower()
    reason_lower = (reason or "").lower()
    if "insufficient" in msg_lower or "scope" in msg_lower or "insufficientpermissions" in reason_lower:
        category = "scope_mismatch"
        user_message = "Missing Google scopes. Please reconnect Google services."
    if "quota" in msg_lower or "limit" in msg_lower or reason_lower in {"dailylimitexceeded", "quotaexceeded"}:
        category = "quota_exceeded"
        user_message = "Google API quota exceeded. Try again later."
    if "invalid_grant" in msg_lower or "revoked" in msg_lower:
        category = "auth_revoked"
        user_message = "Google permissions were revoked. Please reconnect services."

    return {
        "body": body,
        "message": message,
        "reason": reason,
        "category": category,
        "user_message": user_message,
        "status_code": status_code,
    }


def _google_error_response(user, response: requests.Response, context: str):
    """Create a consistent API response + log for Google API failures."""
    info = _parse_google_error(response)
    next_steps_map = {
        "auth_expired": "Reconnect Google services.",
        "auth_revoked": "Reconnect Google services.",
        "auth_refresh_failed": "Reconnect Google services.",
        "scope_mismatch": "Reconnect and approve required scopes.",
        "permission_denied": "Verify you own the Classroom/Drive resource or reconnect.",
        "quota_exceeded": "Wait a few minutes and retry.",
        "resource_exists": "Use a different title or reuse the existing resource.",
        "resource_not_found": "Confirm the ID or choose a valid resource.",
        "google_unavailable": "Try again later.",
    }
    next_steps = next_steps_map.get(info["category"], "Retry the request.")
    log_google(
        f"{context} failed for {user.email}: status={info['status_code']}; reason={info['reason']}; message={info['message']}",
        category=info["category"],
    )
    return Response(
        {
            "detail": info["user_message"],
            "category": info["category"],
            "error": info["body"],
            "next_steps": next_steps,
        },
        status=info["status_code"],
    )


def _get_session_user(request):
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    try:
        return FacultyUser.objects.get(id=user_id)
    except FacultyUser.DoesNotExist:
        request.session.flush()
        return None


def _is_allowed_email(email: str) -> bool:
    lowered = (email or "").lower()
    return lowered in WHITELISTED_EMAILS or any(lowered.endswith(s) for s in ALLOWED_DOMAIN_SUFFIXES)


def _get_or_refresh_service_token(user):
    token = GoogleServiceToken.objects.filter(user=user).first()
    if not token:
        return None, False

    if token.token_expiry and token.token_expiry > timezone.now():
        return token, False

    if token.refresh_token:
        try:
            refreshed = refresh_access_token(token.refresh_token)
        except GoogleAuthError as exc:  # noqa: BLE001 - surfaced to caller
            log_google(f"Token refresh failed for {user.email}: {exc}", category="auth_refresh_failed")
            return None, False

        token.access_token = refreshed.get("access_token", token.access_token)
        token.token_expiry = timezone.now() + timedelta(seconds=refreshed.get("expires_in", 3600))
        token.scopes = refreshed.get("scope") or token.scopes
        token.save(update_fields=["access_token", "token_expiry", "scopes", "updated_at"])
        log_google(f"Token refreshed for {user.email}", category="auth_refresh_ok")
        return token, True

    # Missing refresh token means we cannot recover without user consent.
    log_google(f"Missing refresh token for {user.email}", category="auth_refresh_missing")
    return None, False


def _has_required_service_scopes(token) -> bool:
    """Check that the stored Google service token includes all required scopes."""

    if not token or not getattr(token, "scopes", ""):
        return False
    token_scopes = set(str(token.scopes).split())

    # Allow broader student scope to satisfy the readonly requirement
    scope_aliases = {
        "https://www.googleapis.com/auth/classroom.coursework.students.readonly": [
            "https://www.googleapis.com/auth/classroom.coursework.students"
        ],
    }

    for scope in settings.REQUIRED_GOOGLE_SERVICE_SCOPES:
        if scope in token_scopes:
            continue
        aliases = scope_aliases.get(scope, [])
        if any(alias in token_scopes for alias in aliases):
            continue
        return False
    return True


def _google_api_request(user, method: str, url: str, **kwargs):
    token, _ = _get_or_refresh_service_token(user)
    if not token:
        # Avoid silent failure when token is missing or expired.
        return None, Response(
            {
                "detail": "Google services not connected",
                "category": "auth_missing",
                "next_steps": "Reconnect Google services to continue.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = f"Bearer {token.access_token}"
    log_google(f"API call {method} {url} for {user.email}", category="api_call")
    request_timeout = kwargs.pop("timeout", 30)
    allow_retry = method.upper() in {"GET", "DELETE"}
    try:
        response = requests.request(method, url, headers=headers, timeout=request_timeout, **kwargs)
    except requests_exceptions.Timeout as exc:
        log_google(f"Timeout calling {url} for {user.email}: {exc}", category="network_timeout")
        if allow_retry:
            time.sleep(0.4)
            try:
                response = requests.request(method, url, headers=headers, timeout=request_timeout, **kwargs)
            except Exception as retry_exc:  # noqa: BLE001
                log_google(f"Retry failed for {url}: {retry_exc}", category="network_timeout")
                return None, Response(
                    {
                        "detail": "Google request timed out",
                        "category": "network_timeout",
                        "next_steps": "Check your internet connection and retry.",
                    },
                    status=status.HTTP_504_GATEWAY_TIMEOUT,
                )
        else:
            return None, Response(
                {
                    "detail": "Google request timed out",
                    "category": "network_timeout",
                    "next_steps": "Retry in a moment.",
                },
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
    except requests_exceptions.ConnectionError as exc:
        log_google(f"Network error calling {url} for {user.email}: {exc}", category="network_error")
        return None, Response(
            {
                "detail": "Network error contacting Google",
                "category": "network_error",
                "next_steps": "Check connectivity and retry.",
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )
    except Exception as exc:  # noqa: BLE001
        log_google(f"Google API request failed: {exc}", category="api_error")
        return None, Response(
            {
                "detail": "Google API request failed",
                "category": "api_error",
                "error": str(exc),
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if response.status_code == 401 and token.refresh_token:
        try:
            refreshed = refresh_access_token(token.refresh_token)
            token.access_token = refreshed.get("access_token", token.access_token)
            token.token_expiry = timezone.now() + timedelta(seconds=refreshed.get("expires_in", 3600))
            token.scopes = refreshed.get("scope") or token.scopes
            token.save(update_fields=["access_token", "token_expiry", "scopes", "updated_at"])
            headers["Authorization"] = f"Bearer {token.access_token}"
            log_google(f"Token refreshed during API call for {user.email}", category="auth_refresh_ok")
            response = requests.request(method, url, headers=headers, timeout=request_timeout, **kwargs)
        except GoogleAuthError as exc:  # noqa: BLE001
            log_google(f"Google API refresh failed for {user.email}: {exc}", category="auth_refresh_failed")
            return None, Response(
                {
                    "detail": "Token refresh failed",
                    "category": "auth_refresh_failed",
                    "next_steps": "Reconnect Google services.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

    log_google(
        f"API response {response.status_code} for {method} {url} ({user.email})",
        category="api_response",
    )

    return response, None


def _is_remote_debugger_running():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', 9222))
    sock.close()
    return result == 0


def _is_port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(('127.0.0.1', port)) == 0


@lru_cache(maxsize=None)
def get_driver_path():
    path = ChromeDriverManager().install()
    if not path.endswith("chromedriver.exe"):
        dir_path = os.path.dirname(path)
        candidate = os.path.join(dir_path, "chromedriver.exe")
        if os.path.exists(candidate):
            return candidate
        candidate_up = os.path.join(os.path.dirname(dir_path), "chromedriver.exe")
        if os.path.exists(candidate_up):
            return candidate_up
    return path


def _wait_and_click(driver, xpath, timeout=5):
    """Helper to wait for an element and click it using JS (most reliable method)."""
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
        driver.execute_script("arguments[0].click();", element)
        return True
    except TimeoutException:
        return False


def _find_chrome_executable():
    candidates = [
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def set_value_js(driver, element, value):
    driver.execute_script(
        "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
        element,
        value,
    )


def _normalize_header_text(value):
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


def _is_registration_header(header_key):
    if not header_key:
        return False
    explicit_matches = {
        "registration",
        "registrationno",
        "registrationnumber",
        "reg",
        "regno",
        "regnumber",
        "roll",
        "rollno",
        "rollnumber",
    }
    return header_key in explicit_matches or "registration" in header_key or "regno" in header_key


def _is_student_name_header(header_key):
    if not header_key:
        return False
    if header_key in {"name", "student", "studentname"}:
        return True
    if "studentname" in header_key:
        return True
    if header_key.endswith("name") and "course" not in header_key and "faculty" not in header_key:
        return True
    return False


def _parse_json_request_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return None

