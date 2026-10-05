"""Google Classroom courses and coursework operations."""

import os
import json
import requests
from datetime import datetime, timedelta

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
from auth_app.models import (
    ClassroomCourse,
    ClassroomCoursework,
    DriveFile,
)
from auth_app.serializers import (
    ClassroomCourseSerializer,
    ClassroomCourseworkSerializer,
    DriveFileSerializer,
)
from auto_reminder.entry_deadlines import ENTRY_DEADLINES
from auto_reminder.quiz_entry_deadlines import QUIZ_ENTRY_DEADLINES
from auto_reminder.mid_term_events import MID_TERM_EVENTS
from .helpers import (
    _get_session_user,
    _get_or_refresh_service_token,
    _google_api_request,
    _google_error_response,
    log_google,
)

@api_view(["GET"])
@permission_classes([AllowAny])
def google_classroom_courses(request):
    user = _get_session_user(request)
    if not user:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    url = "https://classroom.googleapis.com/v1/courses?courseStates=ACTIVE&pageSize=50"
    response, err = _google_api_request(user, "GET", url)
    if err:
        return err

    if not response.ok:
        return _google_error_response(user, response, "Classroom courses")

    data = response.json()
    courses = data.get("courses", [])
    serialized_courses = []
    db_warnings = []
    for c in courses:
        try:
            course_obj, _ = ClassroomCourse.objects.update_or_create(
                user=user,
                course_id=c.get("id", ""),
                defaults={
                    "name": c.get("name", ""),
                    "section": c.get("section", ""),
                    "description_heading": c.get("descriptionHeading", ""),
                    "enrollment_code": c.get("enrollmentCode", ""),
                    "alternate_link": c.get("alternateLink", ""),
                    "state": c.get("courseState", ""),
                },
            )
            serialized_courses.append(course_obj)
        except (IntegrityError, DatabaseError) as exc:
            # Continue sync even if a single course fails to persist.
            log_google(
                f"Course DB save failed for {user.email} course_id={c.get('id')}: {exc}",
                category="db_error",
            )
            db_warnings.append(c.get("id", "unknown"))

    serializer = ClassroomCourseSerializer(serialized_courses, many=True)
    response_payload = {"courses": serializer.data}
    if db_warnings:
        response_payload["warnings"] = {
            "category": "db_error",
            "detail": "Some courses could not be saved locally.",
            "course_ids": db_warnings,
        }
    log_google(
        f"Classroom courses synced for {user.email}: count={len(serialized_courses)}",
        category="classroom_sync",
    )
    return Response(response_payload)


@api_view(["POST"])
@permission_classes([AllowAny])
def google_classroom_create_course(request):
    user = _get_session_user(request)
    if not user:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    payload = request.data or {}
    name = (payload.get("name") or "").strip()
    section = (payload.get("section") or "").strip()
    description_heading = (payload.get("description_heading") or "").strip()
    description = (payload.get("description") or "").strip()
    room = (payload.get("room") or "").strip()
    course_state = (payload.get("course_state") or "").strip()

    if not name:
        log_google(f"Course create missing name for {user.email}", category="input_invalid")
        return Response({"detail": "name is required"}, status=status.HTTP_400_BAD_REQUEST)

    course_body = {"name": name, "ownerId": "me"}
    if section:
        course_body["section"] = section
    if description_heading:
        course_body["descriptionHeading"] = description_heading
    if description:
        course_body["description"] = description
    if room:
        course_body["room"] = room
    if course_state:
        course_body["courseState"] = course_state.upper()

    response, err = _google_api_request(
        user,
        "POST",
        "https://classroom.googleapis.com/v1/courses",
        headers={"Content-Type": "application/json"},
        data=json.dumps(course_body),
    )
    if err:
        return err

    if not response.ok:
        return _google_error_response(user, response, "Course create")

    course = response.json()
    try:
        with transaction.atomic():
            course_obj, _ = ClassroomCourse.objects.update_or_create(
                user=user,
                course_id=course.get("id", ""),
                defaults={
                    "name": course.get("name", name),
                    "section": course.get("section", section),
                    "description_heading": course.get("descriptionHeading", description_heading),
                    "enrollment_code": course.get("enrollmentCode", ""),
                    "alternate_link": course.get("alternateLink", ""),
                    "state": course.get("courseState", course_state),
                },
            )
    except (IntegrityError, DatabaseError) as exc:
        log_google(
            f"Course DB save failed for {user.email} course_id={course.get('id')}: {exc}",
            category="db_error",
        )
        return Response(
            {
                "detail": "Course created in Classroom, but local save failed",
                "category": "db_error",
                "next_steps": "Retry sync or refresh courses list.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    serializer = ClassroomCourseSerializer(course_obj)
    log_google(
        f"Classroom course created for {user.email}: course_id={course_obj.course_id}",
        category="classroom_sync",
    )
    return Response({"course": serializer.data}, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([AllowAny])
def google_classroom_coursework(request):
    user = _get_session_user(request)
    if not user:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    payload = request.data or {}
    course_id = (payload.get("course_id") or "").strip()
    title = (payload.get("title") or "").strip()
    description = payload.get("description") or ""
    max_points = payload.get("max_points")
    due_date = (payload.get("due_date") or "").strip()
    due_time = (payload.get("due_time") or "").strip()
    drive_file_id = (payload.get("drive_file_id") or "").strip()

    if not course_id:
        log_google(f"Coursework create missing course_id for {user.email}", category="input_invalid")
        return Response({"detail": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
    if not title:
        log_google(f"Coursework create missing title for {user.email}", category="input_invalid")
        return Response({"detail": "title is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        course_obj, _ = ClassroomCourse.objects.get_or_create(
            user=user,
            course_id=course_id,
            defaults={"name": course_id, "state": "UNKNOWN"},
        )
    except DatabaseError as exc:
        log_google(
            f"Coursework create course lookup failed for {user.email}: {exc}",
            category="db_error",
        )
        return Response(
            {
                "detail": "Failed to prepare coursework",
                "category": "db_error",
                "next_steps": "Retry or refresh courses list.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    coursework_body = {
        "title": title,
        "description": description,
        "state": "PUBLISHED",
        "workType": "ASSIGNMENT",
    }
    if max_points not in (None, ""):
        try:
            coursework_body["maxPoints"] = float(max_points)
        except ValueError:
            return Response({"detail": "max_points must be numeric"}, status=status.HTTP_400_BAD_REQUEST)

    now_local = timezone.localtime()
    due_dt = None

    if due_date:
        try:
            dt = datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            return Response({"detail": "due_date must be YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        due_dt = dt.date()
        if due_dt < now_local.date():
            return Response({"detail": "Due date cannot be in the past"}, status=status.HTTP_400_BAD_REQUEST)

        coursework_body["dueDate"] = {"year": dt.year, "month": dt.month, "day": dt.day}

    if due_time:
        tt = None
        for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
            try:
                tt = datetime.strptime(due_time, fmt).time()
                break
            except ValueError:
                continue

        if not tt:
            return Response({"detail": "due_time must be HH:MM in 24h or 12h with AM/PM"}, status=status.HTTP_400_BAD_REQUEST)

        coursework_body["dueTime"] = {"hours": tt.hour, "minutes": tt.minute}

        if due_dt and due_dt == now_local.date() and (tt.hour, tt.minute) <= (now_local.hour, now_local.minute):
            # Auto-shift to a near-future time for demo friendliness.
            shifted = now_local + timedelta(minutes=5)
            if shifted.date() == due_dt:
                coursework_body["dueTime"] = {"hours": shifted.hour, "minutes": shifted.minute}
            else:
                coursework_body["dueTime"] = {"hours": 23, "minutes": 59}

    elif due_dt and due_dt == now_local.date():
        # Default to end-of-day when assigning today to avoid Google rejecting missing time.
        coursework_body["dueTime"] = {"hours": 23, "minutes": 59}

    if drive_file_id:
        coursework_body["materials"] = [
            {
                "driveFile": {
                    "driveFile": {"id": drive_file_id},
                    "shareMode": "VIEW",
                }
            }
        ]

    url = f"https://classroom.googleapis.com/v1/courses/{course_id}/courseWork?fields=id,title,alternateLink,state,dueDate,dueTime,materials,maxPoints"
    response, err = _google_api_request(
        user,
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        data=json.dumps(coursework_body),
    )
    if err:
        return err

    if not response.ok:
        return _google_error_response(user, response, "Coursework create")

    cw = response.json()
    due_date_value = None
    due_time_value = ""
    if cw.get("dueDate"):
        d = cw["dueDate"]
        due_date_value = datetime(d.get("year", 2000), d.get("month", 1), d.get("day", 1)).date()
    if cw.get("dueTime"):
        t = cw["dueTime"]
        hours = t.get("hours") or 0
        minutes = t.get("minutes") or 0
        due_time_value = f"{hours:02d}:{minutes:02d}"

    try:
        with transaction.atomic():
            coursework_obj, _ = ClassroomCoursework.objects.update_or_create(
                user=user,
                course=course_obj,
                course_google_id=course_id,
                coursework_id=cw.get("id", ""),
                defaults={
                    "title": cw.get("title", title),
                    "description": description,
                    "state": cw.get("state", ""),
                    "alternate_link": cw.get("alternateLink", ""),
                    "due_date": due_date_value,
                    "due_time": due_time_value,
                    "max_points": cw.get("maxPoints"),
                    "drive_file_id": drive_file_id,
                },
            )
    except (IntegrityError, DatabaseError) as exc:
        log_google(
            f"Coursework DB save failed for {user.email} coursework_id={cw.get('id')}: {exc}",
            category="db_error",
        )
        return Response(
            {
                "detail": "Coursework created in Classroom, but local save failed",
                "category": "db_error",
                "next_steps": "Retry sync or refresh coursework list.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    serializer = ClassroomCourseworkSerializer(coursework_obj)
    log_google(
        f"Coursework created for {user.email}: coursework_id={coursework_obj.coursework_id}",
        category="classroom_sync",
    )
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def google_classroom_material_upload(request):
    """Upload a lecture document to Drive and post it as Classroom material."""
    user = _get_session_user(request)
    if not user:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    try:
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
        drive_file_id = (request.POST.get("drive_file_id") or "").strip()
        course_id = (request.POST.get("course_id") or "").strip()
        title = (request.POST.get("title") or "").strip()
        description = request.POST.get("description") or ""
        folder_id = (request.POST.get("folder_id") or "").strip()

        if not course_id:
            log_google(f"Material upload missing course_id for {user.email}", category="input_invalid")
            return Response({"detail": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not file_obj and not drive_file_id:
            log_google(f"Material upload missing file for {user.email}", category="input_invalid")
            return Response({"detail": "file or drive_file_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        drive_file = None

        if drive_file_id:
            drive_file = DriveFile.objects.filter(user=user, drive_file_id=drive_file_id).first()
            # If not found locally, still allow using provided ID for Classroom material.
            drive_payload = {
                "id": drive_file_id,
                "name": (drive_file.name if drive_file else title or "Material"),
                "mimeType": drive_file.mime_type if drive_file else "application/octet-stream",
                "parents": [folder_id] if folder_id else [],
                "webViewLink": getattr(drive_file, "web_view_link", ""),
                "webContentLink": getattr(drive_file, "web_content_link", ""),
            }
            material_title = title or drive_payload.get("name") or "Material"
        else:
            metadata = {"name": file_obj.name}
            if folder_id:
                metadata["parents"] = [folder_id]

            upload_url = (
                "https://www.googleapis.com/upload/drive/v3/files"
                "?uploadType=multipart&fields=id,name,mimeType,size,webViewLink,webContentLink,parents"
            )

            files = {
                "metadata": ("metadata", json.dumps(metadata), "application/json"),
                "file": (
                    file_obj.name,
                    file_obj.read(),
                    getattr(file_obj, "content_type", None) or "application/octet-stream",
                ),
            }

            def _attempt_upload(auth_header: str):
                headers_local = {"Authorization": auth_header, "Accept": "application/json"}
                return requests.post(upload_url, headers=headers_local, files=files, timeout=30)

            try:
                drive_response = _attempt_upload(f"Bearer {token.access_token}")
            except requests_exceptions.Timeout as exc:
                log_google(f"Drive upload timeout for {user.email}: {exc}", category="network_timeout")
                return Response(
                    {
                        "detail": "Drive upload timed out",
                        "category": "network_timeout",
                        "next_steps": "Check network and retry.",
                    },
                    status=status.HTTP_504_GATEWAY_TIMEOUT,
                )
            except requests_exceptions.ConnectionError as exc:
                log_google(f"Drive upload network error for {user.email}: {exc}", category="network_error")
                return Response(
                    {
                        "detail": "Network error contacting Google Drive",
                        "category": "network_error",
                        "next_steps": "Check connectivity and retry.",
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            if drive_response.status_code == 401 and token.refresh_token:
                try:
                    refreshed = refresh_access_token(token.refresh_token)
                    token.access_token = refreshed.get("access_token", token.access_token)
                    token.token_expiry = timezone.now() + timedelta(seconds=refreshed.get("expires_in", 3600))
                    token.scopes = refreshed.get("scope") or token.scopes
                    token.save(update_fields=["access_token", "token_expiry", "scopes", "updated_at"])
                    drive_response = _attempt_upload(f"Bearer {token.access_token}")
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

            if not drive_response.ok:
                return _google_error_response(user, drive_response, "Drive upload (material)")

            drive_payload = drive_response.json()
            try:
                with transaction.atomic():
                    drive_file = DriveFile.objects.create(
                        user=user,
                        drive_file_id=drive_payload.get("id", ""),
                        name=drive_payload.get("name", file_obj.name),
                        mime_type=drive_payload.get("mimeType", getattr(file_obj, "content_type", "")),
                        size_bytes=int(drive_payload.get("size", 0)) if drive_payload.get("size") else None,
                        folder_id=(drive_payload.get("parents") or [None])[0] or folder_id,
                        web_view_link=drive_payload.get("webViewLink", ""),
                        web_content_link=drive_payload.get("webContentLink", ""),
                    )
            except (IntegrityError, DatabaseError) as exc:
                # Avoid partial DB state while keeping Drive upload intact.
                log_google(
                    f"Material Drive file DB save failed for {user.email}: {exc}; drive_id={drive_payload.get('id')}",
                    category="db_error",
                )
                return Response(
                    {
                        "detail": "Drive upload succeeded, but local save failed",
                        "category": "db_error",
                        "drive_file_id": drive_payload.get("id"),
                        "next_steps": "Retry refresh or contact support to resync files.",
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            log_google(
                f"Drive upload (material) saved for {user.email}: drive_id={drive_file.drive_file_id}",
                category="drive_success",
            )

            material_title = title or drive_file.name
        classroom_body = {
            "title": material_title,
            "description": description,
            "state": "PUBLISHED",
            "materials": [
                {
                    "driveFile": {
                        "driveFile": {"id": drive_file.drive_file_id if drive_file else drive_payload.get("id", "")},
                        "shareMode": "VIEW",
                    }
                }
            ],
        }

        material_url = (
            f"https://classroom.googleapis.com/v1/courses/{course_id}/courseWorkMaterials"
            "?fields=id,title,alternateLink,state,materials"
        )

        response, err = _google_api_request(
            user,
            "POST",
            material_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(classroom_body),
        )
        if err:
            return err

        if not response.ok:
            return _google_error_response(user, response, "Material create")

        material = response.json()
        log_google(
            f"Material created for {user.email}: course_id={course_id}",
            category="classroom_sync",
        )
        return Response(
            {
                "material": material,
                "drive_file": DriveFileSerializer(drive_file).data,
            },
            status=status.HTTP_201_CREATED,
        )
    except Exception as exc:  # noqa: BLE001
        # Avoid crashing the module; surface a clear recovery path.
        log_google(f"Material upload unexpected error for {user.email}: {exc}", category="unexpected")
        return Response({"detail": "Unexpected error", "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([AllowAny])
def google_classroom_submissions(request):
    user = _get_session_user(request)
    if not user:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    course_id = (request.GET.get("course_id") or "").strip()
    coursework_id = (request.GET.get("coursework_id") or "").strip()
    if not course_id or not coursework_id:
        log_google(f"Submissions fetch missing params for {user.email}", category="input_invalid")
        return Response({"detail": "course_id and coursework_id are required"}, status=status.HTTP_400_BAD_REQUEST)

    url = f"https://classroom.googleapis.com/v1/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions"
    response, err = _google_api_request(user, "GET", url)
    if err:
        return err

    if not response.ok:
        return _google_error_response(user, response, "Submissions fetch")

    data = response.json()
    return Response({"submissions": data.get("studentSubmissions", [])})

