"""Shared helpers to run assignment alerts on demand.

These wrap the existing management command logic so we can invoke it from
API endpoints or scheduler without duplicating code.
"""

from __future__ import annotations

import io
from typing import Optional

from django.conf import settings
from django.db import DatabaseError

from auth_app.models import FacultyUser
from auto_reminder.management.commands.run_auto_alerts import Command
from .models import ReminderPreference
from .classroom_service import ClassroomAPIError
from .calendar_service import CalendarAPIError


def _make_command(dry_run: bool = False, course_id: Optional[str] = None, force_send: bool = False) -> Command:
    cmd = Command()
    # Align with management command defaults
    cmd.dry_run = dry_run
    cmd.email_only = True
    cmd.course_filter = course_id
    cmd.force_send = force_send
    cmd.stdout = io.StringIO()
    cmd.stderr = io.StringIO()
    return cmd


def _pref_enabled(faculty: FacultyUser) -> bool:
    pref, _ = ReminderPreference.objects.get_or_create(faculty=faculty)
    return bool(pref.enable_reminders)


def _email_configured() -> bool:
    return bool(getattr(settings, "EMAIL_HOST_USER", "") and getattr(settings, "EMAIL_HOST_PASSWORD", ""))


def run_alerts_for_faculty(
    faculty: FacultyUser,
    course_id: Optional[str] = None,
    dry_run: bool = False,
    force_send: bool = False,
) -> dict:
    """Run alerts for a single faculty using existing command logic."""

    warnings: list[str] = []
    email_ready = _email_configured()
    if not email_ready and not dry_run:
        warnings.append("Email sending is not configured; set EMAIL_HOST_USER and EMAIL_HOST_PASSWORD")
        return {
            "ok": False,
            "skipped": True,
            "reason": "email_not_configured",
            "logs": [],
            "warnings": warnings,
            "email_configured": email_ready,
        }

    if not _pref_enabled(faculty):
        return {
            "ok": False,
            "skipped": True,
            "reason": "alerts_disabled",
            "logs": [],
            "warnings": warnings,
            "email_configured": email_ready,
        }

    cmd = _make_command(dry_run=dry_run, course_id=course_id, force_send=force_send)
    try:
        cmd._process_faculty(faculty)
    except (ClassroomAPIError, CalendarAPIError, DatabaseError, ValueError, RuntimeError) as exc:  # pragma: no cover - defensive
        cmd.stderr.write(f"Error: {exc}\n")

    logs = cmd.stdout.getvalue().splitlines()
    errors = cmd.stderr.getvalue().splitlines()
    sent_count = sum(1 for line in logs if "Email sent to" in line)
    skipped_count = sum(1 for line in logs if line.startswith("Skipping"))
    email_skipped = any("Email not configured; skipped email" in line for line in logs)
    return {
        "ok": len(errors) == 0,
        "skipped": False,
        "logs": logs,
        "errors": errors,
        "warnings": warnings,
        "email_configured": email_ready,
        "sent": sent_count,
        "skipped_count": skipped_count,
        "email_skipped": email_skipped,
    }


def run_alerts_for_all_faculty(dry_run: bool = False, course_id: Optional[str] = None) -> dict:
    """Run alerts for every faculty, honoring per-faculty enable preference."""

    results = []
    for faculty in FacultyUser.objects.all():
        results.append({
            "faculty": faculty.email,
            **run_alerts_for_faculty(faculty, course_id=course_id, dry_run=dry_run),
        })
    return {
        "ok": all(r.get("ok") or r.get("skipped") for r in results),
        "results": results,
    }
