"""
Email service for sending reminder emails via Gmail SMTP.

Configuration via .env:
  EMAIL_BACKEND = django.core.mail.backends.smtp.EmailBackend
  EMAIL_HOST = smtp.gmail.com
  EMAIL_PORT = 587
  EMAIL_USE_TLS = True
  EMAIL_HOST_USER = your-gmail@gmail.com
  EMAIL_HOST_PASSWORD = your-app-password
"""

import logging
import smtplib
from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.core.mail import send_mail, BadHeaderError
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)


class ReminderEmailService:
    """Sends deadline reminder emails via Gmail SMTP."""

    EMAIL_FROM = getattr(settings, "EMAIL_FROM_ADDRESS", settings.EMAIL_HOST_USER)
    EMAIL_SUBJECT_PREFIX = "[Faculty Tasks Automation Reminder]"

    @staticmethod
    def build_subject(task_name: str, days_until: int) -> str:
        """Build email subject line."""
        if days_until == 1:
            return f"{ReminderEmailService.EMAIL_SUBJECT_PREFIX} {task_name} — Due tomorrow!"
        elif days_until <= 3:
            return f"{ReminderEmailService.EMAIL_SUBJECT_PREFIX} {task_name} — {days_until} days left"
        else:
            return f"{ReminderEmailService.EMAIL_SUBJECT_PREFIX} {task_name} — Coming up"

    @staticmethod
    def build_email_body(
        faculty_name: str,
        task_name: str,
        deadline_date: datetime,
        days_until: int,
        description: str = "",
    ) -> str:
        """
        Build plaintext + HTML email body.
        
        Args:
            faculty_name: Full name of faculty member
            task_name: Name of the deadline task
            deadline_date: The deadline datetime (Asia/Karachi timezone)
            days_until: Days remaining until deadline
            description: Optional additional context
        
        Returns:
            Plain text email body (HTML can be added later if needed)
        """
        deadline_str = deadline_date.strftime("%B %d, %Y at %I:%M %p")
        
        body = f"""
Dear {faculty_name},

This is a reminder from Faculty Tasks Automation.

📋 Task: {task_name}
📅 Deadline: {deadline_str} (Asia/Karachi)
⏱️  Time remaining: {days_until} day{"s" if days_until != 1 else ""}

"""
        
        if description:
            body += f"Description:\n{description}\n\n"
        
        body += """Please complete this task before the deadline.

If you have any questions, contact the faculty administration.

---
Faculty Tasks Automation
https://zabdesk.szabist-isb.pk
"""
        
        return body

    @staticmethod
    def send_reminder(
        recipient_email: str,
        faculty_name: str,
        task_name: str,
        deadline_date: datetime,
        days_until: int,
        description: str = "",
    ) -> tuple[bool, Optional[str]]:
        """
        Send a single reminder email.
        
        Args:
            recipient_email: Email to send to
            faculty_name: Faculty member name for greeting
            task_name: Deadline task name
            deadline_date: Deadline datetime (timezone-aware)
            days_until: Days until deadline
            description: Optional task description
        
        Returns:
            (success: bool, error_message: Optional[str])
        """
        try:
            subject = ReminderEmailService.build_subject(task_name, days_until)
            body = ReminderEmailService.build_email_body(
                faculty_name,
                task_name,
                deadline_date,
                days_until,
                description,
            )
            
            send_mail(
                subject=subject,
                message=body,
                from_email=ReminderEmailService.EMAIL_FROM,
                recipient_list=[recipient_email],
                fail_silently=False,
            )
            
            logger.info(
                "[%s] [AutoReminder.Email] [send_ok] %s → %s (%sd)",
                timezone.now().isoformat(),
                recipient_email,
                task_name,
                days_until,
            )
            return True, None

        except BadHeaderError as exc:
            error_msg = str(exc)
            logger.error(
                "[%s] [AutoReminder.Email] [invalid_header] %s: %s",
                timezone.now().isoformat(),
                recipient_email,
                error_msg,
            )
            return False, error_msg
        except smtplib.SMTPException as exc:
            error_msg = str(exc)
            logger.error(
                "[%s] [AutoReminder.Email] [smtp_error] %s: %s",
                timezone.now().isoformat(),
                recipient_email,
                error_msg,
            )
            return False, error_msg
        except ValueError as exc:
            error_msg = str(exc)
            logger.error(
                "[%s] [AutoReminder.Email] [email_error] %s: %s",
                timezone.now().isoformat(),
                recipient_email,
                error_msg,
            )
            return False, error_msg

    @staticmethod
    def send_batch_reminders(
        reminders: list[dict],
    ) -> dict:
        """
        Send multiple reminders efficiently.
        
        Args:
            reminders: List of dicts with keys:
              - recipient_email
              - faculty_name
              - task_name
              - deadline_date
              - days_until
              - description (optional)
        
        Returns:
            {
              'sent': int,
              'failed': int,
              'errors': list[{'email': str, 'error': str}]
            }
        """
        results = {
            "sent": 0,
            "failed": 0,
            "errors": [],
        }
        
        for reminder in reminders:
            success, error = ReminderEmailService.send_reminder(
                recipient_email=reminder["recipient_email"],
                faculty_name=reminder["faculty_name"],
                task_name=reminder["task_name"],
                deadline_date=reminder["deadline_date"],
                days_until=reminder["days_until"],
                description=reminder.get("description", ""),
            )
            
            if success:
                results["sent"] += 1
            else:
                results["failed"] += 1
                results["errors"].append({
                    "email": reminder["recipient_email"],
                    "error": error,
                })
        
        return results
