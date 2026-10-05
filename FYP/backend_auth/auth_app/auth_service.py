from urllib.parse import urlencode

import requests
from django.conf import settings
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class GoogleAuthError(Exception):
    """Raised when Google OAuth flow fails."""


def build_authorization_url(state: str | None = None) -> str:
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return f"{AUTH_URL}?{urlencode(params)}"


def build_service_authorization_url(state: str | None = None) -> str:
    scopes = " ".join(settings.GOOGLE_SERVICE_SCOPES)
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_SERVICES_REDIRECT_URI,
        "response_type": "code",
        "scope": scopes,
        "access_type": "offline",
        # 'prompt=consent' forces the consent screen; approval_prompt is deprecated and conflicts with prompt.
        "prompt": "consent",
        "include_granted_scopes": "false",
    }
    if state:
        params["state"] = state
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    data = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    response = requests.post(TOKEN_URL, data=data, timeout=10)
    if not response.ok:
        raise GoogleAuthError(f"Token exchange failed: {response.text}")
    payload = response.json()
    if "id_token" not in payload:
        raise GoogleAuthError("No id_token returned from Google")
    return payload


def exchange_code_for_service_tokens(code: str) -> dict:
    data = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_SERVICES_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    response = requests.post(TOKEN_URL, data=data, timeout=10)
    if not response.ok:
        raise GoogleAuthError(f"Token exchange failed: {response.text}")
    payload = response.json()
    if "access_token" not in payload:
        raise GoogleAuthError("No access_token returned from Google")
    return payload


def refresh_access_token(refresh_token: str) -> dict:
    data = {
        "refresh_token": refresh_token,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }
    response = requests.post(TOKEN_URL, data=data, timeout=10)
    if not response.ok:
        raise GoogleAuthError(f"Token refresh failed: {response.text}")
    payload = response.json()
    if "access_token" not in payload:
        raise GoogleAuthError("No access_token returned during refresh")
    return payload


def fetch_user_profile(tokens: dict) -> dict:
    try:
        id_info = id_token.verify_oauth2_token(
            tokens.get("id_token"),
            GoogleRequest(),
            settings.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=300,
        )
    except Exception as exc:  # noqa: BLE001 - google library raises many types
        raise GoogleAuthError(f"Invalid id_token: {exc}") from exc

    access_token = tokens.get("access_token")
    picture = None
    name = None
    if access_token:
        userinfo_resp = requests.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10
        )
        if userinfo_resp.ok:
            userinfo = userinfo_resp.json()
            picture = userinfo.get("picture")
            name = userinfo.get("name")

    profile = {
        "google_id": id_info.get("sub"),
        "email": id_info.get("email"),
        "email_verified": id_info.get("email_verified", False),
        "name": name or id_info.get("name", ""),
        "picture": picture or id_info.get("picture", ""),
    }

    if not profile["google_id"] or not profile["email"]:
        raise GoogleAuthError("Missing required user info from Google response")

    return profile
