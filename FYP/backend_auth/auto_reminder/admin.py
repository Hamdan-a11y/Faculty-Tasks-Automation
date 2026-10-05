"""
Django admin configuration for Auto Reminder models.
"""

from django.contrib import admin

from .models import Deadline, ReminderPreference, ReminderLog


@admin.register(Deadline)
class DeadlineAdmin(admin.ModelAdmin):
    """Admin interface for Deadline management."""

    list_display = [
        "task_name",
        "deadline_date",
        "week_number",
        "is_active",
        "created_at",
    ]
    list_filter = ["is_active", "deadline_date", "week_number"]
    search_fields = ["task_name", "description"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        ("Deadline Info", {
            "fields": ("task_name", "deadline_date", "week_number", "is_active"),
        }),
        ("Details", {
            "fields": ("description", "reminder_offsets"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(ReminderPreference)
class ReminderPreferenceAdmin(admin.ModelAdmin):
    """Admin interface for ReminderPreference management."""

    list_display = [
        "faculty_email",
        "enable_reminders",
        "email_override",
        "updated_at",
    ]
    list_filter = ["enable_reminders", "updated_at"]
    search_fields = ["faculty__email"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        ("Faculty", {
            "fields": ("faculty",),
        }),
        ("Settings", {
            "fields": ("enable_reminders", "email_override", "recipient_emails"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    def faculty_email(self, obj):
        return obj.faculty.email

    faculty_email.short_description = "Faculty Email"


@admin.register(ReminderLog)
class ReminderLogAdmin(admin.ModelAdmin):
    """Admin interface for ReminderLog (audit trail)."""

    list_display = [
        "id",
        "faculty_email",
        "deadline_task",
        "offset_days",
        "status",
        "sent_at",
    ]
    list_filter = ["status", "sent_at", "offset_days"]
    search_fields = [
        "faculty__email",
        "deadline__task_name",
        "email_recipient",
    ]
    readonly_fields = ["sent_at", "faculty", "deadline", "offset_days"]

    fieldsets = (
        ("Reminder Info", {
            "fields": ("faculty", "deadline", "offset_days", "email_recipient"),
        }),
        ("Status", {
            "fields": ("status", "error_message"),
        }),
        ("Timestamp", {
            "fields": ("sent_at",),
        }),
    )

    def faculty_email(self, obj):
        return obj.faculty.email

    def deadline_task(self, obj):
        return obj.deadline.task_name

    faculty_email.short_description = "Faculty"
    deadline_task.short_description = "Task"

    def has_add_permission(self, request):
        # Prevent manual creation of logs (created automatically by scheduler)
        return False

    def has_delete_permission(self, request, obj=None):
        # Allow deletion for audit cleanup
        return True
