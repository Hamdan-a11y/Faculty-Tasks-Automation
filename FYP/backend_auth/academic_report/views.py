"""Views for the Academic Report Generator module."""

import io
import json
import statistics
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt

from .models import AcademicReportRun, AcademicReportStudent

SESSION_KEY = "faculty_user_id"

# ── Hardcoded CLO → Assessment Mapping ──────────────────────────────────
CLO_MAPPING = {
    "CLO1": ["quiz1", "assign1", "mid_q1", "final_q1"],
    "CLO2": ["quiz2", "assign2", "mid_q2", "final_q2"],
    "CLO3": ["quiz3", "assign3", "mid_q3", "final_q3"],
    "CLO4": ["quiz4", "assign4", "mid_q4", "final_q4", "final_viva"],
}

# Display labels for CLOs
CLO_DISPLAY = {
    "CLO1": "CLO1", "CLO2": "CLO2", "CLO3": "CLO3", "CLO4": "CLO4",
}

# All assessment keys used in the model
ASSESSMENT_KEYS = [
    "quiz1", "quiz2", "quiz3", "quiz4",
    "assign1", "assign2", "assign3", "assign4",
    "mid_q1", "mid_q2", "mid_q3", "mid_q4",
    "final_q1", "final_q2", "final_q3", "final_q4",
    "final_viva",
]

# Assessment display labels
ASSESSMENT_LABELS = {
    "quiz1": "Quiz 1", "quiz2": "Quiz 2", "quiz3": "Quiz 3", "quiz4": "Quiz 4",
    "assign1": "Assignment 1", "assign2": "Assignment 2", "assign3": "Assignment 3", "assign4": "Assignment 4",
    "mid_q1": "Mid Term (Q1)", "mid_q2": "Mid Term (Q2)", "mid_q3": "Mid Term (Q3)", "mid_q4": "Mid Term (Q4)",
    "final_q1": "Final Term (Q1)", "final_q2": "Final Term (Q2)", "final_q3": "Final Term (Q3)", "final_q4": "Final Term (Q4)",
    "final_viva": "Final Viva",
}

# ── Grade letter → GPA mapping ──────────────────────────────────────────
GRADE_GPA = {
    "A+": 4.0, "A": 4.0, "A-": 3.7,
    "B+": 3.3, "B": 3.0, "B-": 2.7,
    "C+": 2.3, "C": 2.0, "C-": 1.7,
    "D+": 1.3, "D": 1.0, "D-": 0.7,
    "F": 0.0, "": 0.0,
}

# All valid grade letters (ordered for display)
GRADE_LETTERS = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "F"]

# ── Achievement level thresholds ────────────────────────────────────────
def get_achievement_level(epan: float) -> str:
    if epan >= 3.0:
        return "Expert"
    elif epan >= 2.0:
        return "Practitioner"
    else:
        return "Apprentice"


# ── Parse a grade letter to GPA value ───────────────────────────────────
def parse_grade_gpa(val):
    """Convert a cell value (grade letter like 'A+', 'B', etc.) to its GPA value."""
    if val is None:
        return 0.0
    s = str(val).strip().upper()
    # Normalize common variations
    s = s.replace(" ", "").replace("-","-").replace("+","+")
    return GRADE_GPA.get(s, 0.0)


# ── EEMU calculation: group grade counts into 4-level OBE taxonomy ──────
def compute_eemu_vector(grade_counts):
    """
    Compute OBE_EEMU vector from grade letter counts.
    Grouping:
      - Excellent (E): A+, A
      - Effective (E): A-, B+, B
      - Minimal (M): B-, C+, C
      - Unsatisfactory (U): C-, F
    Returns: [excellent, effective, minimal, unsatisfactory]
    """
    excellent = grade_counts.get("A+", 0) + grade_counts.get("A", 0)
    effective = grade_counts.get("A-", 0) + grade_counts.get("B+", 0) + grade_counts.get("B", 0)
    minimal = grade_counts.get("B-", 0) + grade_counts.get("C+", 0) + grade_counts.get("C", 0)
    unsatisfactory = grade_counts.get("C-", 0) + grade_counts.get("F", 0)
    return [excellent, effective, minimal, unsatisfactory]


# ── Compute average GPA from grade counts ───────────────────────────────
def compute_gpa_average(grade_counts):
    """Calculate weighted average GPA from grade distribution counts."""
    total_weight = 0.0
    total_count = 0
    for grade, count in grade_counts.items():
        gpa = GRADE_GPA.get(grade, 0.0)
        total_weight += gpa * count
        total_count += count
    return round(total_weight / total_count, 2) if total_count > 0 else 0.0


# ── Program Outcomes list (a-k) ─────────────────────────────────────────
PROGRAM_OUTCOMES = [
    ("a", "An ability to select and apply the knowledge, techniques, skills, and modern tools of the discipline to broadly-defined software engineering technology activities."),
    ("b", "An ability to select and apply knowledge of mathematics, computing and software engineering technology problems that require the application of principles and applied procedures or methodologies."),
    ("c", "An ability to apply mathematical foundations, algorithmic principles, and computer science theory in the modeling and design of software systems in a way that demonstrates comprehension of the tradeoffs involved in design choices."),
    ("d", "An ability to conduct standard tests and measurements; to conduct, analyze, and interpret experiments; and to apply experimental results to improve software engineering processes."),
    ("e", "An ability to design systems, components, or processes for broadly-defined software engineering technology problems appropriate to program learning objectives."),
    ("f", "An understanding of professional, ethical, legal, security and social issues and responsibilities."),
    ("g", "An ability to identify, analyze, and solve broadly-defined software engineering technology problems."),
    ("h", "An ability to apply written, oral, and graphical communication in both technical and non-technical environments; and an ability to identify and use appropriate technical literature."),
    ("i", "An understanding of the need for and an ability to engage in self-directed continuing professional development."),
    ("j", "A knowledge of the impact of software engineering technology solutions in a societal and global context; and a commitment to quality, timeliness, and continuous improvement."),
    ("k", "An ability to apply design and development principles in the construction of software systems of varying complexity."),
]

# Default CLO → PO mapping (X marks in columns c, d, e for all CLOs)
DEFAULT_PO_MAPPING = {
    "CLO1": {"description": "Explain the importance of information within an organization and how ICTs help to manage information.", "marks": ["c", "d", "e"]},
    "CLO2": {"description": "Describe the uses, deployment of computer networks, identify their relevance to the respective environment.", "marks": ["d", "e"]},
    "CLO3": {"description": "Use of tools and technologies such as MS Office Suite for writing, storing record, making powerful presentations and its use for communication purpose.", "marks": ["c", "d", "e"]},
}


# ── Derive program info from sections / course code ────────────────────
def derive_program_info(sections, course_code):
    """
    Extract program abbreviation and full name from user-provided fields.
    e.g. "BS(SE)-1A" → ("SE", "Software Engineering"), "BSCS-1A" → ("CS", "Computer Science")
    """
    import re

    PROGRAM_NAMES = {
        "CS": "Computer Science",
        "SE": "Software Engineering",
        "IT": "Information Technology",
        "DS": "Data Science",
        "AI": "Artificial Intelligence",
        "CE": "Computer Engineering",
        "EE": "Electrical Engineering",
        "ME": "Mechanical Engineering",
    }

    program_abbr = ""

    # 1. Try to extract abbreviation from parentheses in sections: BS(XX) → XX
    if sections:
        paren_match = re.search(r'\(([^)]+)\)', sections)
        if paren_match:
            program_abbr = paren_match.group(1).strip().upper()

    # 2. Try to find BSXX pattern: "BSCS", "BSIT", "BSSE", "BSCS 1a"
    if not program_abbr and sections:
        code_match = re.search(r'BS\s*([A-Za-z]{2,})', sections.upper())
        if code_match:
            program_abbr = code_match.group(1).strip()

    # 3. Fall back to course code prefix: "CSC 1108" → CS
    if not program_abbr and course_code:
        code_match = re.search(r'^([A-Za-z]{2,})', course_code.upper())
        if code_match:
            prefix = code_match.group(1)
            # Take last 2 chars if longer than 3 (e.g., "CSC" → "CS")
            if len(prefix) >= 3 and prefix in ("CSC", "CSS", "CSE"):
                program_abbr = prefix[:2]
            elif len(prefix) <= 3:
                program_abbr = prefix

    # Map to full name, fall back to abbreviation itself
    program_full = PROGRAM_NAMES.get(program_abbr, program_abbr or "Program")

    # If still empty, default
    if not program_abbr:
        program_abbr = "Program"
        program_full = "Program"

    return program_abbr, program_full


# ── GPA to grade letter conversion ─────────────────────────────────────
def _gpa_to_grade(gpa_val):
    """Convert GPA value back to grade letter."""
    if gpa_val >= 4.0:
        return "A+"
    elif gpa_val >= 3.7:
        return "A"
    elif gpa_val >= 3.3:
        return "A-"
    elif gpa_val >= 3.0:
        return "B+"
    elif gpa_val >= 2.7:
        return "B"
    elif gpa_val >= 2.3:
        return "B-"
    elif gpa_val >= 2.0:
        return "C+"
    elif gpa_val >= 1.7:
        return "C"
    elif gpa_val > 0.0:
        return "C-"
    else:
        return "F"


# =========================================================================
# PAGE VIEWS
# =========================================================================

def index(request):
    """Render the Academic Report Generator upload page."""
    user_name = None
    user_id = request.session.get(SESSION_KEY)
    if user_id:
        from auth_app.models import FacultyUser
        try:
            user = FacultyUser.objects.get(id=user_id)
            user_name = user.name or user.email
        except FacultyUser.DoesNotExist:
            pass
    return render(request, "academic_report/index.html", {"user_name": user_name})


# =========================================================================
# API ENDPOINTS
# =========================================================================

@csrf_exempt
def upload_excel(request):
    """Parse uploaded Excel, extract assessment columns, calculate CLO/EPAN, save to DB."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only POST method is allowed"}, status=405)

    excel_file = request.FILES.get("file")
    if not excel_file:
        return JsonResponse({"success": False, "error": "No file uploaded"}, status=400)

    # Validate file extension
    allowed_extensions = (".xlsx", ".xlsm", ".xlsb", ".xltx", ".xltm", ".xls")
    if not excel_file.name.lower().endswith(allowed_extensions):
        return JsonResponse({
            "success": False,
            "error": "Invalid file format. Please upload an Excel file (.xlsx, .xlsm, .xlsb, .xltx, .xltm, .xls)"
        }, status=400)

    # Optional form fields
    course_code = (request.POST.get("course_code") or "").strip()
    course_name = (request.POST.get("course_name") or "").strip()
    course_instructor = (request.POST.get("course_instructor") or "").strip()
    semester = (request.POST.get("semester") or "").strip()
    sections = (request.POST.get("sections") or "").strip()
    total_credits = (request.POST.get("total_credits") or "").strip()
    academic_term = (request.POST.get("academic_term") or "").strip()
    catalog_description = (request.POST.get("catalog_description") or "").strip()

    try:
        wb = openpyxl.load_workbook(excel_file, data_only=True)

        # ── Candidate patterns for each required column ──
        REQUIRED_COLUMNS = {
            "name": [
                "student_name", "student name", "name", "student", "full name",
                "reg no", "reg_no", "regno", "registration", "reg #", "reg#",
                "roll no", "roll_no", "rollno", "roll number", "roll #", "roll#",
                "id", "student id", "student_id", "sno", "sr no", "sr_no", "sr.",
            ],
            "quiz1": ["quiz1", "quiz 1", "quiz-1", "q1", "quiz_1", "quiz i", "quiz one", "quiz no 1", "quiz #1"],
            "quiz2": ["quiz2", "quiz 2", "quiz-2", "q2", "quiz_2", "quiz ii", "quiz two", "quiz no 2", "quiz #2"],
            "quiz3": ["quiz3", "quiz 3", "quiz-3", "q3", "quiz_3", "quiz iii", "quiz three", "quiz no 3", "quiz #3"],
            "quiz4": ["quiz4", "quiz 4", "quiz-4", "q4", "quiz_4", "quiz iv", "quiz four", "quiz no 4", "quiz #4"],
            "assign1": ["assign1", "assign 1", "assign-1", "assignment1", "assignment 1", "assignment-1",
                        "a1", "assign_1", "asgn1", "asgn 1", "ass1", "ass 1", "asst 1", "asst1"],
            "assign2": ["assign2", "assign 2", "assign-2", "assignment2", "assignment 2", "assignment-2",
                        "a2", "assign_2", "asgn2", "asgn 2", "ass2", "ass 2", "asst 2", "asst2"],
            "assign3": ["assign3", "assign 3", "assign-3", "assignment3", "assignment 3", "assignment-3",
                        "a3", "assign_3", "asgn3", "asgn 3", "ass3", "ass 3", "asst 3", "asst3"],
            "assign4": ["assign4", "assign 4", "assign-4", "assignment4", "assignment 4", "assignment-4",
                        "a4", "assign_4", "asgn4", "asgn 4", "ass4", "ass 4", "asst 4", "asst4"],
            "mid_q1": ["mid q1", "mid term q1", "midterm q1", "mid-q1", "mid_q1", "mid 1", "mids q1",
                       "midterm question 1", "mid question 1", "mt q1", "m_q1", "midterm1"],
            "mid_q2": ["mid q2", "mid term q2", "midterm q2", "mid-q2", "mid_q2", "mid 2", "mids q2",
                       "midterm question 2", "mid question 2", "mt q2", "m_q2", "midterm2"],
            "mid_q3": ["mid q3", "mid term q3", "midterm q3", "mid-q3", "mid_q3", "mid 3", "mids q3",
                       "midterm question 3", "mid question 3", "mt q3", "m_q3", "midterm3"],
            "mid_q4": ["mid q4", "mid term q4", "midterm q4", "mid-q4", "mid_q4", "mid 4", "mids q4",
                       "midterm question 4", "mid question 4", "mt q4", "m_q4", "midterm4"],
            "final_q1": ["final q1", "final term q1", "finalterm q1", "final-q1", "final_q1", "fin q1",
                         "final question 1", "ft q1", "f_q1", "final1"],
            "final_q2": ["final q2", "final term q2", "finalterm q2", "final-q2", "final_q2", "fin q2",
                         "final question 2", "ft q2", "f_q2", "final2"],
            "final_q3": ["final q3", "final term q3", "finalterm q3", "final-q3", "final_q3", "fin q3",
                         "final question 3", "ft q3", "f_q3", "final3"],
            "final_q4": ["final q4", "final term q4", "finalterm q4", "final-q4", "final_q4", "fin q4",
                         "final question 4", "ft q4", "f_q4", "final4"],
            "final_viva": ["viva", "final viva", "fv", "viva voce", "final_viva", "oral", "final oral",
                           "viva marks", "oral exam", "final presentation"],
        }

        # ── Helper: normalize a cell value for header matching ──
        def norm(cell):
            if cell is None:
                return ""
            s = str(cell).strip().lower()
            for ch in "*#.:()[]{}@$%^&_|":
                s = s.replace(ch, " ")
            return " ".join(s.split())

        # ── Helper: find column index for a key in a given header row ──
        def find_col_in_headers(headers, key):
            candidates = REQUIRED_COLUMNS[key]
            for idx, h in enumerate(headers):
                nh = norm(h)
                if not nh:
                    continue
                for cand in candidates:
                    if nh == cand or nh.startswith(cand) or cand in nh:
                        return idx
            return None

        # ── Score a header row by how many required columns it matches ──
        def score_header_row(headers):
            score = 0
            indices = {}
            all_keys = list(REQUIRED_COLUMNS.keys())
            for key in all_keys:
                idx = find_col_in_headers(headers, key)
                if idx is not None:
                    score += 1
                    indices[key] = idx
            return score, indices

        # ── Try each sheet to find the best header row ──
        best_score = 0
        best_indices = {}
        best_sheet = None
        best_header_row_idx = 0
        best_rows = None
        best_sheet_name = ""

        for sheet in wb.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            max_scan = min(25, len(rows))
            for row_idx in range(max_scan):
                headers = [cell for cell in rows[row_idx]]
                score, indices = score_header_row(headers)
                if score > best_score:
                    best_score = score
                    best_indices = indices
                    best_sheet = sheet
                    best_header_row_idx = row_idx
                    best_rows = rows
                    best_sheet_name = sheet.title

        if best_score < 3 or best_sheet is None:
            sample_headers = []
            if wb.active:
                first_rows = list(wb.active.iter_rows(values_only=True))
                if first_rows:
                    sample_headers = [str(c) for c in first_rows[0] if c is not None][:15]
            found_msg = f"Found headers: {', '.join(sample_headers)}" if sample_headers else "No headers detected"
            return JsonResponse({
                "success": False,
                "error": (
                    f"Could not find enough required columns in any sheet. "
                    f"Expected columns like: Student Name, Quiz 1-4, Assignment 1-4, "
                    f"Mid Term Q1-4, Final Term Q1-4. "
                    f"Cells should contain grade letters (A+, A, A-, B+, etc.). "
                    f"{found_msg}. Matched only {best_score} column(s) across {len(wb.worksheets)} sheet(s)."
                )
            }, status=400)

        # ── Build column index map ──
        col_map = {}
        for key in REQUIRED_COLUMNS:
            col_map[key] = best_indices.get(key)

# ── Parse grade letter from cell value ──
        def parse_grade(val):
            """Extract grade letter from cell value, return GPA equivalent."""
            if val is None or str(val).strip() == "":
                return 0.0
            s = str(val).strip().upper()
            # Handle numeric values (convert to grade if needed)
            try:
                num = float(s)
                # Map numeric values to GPA directly (assume 4.0 scale)
                return max(0.0, min(4.0, num))
            except ValueError:
                pass
            # Handle grade letters
            cleaned = s.replace(" ", "")
            return GRADE_GPA.get(cleaned, 0.0)

        # ── Parse student data rows ──
        student_records = []
        name_idx = col_map.get("name")

        for r_idx in range(best_header_row_idx + 1, len(best_rows)):
            row = best_rows[r_idx]
            if not row:
                continue

            # Get student identifier
            if name_idx is None or name_idx >= len(row):
                student_name = None
                for cell in row:
                    if cell is not None and str(cell).strip():
                        student_name = str(cell).strip()
                        break
                if not student_name:
                    continue
            else:
                if row[name_idx] is None or str(row[name_idx]).strip() == "":
                    continue
                student_name = str(row[name_idx]).strip()

            # Skip header/summary rows
            name_lower = student_name.lower().strip()
            skip_words = ["total", "average", "avg", "grade", "head wise", "student name",
                         "name", "roll no", "reg no", "student", "sno", "sr."]
            if any(name_lower == w or name_lower.startswith(w) for w in skip_words):
                continue

            def safe_get(key):
                idx = col_map.get(key)
                if idx is not None and idx < len(row):
                    return parse_grade(row[idx])
                return 0.0

            q1 = safe_get("quiz1")
            q2 = safe_get("quiz2")
            q3 = safe_get("quiz3")
            q4 = safe_get("quiz4")
            a1 = safe_get("assign1")
            a2 = safe_get("assign2")
            a3 = safe_get("assign3")
            a4 = safe_get("assign4")
            mq1 = safe_get("mid_q1")
            mq2 = safe_get("mid_q2")
            mq3 = safe_get("mid_q3")
            mq4 = safe_get("mid_q4")
            fq1 = safe_get("final_q1")
            fq2 = safe_get("final_q2")
            fq3 = safe_get("final_q3")
            fq4 = safe_get("final_q4")
            fv = safe_get("final_viva")

            # Calculate per-student CLO scores using the mapping (GPA average)
            clo1_raw = (q1 + a1 + mq1 + fq1) / 4.0 if (q1 + a1 + mq1 + fq1) > 0 else 0.0
            clo2_raw = (q2 + a2 + mq2 + fq2) / 4.0 if (q2 + a2 + mq2 + fq2) > 0 else 0.0
            clo3_raw = (q3 + a3 + mq3 + fq3) / 4.0 if (q3 + a3 + mq3 + fq3) > 0 else 0.0
            clo4_raw = (q4 + a4 + mq4 + fq4 + fv) / 5.0 if (q4 + a4 + mq4 + fq4 + fv) > 0 else 0.0

            total = q1 + q2 + q3 + q4 + a1 + a2 + a3 + a4 + mq1 + mq2 + mq3 + mq4 + fq1 + fq2 + fq3 + fq4 + fv

            if total == 0.0:
                continue

            student_records.append(AcademicReportStudent(
                student_name=student_name,
                quiz1=q1, quiz2=q2, quiz3=q3, quiz4=q4,
                assign1=a1, assign2=a2, assign3=a3, assign4=a4,
                mid_q1=mq1, mid_q2=mq2, mid_q3=mq3, mid_q4=mq4,
                final_q1=fq1, final_q2=fq2, final_q3=fq3, final_q4=fq4,
                final_viva=fv,
                clo1_score=round(clo1_raw, 2),
                clo2_score=round(clo2_raw, 2),
                clo3_score=round(clo3_raw, 2),
                clo4_score=round(clo4_raw, 2),
                total_score=round(total, 2),
            ))

        # ── Per-assessment grade distribution from GPA values ──
        # Convert GPA values back to grade letters for distribution counting
        def gpa_to_grade(gpa_val):
            """Convert GPA value back to grade letter."""
            if gpa_val >= 4.0:
                return "A+"
            elif gpa_val >= 3.7:
                return "A"
            elif gpa_val >= 3.3:
                return "A-"
            elif gpa_val >= 3.0:
                return "B+"
            elif gpa_val >= 2.7:
                return "B"
            elif gpa_val >= 2.3:
                return "B-"
            elif gpa_val >= 2.0:
                return "C+"
            elif gpa_val >= 1.7:
                return "C"
            elif gpa_val > 0.0:
                return "C-"
            else:
                return "F"

        # Compute per-assessment grade distribution counts
        assessment_grade_dist = {}
        for key in ASSESSMENT_KEYS:
            grade_counts = {g: 0 for g in GRADE_LETTERS}
            for s in student_records:
                gpa_val = getattr(s, key, 0.0)
                grade = gpa_to_grade(gpa_val)
                grade_counts[grade] += 1
            assessment_grade_dist[key] = grade_counts

        # Compute overall grade distribution (from total score GPA)
        overall_grade_dist = {g: 0 for g in GRADE_LETTERS}
        for s in student_records:
            # Average GPA across all assessments
            values = [getattr(s, k, 0.0) for k in ASSESSMENT_KEYS]
            non_zero = [v for v in values if v > 0]
            avg_gpa = sum(non_zero) / len(non_zero) if non_zero else 0.0
            grade = gpa_to_grade(avg_gpa)
            overall_grade_dist[grade] += 1

        # Remove zero-count grades for cleaner display
        overall_grade_dist = {k: v for k, v in overall_grade_dist.items() if v > 0}

        # Assign grade letters to students
        for s in student_records:
            values = [getattr(s, k, 0.0) for k in ASSESSMENT_KEYS]
            non_zero = [v for v in values if v > 0]
            avg_gpa = sum(non_zero) / len(non_zero) if non_zero else 0.0
            s.grade_letter = gpa_to_grade(avg_gpa)

        # ── Calculate CLO averages from student CLO scores ──
        clo_averages = {}
        for clo_name in ["CLO1", "CLO2", "CLO3", "CLO4"]:
            field = f"clo{clo_name[-1]}_score"
            values = [getattr(s, field, 0.0) for s in student_records if getattr(s, field, 0.0) > 0]
            clo_averages[clo_name] = round(sum(values) / len(values), 2) if values else 0.0

        # ── Calculate EPAN ──
        epan_avg = round(
            (clo_averages["CLO1"] + clo_averages["CLO2"] + clo_averages["CLO3"] + clo_averages["CLO4"]) / 4.0,
            2
        )

        # ── Achievement level ──
        achievement_level = get_achievement_level(epan_avg)

        # ── Build CLO detail JSON for report ──
        clo_detail = {}
        for clo_name, mapped_keys in CLO_MAPPING.items():
            clo_assessments = []
            for key in mapped_keys:
                grade_counts = assessment_grade_dist.get(key, {})
                eemu = compute_eemu_vector(grade_counts)
                avg_gpa = compute_gpa_average(grade_counts)
                clo_assessments.append({
                    "key": key,
                    "label": ASSESSMENT_LABELS.get(key, key),
                    "grade_counts": grade_counts,
                    "eemu": eemu,
                    "average": avg_gpa,
                })
            # Overall CLO average from assessments
            clo_avg = clo_averages.get(clo_name, 0.0)
            clo_detail[clo_name] = {
                "assessments": clo_assessments,
                "average": clo_avg,
            }

        # ── Assessment averages (GPA) ──
        assessment_averages = {}
        for key in ASSESSMENT_KEYS:
            grade_counts = assessment_grade_dist.get(key, {})
            assessment_averages[key] = compute_gpa_average(grade_counts)

        # ── Save to database ──
        num_students = len(student_records)
        try:
            with transaction.atomic():
                run = AcademicReportRun.objects.create(
                    course_code=course_code,
                    course_name=course_name,
                    course_instructor=course_instructor,
                    semester=semester,
                    sections=sections,
                    total_credits=total_credits,
                    academic_term=academic_term,
                    catalog_description=catalog_description,
                    source_file_name=excel_file.name,
                    total_students=num_students,
                    clo1_avg=clo_averages["CLO1"],
                    clo2_avg=clo_averages["CLO2"],
                    clo3_avg=clo_averages["CLO3"],
                    clo4_avg=clo_averages["CLO4"],
                    epan_avg=epan_avg,
                    achievement_level=achievement_level,
                    grade_distribution=overall_grade_dist,
                    assessment_averages=assessment_averages,
                    status="completed",
                )
                for s in student_records:
                    s.run = run
                AcademicReportStudent.objects.bulk_create(student_records)
        except Exception as exc:
            return JsonResponse({
                "success": False,
                "error": f"Database error while saving report: {str(exc)}"
            }, status=500)

        # ── Build response ──
        return JsonResponse({
            "success": True,
            "run_id": run.id,
            "message": f"Successfully parsed {num_students} student records from sheet '{best_sheet_name}'.",
            "summary": {
                "total_students": num_students,
                "clo_averages": clo_averages,
                "epan_avg": epan_avg,
                "achievement_level": achievement_level,
                "grade_distribution": overall_grade_dist,
            }
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": f"Failed to parse Excel file: {str(e)}"
        }, status=500)


def report(request, run_id):
    """Render the academic report preview page."""
    user_name = None
    user_id = request.session.get(SESSION_KEY)
    if user_id:
        from auth_app.models import FacultyUser
        try:
            user = FacultyUser.objects.get(id=user_id)
            user_name = user.name or user.email
        except FacultyUser.DoesNotExist:
            pass

    run = get_object_or_404(AcademicReportRun, id=run_id)
    students = run.students.all()

    # ── Derive program info from sections / course code ──
    program_abbr, program_full = derive_program_info(run.sections, run.course_code)

    # ── Build CLO detail from assessment averages and mapping ──
    clo_detail = {}
    for clo_name, mapped_keys in CLO_MAPPING.items():
        clo_assessments = []
        for key in mapped_keys:
            avg_val = run.assessment_averages.get(key, 0.0)
            # Build grade distribution for this assessment from students
            grade_counts = {g: 0 for g in GRADE_LETTERS}
            for s in students:
                gpa_val = getattr(s, key, 0.0)
                if gpa_val > 0:
                    grade = _gpa_to_grade(gpa_val)
                    grade_counts[grade] += 1
            eemu = compute_eemu_vector(grade_counts)
            clo_assessments.append({
                "key": key,
                "label": ASSESSMENT_LABELS.get(key, key),
                "grade_counts": grade_counts,
                "eemu": eemu,
                "average": avg_val,
            })
        clo_avg = getattr(run, f"clo{clo_name[-1]}_avg", 0.0)
        clo_detail[clo_name] = {
            "assessments": clo_assessments,
            "average": clo_avg,
        }

    context = {
        "user_name": user_name,
        "run": run,
        "students": students,
        "program_abbr": program_abbr,
        "program_full": program_full,
        "clo_mapping": CLO_MAPPING,
        "clo_labels": ["CLO1", "CLO2", "CLO3", "CLO4"],
        "clo_averages": [run.clo1_avg, run.clo2_avg, run.clo3_avg, run.clo4_avg],
        "clo_detail": clo_detail,
        "assessment_keys": ASSESSMENT_KEYS,
        "assessment_labels": ASSESSMENT_LABELS,
        "grade_letters": GRADE_LETTERS,
        "program_outcomes": PROGRAM_OUTCOMES,
        "po_mapping": DEFAULT_PO_MAPPING,
        "grade_distribution_labels": json.dumps(list(run.grade_distribution.keys())),
        "grade_distribution_values": json.dumps(list(run.grade_distribution.values())),
    }
    return render(request, "academic_report/report.html", context)


def download_pdf(request, run_id):
    """Generate a downloadable HTML report (print-friendly for PDF conversion)."""
    run = get_object_or_404(AcademicReportRun, id=run_id)
    students = run.students.all()

    # Derive program info
    program_abbr, program_full = derive_program_info(run.sections, run.course_code)

    # Build CLO detail
    clo_detail = {}
    for clo_name, mapped_keys in CLO_MAPPING.items():
        clo_assessments = []
        for key in mapped_keys:
            avg_val = run.assessment_averages.get(key, 0.0)
            grade_counts = {g: 0 for g in GRADE_LETTERS}
            for s in students:
                gpa_val = getattr(s, key, 0.0)
                if gpa_val > 0:
                    grade = _gpa_to_grade(gpa_val)
                    grade_counts[grade] += 1
            eemu = compute_eemu_vector(grade_counts)
            clo_assessments.append({
                "key": key,
                "label": ASSESSMENT_LABELS.get(key, key),
                "grade_counts": grade_counts,
                "eemu": eemu,
                "average": avg_val,
            })
        clo_avg = getattr(run, f"clo{clo_name[-1]}_avg", 0.0)
        clo_detail[clo_name] = {
            "assessments": clo_assessments,
            "average": clo_avg,
        }

    html_content = render_to_string("academic_report/report_pdf.html", {
        "run": run,
        "students": students,
        "program_abbr": program_abbr,
        "program_full": program_full,
        "clo_mapping": CLO_MAPPING,
        "clo_labels": ["CLO1", "CLO2", "CLO3", "CLO4"],
        "clo_averages": [run.clo1_avg, run.clo2_avg, run.clo3_avg, run.clo4_avg],
        "clo_detail": clo_detail,
        "assessment_keys": ASSESSMENT_KEYS,
        "assessment_labels": ASSESSMENT_LABELS,
        "grade_letters": GRADE_LETTERS,
        "program_outcomes": PROGRAM_OUTCOMES,
        "po_mapping": DEFAULT_PO_MAPPING,
    })

    response = HttpResponse(html_content, content_type="text/html; charset=utf-8")
    filename = f"academic_report_{run.course_code or 'report'}_{run.id}.html"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def download_excel(request, run_id):
    """Generate a downloadable Excel (.xlsx) report matching the image format."""
    run = get_object_or_404(AcademicReportRun, id=run_id)
    students = run.students.all()

    # Derive program info
    program_abbr, program_full = derive_program_info(run.sections, run.course_code)

    wb = openpyxl.Workbook()

    # ── Styles ──
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="0D3B57", end_color="0D3B57", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    label_font = Font(name="Calibri", size=11, bold=True, color="1F2D3D")
    value_font = Font(name="Calibri", size=11, color="1F2D3D")
    section_font = Font(name="Calibri", size=13, bold=True, color="0D3B57")
    section_fill = PatternFill(start_color="E8F3FB", end_color="E8F3FB", fill_type="solid")
    title_font = Font(name="Calibri", size=16, bold=True, color="0D3B57")
    title_fill = PatternFill(start_color="F0F4F8", end_color="F0F4F8", fill_type="solid")

    def style_header_row(ws, row, col_count):
        for col in range(1, col_count + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border

    def style_data_cell(ws, row, col):
        cell = ws.cell(row=row, column=col)
        cell.font = value_font
        cell.border = thin_border
        cell.alignment = Alignment(vertical="center")

    def style_section_row(ws, row, col_count, text):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=col_count)
        cell = ws.cell(row=row, column=1, value=text)
        cell.font = section_font
        cell.fill = section_fill
        for c in range(1, col_count + 1):
            ws.cell(row=row, column=c).border = thin_border

    def auto_width(ws, min_width=10, max_width=35):
        for col_cells in ws.columns:
            col_letter = get_column_letter(col_cells[0].column)
            widths = []
            for cell in col_cells:
                if cell.value:
                    for line in str(cell.value).split('\n'):
                        widths.append(len(line))
            ws.column_dimensions[col_letter].width = max(min_width, min(max(widths) + 3 if widths else min_width, max_width))

    # ── Build CLO detail data ──
    clo_detail = {}
    for clo_name, mapped_keys in CLO_MAPPING.items():
        clo_assessments = []
        for key in mapped_keys:
            avg_val = run.assessment_averages.get(key, 0.0)
            grade_counts = {g: 0 for g in GRADE_LETTERS}
            for s in students:
                gpa_val = getattr(s, key, 0.0)
                if gpa_val > 0:
                    grade = _gpa_to_grade(gpa_val)
                    grade_counts[grade] += 1
            eemu = compute_eemu_vector(grade_counts)
            clo_assessments.append({
                "key": key,
                "label": ASSESSMENT_LABELS.get(key, key),
                "grade_counts": grade_counts,
                "eemu": eemu,
                "average": avg_val,
            })
        clo_avg = getattr(run, f"clo{clo_name[-1]}_avg", 0.0)
        clo_detail[clo_name] = {
            "assessments": clo_assessments,
            "average": clo_avg,
        }

    # =====================================================================
    # Sheet 1: Report
    # =====================================================================
    ws1 = wb.active
    ws1.title = "Academic Report"

    row = 1
    max_cols = 15

    # ── Title ──
    ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max_cols)
    tc = ws1.cell(row=row, column=1, value=f"{run.course_name or 'Course'} {run.sections or ''}")
    tc.font = title_font
    tc.fill = title_fill
    tc.alignment = Alignment(horizontal="center")
    tc.border = thin_border
    for c in range(2, max_cols + 1):
        ws1.cell(row=row, column=c).border = thin_border
        ws1.cell(row=row, column=c).fill = title_fill
    row += 1

    # ── Program Outcomes Table ──
    style_section_row(ws1, row, max_cols, "Program Outcomes")
    row += 1

    # PO header row: CLO | Description | a | b | c | d | e | f | g | h | i | j | k | Metric Goal
    po_cols = ["CLO", "Description"] + [l for l, _ in PROGRAM_OUTCOMES] + ["Metric Goal"]
    for ci, h in enumerate(po_cols, 1):
        ws1.cell(row=row, column=ci, value=h)
    style_header_row(ws1, row, len(po_cols))
    row += 1

    for clo_name in ["CLO1", "CLO2", "CLO3"]:
        po_info = DEFAULT_PO_MAPPING.get(clo_name, {})
        desc = po_info.get("description", "")
        marks = po_info.get("marks", [])
        ws1.cell(row=row, column=1, value=clo_name)
        ws1.cell(row=row, column=2, value=desc)
        col_offset = 3
        for letter, _ in PROGRAM_OUTCOMES:
            ws1.cell(row=row, column=col_offset, value="X" if letter in marks else "")
            col_offset += 1
        for c in range(1, len(po_cols) + 1):
            style_data_cell(ws1, row, c)
        row += 1

    # Metric Goal row
    ws1.cell(row=row, column=1, value="Metric Goal")
    col_offset = 3
    for letter, _ in PROGRAM_OUTCOMES:
        if letter in ["c", "d"]:
            ws1.cell(row=row, column=col_offset, value=2.0)
        col_offset += 1
    for c in range(1, len(po_cols) + 1):
        style_data_cell(ws1, row, c)
    row += 2

    # ── Course Information ──
    style_section_row(ws1, row, max_cols, "Course Information")
    row += 1

    info_fields = [
        ("Course Code", run.course_code or "N/A"),
        ("Sections", run.sections or "N/A"),
        ("Total Credits", run.total_credits or "N/A"),
        ("Professor", run.course_instructor or "N/A"),
        ("Course Title", run.course_name or "N/A"),
        ("Academic Term", run.academic_term or run.semester or "N/A"),
    ]
    for label, value in info_fields:
        c1 = ws1.cell(row=row, column=1, value=label)
        c1.font = label_font
        c1.border = thin_border
        ws1.merge_cells(start_row=row, start_column=2, end_row=row, end_column=max_cols)
        c2 = ws1.cell(row=row, column=2, value=value)
        c2.font = value_font
        c2.border = thin_border
        for c in range(3, max_cols + 1):
            ws1.cell(row=row, column=c).border = thin_border
        row += 1

    # Catalog Description
    if run.catalog_description:
        c1 = ws1.cell(row=row, column=1, value="Catalog Description")
        c1.font = label_font
        c1.border = thin_border
        ws1.merge_cells(start_row=row, start_column=2, end_row=row, end_column=max_cols)
        c2 = ws1.cell(row=row, column=2, value=run.catalog_description)
        c2.font = value_font
        c2.border = thin_border
        c2.alignment = Alignment(wrap_text=True, vertical="top")
        for c in range(3, max_cols + 1):
            ws1.cell(row=row, column=c).border = thin_border
        row += 1
    row += 1

    # ── Grade Distribution ──
    style_section_row(ws1, row, max_cols, "Grade Distribution")
    row += 1

    gd_headers = ["Subject Grades"] + GRADE_LETTERS[:-1] + ["Total"]  # Exclude F
    for ci, h in enumerate(gd_headers, 1):
        ws1.cell(row=row, column=ci, value=h)
    style_header_row(ws1, row, len(gd_headers))
    row += 1

    ws1.cell(row=row, column=1, value=f"Total ({run.total_students})")
    total_non_f = 0
    for gi, grade in enumerate(GRADE_LETTERS[:-1], 2):  # Exclude F
        cnt = run.grade_distribution.get(grade, 0)
        ws1.cell(row=row, column=gi, value=cnt)
        total_non_f += cnt
    ws1.cell(row=row, column=len(gd_headers), value=total_non_f)
    for c in range(1, len(gd_headers) + 1):
        style_data_cell(ws1, row, c)
    row += 1

    ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(gd_headers))
    note = ws1.cell(row=row, column=1, value="*F Grades are excluded for calculating EPAN and EEMM.")
    note.font = Font(name="Calibri", size=9, italic=True, color="64748B")
    row += 2

    # ── Modifications Made ──
    style_section_row(ws1, row, max_cols, "Modifications Made to Course")
    row += 1
    ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max_cols)
    mod_cell = ws1.cell(row=row, column=1, value=run.modifications_made or "None")
    mod_cell.font = value_font
    mod_cell.border = thin_border
    for c in range(2, max_cols + 1):
        ws1.cell(row=row, column=c).border = thin_border
    row += 2

    # ── Course Outcomes with Measurement Data ──
    style_section_row(ws1, row, max_cols, "Course Outcomes | Measurement (EEMU vector), Average")
    row += 1

    for clo_name in ["CLO1", "CLO2", "CLO3"]:
        detail = clo_detail.get(clo_name, {})
        assessments = detail.get("assessments", [])

        # Outcome header
        ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max_cols)
        oc = ws1.cell(row=row, column=1, value=f"Outcome #{clo_name[-1]}")
        oc.font = Font(name="Calibri", size=12, bold=True, color="0D3B57")
        oc.fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        for c in range(1, max_cols + 1):
            ws1.cell(row=row, column=c).border = thin_border
            ws1.cell(row=row, column=c).fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        row += 1

        # Head Wise Grades header
        hwg_headers = ["Assessment"] + GRADE_LETTERS[:-1] + ["OBE_EEMU", "Ave."]  # Exclude F from grades
        for ci, h in enumerate(hwg_headers, 1):
            ws1.cell(row=row, column=ci, value=h)
        style_header_row(ws1, row, len(hwg_headers))
        row += 1

        for assess in assessments:
            ws1.cell(row=row, column=1, value=assess["label"])
            grade_counts = assess["grade_counts"]
            for gi, grade in enumerate(GRADE_LETTERS[:-1], 2):  # Exclude F from display
                ws1.cell(row=row, column=gi, value=grade_counts.get(grade, 0))
            # OBE_EEMU
            eemu = assess["eemu"]
            ws1.cell(row=row, column=len(GRADE_LETTERS) + 1, value=f"{eemu[0]},{eemu[1]},{eemu[2]},{eemu[3]}")
            # Average
            ws1.cell(row=row, column=len(GRADE_LETTERS) + 2, value=assess["average"])
            for c in range(1, len(hwg_headers) + 1):
                style_data_cell(ws1, row, c)
            row += 1

        # CLO Average row
        ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(GRADE_LETTERS))
        avg_label = ws1.cell(row=row, column=1, value="Average:")
        avg_label.font = Font(name="Calibri", size=11, bold=True, color="0D3B57")
        avg_label.alignment = Alignment(horizontal="right")
        avg_val_cell = ws1.cell(row=row, column=len(GRADE_LETTERS) + 2, value=detail["average"])
        avg_val_cell.font = Font(name="Calibri", size=11, bold=True, color="0D3B57")
        for c in range(1, len(hwg_headers) + 1):
            style_data_cell(ws1, row, c)
        row += 2

    # ── Program Outcome Assessment ──
    style_section_row(ws1, row, max_cols, "Program Outcome Assessment")
    row += 1

    epan_text = (
        f"Assessing the student performance using Cohort Longitudinal Analysis, or CLA, "
        f"the corresponding EPAN Average for outcome 1, 2, 3 has "
        f"({run.clo1_avg:.2f}, {run.clo2_avg:.2f}, {run.clo3_avg:.2f}) "
        f"which means the students demonstrated and learnt the outcomes 1, 2, and 3, "
        f"and performed {run.achievement_level.upper()}. "
        f"More efforts are required to achieve a better level. "
        f"I am planning to improve the course based on student's feedback and "
        f"hopefully better level will be achieved in the next semester."
    )
    ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max_cols)
    epan_cell = ws1.cell(row=row, column=1, value=epan_text)
    epan_cell.font = value_font
    epan_cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws1.row_dimensions[row].height = 60
    for c in range(1, max_cols + 1):
        ws1.cell(row=row, column=c).border = thin_border
    row += 2

    # ── BS Program Metric Goals ──
    style_section_row(ws1, row, max_cols, f"BS({program_abbr}) Program (Metric Goal)")
    row += 1

    metric_headers = ["Metric", "c", "d"]
    for ci, h in enumerate(metric_headers, 1):
        ws1.cell(row=row, column=ci, value=h)
    style_header_row(ws1, row, len(metric_headers))
    row += 1

    metric_data = [
        ("EPAN Average", round(run.epan_avg, 2), round(run.epan_avg, 2)),
        ("Apprentice Average", 2.0, 2.0),
        ("Program Goal", 2.0, 2.0),
    ]
    for label, c_val, d_val in metric_data:
        ws1.cell(row=row, column=1, value=label)
        ws1.cell(row=row, column=2, value=c_val)
        ws1.cell(row=row, column=3, value=d_val)
        for c in range(1, 4):
            style_data_cell(ws1, row, c)
        row += 1
    row += 1

    # ── Student Feedback ──
    style_section_row(ws1, row, max_cols, "Student Feedback")
    row += 1
    ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max_cols)
    fb = ws1.cell(row=row, column=1, value=run.student_feedback or "No student feedback recorded.")
    fb.font = value_font
    fb.alignment = Alignment(wrap_text=True, vertical="top")
    ws1.row_dimensions[row].height = 40
    for c in range(1, max_cols + 1):
        ws1.cell(row=row, column=c).border = thin_border
    row += 2

    # ── Reflection ──
    style_section_row(ws1, row, max_cols, "Reflection")
    row += 1
    ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max_cols)
    ref = ws1.cell(row=row, column=1, value=run.reflection or "No reflection recorded.")
    ref.font = value_font
    ref.alignment = Alignment(wrap_text=True, vertical="top")
    ws1.row_dimensions[row].height = 40
    for c in range(1, max_cols + 1):
        ws1.cell(row=row, column=c).border = thin_border
    row += 2

    # ── Proposed Actions ──
    style_section_row(ws1, row, max_cols, "Proposed Actions for Course Improvement")
    row += 1
    ws1.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max_cols)
    prop = ws1.cell(row=row, column=1, value=run.proposed_improvements or "No proposed actions recorded.")
    prop.font = value_font
    prop.alignment = Alignment(wrap_text=True, vertical="top")
    ws1.row_dimensions[row].height = 40
    for c in range(1, max_cols + 1):
        ws1.cell(row=row, column=c).border = thin_border
    row += 2

    # ── Program Outcomes List ──
    style_section_row(ws1, row, max_cols, f"BS({program_full}) Program Outcomes")
    row += 1

    for letter, desc in PROGRAM_OUTCOMES:
        c1 = ws1.cell(row=row, column=1, value=f"{letter}.")
        c1.font = Font(name="Calibri", size=11, bold=True, color="0D3B57")
        c1.border = thin_border
        ws1.merge_cells(start_row=row, start_column=2, end_row=row, end_column=max_cols)
        c2 = ws1.cell(row=row, column=2, value=desc)
        c2.font = value_font
        c2.border = thin_border
        c2.alignment = Alignment(wrap_text=True, vertical="top")
        for c in range(3, max_cols + 1):
            ws1.cell(row=row, column=c).border = thin_border
        ws1.row_dimensions[row].height = 30
        row += 1

    # Auto-width
    auto_width(ws1)
    ws1.column_dimensions['A'].width = 20
    ws1.column_dimensions['B'].width = 50

    # =====================================================================
    # Sheet 2: Student Grade Records
    # =====================================================================
    ws2 = wb.create_sheet("Student Records")
    row = 1

    sr_headers = [
        "Student Name",
        "Quiz 1", "Quiz 2", "Quiz 3", "Quiz 4",
        "Assign 1", "Assign 2", "Assign 3", "Assign 4",
        "Mid Q1", "Mid Q2", "Mid Q3", "Mid Q4",
        "Final Q1", "Final Q2", "Final Q3", "Final Q4",
        "Viva", "CLO1", "CLO2", "CLO3", "CLO4", "Grade",
    ]
    for ci, h in enumerate(sr_headers, 1):
        ws2.cell(row=row, column=ci, value=h)
    style_header_row(ws2, row, len(sr_headers))
    row += 1

    for s in students:
        values = [
            s.student_name,
            _gpa_to_grade(s.quiz1), _gpa_to_grade(s.quiz2), _gpa_to_grade(s.quiz3), _gpa_to_grade(s.quiz4),
            _gpa_to_grade(s.assign1), _gpa_to_grade(s.assign2), _gpa_to_grade(s.assign3), _gpa_to_grade(s.assign4),
            _gpa_to_grade(s.mid_q1), _gpa_to_grade(s.mid_q2), _gpa_to_grade(s.mid_q3), _gpa_to_grade(s.mid_q4),
            _gpa_to_grade(s.final_q1), _gpa_to_grade(s.final_q2), _gpa_to_grade(s.final_q3), _gpa_to_grade(s.final_q4),
            _gpa_to_grade(s.final_viva),
            round(s.clo1_score, 2), round(s.clo2_score, 2), round(s.clo3_score, 2), round(s.clo4_score, 2),
            s.grade_letter,
        ]
        for ci, val in enumerate(values, 1):
            ws2.cell(row=row, column=ci, value=val)
            style_data_cell(ws2, row, ci)
        row += 1

    auto_width(ws2)

    # ── Write to response ──
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = f"academic_report_{run.course_code or 'report'}_{run.id}.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
