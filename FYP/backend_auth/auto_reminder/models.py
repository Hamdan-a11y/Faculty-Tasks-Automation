"""
Auto Reminder models for deadline-based email reminders.

Models:
  - Deadline: Academic deadlines seeded from the requirements table.
  - ReminderPreference: Faculty-level reminder settings.
  - ReminderLog: Log of sent reminders (duplicate prevention).
"""

from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

from auth_app.models import FacultyUser
from auth_app.models import ClassroomCourse


# Minimal assignment alert statuses/types
ASSIGNMENT_ALERT_TYPES = (
    ("created", "Assignment created"),
    ("deadline", "Deadline approaching"),
    ("summary", "Submission summary"),
    ("entry_deadline", "Assignment entry deadline"),
    ("quiz_entry_deadline", "Quiz entry deadline"),
    ("mid_term_event", "Mid term event"),
)

ALERT_STAGE_CHOICES = (
    ("three_days", "3 days before"),
    ("two_days", "2 days before"),
    ("one_day", "1 day before"),
    ("today", "Deadline day"),
    ("overdue", "Overdue"),
    ("single", "Single/legacy"),
)

ALERT_TARGET_CHOICES = (
    ("faculty", "Faculty"),
    ("student", "Student"),
)

ASSIGNMENT_ALERT_STATUSES = (
    ("queued", "Queued"),
    ("sent", "Sent"),
)


class Deadline(models.Model):
    """
    Academic deadline with multiple reminder triggers.
    
    Example: Course Outline due 12 Sept 2025
      → reminders on 5 Sept (7 days), 9 Sept (3 days), 11 Sept (1 day)
    """

    task_name = models.CharField(
        max_length=255,
        unique=True,
        help_text="e.g. 'Course Outline', 'Quiz 1 Entries'"
    )
    deadline_date = models.DateTimeField(
        help_text="Deadline date/time in Asia/Karachi timezone"
    )
    week_number = models.IntegerField(
        null=True,
        blank=True,
        help_text="Week number for reference (e.g. Week 0, Week 3)"
    )
    description = models.TextField(
        blank=True,
        help_text="Additional context for the reminder email"
    )
    reminder_offsets = models.JSONField(
        default=list,
        help_text="List of days before deadline to send reminder (e.g. [7, 3, 1])"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Disable to pause reminders for this deadline"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["deadline_date"]
        verbose_name = "Deadline"
        verbose_name_plural = "Deadlines"
        indexes = [
            models.Index(fields=["deadline_date", "is_active"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.task_name} ({self.deadline_date.strftime('%Y-%m-%d')})"


class ReminderPreference(models.Model):
    """
    Per-faculty reminder preferences and settings.
    """

    faculty = models.OneToOneField(
        FacultyUser,
        on_delete=models.CASCADE,
        related_name="reminder_preference"
    )
    enable_reminders = models.BooleanField(
        default=True,
        help_text="Master toggle for all reminders"
    )
    email_override = models.EmailField(
        blank=True,
        help_text="Optional: send reminders to this email instead of faculty email"
    )
    recipient_emails = models.JSONField(
        default=list,
        help_text="List of recipient emails (overrides single email_override if set)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Reminder Preference"
        verbose_name_plural = "Reminder Preferences"

    def __str__(self) -> str:
        return f"Reminders for {self.faculty.email}"

    def get_recipient_emails(self) -> list[str]:
        """Get final list of recipient emails."""
        if self.recipient_emails and isinstance(self.recipient_emails, list):
            return self.recipient_emails
        if self.email_override:
            return [self.email_override]
        return [self.faculty.email]


class ReminderLog(models.Model):
    """
    Log of sent reminders for idempotency and audit trail.
    Prevents duplicate email sends.
    """

    STATUS_CHOICES = [
        ("sent", "Successfully sent"),
        ("failed", "Failed to send"),
        ("skipped", "Skipped (user disabled)"),
    ]

    faculty = models.ForeignKey(
        FacultyUser,
        on_delete=models.CASCADE,
        related_name="reminder_logs"
    )
    deadline = models.ForeignKey(
        Deadline,
        on_delete=models.CASCADE,
        related_name="reminder_logs"
    )
    offset_days = models.IntegerField(
        help_text="Which reminder trigger: 7, 3, or 1 days before"
    )
    email_recipient = models.EmailField(
        help_text="Email address that received the reminder"
    )
    sent_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the reminder was sent (UTC, stored timezone-aware)"
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="sent"
    )
    error_message = models.TextField(
        blank=True,
        help_text="Error details if status is 'failed'"
    )

    class Meta:
        ordering = ["-sent_at"]
        verbose_name = "Reminder Log"
        verbose_name_plural = "Reminder Logs"
        unique_together = [["faculty", "deadline", "offset_days"]]
        indexes = [
            models.Index(fields=["faculty", "deadline", "offset_days"]),
            models.Index(fields=["sent_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.faculty.email} → {self.deadline.task_name} ({self.offset_days}d)"


class AlertType(models.Model):
    """Type of alert (e.g., assignment_created, deadline_reminder)."""

    code = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.code


class AlertStatus(models.Model):
    """Lifecycle status for alerts (queued, sent, failed, skipped)."""

    code = models.CharField(max_length=32, unique=True)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.code


class Alert(models.Model):
    """Alert record for Classroom announcements and deadline nudges."""

    faculty = models.ForeignKey(FacultyUser, on_delete=models.CASCADE, related_name="alerts")
    course = models.ForeignKey(
        ClassroomCourse, on_delete=models.CASCADE, related_name="alerts", null=True, blank=True
    )
    alert_type = models.ForeignKey(AlertType, on_delete=models.PROTECT, related_name="alerts")
    status = models.ForeignKey(AlertStatus, on_delete=models.PROTECT, related_name="alerts")
    trigger_date = models.DateField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    external_id = models.CharField(
        max_length=255,
        help_text="External identifier to prevent duplicates (e.g., coursework ID or deadline key)",
    )
    payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [
            ["faculty", "course", "alert_type", "external_id"],
        ]
        indexes = [
            models.Index(fields=["trigger_date", "status"]),
            models.Index(fields=["faculty", "status"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.alert_type.code} for {self.faculty.email} ({self.external_id})"


class AssignmentAlert(models.Model):
    """Minimal model for assignment-related alerts only."""

    faculty = models.ForeignKey(FacultyUser, on_delete=models.CASCADE, related_name="assignment_alerts")
    course_id = models.CharField(max_length=128, help_text="Google Classroom course ID")
    assignment_id = models.CharField(max_length=128, help_text="Google Classroom coursework ID")
    assignment_number = models.PositiveIntegerField(null=True, blank=True, help_text="Assignment sequence number")
    deadline_date = models.DateField(null=True, blank=True, help_text="Entry/deadline date for the assignment")
    quiz_number = models.PositiveIntegerField(null=True, blank=True, help_text="Quiz sequence number for entry deadlines")
    event_name = models.CharField(max_length=255, null=True, blank=True, help_text="Mid term event name")
    event_date = models.DateField(null=True, blank=True, help_text="Mid term event date (start for ranges)")
    alert_type = models.CharField(max_length=32, choices=ASSIGNMENT_ALERT_TYPES)
    alert_stage = models.CharField(
        max_length=32,
        choices=ALERT_STAGE_CHOICES,
        default="single",
        help_text="Multi-stage reminder bucket (3d/2d/1d/today/overdue)",
    )
    alert_target = models.CharField(
        max_length=16,
        choices=ALERT_TARGET_CHOICES,
        default="faculty",
        help_text="Who should see this alert",
    )
    status = models.CharField(max_length=16, choices=ASSIGNMENT_ALERT_STATUSES, default="queued")
    is_completed = models.BooleanField(default=False, help_text="Marked done by faculty to stop future alerts")
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        FacultyUser,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_assignment_alerts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    summary_snapshot = models.JSONField(default=dict, blank=True, help_text="Last known submission summary")
    calendar_event_id = models.CharField(
        max_length=256,
        blank=True,
        help_text="Google Calendar event id to prevent duplicate creations",
    )
    sync_status = models.CharField(
        max_length=32,
        default="ok",
        help_text="Last sync status for Calendar/Email actions",
    )
    last_error = models.TextField(
        blank=True,
        help_text="Last sync error message (for support/debug)",
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [
            ["faculty", "course_id", "assignment_id", "alert_type", "alert_stage"],
        ]
        indexes = [
            models.Index(fields=["faculty", "status"]),
            models.Index(fields=["course_id", "assignment_id"]),
            models.Index(fields=["assignment_number", "deadline_date"]),
            models.Index(fields=["quiz_number", "deadline_date"]),
            models.Index(fields=["event_date", "alert_type"]),
            models.Index(fields=["alert_stage", "alert_type"]),
            models.Index(fields=["is_completed", "deadline_date"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        label = self.assignment_number or self.quiz_number or self.event_name or self.assignment_id
        return f"{self.alert_type} {label} for {self.faculty.email}"
