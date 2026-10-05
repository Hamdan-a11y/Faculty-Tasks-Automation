"""Daily assignment-alert job for Google Classroom announcements.

Scope limited to assignments only:
    - Detect new assignments (courseWork)
    - Detect deadlines 1 day before due date
    - Detect fixed assignment entry deadlines (marks submission)
    - Post a Classroom announcement (Google emails faculty automatically)

Design: simple Django management command (cron friendly) to avoid Celery/Redis.
"""

from __future__ import annotations

import base64
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Dict, List

import requests
from django.core.management.base import BaseCommand
from django.core.mail import BadHeaderError, send_mail
from django.conf import settings
from django.utils import timezone
from django.db import DatabaseError

from auth_app.models import ClassroomCourse, FacultyUser
from auth_app.auth_service import refresh_access_token
from auth_app.models import GoogleServiceToken
from auto_reminder.classroom_service import ClassroomAPI, ClassroomAPIError
from auto_reminder.calendar_service import CalendarAPI, CalendarAPIError
from auto_reminder.entry_deadlines import ENTRY_DEADLINES
from auto_reminder.quiz_entry_deadlines import QUIZ_ENTRY_DEADLINES
from auto_reminder.mid_term_events import MID_TERM_EVENTS
from auto_reminder.models import AssignmentAlert, ASSIGNMENT_ALERT_TYPES, ReminderPreference


def get_faculty_preference(faculty: FacultyUser) -> bool:
    """Return whether alerts are enabled for the given faculty."""

    pref, _ = ReminderPreference.objects.get_or_create(faculty=faculty)
    return bool(pref.enable_reminders)


class Command(BaseCommand):
    help = "Run assignment alerts (email only; Classroom announcements bypassed)"

    def add_arguments(self, parser):
        parser.add_argument("--faculty-email", type=str, help="Run for a single faculty email")
        parser.add_argument("--dry-run", action="store_true", help="Log only; do not post announcements")
        parser.add_argument("--course", type=str, help="Limit to a single Classroom course ID")

    def handle(self, *args, **options):
        self.dry_run = bool(options.get("dry_run"))
        # Hard-disable Classroom announcements per requirements; always email-only.
        self.email_only = True
        if not hasattr(self, "force_send"):
            self.force_send = False
        faculty_qs = FacultyUser.objects.all()
        if options.get("faculty_email"):
            faculty_qs = faculty_qs.filter(email=options["faculty_email"])

        if not faculty_qs.exists():
            self.stdout.write(self.style.WARNING("No faculty found; exiting."))
            return

        self.course_filter = options.get("course")
        self.stdout.write(self.style.SUCCESS("\n▶ Assignment alerts job starting"))
        if self.dry_run:
            self.stdout.write(self.style.WARNING("Dry-run mode: no announcements will be posted"))
        if self.course_filter:
            self.stdout.write(self.style.WARNING(f"Course filter active: only {self.course_filter}"))
        for faculty in faculty_qs:
            pref_enabled = get_faculty_preference(faculty)
            if not pref_enabled:
                self.stdout.write(f"Skipping {faculty.email}: alerts disabled")
                continue
            self._process_faculty(faculty)

        self.stdout.write(self.style.SUCCESS("\n✔ Assignment alerts job finished"))

    # ------------------------------------------------------------------
    # Per-faculty processing
    # ------------------------------------------------------------------
    def _process_faculty(self, faculty: FacultyUser):
        client = None
        try:
            client = ClassroomAPI(faculty)
        except ClassroomAPIError as exc:
            self.stdout.write(self.style.WARNING(f"Classroom unavailable for {faculty.email}: {exc}"))

        courses = list(ClassroomCourse.objects.filter(user=faculty))
        self.course_name_lookup = {c.course_id: c.name for c in courses}
        if self.course_filter:
            courses = [c for c in courses if c.course_id == self.course_filter]

        today = timezone.localdate()
        if client and courses:
            self._process_entry_deadlines(faculty, client, courses, today)
            self._process_quiz_deadlines(faculty, client, courses, today)
            self._process_mid_term_events(faculty, client, courses, today)
            for course in courses:
                try:
                    coursework_list = client.list_coursework(course.course_id)
                except ClassroomAPIError as exc:
                    self.stdout.write(self.style.ERROR(f"Coursework fetch failed for {course.course_id}: {exc}"))
                    continue

                for cw in coursework_list:
                    cw_id = cw.get("id")
                    if not cw_id:
                        continue

                    due = parse_due_date(cw)
                    summary = None

                    def get_summary_once():
                        """Fetch submission summary but never crash the job on API errors."""
                        nonlocal summary
                        if summary is not None:
                            return summary
                        try:
                            summary = client.get_submission_summary(course.course_id, cw_id)
                        except ClassroomAPIError as exc:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"Submission summary failed for {faculty.email} / {course.course_id} / {cw_id}: {exc}"
                                )
                            )
                            summary = {"total": 0, "submitted": 0, "not_submitted": 0}
                        return summary

                    # New assignment alert
                    created_alert = self._ensure_alert(faculty, course.course_id, cw_id, "created")
                    if created_alert:
                        summary = get_summary_once()
                        self._send_announcement(
                            client,
                            faculty,
                            course.course_id,
                            cw.get("title", "New assignment"),
                            due,
                            summary,
                            alert_obj=created_alert,
                            stage_code="single",
                        )

                    # Submission-progress alert (fires whenever submission counts change)
                    summary_alert = self._ensure_alert(
                        faculty,
                        course.course_id,
                        cw_id,
                        "summary",
                        alert_stage="single",
                    )
                    if summary_alert:
                        summary = get_summary_once()
                        if self._summary_changed(summary_alert.summary_snapshot, summary):
                            self._send_submission_summary(
                                client,
                                faculty,
                                course.course_id,
                                cw.get("title", "Assignment"),
                                summary,
                                alert_obj=summary_alert,
                            )

                    # Progressive deadline alerts (3,2,1,0 days; overdue if pending)
                    if due:
                        days_remaining = (due - today).days
                        stage_code = self._stage_from_days(days_remaining)
                        if stage_code:
                            summary = get_summary_once()
                            pending = summary.get("not_submitted", 0)
                            if stage_code == "overdue" and pending <= 0:
                                continue
                            # Stop future alerts if everything is already submitted
                            if pending <= 0 and stage_code != "overdue":
                                continue

                            deadline_alert = self._ensure_alert(
                                faculty,
                                course.course_id,
                                cw_id,
                                "deadline",
                                alert_stage=stage_code,
                                deadline_date=due,
                                summary_snapshot=summary,
                            )
                            if deadline_alert:
                                self._send_announcement(
                                    client,
                                    faculty,
                                    course.course_id,
                                    cw.get("title", "Assignment"),
                                    due,
                                    summary,
                                    alert_obj=deadline_alert,
                                    deadline_mode=True,
                                    stage_code=stage_code,
                                )

        if not courses:
            self.stdout.write(f"No courses for {faculty.email}; classroom alerts skipped.")
        if not client:
            self.stdout.write(f"Classroom client unavailable for {faculty.email}; classroom alerts skipped.")

        self._process_manual_alerts(faculty, today)

    def _process_manual_alerts(self, faculty: FacultyUser, today: date) -> None:
        try:
            manual_alerts = AssignmentAlert.objects.filter(
                faculty=faculty,
                course_id="manual-event",
                alert_type__in=["deadline", "mid_term_event", "entry_deadline", "quiz_entry_deadline"],
            ).exclude(status="deleted")
        except DatabaseError as exc:
            self.stdout.write(self.style.WARNING(
                f"Manual alerts load failed for {faculty.email}: {exc}"
            ))
            return

        for alert in manual_alerts:
            due = alert.deadline_date or alert.event_date
            if not due:
                continue
            if due < today and not alert.is_completed:
                continue
            if alert.is_completed:
                self.stdout.write(
                    f"Skipping completed manual alert {alert.id} for {faculty.email}"
                )
                continue
            if alert.status == "sent" and not self.force_send:
                self.stdout.write(
                    f"Skipping already-sent manual alert {alert.id} for {faculty.email}"
                )
                continue

            stage_code = alert.alert_stage or self._stage_from_days((due - today).days) or "single"
            stage_label = self._stage_label(stage_code)
            stage_phrase = self._email_stage_phrase(stage_code)
            title = alert.event_name or "Manual Alert"
            subject = f"[Faculty Tasks Automation] {title} – {stage_phrase}".strip()
            lines = [
                f"Dear {faculty.name or faculty.email},",
                "",
                "This is an automated reminder from Faculty Tasks Automation.",
                f"Alert Type: {alert.alert_type.replace('_', ' ').title()}",
                f"Title: {title}",
                f"Due Date: {due.isoformat()}",
            ]
            if stage_label:
                lines.append(f"Stage: {stage_label}")
            lines.extend([
                "",
                "Please ensure marks and records are updated accordingly.",
                "",
                "— Faculty Tasks Automation",
            ])
            message = "\n".join(lines)

            self._maybe_create_calendar_event(
                faculty,
                alert,
                f"[Manual] – {title}",
                due,
                "Manual Alert",
                stage_code=stage_code,
                summary=None,
                source_label="Manual",
            )

            sent = True
            try:
                if not self.dry_run:
                    sent = self._send_email_faculty(
                        faculty,
                        subject=subject,
                        message=message,
                    )
                if sent:
                    alert.status = "sent"
                    alert.sent_at = timezone.now()
                    alert.save(update_fields=["status", "sent_at"])
                    self._update_alert_sync(alert, "ok")
                    self.stdout.write(self.style.SUCCESS(
                        f"✓ Manual alert emailed for {faculty.email}: {title}"
                    ))
                else:
                    self._update_alert_sync(alert, "email_failed", "Email send failed")
                    self.stdout.write(self.style.WARNING(
                        f"Email not sent for manual alert {alert.id} / {faculty.email}; leaving queued"
                    ))
            except (DatabaseError, ValueError) as exc:
                self._update_alert_sync(alert, "email_failed", str(exc))
                self.stdout.write(self.style.ERROR(
                    f"Manual alert email failed for {faculty.email}: {exc}"
                ))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _ensure_alert(
        self,
        faculty: FacultyUser,
        course_id: str,
        assignment_id: str,
        alert_type: str,
        alert_stage: str = "single",
        **extra_defaults,
    ):
        if alert_type not in dict(ASSIGNMENT_ALERT_TYPES):
            return None

        if AssignmentAlert.objects.filter(
            faculty=faculty,
            course_id=course_id,
            assignment_id=assignment_id,
            is_completed=True,
        ).exists():
            return None

        defaults = {"status": "queued", "alert_stage": alert_stage, "alert_target": "faculty"}
        defaults.update(extra_defaults)
        alert, created = AssignmentAlert.objects.get_or_create(
            faculty=faculty,
            course_id=course_id,
            assignment_id=assignment_id,
            alert_type=alert_type,
            alert_stage=alert_stage,
            defaults=defaults,
        )

        if alert.is_completed:
            return None

        # For entry/quiz deadlines, if the stored deadline/number changed, refresh fields
        # and requeue so the new date can fire once. If already sent for the current date,
        # do not re-send.
        if alert_type in {"entry_deadline", "quiz_entry_deadline", "mid_term_event"}:
            new_deadline = extra_defaults.get("deadline_date")
            new_assignment_number = extra_defaults.get("assignment_number")
            new_quiz_number = extra_defaults.get("quiz_number")
            new_event_name = extra_defaults.get("event_name")
            new_summary = extra_defaults.get("summary_snapshot")
            if (
                (new_deadline and alert.deadline_date != new_deadline)
                or (new_assignment_number and alert.assignment_number != new_assignment_number)
                or (new_quiz_number and alert.quiz_number != new_quiz_number)
                or (new_event_name and alert.event_name != new_event_name)
                or (new_summary and alert.summary_snapshot != new_summary)
            ):
                if new_deadline:
                    alert.deadline_date = new_deadline
                if new_assignment_number:
                    alert.assignment_number = new_assignment_number
                if new_quiz_number:
                    alert.quiz_number = new_quiz_number
                if new_event_name:
                    alert.event_name = new_event_name
                if new_summary:
                    alert.summary_snapshot = new_summary
                if alert_type == "mid_term_event":
                    alert.event_date = new_deadline or alert.event_date
                alert.status = "queued"
                alert.sent_at = None
                alert.save(
                    update_fields=[
                        "deadline_date",
                        "assignment_number",
                        "quiz_number",
                        "event_name",
                        "event_date",
                        "summary_snapshot",
                        "status",
                        "sent_at",
                    ]
                )

        # For submission summaries, always allow re-queue when counts change
        if alert_type == "summary":
            incoming_snapshot = extra_defaults.get("summary_snapshot")
            if incoming_snapshot and alert.summary_snapshot != incoming_snapshot:
                alert.summary_snapshot = incoming_snapshot
                alert.status = "queued"
                alert.sent_at = None
                alert.save(update_fields=["summary_snapshot", "status", "sent_at"])
            return alert

        if not created and alert.status == "sent":
            return None
        return alert

    def _maybe_create_calendar_event(
        self,
        faculty: FacultyUser,
        alert_obj: AssignmentAlert,
        title: str,
        due: date | None,
        course_name: str,
        stage_code: str | None = None,
        summary: Dict[str, int] | None = None,
        source_label: str = "Zabdesk",
    ) -> None:
        """Create a Calendar event once for this alert (idempotent by calendar_event_id)."""

        if not due:
            return
        summary = summary or {}
        submitted = summary.get("submitted", 0)
        total = summary.get("total", 0)
        pending = summary.get("not_submitted", max(total - submitted, 0))

        try:
            cal = CalendarAPI(faculty)
            description_lines = [
                f"Source: {source_label}",
                f"Course: {course_name}",
                f"Assessment: {title}",
                f"Deadline: {due.isoformat()}",
                f"Submission Progress: {submitted}/{total}",
                f"Pending: {pending}",
            ]
            stage_label = self._stage_label(stage_code)
            if stage_label:
                description_lines.append(f"Stage: {stage_label}")
            description = "\n".join(description_lines)

            if alert_obj.calendar_event_id:
                cal.update_event(alert_obj.calendar_event_id, summary=title, description=description)
                self._update_alert_sync(alert_obj, "ok")
                return

            event = cal.create_all_day_event(
                summary=title,
                description=description,
                start_date=due,
                end_date=due + timedelta(days=1),
            )
            event_id = event.get("id")
            if event_id:
                alert_obj.calendar_event_id = event_id
                alert_obj.save(update_fields=["calendar_event_id"])
                self._update_alert_sync(alert_obj, "ok")
                self.stdout.write(self.style.SUCCESS(
                    f"[{timezone.now().isoformat()}] Calendar event created for {faculty.email}: {title} on {due.isoformat()}"
                ))
        except CalendarAPIError as exc:
            self.stdout.write(self.style.WARNING(
                f"[{timezone.now().isoformat()}] Calendar event failed for {faculty.email}: {exc}"
            ))
            self._update_alert_sync(alert_obj, "calendar_failed", getattr(exc, "user_message", str(exc)))

    def _send_announcement(
        self,
        client: ClassroomAPI,
        faculty: FacultyUser,
        course_id: str,
        title: str,
        due: date | None,
        summary: Dict[str, int],
        alert_obj: AssignmentAlert,
        deadline_mode: bool = False,
        stage_code: str | None = None,
    ):
        pending = summary.get("not_submitted", 0)
        total = summary.get("total", 0)
        submitted = summary.get("submitted", 0)
        due_text = due.isoformat() if due else "No due date"
        prefix = "Deadline reminder" if deadline_mode else "Assignment update"
        stage_label = self._stage_label(stage_code) if stage_code else None
        course_name = getattr(self, "course_name_lookup", {}).get(course_id, course_id)
        stage_phrase = self._email_stage_phrase(stage_code)

        subject = f"[Faculty Tasks Automation] {title} – {stage_phrase}".strip()
        lines = [
            f"Dear {faculty.name or faculty.email},",
            "",
            "This is an automated reminder from Faculty Tasks Automation.",
            f"Course: {course_name}",
            f"Assessment: {title}",
            f"Deadline: {due_text}",
        ]
        if stage_label:
            lines.append(f"Stage: {stage_label}")
        lines.extend([
            f"Current Submissions: {submitted}/{total}",
            f"Pending: {pending}",
            "",
            "Please ensure marks and records are updated accordingly.",
            "",
            "— Faculty Tasks Automation",
        ])
        text = "\n".join(lines)

        if alert_obj.status == "sent" and not self.force_send:
            self.stdout.write(f"Skipping already-sent {alert_obj.alert_type}/{alert_obj.alert_stage} for {faculty.email} / {course_id}")
            return

        if alert_obj.is_completed:
            self.stdout.write(
                f"Skipping completed alert {alert_obj.alert_type}/{alert_obj.alert_stage} for {faculty.email} / {course_id}"
            )
            return

        alert_title = f"Assignment – {title}"
        self._maybe_create_calendar_event(
            faculty,
            alert_obj,
            alert_title,
            due,
            course_name,
            stage_code=stage_code,
            summary=summary,
            source_label="Classroom",
        )

        sent = True
        try:
            if not self.dry_run:
                sent = self._send_email_faculty(
                    faculty,
                    subject=subject,
                    message=text,
                )
            if sent:
                alert_obj.status = "sent"
                alert_obj.sent_at = timezone.now()
                alert_obj.save(update_fields=["status", "sent_at"])
                self._update_alert_sync(alert_obj, "ok")
                self.stdout.write(self.style.SUCCESS(
                    f"✓ {prefix.lower()} emailed for {faculty.email} / course {course_id} / stage {stage_code or 'single'}"
                ))
            else:
                self._update_alert_sync(alert_obj, "email_failed", "Email send failed")
                self.stdout.write(self.style.WARNING(
                    f"Email not sent for {faculty.email} / course {course_id}; leaving alert queued"
                ))
        except ClassroomAPIError as exc:
            self.stdout.write(self.style.ERROR(f"✗ Alert failed for {faculty.email} / {course_id}: {exc}"))

    def _send_submission_summary(
        self,
        client: ClassroomAPI,
        faculty: FacultyUser,
        course_id: str,
        title: str,
        summary: Dict[str, int],
        alert_obj: AssignmentAlert,
    ):
        pending = summary.get("not_submitted", 0)
        total = summary.get("total", 0)
        submitted = summary.get("submitted", 0)
        course_name = getattr(self, "course_name_lookup", {}).get(course_id, course_id)
        subject = f"[Faculty Tasks Automation] Submission Update — {title} ({course_name})"
        text = (
            f"Dear {faculty.name or faculty.email},\n\n"
            "This is an automated reminder from Faculty Tasks Automation.\n"
            f"Course: {course_name}\n"
            f"Assessment: {title}\n"
            f"Submitted: {submitted}/{total}\n"
            f"Pending: {pending}\n\n"
            "Please ensure marks and records are updated accordingly.\n\n"
            "— Faculty Tasks Automation"
        )

        if alert_obj.status == "sent" and not self.force_send:
            self.stdout.write(
                f"Skipping summary alert already sent for {faculty.email} / {course_id}"
            )
            return

        if alert_obj.is_completed:
            self.stdout.write(
                f"Skipping summary alert (completed) for {faculty.email} / {course_id}"
            )
            return

        sent = True
        try:
            if not self.dry_run:
                sent = self._send_email_faculty(
                    faculty,
                    subject=subject,
                    message=text,
                )
            if sent:
                alert_obj.summary_snapshot = summary
                alert_obj.status = "sent"
                alert_obj.sent_at = timezone.now()
                alert_obj.save(update_fields=["summary_snapshot", "status", "sent_at"])
                self._update_alert_sync(alert_obj, "ok")
                self.stdout.write(self.style.SUCCESS(
                    f"✓ Submission summary emailed for {faculty.email} / course {course_id}"
                ))
            else:
                self._update_alert_sync(alert_obj, "email_failed", "Email send failed")
                self.stdout.write(self.style.WARNING(
                    f"Email not sent for {faculty.email} / course {course_id}; leaving summary queued"
                ))
        except ClassroomAPIError as exc:
            self.stdout.write(self.style.ERROR(
                f"✗ Submission summary failed for {faculty.email} / {course_id}: {exc}"
            ))

    # ------------------------------------------------------------------
    # Entry deadline alerts (fixed calendar)
    # ------------------------------------------------------------------
    def _process_entry_deadlines(self, faculty: FacultyUser, client: ClassroomAPI, courses: List[ClassroomCourse], today: date):
        for entry in ENTRY_DEADLINES:
            deadline = entry["deadline"]
            days_remaining = (deadline - today).days
            stage_code = self._stage_from_days(days_remaining)
            if not stage_code:
                continue

            assignment_number = entry["assignment_number"]
            # Use sentinel IDs to keep uniqueness consistent without new tables.
            course_id = "entry-deadline"
            assignment_id = f"entry-{assignment_number}"

            alert = self._ensure_alert(
                faculty,
                course_id,
                assignment_id,
                "entry_deadline",
                alert_stage=stage_code,
                assignment_number=assignment_number,
                deadline_date=deadline,
            )
            if not alert:
                continue

            message = (
                f"Assignment {assignment_number} marks entry deadline ({self._stage_label(stage_code)}).\n"
                f"Deadline: {deadline.isoformat()}\n"
                "Automated alert from Faculty Tasks Automation"
            )
            self._post_entry_deadline_announcements(
                client,
                faculty,
                courses,
                message,
                alert,
                stage_code,
                deadline,
                assignment_number,
            )

    def _post_entry_deadline_announcements(
        self,
        client: ClassroomAPI,
        faculty: FacultyUser,
        courses: List[ClassroomCourse],
        message: str,
        alert_obj: AssignmentAlert,
        stage_code: str,
        deadline: date,
        assignment_number: int,
    ):
        stage_phrase = self._email_stage_phrase(stage_code)
        subject = f"[Faculty Tasks Automation] Assignment {assignment_number} Marks Entry – {stage_phrase}".strip()
        if not courses:
            self.stdout.write(f"No courses for {faculty.email}; entry deadline not emailed.")
            return

        self._maybe_create_calendar_event(
            faculty,
            alert_obj,
            f"Assignment Entry – Assignment {assignment_number}",
            deadline,
            "Marks Entry",
            stage_code=stage_code,
            source_label="Zabdesk",
        )

        sent = True
        if not self.dry_run:
            sent = self._send_email_faculty(
                faculty,
                subject=subject,
                message=(
                    f"Dear {faculty.name or faculty.email},\n\n"
                    "This is an automated reminder from Faculty Tasks Automation.\n"
                    f"Assessment: Assignment {assignment_number} marks entry\n"
                    f"Deadline: {deadline.isoformat()}\n"
                    f"Stage: {self._stage_label(stage_code)}\n\n"
                    "Please ensure marks and records are updated accordingly.\n\n"
                    "— Faculty Tasks Automation"
                ),
            )
        if sent:
            alert_obj.status = "sent"
            alert_obj.sent_at = timezone.now()
            alert_obj.save(update_fields=["status", "sent_at"])
            self._update_alert_sync(alert_obj, "ok")
        else:
            self._update_alert_sync(alert_obj, "email_failed", "Email send failed")
            self.stdout.write(self.style.WARNING(
                f"Email not sent for {faculty.email}; entry deadline left queued"
            ))

    # ------------------------------------------------------------------
    # Mid-term events (fixed calendar)
    # ------------------------------------------------------------------
    def _process_mid_term_events(self, faculty: FacultyUser, client: ClassroomAPI, courses: List[ClassroomCourse], today: date):
        for entry in MID_TERM_EVENTS:
            event_date = entry["event_date"]
            days_remaining = (event_date - today).days
            stage_code = self._stage_from_days(days_remaining)
            if not stage_code:
                continue

            key = entry["key"]
            name = entry["name"]
            course_id = "mid-term"
            assignment_id = f"mid-term-{key}"

            alert = self._ensure_alert(
                faculty,
                course_id,
                assignment_id,
                "mid_term_event",
                alert_stage=stage_code,
                event_name=name,
                deadline_date=event_date,
            )
            if not alert:
                continue

            message = (
                f"{name} is approaching ({self._stage_label(stage_code)}).\n"
                f"Event date: {event_date.isoformat()}\n"
                "Automated alert from Faculty Tasks Automation"
            )
            self._post_mid_term_announcements(
                client,
                faculty,
                courses,
                message,
                alert,
                stage_code,
                event_date,
                name,
            )

    def _post_mid_term_announcements(
        self,
        client: ClassroomAPI,
        faculty: FacultyUser,
        courses: List[ClassroomCourse],
        message: str,
        alert_obj: AssignmentAlert,
        stage_code: str,
        event_date: date,
        name: str,
    ):
        stage_phrase = self._email_stage_phrase(stage_code)
        subject = f"[Faculty Tasks Automation] {name} – {stage_phrase}".strip()
        if not courses:
            self.stdout.write(f"No courses for {faculty.email}; mid-term alert not emailed.")
            return

        self._maybe_create_calendar_event(
            faculty,
            alert_obj,
            f"Mid Term – {name}",
            event_date,
            "Mid-term",
            stage_code=stage_code,
            source_label="Zabdesk",
        )

        sent = True
        if not self.dry_run:
            sent = self._send_email_faculty(
                faculty,
                subject=subject,
                message=(
                    f"Dear {faculty.name or faculty.email},\n\n"
                    "This is an automated reminder from Faculty Tasks Automation.\n"
                    f"Event: {name}\n"
                    f"Date: {event_date.isoformat()}\n"
                    f"Stage: {self._stage_label(stage_code)}\n\n"
                    "Please ensure marks and records are updated accordingly.\n\n"
                    "— Faculty Tasks Automation"
                ),
            )
        if sent:
            alert_obj.status = "sent"
            alert_obj.sent_at = timezone.now()
            alert_obj.save(update_fields=["status", "sent_at"])
            self._update_alert_sync(alert_obj, "ok")
        else:
            self._update_alert_sync(alert_obj, "email_failed", "Email send failed")
            self.stdout.write(self.style.WARNING(
                f"Email not sent for {faculty.email}; mid-term alert left queued"
            ))

    # ------------------------------------------------------------------
    # Quiz entry deadline alerts (fixed calendar)
    # ------------------------------------------------------------------
    def _process_quiz_deadlines(self, faculty: FacultyUser, client: ClassroomAPI, courses: List[ClassroomCourse], today: date):
        for entry in QUIZ_ENTRY_DEADLINES:
            deadline = entry["deadline"]
            days_remaining = (deadline - today).days
            stage_code = self._stage_from_days(days_remaining)
            if not stage_code:
                continue

            quiz_number = entry["quiz_number"]
            course_id = "quiz-entry"
            assignment_id = f"quiz-{quiz_number}"

            alert = self._ensure_alert(
                faculty,
                course_id,
                assignment_id,
                "quiz_entry_deadline",
                alert_stage=stage_code,
                quiz_number=quiz_number,
                deadline_date=deadline,
            )
            if not alert:
                continue

            message = (
                f"Quiz {quiz_number} marks entry deadline ({self._stage_label(stage_code)}).\n"
                f"Deadline: {deadline.isoformat()}\n"
                "Automated alert from Faculty Tasks Automation"
            )
            self._post_quiz_deadline_announcements(
                client,
                faculty,
                courses,
                message,
                alert,
                stage_code,
                deadline,
                quiz_number,
            )

    def _post_quiz_deadline_announcements(
        self,
        client: ClassroomAPI,
        faculty: FacultyUser,
        courses: List[ClassroomCourse],
        message: str,
        alert_obj: AssignmentAlert,
        stage_code: str,
        deadline: date,
        quiz_number: int,
    ):
        stage_phrase = self._email_stage_phrase(stage_code)
        subject = f"[Faculty Tasks Automation] Quiz {quiz_number} Marks Entry – {stage_phrase}".strip()
        if not courses:
            self.stdout.write(f"No courses for {faculty.email}; quiz entry deadline not emailed.")
            return

        self._maybe_create_calendar_event(
            faculty,
            alert_obj,
            f"Quiz Entry – Quiz {quiz_number}",
            deadline,
            "Marks Entry",
            stage_code=stage_code,
            source_label="Zabdesk",
        )

        sent = True
        if not self.dry_run:
            sent = self._send_email_faculty(
                faculty,
                subject=subject,
                message=(
                    f"Dear {faculty.name or faculty.email},\n\n"
                    "This is an automated reminder from Faculty Tasks Automation.\n"
                    f"Assessment: Quiz {quiz_number} marks entry\n"
                    f"Deadline: {deadline.isoformat()}\n"
                    f"Stage: {self._stage_label(stage_code)}\n\n"
                    "Please ensure marks and records are updated accordingly.\n\n"
                    "— Faculty Tasks Automation"
                ),
            )
        if sent:
            alert_obj.status = "sent"
            alert_obj.sent_at = timezone.now()
            alert_obj.save(update_fields=["status", "sent_at"])
            self._update_alert_sync(alert_obj, "ok")
        else:
            self._update_alert_sync(alert_obj, "email_failed", "Email send failed")
            self.stdout.write(self.style.WARNING(
                f"Email not sent for {faculty.email}; quiz entry alert left queued"
            ))

    def _stage_from_days(self, days_remaining: int | None) -> str | None:
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

    def _stage_label(self, stage_code: str | None) -> str | None:
        lookup = {
            "two_days": "Due in 2 days",
            "one_day": "Due tomorrow",
            "today": "Due today",
            "overdue": "Overdue",
            "single": None,
            None: None,
        }
        return lookup.get(stage_code)

    def _email_stage_phrase(self, stage_code: str | None) -> str:
        phrase_map = {
            "two_days": "2 Days Remaining",
            "one_day": "1 Day Remaining",
            "today": "Due Today",
            "overdue": "Overdue",
            "single": "Update",
            None: "Update",
        }
        return phrase_map.get(stage_code, "update")

    def _summary_changed(self, existing: Dict[str, int] | None, incoming: Dict[str, int]) -> bool:
        existing = existing or {}
        return (
            existing.get("total") != incoming.get("total")
            or existing.get("submitted") != incoming.get("submitted")
            or existing.get("not_submitted") != incoming.get("not_submitted")
        )

    def _update_alert_sync(self, alert_obj: AssignmentAlert, sync_status: str, last_error: str = "") -> None:
        """Persist last sync outcome without breaking the job on DB errors."""
        try:
            alert_obj.sync_status = sync_status
            alert_obj.last_error = last_error or ""
            alert_obj.save(update_fields=["sync_status", "last_error", "updated_at"])
        except DatabaseError as exc:
            self.stdout.write(self.style.WARNING(f"Sync status update failed for alert {alert_obj.id}: {exc}"))

    def _send_email_faculty(self, faculty: FacultyUser, subject: str, message: str) -> bool:
        """Send a plain-text email to the faculty; return True on success."""

        to_email = faculty.email
        from_email = getattr(settings, "EMAIL_FROM_ADDRESS", "") or settings.EMAIL_HOST_USER
        if not (from_email and settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD):
            self.stdout.write(self.style.WARNING(
                f"[{timezone.now().isoformat()}] Email not configured; skipped email for {faculty.email} (set EMAIL_HOST_USER/PASSWORD)."
            ))
            return False
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=[to_email],
                fail_silently=False,
            )
            self.stdout.write(self.style.SUCCESS(
                f"[{timezone.now().isoformat()}] ✓ Email sent to {to_email}: {subject}"
            ))
            return True
        except BadHeaderError:
            self.stdout.write(self.style.ERROR(
                f"[{timezone.now().isoformat()}] Invalid email header when sending to {to_email}"
            ))
        except smtplib.SMTPException as exc:
            self.stdout.write(self.style.ERROR(
                f"[{timezone.now().isoformat()}] SMTP error sending to {to_email}: {exc}"
            ))
        except ValueError as exc:
            self.stdout.write(self.style.ERROR(
                f"[{timezone.now().isoformat()}] Email send failed for {to_email}: {exc}"
            ))
        return False


def parse_due_date(coursework: Dict) -> date | None:
    due = coursework.get("dueDate") or {}
    year = due.get("year")
    month = due.get("month")
    day = due.get("day")
    if not (year and month and day):
        return None
    try:
        return date(int(year), int(month), int(day))
    except (TypeError, ValueError):  # pragma: no cover - defensive parsing
        return None
