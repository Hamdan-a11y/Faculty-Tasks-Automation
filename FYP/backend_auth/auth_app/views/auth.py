"""User authentication, session management, Google OAuth and portal pages."""

import os
import json
import requests
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from auth_app.auth_service import (
    GoogleAuthError,
    build_authorization_url,
    build_service_authorization_url,
    exchange_code_for_service_tokens,
    exchange_code_for_tokens,
    fetch_user_profile,
    refresh_access_token,
)
from auth_app.models import (
    ClassroomCourse,
    ClassroomCoursework,
    DriveFile,
    FacultyUser,
    GoogleServiceToken,
)
from auth_app.serializers import FacultyUserSerializer
from auto_reminder.entry_deadlines import ENTRY_DEADLINES
from auto_reminder.quiz_entry_deadlines import QUIZ_ENTRY_DEADLINES
from auto_reminder.mid_term_events import MID_TERM_EVENTS
from .helpers import (
    log_auth,
    log_google,
    _get_session_user,
    _is_allowed_email,
    _get_or_refresh_service_token,
    _has_required_service_scopes,
    SESSION_KEY,
    WHITELISTED_EMAILS,
    ALLOWED_DOMAIN_SUFFIXES,
)

def home_redirect(request):
    user = _get_session_user(request)
    if user:
        return redirect("auth_app:dashboard")
    return redirect("auth_app:login")


def login_page(request):
    return render(request, "auth/auth.html")


def unauthorized_page(request):
    user_id = request.session.get(SESSION_KEY)
    log_auth(
        "unauthorized_page hit; session keys="
        + ",".join(request.session.keys())
        + f"; faculty_user_id={user_id}"
    )
    return render(request, "auth/unauthorized.html")


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def logout_view(request):
    # Flush session and send user back to login
    request.session.flush()
    response = redirect("auth_app:login")
    response.delete_cookie(SESSION_KEY)
    return response


def google_services_page(request):
    user = _get_session_user(request)
    if not user:
        return redirect("auth_app:login")

    token, refreshed = _get_or_refresh_service_token(user)
    has_scopes = _has_required_service_scopes(token)
    context = {
        "user_name": user.name or user.email,
        "google_connected": bool(token) and has_scopes,
        "google_connect_url": reverse("google-services-connect-root"),
        "token_expires_at": token.token_expiry.isoformat() if token and token.token_expiry else "",
        "token_refreshed": refreshed,
        "missing_scopes": not has_scopes,
    }
    # No auto-redirect; user can opt-in manually
    request.session.pop("services_consent_attempted", None)
    return render(request, "auth/google_services.html", context)


def google_services_connect(request):
    user = _get_session_user(request)
    if not user:
        return redirect("auth_app:login")

    force = request.GET.get("force") == "1"

    if not _is_allowed_email(user.email):
        log_auth(f"services_connect blocked email {user.email}")
        request.session.flush()
        return redirect("auth_app:unauthorized")

    token, _ = _get_or_refresh_service_token(user)
    if token and not force:
        if not _has_required_service_scopes(token):
            token.delete()
        else:
            return redirect(f"{reverse('auth_app:google_services')}?connected=1")

    if token and force:
        token.delete()

    state = uuid.uuid4().hex
    request.session["google_services_state"] = state
    auth_url = build_service_authorization_url(state)
    log_auth(f"services_connect redirect for {user.email} state={state}")
    return redirect(auth_url)


def google_services_callback(request):
    user = _get_session_user(request)
    if not user:
        log_auth("services_callback missing session user")
        return redirect("auth_app:login")

    expected_state = request.session.pop("google_services_state", None)
    if expected_state and request.GET.get("state") != expected_state:
        return HttpResponseBadRequest("Invalid or missing state parameter")

    if request.GET.get("error"):
        log_auth(f"Google services callback error: {request.GET.get('error')}")
        return redirect(f"{reverse('auth_app:google_services')}?error=access_denied")

    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Missing authorization code")

    existing_token = GoogleServiceToken.objects.filter(user=user).first()

    try:
        tokens = exchange_code_for_service_tokens(code)
    except GoogleAuthError as exc:  # noqa: BLE001
        log_auth(f"Google services token exchange failed for {user.email}: {exc}")
        return redirect(f"{reverse('auth_app:google_services')}?error=token_exchange_failed")

    expires_in = tokens.get("expires_in", 3600)
    expiry = timezone.now() + timedelta(seconds=expires_in)
    scopes = tokens.get("scope") or " ".join(settings.GOOGLE_SERVICE_SCOPES)
    refresh_token = tokens.get("refresh_token") or (existing_token.refresh_token if existing_token else "")

    log_auth(
        f"services_callback tokens for {user.email}: scope_keys={list(tokens.keys())}, "
        f"scopes={scopes}, refresh_present={bool(refresh_token)}"
    )

    GoogleServiceToken.objects.update_or_create(
        user=user,
        defaults={
            "access_token": tokens.get("access_token", ""),
            "refresh_token": refresh_token,
            "token_expiry": expiry,
            "scopes": scopes,
        },
    )
    # Clear consent attempt flag after successful connect
    request.session.pop("services_consent_attempted", None)

    return redirect(f"{reverse('auth_app:google_services')}?connected=1")


@api_view(["GET"])
@permission_classes([AllowAny])
def google_services_status(request):
    user = _get_session_user(request)
    if not user:
        return Response({"connected": False}, status=status.HTTP_401_UNAUTHORIZED)

    token, _ = _get_or_refresh_service_token(user)
    if not token:
        return Response({"connected": False, "reason": "not_connected"}, status=status.HTTP_200_OK)

    return Response(
        {
            "connected": True,
            "token_expiry": token.token_expiry,
            "scopes": token.scopes,
        }
    )


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def google_services_disconnect(request):
    user = _get_session_user(request)
    if not user:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    token = GoogleServiceToken.objects.filter(user=user).first()
    if token:
        revoke_token = token.refresh_token or token.access_token
        if revoke_token:
            try:
                requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": revoke_token},
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
            except Exception as exc:  # noqa: BLE001 - revoke best-effort
                log_google(f"services_disconnect revoke failed for {user.email}: {exc}", category="api_error")
        token.delete()

    purge = (request.POST.get("purge") or "").lower() in {"1", "true", "yes", "on"}
    if purge:
        try:
            DriveFile.objects.filter(user=user).delete()
            ClassroomCoursework.objects.filter(user=user).delete()
            ClassroomCourse.objects.filter(user=user).delete()
        except DatabaseError as exc:
            log_google(f"services_disconnect purge failed for {user.email}: {exc}", category="db_error")
            return Response(
                {
                    "detail": "Disconnected, but local cleanup failed",
                    "category": "db_error",
                    "next_steps": "Retry purge or contact support.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    request.session.pop("services_consent_attempted", None)
    return Response({"disconnected": True})


def auto_reminder_page(request):
    user_id = request.session.get(SESSION_KEY)
    user_name = None
    if user_id:
        try:
            user = FacultyUser.objects.get(id=user_id)
            user_name = user.name or user.email
        except FacultyUser.DoesNotExist:
            pass
    context = {
        "user_name": user_name,
        "entry_deadlines": ENTRY_DEADLINES,
        "quiz_deadlines": QUIZ_ENTRY_DEADLINES,
        "midterm_events": MID_TERM_EVENTS,
    }
    return render(request, "auth/auto_reminder.html", context)


def auto_alert_page(request):
    # Legacy alias to keep old links working
    return auto_reminder_page(request)


def dashboard_page(request):
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return redirect("auth_app:login")
    try:
        user = FacultyUser.objects.get(id=user_id)
    except FacultyUser.DoesNotExist:
        request.session.flush()
        return redirect("auth_app:login")
    context = {"user_name": user.name or user.email}
    return render(request, "auth/dashboard.html", context)


@api_view(["GET"])
@permission_classes([AllowAny])
def current_user(request):
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        user = FacultyUser.objects.get(id=user_id)
    except FacultyUser.DoesNotExist:
        request.session.flush()
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    serializer = FacultyUserSerializer(user)
    return Response(serializer.data)


def google_login(request):
    return redirect(build_authorization_url())


def google_callback(request):
    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Missing authorization code")

    try:
        tokens = exchange_code_for_tokens(code)
        profile = fetch_user_profile(tokens)
        log_auth("tokens received keys=" + ",".join(tokens.keys()))
    except GoogleAuthError as exc:
        log_auth(f"GoogleAuthError: {exc}")
        return redirect("auth_app:unauthorized")
    except Exception as exc:  # noqa: BLE001
        log_auth(f"Unexpected auth error: {exc}")
        return redirect("auth_app:unauthorized")

    email = (profile.get("email") or "").lower()
    username = email.split("@", 1)[0] if "@" in email else ""
    hd_param = (request.GET.get("hd") or "").lower()
    is_whitelisted = email in WHITELISTED_EMAILS

    # Absolute allow for static/test accounts: skip domain and username checks
    if is_whitelisted:
        pass

    log_auth(
        f"callback email='{email}', hd='{hd_param}', verified={profile.get('email_verified')}, "
        f"whitelist_match={is_whitelisted}, whitelist={sorted(WHITELISTED_EMAILS)}, "
        f"domains={ALLOWED_DOMAIN_SUFFIXES}, username='{username}'"
    )

    if not is_whitelisted:
        if not any(email.endswith(suffix) for suffix in ALLOWED_DOMAIN_SUFFIXES):
            log_auth(f"BLOCK domain policy for '{email}'")
            request.session.flush()
            return redirect("auth_app:unauthorized")

    user, _ = FacultyUser.objects.update_or_create(
        google_id=profile.get("google_id"),
        defaults={
            "email": email,
            "name": profile.get("name") or "",
            "profile_image": profile.get("picture") or "",
            "last_login": timezone.now(),
        },
    )

    request.session.flush()
    request.session[SESSION_KEY] = user.id

    log_auth(f"SUCCESS login '{email}' -> user_id={user.id}")
    return redirect("auth_app:dashboard")

