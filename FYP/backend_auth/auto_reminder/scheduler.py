"""Lightweight periodic scheduler for auto reminders.

Opt-in via environment variable:
    AUTO_REMINDER_SCHEDULE_MINUTES (default: 0 -> disabled)

Runs in-process with a daemon timer to avoid adding Celery/cron dependencies.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from django.conf import settings
from django.db import DatabaseError

from .runner import run_alerts_for_all_faculty

logger = logging.getLogger(__name__)


def _should_run(interval_minutes: int) -> bool:
    if interval_minutes <= 0:
        return False
    # Avoid double-start under Django autoreload
    if os.environ.get("RUN_MAIN") != "true" and settings.DEBUG:
        return False
    return True


def start_scheduler(interval_minutes: Optional[int] = None):
    """Start a background timer that runs alerts for all faculty."""

    interval = interval_minutes if interval_minutes is not None else int(os.getenv("AUTO_REMINDER_SCHEDULE_MINUTES", "0") or 0)
    if not _should_run(interval):
        return

    # Use a function attribute to ensure singleton behavior
    if getattr(start_scheduler, "_started", False):
        return
    start_scheduler._started = True  # type: ignore[attr-defined]

    def _loop():
        try:
            run_alerts_for_all_faculty()
        except (RuntimeError, ValueError, OSError, DatabaseError) as exc:  # pragma: no cover - defensive logging only
            logger.exception("Auto reminder scheduler run failed: %s", exc)
        finally:
            timer = threading.Timer(interval * 60, _loop)
            timer.daemon = True
            timer.start()

    logger.info("Starting auto reminder scheduler (every %s minutes)", interval)
    kickoff = threading.Thread(target=_loop, daemon=True)
    kickoff.start()
