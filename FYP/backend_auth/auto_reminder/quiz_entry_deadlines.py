"""Fixed quiz marks entry deadlines (faculty submissions).

Dates are naive ``date`` objects and consumed by the assignment alert command
(one day before each deadline) for Classroom announcements and UI display.
"""

from datetime import date

QUIZ_ENTRY_DEADLINES = [
    {"quiz_number": 1, "deadline": date(2025, 10, 3)},
    {"quiz_number": 2, "deadline": date(2025, 10, 24)},
    {"quiz_number": 3, "deadline": date(2025, 12, 5)},
    {"quiz_number": 4, "deadline": date(2025, 12, 26)},
]
