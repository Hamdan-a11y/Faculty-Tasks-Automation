"""Google Drive operations."""

import os
import json
import requests
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from requests import exceptions as requests_exceptions

from auth_app.auth_service import (
    GoogleAuthError,
    refresh_access_token,
)
from auth_app.models import DriveFile
from auth_app.serializers import DriveFileSerializer
from .helpers import (
    _get_session_user,
    _get_or_refresh_service_token,
    _parse_google_error,
    _google_error_response,
    log_google,
)

@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def google_drive_upload(request):
    user = _get_session_user(request)
    if not user:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    token, _ = _get_or_refresh_service_token(user)
    if not token:
        return Response(
            {
                "detail": "Google services not connected",
                "category": "auth_missing",
                "next_steps": "Reconnect Google services.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    file_obj = request.FILES.get("file")
    folder_id = request.POST.get("folder_id", "").strip()

    if not file_obj:
        log_google(f"Drive upload missing file for {user.email}", category="input_invalid")
        return Response({"detail": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

    metadata = {"name": file_obj.name}
    if folder_id:
        metadata["parents"] = [folder_id]

    upload_url = (
        "https://www.googleapis.com/upload/drive/v3/files"
        "?uploadType=multipart&fields=id,name,mimeType,size,webViewLink,webContentLink,parents"
    )

    headers = {"Authorization": f"Bearer {token.access_token}"}
    files = {
        "metadata": ("metadata", json.dumps(metadata), "application/json"),
        "file": (
            file_obj.name,
            file_obj.read(),
            getattr(file_obj, "content_type", None) or "application/octet-stream",
        ),
    }

    def _attempt_upload(auth_header: str):
        headers_local = {"Authorization": auth_header}
        headers_local.update({"Accept": "application/json"})
        return requests.post(upload_url, headers=headers_local, files=files, timeout=30)

    try:
        response = _attempt_upload(headers["Authorization"])
    except requests_exceptions.Timeout as exc:
        log_google(f"Drive upload timeout for {user.email}: {exc}", category="network_timeout")
        return Response(
            {
                "detail": "Drive upload timed out",
                "category": "network_timeout",
                "next_steps": "Check network connectivity and retry.",
            },
            status=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except requests_exceptions.ConnectionError as exc:
        log_google(f"Drive upload network error for {user.email}: {exc}", category="network_error")
        return Response(
            {
                "detail": "Network error contacting Google Drive",
                "category": "network_error",
                "next_steps": "Check network connectivity and retry.",
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )
    except Exception as exc:  # noqa: BLE001
        log_google(f"Drive upload unexpected error for {user.email}: {exc}", category="api_error")
        return Response(
            {
                "detail": "Drive upload failed",
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
            response = _attempt_upload(f"Bearer {token.access_token}")
        except GoogleAuthError as exc:  # noqa: BLE001
            log_google(f"Drive upload refresh failed for {user.email}: {exc}", category="auth_refresh_failed")
            return Response(
                {
                    "detail": "Token refresh failed",
                    "category": "auth_refresh_failed",
                    "next_steps": "Reconnect Google services.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

    if not response.ok:
        return _google_error_response(user, response, "Drive upload")

    payload = response.json()
    try:
        with transaction.atomic():
            drive_file = DriveFile.objects.create(
                user=user,
                drive_file_id=payload.get("id", ""),
                name=payload.get("name", file_obj.name),
                mime_type=payload.get("mimeType", getattr(file_obj, "content_type", "")),
                size_bytes=int(payload.get("size", 0)) if payload.get("size") else None,
                folder_id=(payload.get("parents") or [None])[0] or folder_id,
                web_view_link=payload.get("webViewLink", ""),
                web_content_link=payload.get("webContentLink", ""),
            )
    except (IntegrityError, DatabaseError) as exc:
        # Avoid partial DB state while keeping Drive upload intact.
        log_google(
            f"Drive file DB save failed for {user.email}: {exc}; drive_id={payload.get('id')}",
            category="db_error",
        )
        return Response(
            {
                "detail": "Drive upload succeeded, but local save failed",
                "category": "db_error",
                "drive_file_id": payload.get("id"),
                "next_steps": "Retry refresh or contact support to resync files.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    log_google(
        f"Drive upload saved for {user.email}: drive_id={drive_file.drive_file_id}",
        category="drive_success",
    )

    serializer = DriveFileSerializer(drive_file)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([AllowAny])
def google_drive_files(request):
    user = _get_session_user(request)
    if not user:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        files_qs = DriveFile.objects.filter(user=user).order_by("-created_at")[:25]
        serializer = DriveFileSerializer(files_qs, many=True)
        return Response({"files": serializer.data})
    except DatabaseError as exc:
        log_google(f"Drive files load failed for {user.email}: {exc}", category="db_error")
        return Response(
            {
                "detail": "Failed to load Drive files",
                "category": "db_error",
                "next_steps": "Retry or refresh the page.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["DELETE"])
@permission_classes([AllowAny])
def google_drive_delete(request, file_id: str):
    user = _get_session_user(request)
    if not user:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    file_qs = DriveFile.objects.filter(user=user, drive_file_id=file_id)
    file_obj = file_qs.first()
    if not file_obj:
        # If we do not have a record, treat as gone to keep UX smooth.
        return Response(status=status.HTTP_204_NO_CONTENT)

    token, _ = _get_or_refresh_service_token(user)
    if not token:
        return Response(
            {
                "detail": "Google services not connected",
                "category": "auth_missing",
                "next_steps": "Reconnect Google services.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    delete_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"

    def _attempt_delete(auth_header: str):
        headers_local = {"Authorization": auth_header, "Accept": "application/json"}
        return requests.delete(delete_url, headers=headers_local, timeout=20)

    try:
        response = _attempt_delete(f"Bearer {token.access_token}")
    except requests_exceptions.Timeout as exc:
        log_google(f"Drive delete timeout for {user.email}: {exc}", category="network_timeout")
        return Response(
            {
                "detail": "Drive delete timed out",
                "category": "network_timeout",
                "next_steps": "Retry in a moment.",
            },
            status=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except requests_exceptions.ConnectionError as exc:
        log_google(f"Drive delete network error for {user.email}: {exc}", category="network_error")
        return Response(
            {
                "detail": "Network error contacting Google Drive",
                "category": "network_error",
                "next_steps": "Check connectivity and retry.",
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
            response = _attempt_delete(f"Bearer {token.access_token}")
        except GoogleAuthError as exc:  # noqa: BLE001
            log_google(f"Drive delete refresh failed for {user.email}: {exc}", category="auth_refresh_failed")
            return Response(
                {
                    "detail": "Token refresh failed",
                    "category": "auth_refresh_failed",
                    "next_steps": "Reconnect Google services.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

    # Remove local record regardless of Drive response to avoid stale UI; log and warn when needed.
    warning = None
    if response.status_code not in (200, 204, 404):
        warning = _parse_google_error(response)
        log_google(
            f"Drive delete non-OK ({response.status_code}) for {user.email}",
            category=warning["category"],
        )

    try:
        file_qs.delete()
    except DatabaseError as exc:
        log_google(f"Drive delete DB cleanup failed for {user.email}: {exc}", category="db_error")
        return Response(
            {
                "detail": "Drive delete completed, but local cleanup failed",
                "category": "db_error",
                "next_steps": "Refresh and retry if needed.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if warning:
        # Surface warning so the frontend can advise the faculty.
        return Response(
            {
                "detail": "Deleted locally. Google Drive delete needs attention.",
                "category": warning["category"],
                "error": warning["body"],
                "next_steps": "Check Drive permissions or retry.",
            },
            status=status.HTTP_200_OK,
        )

    log_google(f"Drive delete succeeded for {user.email}: drive_id={file_id}", category="drive_success")
    return Response(status=status.HTTP_204_NO_CONTENT)

