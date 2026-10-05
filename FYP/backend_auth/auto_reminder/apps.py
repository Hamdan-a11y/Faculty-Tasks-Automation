"""Auto Reminder app configuration."""

from django.apps import AppConfig


class AutoReminderConfig(AppConfig):
    """Auto Reminder app for deadline-based email reminders."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "auto_reminder"
    verbose_name = "Auto Reminder"

    def ready(self):  # pragma: no cover - startup hook
        # Start optional scheduler (controlled via env AUTO_REMINDER_SCHEDULE_MINUTES)
        from .scheduler import start_scheduler  # imported lazily to avoid app registry issues
        start_scheduler()
