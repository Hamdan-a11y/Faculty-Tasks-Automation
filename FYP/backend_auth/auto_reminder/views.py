"""Assignment alerts API (minimal scope).

Only supports assignment-related alerts with a single enable toggle and a list view.
"""

import uuid
import logging
import smtplib

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.core.mail import send_mail, BadHeaderError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from auth_app.models import FacultyUser, ClassroomCourse
from .classroom_service import ClassroomAPI, ClassroomAPIError
from .models import AssignmentAlert, ReminderPreference, ASSIGNMENT_ALERT_TYPES
from .runner import run_alerts_for_faculty
from .calendar_service import CalendarAPI, CalendarAPIError
from .entry_deadlines import ENTRY_DEADLINES
from .quiz_entry_deadlines import QUIZ_ENTRY_DEADLINES
from .mid_term_events import MID_TERM_EVENTS

SESSION_KEY = "faculty_user_id"
CALENDAR_ASSIGNMENT_RANGE_DAYS = 3
logger = logging.getLogger(__name__)


def log_auto(message: str, category: str = "info") -> None:
    """Auto-reminder log helper with timestamp + category for viva traceability."""
    logger.info("[%s] [AutoReminder] [%s] %s", timezone.now().isoformat(), category, message)


class SessionFacultyPermission(BasePermission):
    """Allow access if faculty session key is present."""

    def has_permission(self, request, view) -> bool:  # pragma: no cover - simple guard
        return SESSION_KEY in request.session


def get_faculty_from_request(request):
    """Extract faculty user from session."""
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    try:
        return FacultyUser.objects.get(id=user_id)
    except FacultyUser.DoesNotExist:
        return None


def get_faculty_preference(faculty: FacultyUser) -> bool:
    """Return whether assignment alerts are enabled (defaults to True)."""

    try:
        pref, _ = ReminderPreference.objects.get_or_create(faculty=faculty)
    except DatabaseError as exc:
        log_auto(f"Preference load failed for {faculty.email}: {exc}", category="db_error")
        return Response(
            {"detail": "Unable to load reminder preferences"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return bool(pref.enable_reminders)


def _stage_from_days(days_remaining: int | None) -> str | None:
    if days_remaining is None:
        return None
    if days_remaining == 2:
        return "two_days"
    if days_remaining == 1:
        return "one_day"
    if days_remaining == 0:
        return "today"
    if days_remaining < 0:
        return "overdue"
    return None


def _within_refresh_window(due_date) -> bool:
    if not due_date:
        return False
    today = timezone.localdate()
    try:
        days = (due_date - today).days
    except (TypeError, AttributeError):
        return False
    return 0 <= days <= 2


def _stage_label(stage_code: str | None) -> str | None:
    lookup = {
        "two_days": "Due in 2 days",
        "one_day": "Due tomorrow",
        "today": "Due today",
        "overdue": "Overdue",
        "single": None,
        None: None,
    }
    return lookup.get(stage_code)


def _seed_fixed_deadlines(faculty: FacultyUser):
    """Ensure fixed calendar alerts exist without heavy Classroom calls."""

    today = timezone.localdate()
    horizon_future = today + timezone.timedelta(days=400)
    horizon_past = today - timezone.timedelta(days=90)

    def seed(course_id: str, assignment_id: str, alert_type: str, defaults: dict):
        try:
            alert, created = AssignmentAlert.objects.get_or_create(
                faculty=faculty,
                course_id=course_id,
                assignment_id=assignment_id,
                alert_type=alert_type,
                alert_stage=defaults.get("alert_stage", "single"),
                defaults=defaults,
            )
        except (IntegrityError, DatabaseError) as exc:
            log_auto(
                f"Seed deadline failed for {faculty.email} / {assignment_id}: {exc}",
                category="db_error",
            )
            return None
        if not created and alert.status == "deleted":
            return alert
        if not created and defaults.get("deadline_date") and alert.deadline_date != defaults["deadline_date"]:
            alert.deadline_date = defaults["deadline_date"]
            alert.status = "queued"
            alert.sent_at = None
            try:
                alert.save(update_fields=["deadline_date", "status", "sent_at"])
            except DatabaseError as exc:
                log_auto(
                    f"Seed deadline update failed for {faculty.email} / {assignment_id}: {exc}",
                    category="db_error",
                )
        return alert

    for entry in ENTRY_DEADLINES:
        deadline = entry.get("deadline")
        if not deadline or deadline < horizon_past or deadline > horizon_future:
            continue
        if deadline < today:
            continue
        stage_code = _stage_from_days((deadline - today).days) or "single"
        seed(
            course_id="entry-deadline",
            assignment_id=f"entry-{entry.get('assignment_number')}",
            alert_type="entry_deadline",
            defaults={
                "assignment_number": entry.get("assignment_number"),
                "deadline_date": deadline,
                "status": "queued",
                "alert_stage": stage_code,
            },
        )

    for entry in QUIZ_ENTRY_DEADLINES:
        deadline = entry.get("deadline")
        if not deadline or deadline < horizon_past or deadline > horizon_future:
            continue
        if deadline < today:
            continue
        stage_code = _stage_from_days((deadline - today).days) or "single"
        seed(
            course_id="quiz-entry",
            assignment_id=f"quiz-{entry.get('quiz_number')}",
            alert_type="quiz_entry_deadline",
            defaults={
                "quiz_number": entry.get("quiz_number"),
                "deadline_date": deadline,
                "status": "queued",
                "alert_stage": stage_code,
            },
        )

    for entry in MID_TERM_EVENTS:
        event_date = entry.get("event_date")
        if not event_date or event_date < horizon_past or event_date > horizon_future:
            continue
        if event_date < today:
            continue
        stage_code = _stage_from_days((event_date - today).days) or "single"
        seed(
            course_id="mid-term",
            assignment_id=f"mid-term-{entry.get('key')}",
            alert_type="mid_term_event",
            defaults={
                "event_name": entry.get("name"),
                "event_date": event_date,
                "deadline_date": event_date,
                "status": "queued",
                "alert_stage": stage_code,
            },
        )


def _serialize_alert(alert: AssignmentAlert) -> dict:
    due = alert.deadline_date or alert.event_date
    today = timezone.localdate()
    overdue = bool(due and due < today and not alert.is_completed)
    stage_label = _stage_label(alert.alert_stage)
    return {
        "id": alert.id,
        "courseId": alert.course_id,
        "assignmentId": alert.assignment_id,
        "assignmentName": alert.event_name
        or (f"Assignment {alert.assignment_number}" if alert.assignment_number else f"Assignment {alert.assignment_id}"),
        "dueDate": due.isoformat() if due else None,
        "alertType": alert.alert_type,
        "alertStage": alert.alert_stage,
        "stageLabel": stage_label,
        "status": "completed" if alert.is_completed else ("overdue" if overdue else alert.status),
        "isCompleted": alert.is_completed,
        "completedAt": alert.completed_at.isoformat() if alert.completed_at else None,
        "submitted": (alert.summary_snapshot or {}).get("submitted"),
        "total": (alert.summary_snapshot or {}).get("total"),
        "notSubmitted": (alert.summary_snapshot or {}).get("not_submitted"),
    }


def _email_configured() -> bool:
    return bool(getattr(settings, "EMAIL_HOST_USER", "") and getattr(settings, "EMAIL_HOST_PASSWORD", ""))


def _build_email_content(alert: AssignmentAlert, course_lookup: dict[str, str]) -> tuple[str, str]:
    course_name = course_lookup.get(alert.course_id, alert.course_id)
    due = alert.deadline_date or alert.event_date
    due_text = due.isoformat() if due else "No due date"
    summary = alert.summary_snapshot or {}
    submitted = summary.get("submitted", 0)
    total = summary.get("total", 0)
    pending = summary.get("not_submitted", max(total - submitted, 0))

    if alert.alert_type == "entry_deadline":
        subject = f"Assignment {alert.assignment_number or '?'} Entry Deadline – Action Required"
        title = f"Assignment {alert.assignment_number or '?'} marks entry"
    elif alert.alert_type == "quiz_entry_deadline":
        subject = f"Quiz {alert.quiz_number or '?'} Entry Deadline – Action Required"
        title = f"Quiz {alert.quiz_number or '?'} marks entry"
    elif alert.alert_type == "mid_term_event":
        subject = f"{alert.event_name or 'Midterm'} – Action Required"
        title = alert.event_name or "Midterm event"
    elif alert.alert_type == "deadline":
        subject = f"Deadline Reminder – Action Required"
        title = f"Assignment {alert.assignment_id}"
    else:
        subject = "Assignment Update"
        title = f"Assignment {alert.assignment_id}"

    lines = [
        f"Dear {alert.faculty.name or alert.faculty.email},",
        "",
        "This is an automated reminder from Faculty Tasks Automation.",
        f"Course: {course_name}",
        f"Assessment: {title}",
        f"Deadline: {due_text}",
    ]

    stage_label = _stage_label(alert.alert_stage)
    if stage_label:
        lines.append(f"Stage: {stage_label}")

    lines.extend(
        [
            f"Current Submissions: {submitted}/{total}",
            f"Pending: {pending}",
            "",
            "Please ensure marks and records are updated accordingly.",
            "",
            "— Faculty Tasks Automation",
        ]
    )
    return subject, "\n".join(lines)


def _update_alert_sync(alert: AssignmentAlert, sync_status: str, last_error: str = "") -> None:
    """Persist last sync state; avoids crashing when DB has transient issues."""
    try:
        with transaction.atomic():
            alert.sync_status = sync_status
            alert.last_error = last_error or ""
            alert.save(update_fields=["sync_status", "last_error", "updated_at"])
    except DatabaseError as exc:
        # Defensive: log DB issues but do not interrupt alert flow.
        log_auto(f"Failed to store sync status for alert {alert.id}: {exc}", category="db_error")


def _send_email_faculty(faculty: FacultyUser, subject: str, message: str) -> tuple[bool, str | None]:
    from_email = getattr(settings, "EMAIL_FROM_ADDRESS", "") or getattr(settings, "EMAIL_HOST_USER", "")
    if not (from_email and _email_configured()):
        return False, "Email settings missing"
    try:
        send_mail(subject=subject, message=message, from_email=from_email, recipient_list=[faculty.email], fail_silently=False)
        return True, None
    except BadHeaderError as exc:
        return False, str(exc)
    except smtplib.SMTPException as exc:
        return False, str(exc)
    except ValueError as exc:
        return False, str(exc)


def _sync_calendar_event(
    faculty: FacultyUser,
    alert: AssignmentAlert,
    title: str,
    description: str,
    due_date,
):
    if not due_date:
        return False
    try:
        cal = CalendarAPI(faculty)
        start = {"date": due_date.isoformat(), "timeZone": "Asia/Karachi"}
        end = {"date": (due_date + timezone.timedelta(days=1)).isoformat(), "timeZone": "Asia/Karachi"}
        if alert.calendar_event_id:
            cal.update_event(alert.calendar_event_id, summary=title, description=description, start=start, end=end)
            _update_alert_sync(alert, "ok")
            return False
        event = cal.create_all_day_event(
            summary=title,
            description=description,
            start_date=due_date,
            end_date=due_date + timezone.timedelta(days=1),
        )
        event_id = event.get("id")
        if event_id:
            alert.calendar_event_id = event_id
            alert.save(update_fields=["calendar_event_id"])
            _update_alert_sync(alert, "ok")
            log_auto(f"Calendar event created for alert {alert.id}", category="calendar_success")
            return True
        return False
    except CalendarAPIError as exc:
        # Calendar can fail due to revoked tokens, quota, or missing permissions.
        _update_alert_sync(alert, "calendar_failed", getattr(exc, "user_message", str(exc)))
        logger.warning("Calendar sync failed for %s: %s", faculty.email, exc)
        raise


@api_view(["GET"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts(request):
    """List assignment alerts with live Classroom data for the logged-in faculty."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    fast_refresh = request.GET.get("fast") == "1"
    client = None
    try:
        client = ClassroomAPI(faculty)
    except ClassroomAPIError:
        # Proceed with cached alerts even if Google is not reachable
        client = None

    # Show deleted alerts only if still upcoming (today or future) and not completed.
    today = timezone.localdate()
    try:
        alert_qs = AssignmentAlert.objects.filter(faculty=faculty).filter(
            Q(status="deleted", is_completed=False, deadline_date__gte=today)
            | Q(status="deleted", is_completed=False, event_date__gte=today)
            | ~Q(status="deleted")
        )
    except DatabaseError as exc:
        log_auto(f"Alert load failed for {faculty.email}: {exc}", category="db_error")
        return Response(
            {"detail": "Unable to load alerts"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    completed_map = {
        (a.course_id, a.assignment_id): a
        for a in alert_qs.filter(is_completed=True)
    }
    summary_map = {
        (a.course_id, a.assignment_id): a.summary_snapshot or {}
        for a in alert_qs.exclude(summary_snapshot=None)
    }

    # Seed upcoming fixed-calendar deadlines (entry, quiz, mid-term) within the next 30 days
    horizon_future = today + timezone.timedelta(days=400)
    horizon_past = today - timezone.timedelta(days=90)

    def seed_fixed_alert(course_id: str, assignment_id: str, alert_type: str, defaults: dict):
        alert, created = AssignmentAlert.objects.get_or_create(
            faculty=faculty,
            course_id=course_id,
            assignment_id=assignment_id,
            alert_type=alert_type,
            defaults=defaults,
        )
        if not created and alert.status == "deleted":
            return None
        if not created and alert_type in {"entry_deadline", "quiz_entry_deadline", "mid_term_event"}:
            # keep deadline info fresh if it changed
            updated = False
            if defaults.get("deadline_date") and alert.deadline_date != defaults["deadline_date"]:
                alert.deadline_date = defaults["deadline_date"]
                updated = True
            if defaults.get("assignment_number") and alert.assignment_number != defaults["assignment_number"]:
                alert.assignment_number = defaults["assignment_number"]
                updated = True
            if defaults.get("quiz_number") and alert.quiz_number != defaults["quiz_number"]:
                alert.quiz_number = defaults["quiz_number"]
                updated = True
            if defaults.get("event_name") and alert.event_name != defaults["event_name"]:
                alert.event_name = defaults["event_name"]
                updated = True
            if alert_type == "mid_term_event" and defaults.get("event_date") and alert.event_date != defaults["event_date"]:
                alert.event_date = defaults["event_date"]
                updated = True
            if updated:
                alert.status = "queued"
                alert.sent_at = None
                alert.save(update_fields=[
                    "deadline_date",
                    "assignment_number",
                    "quiz_number",
                    "event_name",
                    "event_date",
                    "status",
                    "sent_at",
                ])
        return alert

    rows: list[dict] = []

    for entry in ENTRY_DEADLINES:
        deadline = entry.get("deadline")
        if not deadline or deadline < horizon_past or deadline > horizon_future:
            continue
        alert = seed_fixed_alert(
            course_id="entry-deadline",
            assignment_id=f"entry-{entry.get('assignment_number')}",
            alert_type="entry_deadline",
            defaults={
                "assignment_number": entry.get("assignment_number"),
                "deadline_date": deadline,
                "status": "queued",
            },
        )
        if alert:
            key = (alert.course_id, alert.assignment_id)
            is_completed = key in completed_map
            if deadline < today and not is_completed:
                continue
            status_value = "completed" if is_completed else "upcoming"
            if alert.calendar_event_id and not is_completed and alert.status != "deleted":
                try:
                    _sync_calendar_event(
                        faculty,
                        alert,
                        f"[Academic Deadline] – Assignment {alert.assignment_number or '?'} Entry",
                        "\n".join([
                            "Category: Assignment Entry",
                            "Source: Academic Deadlines Overview",
                        ]),
                        deadline,
                    )
                except CalendarAPIError as exc:
                    # Calendar failures should not break alert listing.
                    log_auto(f"Calendar sync skipped for alert {alert.id}: {exc}", category=getattr(exc, "code", "calendar_error"))
            rows.append({
                "id": alert.id,
                "assignmentName": f"Assignment {alert.assignment_number or '?'} Entry Deadline",
                "dueDate": deadline.isoformat(),
                "submitted": None,
                "total": None,
                "notSubmitted": None,
                "status": status_value,
                "alertType": "entry_deadline",
                "alertStage": _stage_from_days((deadline - today).days),
                "stageLabel": _stage_label(_stage_from_days((deadline - today).days)),
                "isCompleted": is_completed,
                "unique_key": f"academic:entry:{alert.assignment_id}",
                "source": "academic_deadline",
            })

    for entry in QUIZ_ENTRY_DEADLINES:
        deadline = entry.get("deadline")
        if not deadline or deadline < horizon_past or deadline > horizon_future:
            continue
        alert = seed_fixed_alert(
            course_id="quiz-entry",
            assignment_id=f"quiz-{entry.get('quiz_number')}",
            alert_type="quiz_entry_deadline",
            defaults={
                "quiz_number": entry.get("quiz_number"),
                "deadline_date": deadline,
                "status": "queued",
            },
        )
        if alert:
            key = (alert.course_id, alert.assignment_id)
            is_completed = key in completed_map
            if deadline < today and not is_completed:
                continue
            status_value = "completed" if is_completed else "upcoming"
            if alert.calendar_event_id and not is_completed and alert.status != "deleted":
                try:
                    _sync_calendar_event(
                        faculty,
                        alert,
                        f"[Academic Deadline] – Quiz {alert.quiz_number or '?'} Entry",
                        "\n".join([
                            "Category: Quiz Entry",
                            "Source: Academic Deadlines Overview",
                        ]),
                        deadline,
                    )
                except CalendarAPIError as exc:
                    log_auto(f"Calendar sync skipped for alert {alert.id}: {exc}", category=getattr(exc, "code", "calendar_error"))
            rows.append({
                "id": alert.id,
                "assignmentName": f"Quiz {alert.quiz_number or '?'} Entry Deadline",
                "dueDate": deadline.isoformat(),
                "submitted": None,
                "total": None,
                "notSubmitted": None,
                "status": status_value,
                "alertType": "quiz_entry_deadline",
                "alertStage": _stage_from_days((deadline - today).days),
                "stageLabel": _stage_label(_stage_from_days((deadline - today).days)),
                "isCompleted": is_completed,
                "unique_key": f"academic:quiz:{alert.assignment_id}",
                "source": "academic_deadline",
            })

    for entry in MID_TERM_EVENTS:
        event_date = entry.get("event_date")
        if not event_date or event_date < horizon_past or event_date > horizon_future:
            continue
        alert = seed_fixed_alert(
            course_id="mid-term",
            assignment_id=f"mid-term-{entry.get('key')}",
            alert_type="mid_term_event",
            defaults={
                "event_name": entry.get("name"),
                "event_date": event_date,
                "deadline_date": event_date,
                "status": "queued",
            },
        )
        if alert:
            key = (alert.course_id, alert.assignment_id)
            is_completed = key in completed_map
            if event_date < today and not is_completed:
                continue
            status_value = "completed" if is_completed else "upcoming"
            if alert.calendar_event_id and not is_completed and alert.status != "deleted":
                try:
                    _sync_calendar_event(
                        faculty,
                        alert,
                        f"[Academic Deadline] – {alert.event_name or 'Mid Term Event'}",
                        "\n".join([
                            "Category: Mid Term Event",
                            "Source: Academic Deadlines Overview",
                        ]),
                        event_date,
                    )
                except CalendarAPIError as exc:
                    log_auto(f"Calendar sync skipped for alert {alert.id}: {exc}", category=getattr(exc, "code", "calendar_error"))
            rows.append({
                "id": alert.id,
                "assignmentName": alert.event_name or "Mid Term Event",
                "dueDate": event_date.isoformat(),
                "submitted": None,
                "total": None,
                "notSubmitted": None,
                "status": status_value,
                "alertType": "mid_term_event",
                "alertStage": _stage_from_days((event_date - today).days),
                "stageLabel": _stage_label(_stage_from_days((event_date - today).days)),
                "isCompleted": is_completed,
                "unique_key": f"academic:mid:{alert.assignment_id}",
                "source": "academic_deadline",
            })

    try:
        course_map = {c.course_id: c for c in ClassroomCourse.objects.filter(user=faculty)}
    except DatabaseError as exc:
        log_auto(f"Course map load failed for {faculty.email}: {exc}", category="db_error")
        return Response(
            {"detail": "Unable to load courses"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    course_ids = list(course_map.keys())
    cache_key = f"classroom_courses:{faculty.id}"
    if client:
        cached_courses = cache.get(cache_key)
        if cached_courses is None:
            if fast_refresh:
                try:
                    api_courses = client.list_courses()
                except ClassroomAPIError as exc:
                    log_auto(f"Courses fetch failed for {faculty.email}: {exc}", category=getattr(exc, "code", "classroom_error"))
                    api_courses = []
            else:
                try:
                    api_courses = client.list_courses()
                except ClassroomAPIError as exc:
                    log_auto(f"Courses fetch failed for {faculty.email}: {exc}", category=getattr(exc, "code", "classroom_error"))
                    api_courses = []
            cache.set(cache_key, api_courses, 120)
        else:
            api_courses = cached_courses
        for course in api_courses:
            course_id = course.get("id")
            if not course_id:
                continue
            obj, _ = ClassroomCourse.objects.update_or_create(
                user=faculty,
                course_id=course_id,
                defaults={
                    "name": course.get("name") or course_id,
                    "section": course.get("section") or "",
                    "description_heading": course.get("descriptionHeading") or "",
                    "enrollment_code": course.get("enrollmentCode") or "",
                    "alternate_link": course.get("alternateLink") or "",
                    "state": course.get("courseState") or "",
                },
            )
            course_map[course_id] = obj
        course_ids = list(course_map.keys())

    coursework_cache: dict[str, dict] = {}

    def get_coursework_cached(course_id: str) -> list[dict]:
        cache_key = f"classroom_coursework:{faculty.id}:{course_id}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        if fast_refresh:
            if client:
                try:
                    cw_list = client.list_coursework(course_id)
                except ClassroomAPIError as exc:
                    log_auto(
                        f"Coursework fetch failed for {faculty.email} / {course_id}: {exc}",
                        category=getattr(exc, "code", "classroom_error"),
                    )
                    cw_list = []
                cache.set(cache_key, cw_list, 300)
                return cw_list
            return []
        try:
            cw_list = client.list_coursework(course_id) if client else []
        except ClassroomAPIError as exc:
            log_auto(
                f"Coursework fetch failed for {faculty.email} / {course_id}: {exc}",
                category=getattr(exc, "code", "classroom_error"),
            )
            cw_list = []
        cache.set(cache_key, cw_list, 300)
        return cw_list

    def get_summary_cached(course_id: str, assignment_id: str, force_refresh: bool = False) -> dict:
        key = f"classroom_summary:{faculty.id}:{course_id}:{assignment_id}"
        if not force_refresh:
            cached = cache.get(key)
            if cached is not None:
                return cached
        try:
            summary = client.get_submission_summary(course_id, assignment_id) if client else {"total": 0, "submitted": 0, "not_submitted": 0}
        except ClassroomAPIError as exc:
            log_auto(
                f"Summary fetch failed for {faculty.email} / {course_id} / {assignment_id}: {exc}",
                category=getattr(exc, "code", "classroom_error"),
            )
            summary = {"total": 0, "submitted": 0, "not_submitted": 0}
        cache.set(key, summary, 300)
        return summary

    for course_id in course_ids:
        course = course_map.get(course_id)
        for cw in get_coursework_cached(course_id):
            cw_id = cw.get("id")
            if not cw_id:
                continue
            coursework_cache[f"{course_id}:{cw_id}"] = cw
            due_date = parse_due_date(cw)
            days_until = (due_date - today).days if due_date else None
            needs_live_summary = days_until is not None and days_until <= CALENDAR_ASSIGNMENT_RANGE_DAYS
            if needs_live_summary:
                summary = get_summary_cached(course_id, cw_id, force_refresh=True)
            else:
                summary = summary_map.get((course_id, cw_id)) or {"total": 0, "submitted": 0, "not_submitted": 0}
            key = (course_id, cw_id)
            is_completed = key in completed_map
            if due_date and due_date < today and not is_completed:
                continue
            status_value = "completed" if is_completed else "upcoming"
            stage_code = _stage_from_days((due_date - today).days) if due_date else None
            pending_count = summary.get("not_submitted", 0)
            within_range = due_date and 0 <= (due_date - today).days <= CALENDAR_ASSIGNMENT_RANGE_DAYS
            alert = AssignmentAlert.objects.filter(
                faculty=faculty,
                course_id=course_id,
                assignment_id=cw_id,
            ).first()
            if not alert and within_range:
                try:
                    alert = AssignmentAlert.objects.create(
                        faculty=faculty,
                        course_id=course_id,
                        assignment_id=cw_id,
                        alert_type="deadline",
                        alert_stage=stage_code or "single",
                        deadline_date=due_date,
                        status="queued",
                        summary_snapshot=summary,
                    )
                except (IntegrityError, DatabaseError) as exc:
                    log_auto(
                        f"Alert create skipped for {faculty.email} / {course_id} / {cw_id}: {exc}",
                        category="db_error",
                    )
                    alert = None
            if alert and alert.calendar_event_id and alert.status != "deleted":
                if alert.deadline_date != due_date or alert.summary_snapshot != summary:
                    alert.deadline_date = due_date
                    alert.summary_snapshot = summary
                    alert.save(update_fields=["deadline_date", "summary_snapshot"])
                if not is_completed and within_range:
                    description = "\n".join([
                        f"Submitted: {summary.get('submitted', 0)} / {summary.get('total', 0)}",
                        f"Pending: {pending_count}",
                        "Source: Google Classroom",
                    ])
                    try:
                        _sync_calendar_event(
                            faculty,
                            alert,
                            f"[Assignment Due] – {cw.get('title') or f'Assignment {cw_id}'}",
                            description,
                            due_date,
                        )
                    except CalendarAPIError as exc:
                        log_auto(
                            f"Calendar sync skipped for alert {alert.id}: {exc}",
                            category=getattr(exc, "code", "calendar_error"),
                        )
            rows.append({
                "id": AssignmentAlert.objects.filter(faculty=faculty, course_id=course_id, assignment_id=cw_id).values_list("id", flat=True).first(),
                "assignmentName": cw.get("title") or f"Assignment {cw_id}",
                "dueDate": due_date.isoformat() if due_date else None,
                "submitted": summary.get("submitted"),
                "total": summary.get("total"),
                "notSubmitted": summary.get("not_submitted"),
                "status": status_value,
                "alertType": "assignment",
                "alertStage": stage_code,
                "stageLabel": _stage_label(stage_code),
                "isCompleted": is_completed,
                "unique_key": f"classroom:{course_id}:{cw_id}",
                "source": "classroom",
            })


    def stage_weight(stage: str | None) -> int:
        weights = {
            "overdue": 0,
            "today": 1,
            "one_day": 2,
            "two_days": 3,
            "three_days": 4,
            "single": 5,
            None: 6,
        }
        return weights.get(stage, 6)

    def due_key(row: dict):
        try:
            return timezone.datetime.fromisoformat(row.get("dueDate")) if row.get("dueDate") else None
        except (TypeError, ValueError):
            return None

    rows.sort(key=lambda r: (stage_weight(r.get("alertStage")), due_key(r) or timezone.datetime.max))
    return Response({"alerts": rows, "count": len(rows)})


@api_view(["GET", "PUT"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts_toggle(request):
    """Get/Set enable switch for assignment alerts (single toggle)."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    pref, _ = ReminderPreference.objects.get_or_create(faculty=faculty)

    if request.method == "GET":
        return Response({"enabled": bool(pref.enable_reminders)})

    enabled = request.data.get("enabled")
    if enabled is None:
        return Response({"detail": "'enabled' is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        pref.enable_reminders = bool(enabled)
        pref.save(update_fields=["enable_reminders", "updated_at"])
    except DatabaseError as exc:
        log_auto(f"Preference update failed for {faculty.email}: {exc}", category="db_error")
        return Response(
            {"detail": "Unable to update reminder preferences"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response({"enabled": pref.enable_reminders})


@api_view(["POST"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts_run_now(request):
    """Trigger alert generation/email for the logged-in faculty on demand."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    course_id = request.data.get("course_id") or None
    dry_run = bool(request.data.get("dry_run"))
    result = run_alerts_for_faculty(faculty, course_id=course_id, dry_run=dry_run, force_send=True)

    http_status = status.HTTP_200_OK if result.get("ok") or result.get("skipped") else status.HTTP_500_INTERNAL_SERVER_ERROR
    return Response(result, status=http_status)


@api_view(["GET"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts_list(request):
    """Lightweight list endpoint for faster UI refresh (no Classroom fetch)."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        _seed_fixed_deadlines(faculty)
    except DatabaseError as exc:
        log_auto(f"Seed deadlines failed for {faculty.email}: {exc}", category="db_error")
        return Response(
            {"detail": "Unable to load alerts"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    today = timezone.localdate()
    try:
        alerts_qs = AssignmentAlert.objects.filter(faculty=faculty).filter(
            Q(status="deleted", is_completed=False, deadline_date__gte=today)
            | Q(status="deleted", is_completed=False, event_date__gte=today)
            | ~Q(status="deleted")
        )
    except DatabaseError as exc:
        log_auto(f"Alert list load failed for {faculty.email}: {exc}", category="db_error")
        return Response(
            {"detail": "Unable to load alerts"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    completed_map = {
        (a.course_id, a.assignment_id): a
        for a in alerts_qs.filter(is_completed=True)
    }
    summary_map = {
        (a.course_id, a.assignment_id): a.summary_snapshot or {}
        for a in alerts_qs.exclude(summary_snapshot=None)
    }
    alert_id_map = {
        (a.course_id, a.assignment_id): a.id
        for a in alerts_qs
    }

    rows: list[dict] = []
    # Academic deadlines (entry, quiz, mid-term)
    for alert in alerts_qs.filter(alert_type__in=["entry_deadline", "quiz_entry_deadline", "mid_term_event"]):
        due = alert.deadline_date or alert.event_date
        if not due:
            continue
        key = (alert.course_id, alert.assignment_id)
        is_completed = key in completed_map
        if due < today and not is_completed:
            continue
        status_value = "completed" if is_completed else "upcoming"
        stage_code = _stage_from_days((due - today).days) if due else None
        if alert.calendar_event_id and not is_completed and alert.status != "deleted" and 0 <= (due - today).days <= CALENDAR_ASSIGNMENT_RANGE_DAYS:
            title = alert.event_name or "Mid Term Event"
            if alert.alert_type == "entry_deadline":
                title = f"[Academic Deadline] – Assignment {alert.assignment_number or '?'} Entry"
            elif alert.alert_type == "quiz_entry_deadline":
                title = f"[Academic Deadline] – Quiz {alert.quiz_number or '?'} Entry"
            elif alert.alert_type == "mid_term_event":
                title = f"[Academic Deadline] – {alert.event_name or 'Mid Term Event'}"
            try:
                _sync_calendar_event(
                    faculty,
                    alert,
                    title,
                    "\n".join([
                        "Source: Academic Deadlines Overview",
                    ]),
                    due,
                )
            except CalendarAPIError as exc:
                log_auto(
                    f"Calendar sync skipped for alert {alert.id}: {exc}",
                    category=getattr(exc, "code", "calendar_error"),
                )
        if alert.alert_type == "entry_deadline":
            type_label = "entry_deadline"
        elif alert.alert_type == "quiz_entry_deadline":
            type_label = "quiz_entry_deadline"
        else:
            name = (alert.event_name or "").lower()
            if "final" in name:
                type_label = "final_term_event"
            elif "mid" in name:
                type_label = "mid_term_event"
            else:
                type_label = "academic_event"

        rows.append({
            "id": alert.id,
            "assignmentName": alert.event_name
            or (f"Assignment {alert.assignment_number} Entry Deadline" if alert.alert_type == "entry_deadline" else None)
            or (f"Quiz {alert.quiz_number} Entry Deadline" if alert.alert_type == "quiz_entry_deadline" else None)
            or "Academic Event",
            "dueDate": due.isoformat(),
            "submitted": None,
            "total": None,
            "notSubmitted": None,
            "status": status_value,
            "alertType": type_label,
            "alertStage": stage_code,
            "stageLabel": _stage_label(stage_code),
            "isCompleted": is_completed,
            "unique_key": f"academic:{alert.alert_type}:{alert.assignment_id}",
            "source": "academic_deadline",
        })

    # Manual custom deadlines (non-Classroom)
    for alert in alerts_qs.filter(alert_type="deadline", course_id="manual-event"):
        due = alert.deadline_date or alert.event_date
        if not due:
            continue
        key = (alert.course_id, alert.assignment_id)
        is_completed = key in completed_map
        if due < today and not is_completed:
            continue
        status_value = "completed" if is_completed else "upcoming"
        stage_code = _stage_from_days((due - today).days) if due else None
        if alert.calendar_event_id and not is_completed and alert.status != "deleted" and 0 <= (due - today).days <= CALENDAR_ASSIGNMENT_RANGE_DAYS:
            title = alert.event_name or "Manual Deadline"
            try:
                _sync_calendar_event(
                    faculty,
                    alert,
                    title,
                    "Source: Manual Alert",
                    due,
                )
            except CalendarAPIError as exc:
                log_auto(
                    f"Calendar sync skipped for manual alert {alert.id}: {exc}",
                    category=getattr(exc, "code", "calendar_error"),
                )

        rows.append({
            "id": alert.id,
            "assignmentName": alert.event_name or "Manual Deadline",
            "dueDate": due.isoformat(),
            "submitted": None,
            "total": None,
            "notSubmitted": None,
            "status": status_value,
            "alertType": "deadline",
            "alertStage": stage_code,
            "stageLabel": _stage_label(stage_code),
            "isCompleted": is_completed,
            "unique_key": f"manual:{alert.assignment_id}",
            "source": "manual",
        })

    # Classroom coursework (use cache; fetch once if missing)
    client = None
    try:
        client = ClassroomAPI(faculty)
    except ClassroomAPIError as exc:
        log_auto(f"Classroom client unavailable for {faculty.email}: {exc}", category=getattr(exc, "code", "classroom_error"))
        client = None

    try:
        course_map = {c.course_id: c for c in ClassroomCourse.objects.filter(user=faculty)}
    except DatabaseError as exc:
        log_auto(f"Course map load failed for {faculty.email}: {exc}", category="db_error")
        course_map = {}
    for course_id in course_map.keys():
        cache_key = f"classroom_coursework:{faculty.id}:{course_id}"
        cw_list = cache.get(cache_key)
        if cw_list is None and client:
            try:
                cw_list = client.list_coursework(course_id)
            except ClassroomAPIError as exc:
                log_auto(
                    f"Coursework fetch failed for {faculty.email} / {course_id}: {exc}",
                    category=getattr(exc, "code", "classroom_error"),
                )
                cw_list = []
            cache.set(cache_key, cw_list, 300)
        if not cw_list:
            continue
        for cw in cw_list:
            cw_id = cw.get("id")
            if not cw_id:
                continue
            due_date = parse_due_date(cw)
            key = (course_id, cw_id)
            is_completed = key in completed_map
            if due_date and due_date < today and not is_completed:
                continue
            status_value = "completed" if is_completed else "upcoming"
            stage_code = _stage_from_days((due_date - today).days) if due_date else None
            summary = summary_map.get(key) or {}
            within_range = due_date and 0 <= (due_date - today).days <= CALENDAR_ASSIGNMENT_RANGE_DAYS
            alert = AssignmentAlert.objects.filter(
                faculty=faculty,
                course_id=course_id,
                assignment_id=cw_id,
            ).first()
            if not alert and due_date:
                try:
                    alert = AssignmentAlert.objects.create(
                        faculty=faculty,
                        course_id=course_id,
                        assignment_id=cw_id,
                        alert_type="deadline",
                        alert_stage=stage_code or "single",
                        deadline_date=due_date,
                        event_name=cw.get("title") or f"Assignment {cw_id}",
                        status="queued",
                        summary_snapshot=summary,
                    )
                except (IntegrityError, DatabaseError) as exc:
                    log_auto(
                        f"Alert create skipped for {faculty.email} / {course_id} / {cw_id}: {exc}",
                        category="db_error",
                    )
                    alert = None
            if alert and alert.calendar_event_id and alert.status != "deleted" and not is_completed and within_range:
                description = "\n".join([
                    f"Submitted: {summary.get('submitted', 0)} / {summary.get('total', 0)}",
                    f"Pending: {summary.get('not_submitted', 0)}",
                    "Source: Google Classroom",
                ])
                try:
                    _sync_calendar_event(
                        faculty,
                        alert,
                        f"[Assignment Due] – {cw.get('title') or f'Assignment {cw_id}'}",
                        description,
                        due_date,
                    )
                except CalendarAPIError as exc:
                    log_auto(
                        f"Calendar sync skipped for alert {alert.id}: {exc}",
                        category=getattr(exc, "code", "calendar_error"),
                    )
            rows.append({
                "id": alert.id if alert else alert_id_map.get(key),
                "assignmentName": cw.get("title") or f"Assignment {cw_id}",
                "dueDate": due_date.isoformat() if due_date else None,
                "submitted": summary.get("submitted"),
                "total": summary.get("total"),
                "notSubmitted": summary.get("not_submitted"),
                "status": status_value,
                "alertType": "assignment",
                "alertStage": stage_code,
                "stageLabel": _stage_label(stage_code),
                "isCompleted": is_completed,
                "unique_key": f"classroom:{course_id}:{cw_id}",
                "source": "classroom",
            })

    def stage_weight(stage: str | None) -> int:
        weights = {
            "overdue": 0,
            "today": 1,
            "one_day": 2,
            "two_days": 3,
            "three_days": 4,
            "single": 5,
            None: 6,
        }
        return weights.get(stage, 6)

    def due_key(row: dict):
        try:
            return timezone.datetime.fromisoformat(row.get("dueDate")) if row.get("dueDate") else None
        except (TypeError, ValueError):
            return None

    rows.sort(key=lambda r: (stage_weight(r.get("alertStage")), due_key(r) or timezone.datetime.max))
    return Response({"alerts": rows, "count": len(rows)})


@api_view(["POST"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts_mark_complete(request):
    """Mark a deadline alert (and its siblings) as completed for this faculty."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    alert_id = request.data.get("alert_id")
    if not alert_id:
        return Response({"detail": "alert_id is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        alert = AssignmentAlert.objects.get(id=alert_id, faculty=faculty)
    except AssignmentAlert.DoesNotExist:
        return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    now = timezone.now()
    try:
        updated = AssignmentAlert.objects.filter(
            faculty=faculty,
            course_id=alert.course_id,
            assignment_id=alert.assignment_id,
        ).update(is_completed=True, completed_at=now, completed_by=faculty, status="sent")
    except DatabaseError as exc:
        log_auto(f"Mark complete failed for {faculty.email}: {exc}", category="db_error")
        return Response(
            {"detail": "Unable to mark completed"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    try:
        event_ids = list(
            AssignmentAlert.objects.filter(
                faculty=faculty,
                course_id=alert.course_id,
                assignment_id=alert.assignment_id,
            )
            .exclude(calendar_event_id="")
            .values_list("calendar_event_id", flat=True)
        )
    except DatabaseError as exc:
        log_auto(f"Calendar event lookup failed for {faculty.email}: {exc}", category="db_error")
        event_ids = []
    if event_ids:
        try:
            cal = CalendarAPI(faculty)
            for event_id in set(event_ids):
                cal.update_event(event_id, description="Completed on Zabdesk")
            _update_alert_sync(alert, "ok")
        except CalendarAPIError as exc:
            # Calendar update failure should not prevent completion in app.
            _update_alert_sync(alert, "calendar_failed", getattr(exc, "user_message", str(exc)))
            log_auto(f"Calendar update failed for alert {alert.id}: {exc}", category=getattr(exc, "code", "calendar_error"))

    return Response({"ok": True, "updated": updated, "completed_at": now})


@api_view(["POST"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts_send_now(request):
    """Force-send all active alerts for this faculty, ignoring sent flags but honoring completion."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    force_send = bool(request.data.get("force_send", True))
    result = run_alerts_for_faculty(faculty, dry_run=False, force_send=force_send)

    # Fallback: if nothing was sent, email all upcoming alerts directly.
    sent_count = int(result.get("sent") or 0)
    if sent_count == 0 and not result.get("skipped"):
        today = timezone.localdate()
        course_lookup = {c.course_id: c.name for c in ClassroomCourse.objects.filter(user=faculty)}
        upcoming_alerts = (
            AssignmentAlert.objects.filter(faculty=faculty, is_completed=False)
            .exclude(status="deleted")
            .filter(
                Q(deadline_date__gte=today) | Q(event_date__gte=today)
            )
            .order_by("deadline_date", "event_date")
        )
        for alert in upcoming_alerts:
            subject, message = _build_email_content(alert, course_lookup)
            ok, _ = _send_email_faculty(faculty, subject, message)
            if ok:
                sent_count += 1
                alert.status = "sent"
                alert.sent_at = timezone.now()
                alert.save(update_fields=["status", "sent_at"])
                _update_alert_sync(alert, "ok")
            else:
                # Store failure reason so faculty can retry without crashing the flow.
                _update_alert_sync(alert, "email_failed", "Email send failed during manual run")
                log_auto(f"Manual send failed for alert {alert.id}", category="email_failed")
        result["sent"] = sent_count

    http_status = status.HTTP_200_OK if result.get("ok") or result.get("skipped") else status.HTTP_500_INTERNAL_SERVER_ERROR
    return Response(result, status=http_status)


@api_view(["POST"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts_create_events(request):
    """Create Google Calendar events for upcoming alerts (today or future)."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        today = timezone.localdate()
        _seed_fixed_deadlines(faculty)
        alerts = (
            AssignmentAlert.objects.filter(faculty=faculty)
            .exclude(status="deleted")
            .order_by("deadline_date", "event_date")
        )
    except DatabaseError as exc:
        logger.exception("Auto-Reminder calendar creation failed (db): %s", exc)
        return Response(
            {
                "ok": False,
                "code": "db_error",
                "user_message": "Unable to load alerts. Please try again.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Ensure Classroom-based alerts exist for today/upcoming dates (without creating calendar events here).
    try:
        client = None
        try:
            client = ClassroomAPI(faculty)
        except ClassroomAPIError as exc:
            log_auto(f"Classroom client unavailable for {faculty.email}: {exc}", category="classroom_error")
            client = None
        course_map = {c.course_id: c for c in ClassroomCourse.objects.filter(user=faculty)}
        for course_id in course_map.keys():
            cache_key = f"classroom_coursework:{faculty.id}:{course_id}"
            cw_list = cache.get(cache_key)
            if cw_list is None and client:
                try:
                    cw_list = client.list_coursework(course_id)
                except ClassroomAPIError as exc:
                    log_auto(
                        f"Coursework fetch failed for {faculty.email} / {course_id}: {exc}",
                        category=getattr(exc, "code", "classroom_error"),
                    )
                    cw_list = []
                cache.set(cache_key, cw_list, 300)
            if not cw_list:
                continue
            for cw in cw_list:
                cw_id = cw.get("id")
                if not cw_id:
                    continue
                due_date = parse_due_date(cw)
                if not due_date or due_date < today:
                    continue
                exists = AssignmentAlert.objects.filter(
                    faculty=faculty,
                    course_id=course_id,
                    assignment_id=cw_id,
                ).exists()
                if not exists:
                    stage_code = _stage_from_days((due_date - today).days) or "single"
                    try:
                        AssignmentAlert.objects.create(
                            faculty=faculty,
                            course_id=course_id,
                            assignment_id=cw_id,
                            alert_type="deadline",
                            alert_stage=stage_code,
                            deadline_date=due_date,
                            event_name=cw.get("title") or f"Assignment {cw_id}",
                            status="queued",
                        )
                    except (IntegrityError, DatabaseError) as exc:
                        # Idempotency: duplicates can happen when alerts already exist.
                        log_auto(
                            f"Alert seed skipped for {faculty.email} / {course_id} / {cw_id}: {exc}",
                            category="db_error",
                        )
    except DatabaseError as exc:
        logger.warning("Failed to seed Classroom alerts for calendar creation: %s", exc)

    # Reload alerts after seeding
    alerts = (
        AssignmentAlert.objects.filter(faculty=faculty)
        .exclude(status="deleted")
        .order_by("deadline_date", "event_date")
    )

    created = 0
    skipped = 0
    skipped_past = 0
    skipped_no_deadline = 0
    skipped_duplicates = 0
    failed = 0
    errors: list[dict] = []

    for alert in alerts:
        try:
            due = alert.deadline_date or alert.event_date
            if hasattr(due, "date"):
                due = due.date()
            if not due:
                skipped += 1
                skipped_no_deadline += 1
                logger.info("Auto-Reminder calendar skip (no deadline): %s", alert.id)
                _update_alert_sync(alert, "skipped", "No deadline available for calendar event")
                continue
            if due < today:
                skipped += 1
                skipped_past += 1
                logger.info("Auto-Reminder calendar skip (past deadline): %s", alert.id)
                _update_alert_sync(alert, "skipped", "Deadline is in the past")
                continue
            if alert.alert_type == "entry_deadline":
                title = f"Assignment {alert.assignment_number or '?'} Entry Deadline"
            elif alert.alert_type == "quiz_entry_deadline":
                title = f"Quiz {alert.quiz_number or '?'} Entry Deadline"
            elif alert.alert_type == "mid_term_event":
                title = alert.event_name or "Mid Term Event"
            elif alert.alert_type == "deadline":
                title = alert.event_name or "Assignment Due"
            else:
                title = alert.event_name or "Alert Deadline"

            description = "Auto-generated by Faculty Tasks Automation Auto-Reminder."

            created_now = _sync_calendar_event(faculty, alert, title, description, due)
            if created_now:
                created += 1
            else:
                skipped += 1
                if alert.calendar_event_id:
                    skipped_duplicates += 1
                    logger.info("Auto-Reminder calendar skip (duplicate): %s", alert.id)
                    _update_alert_sync(alert, "skipped", "Calendar event already exists")
        except CalendarAPIError as exc:
            if getattr(exc, "code", "") == "not_found" and alert.calendar_event_id:
                try:
                    alert.calendar_event_id = ""
                    try:
                        alert.save(update_fields=["calendar_event_id"])
                    except DatabaseError as db_exc:
                        log_auto(f"Failed clearing calendar id for alert {alert.id}: {db_exc}", category="db_error")
                    created_now = _sync_calendar_event(faculty, alert, title, description, due)
                    if created_now:
                        created += 1
                        continue
                except CalendarAPIError as retry_exc:
                    failed += 1
                    errors.append({
                        "alert_id": alert.id,
                        "code": getattr(retry_exc, "code", "calendar_error"),
                        "message": getattr(retry_exc, "user_message", str(retry_exc)),
                    })
                    _update_alert_sync(alert, "calendar_failed", getattr(retry_exc, "user_message", str(retry_exc)))
                    logger.warning("Calendar event recreate failed for %s: %s", alert.id, retry_exc)
                    continue
            failed += 1
            errors.append({
                "alert_id": alert.id,
                "code": getattr(exc, "code", "calendar_error"),
                "message": getattr(exc, "user_message", str(exc)),
            })
            _update_alert_sync(alert, "calendar_failed", getattr(exc, "user_message", str(exc)))
            logger.warning("Calendar event creation failed for %s: %s", alert.id, exc)
        except (DatabaseError, IntegrityError, ValueError, TypeError) as exc:
            # Protect the loop from invalid data or DB transaction issues.
            failed += 1
            errors.append({
                "alert_id": alert.id,
                "code": "calendar_error",
                "message": "Unexpected data error while creating calendar event.",
            })
            logger.exception("Calendar data error for %s: %s", alert.id, exc)

    total = created + skipped + failed
    if total == 0:
        return Response(
            {
                "ok": True,
                "created": 0,
                "skipped": 0,
                "failed": 0,
                "user_message": "No future alerts found to create calendar events.",
                "details": {
                    "skipped_past": skipped_past,
                    "skipped_no_deadline": skipped_no_deadline,
                    "skipped_duplicates": skipped_duplicates,
                },
                "errors": errors,
            }
        )

    return Response(
        {
            "ok": True,
            "created": created,
            "skipped": skipped,
            "failed": failed,
            "details": {
                "skipped_past": skipped_past,
                "skipped_no_deadline": skipped_no_deadline,
                "skipped_duplicates": skipped_duplicates,
            },
            "errors": errors,
        }
    )


@api_view(["POST"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts_delete_events(request):
    """Delete Google Calendar events created for alerts."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        alerts = AssignmentAlert.objects.filter(faculty=faculty).exclude(calendar_event_id="")
    except DatabaseError as exc:
        logger.exception("Auto-Reminder calendar delete failed (db): %s", exc)
        return Response(
            {
                "ok": False,
                "code": "db_error",
                "user_message": "Unable to load calendar events. Please try again.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    deleted = 0
    skipped = 0
    failed = 0
    errors: list[dict] = []

    try:
        cal = CalendarAPI(faculty)
    except CalendarAPIError as exc:
        logger.warning("Calendar delete blocked: %s", exc)
        return Response(
            {
                "ok": False,
                "code": getattr(exc, "code", "calendar_error"),
                "user_message": getattr(exc, "user_message", str(exc)),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    for alert in alerts:
        if not alert.calendar_event_id:
            skipped += 1
            _update_alert_sync(alert, "skipped", "No calendar event id to delete")
            continue
        try:
            cal.delete_event(alert.calendar_event_id)
            alert.calendar_event_id = ""
            try:
                alert.save(update_fields=["calendar_event_id"])
            except DatabaseError as db_exc:
                log_auto(f"Failed clearing calendar id for alert {alert.id}: {db_exc}", category="db_error")
            _update_alert_sync(alert, "ok")
            deleted += 1
        except CalendarAPIError as exc:
            code = getattr(exc, "code", "calendar_error")
            if code == "not_found":
                alert.calendar_event_id = ""
                try:
                    alert.save(update_fields=["calendar_event_id"])
                except DatabaseError as db_exc:
                    log_auto(f"Failed clearing calendar id for alert {alert.id}: {db_exc}", category="db_error")
                _update_alert_sync(alert, "ok")
                deleted += 1
                continue
            if code in {"auth_missing", "auth_revoked", "auth_invalid", "auth_scope", "auth_refresh_failed", "auth_refresh_missing", "api_disabled"}:
                logger.warning("Calendar delete blocked mid-run: %s", exc)
                return Response(
                    {
                        "ok": False,
                        "code": code,
                        "user_message": getattr(exc, "user_message", str(exc)),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            failed += 1
            errors.append({
                "alert_id": alert.id,
                "code": code,
                "message": getattr(exc, "user_message", str(exc)),
            })
            _update_alert_sync(alert, "calendar_failed", getattr(exc, "user_message", str(exc)))
            logger.warning("Calendar event delete failed for %s: %s", alert.id, exc)
        except (DatabaseError, IntegrityError, ValueError, TypeError) as exc:
            failed += 1
            errors.append({
                "alert_id": alert.id,
                "code": "calendar_error",
                "message": "Unexpected data error while deleting calendar event.",
            })
            _update_alert_sync(alert, "calendar_failed", "Unexpected data error while deleting calendar event.")
            logger.exception("Unexpected calendar delete error for %s: %s", alert.id, exc)

    if deleted == 0 and skipped == 0 and failed == 0:
        return Response(
            {
                "ok": True,
                "deleted": 0,
                "skipped": 0,
                "failed": 0,
                "user_message": "No calendar events found to delete.",
                "errors": errors,
            }
        )

    return Response(
        {
            "ok": True,
            "deleted": deleted,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
        }
    )


@api_view(["POST"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts_create(request):
    """Create a manual alert for a custom event/deadline for this faculty."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    title = (request.data.get("title") or "").strip()
    due_str = (request.data.get("due_date") or "").strip()
    alert_type = request.data.get("alert_type") or "mid_term_event"
    course_id = (request.data.get("course_id") or "manual-event").strip() or "manual-event"
    assignment_id = (request.data.get("assignment_id") or uuid.uuid4().hex)

    if not title:
        return Response({"detail": "title is required"}, status=status.HTTP_400_BAD_REQUEST)
    if alert_type not in dict(ASSIGNMENT_ALERT_TYPES):
        alert_type = "mid_term_event"

    due_date = None
    if due_str:
        try:
            due_date = timezone.datetime.fromisoformat(due_str).date()
        except (TypeError, ValueError):
            return Response({"detail": "invalid due_date"}, status=status.HTTP_400_BAD_REQUEST)

    today = timezone.localdate()
    if due_date and due_date < today:
        # Prevent creating alerts for already-past dates (recoverable input error).
        return Response({"detail": "due_date cannot be in the past"}, status=status.HTTP_400_BAD_REQUEST)
    stage_code = _stage_from_days(((due_date - today).days) if due_date else None) or "single"

    try:
        with transaction.atomic():
            alert, _ = AssignmentAlert.objects.update_or_create(
                faculty=faculty,
                course_id=course_id,
                assignment_id=assignment_id,
                alert_type=alert_type,
                alert_stage=stage_code,
                defaults={
                    "event_name": title,
                    "deadline_date": due_date,
                    "event_date": due_date,
                    "status": "queued",
                    "is_completed": False,
                    "sent_at": None,
                },
            )
    except (IntegrityError, DatabaseError) as exc:
        log_auto(f"Manual alert create failed for {faculty.email}: {exc}", category="db_error")
        return Response(
            {"detail": "Unable to create alert"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response({"ok": True, "id": alert.id})


@api_view(["POST"])
@permission_classes([SessionFacultyPermission])
def assignment_alerts_delete(request):
    """Hide this alert from the list until its next upcoming refresh."""

    faculty = get_faculty_from_request(request)
    if not faculty:
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    alert_id = request.data.get("alert_id")
    if not alert_id:
        return Response({"detail": "alert_id is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        alert = AssignmentAlert.objects.get(id=alert_id, faculty=faculty)
    except AssignmentAlert.DoesNotExist:
        return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

    alert.status = "deleted"
    try:
        alert.save(update_fields=["status"])
    except DatabaseError as exc:
        log_auto(f"Alert delete failed for {faculty.email}: {exc}", category="db_error")
        return Response(
            {"detail": "Unable to delete alert"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response({"ok": True, "hidden": True})


# ----------------------------------------------------------------------
# Helper
# ----------------------------------------------------------------------

def parse_due_date(coursework: dict):
    due = coursework.get("dueDate") or {}
    year, month, day = due.get("year"), due.get("month"), due.get("day")
    if not (year and month and day):
        return None
    try:
        return timezone.datetime(int(year), int(month), int(day)).date()
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
