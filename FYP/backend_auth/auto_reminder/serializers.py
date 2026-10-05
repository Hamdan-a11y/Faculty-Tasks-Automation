"""
DRF serializers for Auto Reminder module.
"""

from rest_framework import serializers

from auth_app.models import FacultyUser
from .models import Deadline, ReminderPreference, ReminderLog, Alert, AssignmentAlert


class DeadlineSerializer(serializers.ModelSerializer):
    """Serialize Deadline for API responses."""

    daysUntil = serializers.SerializerMethodField()
    weekNumber = serializers.IntegerField(source="week_number")
    deadlineDate = serializers.DateTimeField(source="deadline_date")
    reminderOffsets = serializers.JSONField(source="reminder_offsets")
    taskName = serializers.CharField(source="task_name")
    isActive = serializers.BooleanField(source="is_active")

    class Meta:
        model = Deadline
        fields = [
            "id",
            "taskName",
            "deadlineDate",
            "weekNumber",
            "description",
            "reminderOffsets",
            "isActive",
            "daysUntil",
        ]

    def get_daysUntil(self, obj) -> int:
        """Calculate days until deadline from now."""
        from django.utils import timezone
        now = timezone.now()
        delta = obj.deadline_date.date() - now.date()
        return delta.days


class ReminderLogSerializer(serializers.ModelSerializer):
    """Serialize ReminderLog for API responses."""

    taskName = serializers.CharField(source="deadline.task_name", read_only=True)
    offsetDays = serializers.IntegerField(source="offset_days")
    emailRecipient = serializers.EmailField(source="email_recipient")
    sentAt = serializers.DateTimeField(source="sent_at")
    errorMessage = serializers.CharField(source="error_message")

    class Meta:
        model = ReminderLog
        fields = [
            "id",
            "taskName",
            "offsetDays",
            "emailRecipient",
            "sentAt",
            "status",
            "errorMessage",
        ]


class ReminderPreferenceSerializer(serializers.ModelSerializer):
    """Serialize ReminderPreference for GET/PUT."""

    enableReminders = serializers.BooleanField(source="enable_reminders", required=False)
    emailOverride = serializers.EmailField(
        source="email_override", required=False, allow_blank=True, allow_null=True
    )
    recipientEmails = serializers.JSONField(source="recipient_emails", required=False)

    class Meta:
        model = ReminderPreference
        fields = [
            "enableReminders",
            "emailOverride",
            "recipientEmails",
        ]

    def update(self, instance, validated_data):
        """Update preference (only settable fields)."""
        instance.enable_reminders = validated_data.get(
            "enable_reminders", instance.enable_reminders
        )
        instance.email_override = validated_data.get(
            "email_override", instance.email_override
        )
        instance.recipient_emails = validated_data.get(
            "recipient_emails", instance.recipient_emails
        )
        instance.save()
        return instance


class AlertSerializer(serializers.ModelSerializer):
    """Serialize Alert entries for UI (queued/upcoming)."""

    alertType = serializers.CharField(source="alert_type.code")
    status = serializers.CharField(source="status.code")
    triggerDate = serializers.DateField(source="trigger_date")
    sentAt = serializers.DateTimeField(source="sent_at", required=False, allow_null=True)
    courseId = serializers.CharField(source="course.course_id", allow_null=True)
    courseName = serializers.CharField(source="course.name", allow_null=True)
    payload = serializers.JSONField()

    class Meta:
        model = Alert
        fields = [
            "id",
            "alertType",
            "status",
            "triggerDate",
            "sentAt",
            "courseId",
            "courseName",
            "payload",
        ]


class AssignmentAlertSerializer(serializers.ModelSerializer):
    """Minimal serializer for assignment alerts."""

    class Meta:
        model = AssignmentAlert
        fields = [
            "id",
            "faculty",
            "course_id",
            "assignment_id",
            "assignment_number",
            "deadline_date",
            "quiz_number",
            "event_name",
            "event_date",
            "alert_type",
            "status",
            "created_at",
            "sent_at",
        ]
