"""
Django management command to send deadline reminders.

Usage:
    python manage.py send_reminders
    python manage.py send_reminders --seed-only
    python manage.py send_reminders --faculty-email=user@example.com

Runs daily to check deadlines and send emails to all faculty.
Idempotent: won't resend to same faculty/deadline/offset twice.
"""

from datetime import datetime, timedelta
from typing import Optional

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import IntegrityError

from auth_app.models import FacultyUser
from auto_reminder.models import Deadline, ReminderPreference, ReminderLog
from auto_reminder.email_service import ReminderEmailService


# Academic deadlines from requirements (authoritative source)
ACADEMIC_DEADLINES = [
    ("Course Outline", "2025-09-12", "Week 0", "Submit course outline to department"),
    ("Quiz 1 Entries", "2025-10-03", "Week 3", "Enter Quiz 1 marks in system"),
    ("Assignment 1 Entries", "2025-10-10", "Week 4", "Enter Assignment 1 marks"),
    ("Mid Exam Dept Submission", "2025-10-22", "Week 6", "Submit mid exam to HoD"),
    ("Quiz 2 Entries", "2025-10-24", "Week 7", "Enter Quiz 2 marks in system"),
    ("Assignment 2 Entries", "2025-10-31", "Week 7", "Enter Assignment 2 marks"),
    ("Mid Term Exams", "2025-11-03", "Week 8", "Mid term exam period (3–9 Nov)"),
    ("Mid FYP Presentations II", "2025-11-18", "Week 9", "FYP mid-term presentation batch II"),
    ("Mid FYP Presentations I", "2025-11-20", "Week 9", "FYP mid-term presentation batch I"),
    ("Mid Term Entries", "2025-11-24", "Week 10", "Enter mid-term grades in system"),
    ("Quiz 3 Entries", "2025-12-05", "Week 11", "Enter Quiz 3 marks in system"),
    ("Assignment 3 Entries", "2025-12-12", "Week 12", "Enter Assignment 3 marks"),
    ("Final Exam Submission to HoD", "2025-12-12", "Week 13", "Submit final exam to HoD"),
    ("Quiz 4 Entries", "2025-12-26", "Week 14", "Enter Quiz 4 marks in system"),
    ("Assignment 4 Entries", "2026-01-02", "Week 15", "Enter Assignment 4 marks"),
    ("Final Exam", "2026-01-05", "Week 16", "Final exam period (5–18 Jan)"),
    ("Final Exam Result Submission", "2026-01-20", "Week 18", "Submit final grades to registrar"),
    ("Final Exam Entries & Recap", "2026-01-26", "Week 19", "Enter final marks and course recap"),
    ("FCAR & Course Folders", "2026-02-06", "Week 20", "Submit FCAR and course materials"),
]

DEFAULT_REMINDER_OFFSETS = [7, 3, 1]  # Send reminders 7, 3, 1 days before


class Command(BaseCommand):
    """Seed deadlines and send reminder emails."""

    help = "Send deadline reminders to all faculty users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed-only",
            action="store_true",
            help="Only seed deadlines, don't send emails",
        )
        parser.add_argument(
            "--faculty-email",
            type=str,
            help="Send reminders only for this faculty email",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be sent without actually sending",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("\n🕐 Auto Reminder Scheduler"))
        self.stdout.write(self.style.SUCCESS("=" * 60))

        # Step 1: Seed deadlines
        self.seed_deadlines()

        if options["seed_only"]:
            self.stdout.write(self.style.SUCCESS("\n✓ Seeding complete. Exiting."))
            return

        # Step 2: Send reminders
        self.send_reminders(
            faculty_email=options.get("faculty_email"),
            dry_run=options.get("dry_run", False),
        )

        self.stdout.write(self.style.SUCCESS("\n" + "=" * 60))
        self.stdout.write(self.style.SUCCESS("✓ Reminder job completed.\n"))

    def seed_deadlines(self):
        """Idempotently seed academic deadlines."""
        self.stdout.write("\n📌 Seeding deadlines...")

        created_count = 0
        for task_name, date_str, week, description in ACADEMIC_DEADLINES:
            # Parse date as midnight Asia/Karachi
            naive_dt = datetime.strptime(date_str, "%Y-%m-%d")
            # Localize using Django helper (safe for zoneinfo)
            tz = timezone.get_current_timezone()  # Asia/Karachi from settings
            deadline_date = timezone.make_aware(naive_dt, tz)

            deadline, is_created = Deadline.objects.update_or_create(
                task_name=task_name,
                defaults={
                    "deadline_date": deadline_date,
                    "week_number": int(week.split()[-1]) if "Week" in week else None,
                    "description": description,
                    "reminder_offsets": DEFAULT_REMINDER_OFFSETS,
                    "is_active": True,
                },
            )

            if is_created:
                created_count += 1
                self.stdout.write(f"  ✓ Created: {task_name} ({date_str})")
            else:
                self.stdout.write(f"  → Updated: {task_name}")

        self.stdout.write(self.style.SUCCESS(f"\n✓ Deadlines seeded ({created_count} new)."))

    def send_reminders(
        self,
        faculty_email: Optional[str] = None,
        dry_run: bool = False,
    ):
        """
        Send reminders for upcoming deadlines.
        
        Logic:
          1. Get all faculty users
          2. For each active deadline:
             - Calculate days until deadline
             - For each reminder offset (7, 3, 1):
               - If today matches "deadline - offset", send reminder
               - Check ReminderLog to prevent duplicates
          3. Log sent reminders
        """
        self.stdout.write("\n📧 Sending reminders...")

        now = timezone.now()
        today = now.date()

        # Fetch faculty
        if faculty_email:
            faculty_qs = FacultyUser.objects.filter(email=faculty_email)
            if not faculty_qs.exists():
                raise CommandError(f"Faculty not found: {faculty_email}")
            self.stdout.write(f"  Filtering to: {faculty_email}")
        else:
            faculty_qs = FacultyUser.objects.all()

        if not faculty_qs.exists():
            self.stdout.write(self.style.WARNING("  ⚠ No faculty users found."))
            return

        self.stdout.write(f"  Processing {faculty_qs.count()} faculty member(s)...\n")

        # Fetch all active deadlines
        deadlines = Deadline.objects.filter(is_active=True).order_by("deadline_date")

        sent_count = 0
        skipped_count = 0
        failed_count = 0

        for deadline in deadlines:
            # Calculate days until deadline
            deadline_date_only = deadline.deadline_date.date()
            days_until = (deadline_date_only - today).days

            # Only send if we're within 7 days OR deadline is in past (catch-up)
            if days_until > 7:
                continue

            # Get reminder offsets for this deadline
            offsets = deadline.reminder_offsets or DEFAULT_REMINDER_OFFSETS

            for faculty in faculty_qs:
                for offset in offsets:
                    # Check if today is exactly "offset days before"
                    target_date = deadline_date_only - timedelta(days=offset)
                    if target_date != today:
                        continue

                    # Check for duplicate log entry
                    log_exists = ReminderLog.objects.filter(
                        faculty=faculty,
                        deadline=deadline,
                        offset_days=offset,
                    ).exists()

                    if log_exists:
                        skipped_count += 1
                        self.stdout.write(
                            f"  ⊘ Already sent: {faculty.email} → "
                            f"{deadline.task_name} ({offset}d)"
                        )
                        continue

                    # Check if reminders are enabled for faculty
                    pref = ReminderPreference.objects.filter(
                        faculty=faculty
                    ).first()
                    if pref and not pref.enable_reminders:
                        skipped_count += 1
                        self.stdout.write(
                            f"  ⊘ Reminders disabled: {faculty.email}"
                        )
                        continue

                    # Get recipient email(s)
                    if pref:
                        recipients = pref.get_recipient_emails()
                    else:
                        recipients = [faculty.email]

                    # Send email
                    for recipient_email in recipients:
                        if dry_run:
                            self.stdout.write(
                                self.style.HTTP_INFO(
                                    f"  [DRY-RUN] Would send: {recipient_email} → "
                                    f"{deadline.task_name} ({offset}d)"
                                )
                            )
                            sent_count += 1
                        else:
                            success, error = ReminderEmailService.send_reminder(
                                recipient_email=recipient_email,
                                faculty_name=faculty.name or faculty.email,
                                task_name=deadline.task_name,
                                deadline_date=deadline.deadline_date,
                                days_until=offset,
                                description=deadline.description,
                            )

                            # Log the attempt
                            status = "sent" if success else "failed"
                            try:
                                ReminderLog.objects.create(
                                    faculty=faculty,
                                    deadline=deadline,
                                    offset_days=offset,
                                    email_recipient=recipient_email,
                                    status=status,
                                    error_message="" if success else error,
                                )
                                sent_count += 1
                                self.stdout.write(
                                    self.style.SUCCESS(
                                        f"  ✓ Sent: {recipient_email} → "
                                        f"{deadline.task_name} ({offset}d)"
                                    )
                                )
                            except IntegrityError:
                                # Race condition or duplicate
                                skipped_count += 1
                                self.stdout.write(
                                    f"  ⊘ Duplicate prevented: {recipient_email}"
                                )
                                continue

                            if not success:
                                failed_count += 1
                                self.stdout.write(
                                    self.style.ERROR(
                                        f"  ✗ Failed: {recipient_email} → {error}"
                                    )
                                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Sent: {sent_count} | Skipped: {skipped_count} | Failed: {failed_count}"
            )
        )
