"""Fixed assignment entry deadlines (faculty marks submission).

Dates are stored as naive ``date`` objects in the academic calendar and used
by the assignment alert command to post reminders one day before each
deadline.
"""

from datetime import date

ENTRY_DEADLINES = [
    # Week 4
    {"assignment_number": 1, "deadline": date(2025, 10, 10)},
    # Week 7
    {"assignment_number": 2, "deadline": date(2025, 10, 31)},
    # Week 12
    {"assignment_number": 3, "deadline": date(2025, 12, 12)},
    # Week 15
    {"assignment_number": 4, "deadline": date(2026, 1, 2)},
]
