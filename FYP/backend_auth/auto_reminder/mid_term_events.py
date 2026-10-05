"""Fixed mid-term related events for faculty reminders.

Alerts fire one day before the event_date (for ranges, use the start date).
"""

from datetime import date

MID_TERM_EVENTS = [
    {
        "key": "course-outline",
        "name": "Course Outline",
        "event_date": date(2025, 9, 12),
    },
    {
        "key": "midterm-dept-submission",
        "name": "Mid Exam Department Submission",
        "event_date": date(2025, 10, 22),
    },
    {
        "key": "midterm-exam-period",
        "name": "Mid Term Exams",
        "event_date": date(2025, 11, 3),  # start date of range 3–9 Nov 2025
    },
    {
        "key": "mid-fyp-presentation-ii",
        "name": "Mid FYP Presentations II",
        "event_date": date(2025, 11, 18),
    },
    {
        "key": "mid-fyp-presentation-i",
        "name": "Mid FYP Presentations I",
        "event_date": date(2025, 11, 20),
    },
    {
        "key": "midterm-marks-entries",
        "name": "Mid Term Entries",
        "event_date": date(2025, 11, 24),
    },
    {
        "key": "final-exam-submission-hod",
        "name": "Final Exam Submission to HoD",
        "event_date": date(2025, 12, 12),
    },
    {
        "key": "final-exam",
        "name": "Final Exam",
        "event_date": date(2026, 1, 5),  # start date of range 5–18 Jan 2026
    },
    {
        "key": "final-exam-result-submission",
        "name": "Final Exam Result submission to Department",
        "event_date": date(2026, 1, 20),
    },
    {
        "key": "final-exam-entries-recap",
        "name": "Final Exam Entries & Recap Sheet Sign.",
        "event_date": date(2026, 1, 26),
    },
    {
        "key": "fcar-course-folders",
        "name": "FCAR and Course Folders",
        "event_date": date(2026, 2, 6),
    },
]
