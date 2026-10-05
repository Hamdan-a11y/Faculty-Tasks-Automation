import io
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from .models import StudentRecord


def index(request):
    user_name = None
    if request.user.is_authenticated:
        user_name = request.user.get_full_name() or request.user.username
    return render(request, "final_academic_insights/index.html", {"user_name": user_name})


@csrf_exempt
def upload_excel(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Only POST method is allowed"}, status=405)

    excel_file = request.FILES.get("file")
    if not excel_file:
        return JsonResponse({"success": False, "error": "No file uploaded"}, status=400)

    allowed_extensions = (".xlsx", ".xlsm", ".xlsb", ".xltx", ".xltm", ".xls")
    if not excel_file.name.lower().endswith(allowed_extensions):
        return JsonResponse({
            "success": False,
            "error": "Invalid file format. Please upload an Excel file (.xlsx, .xlsm, .xlsb, .xltx, .xltm, .xls)"
        }, status=400)

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
            "midterm": ["midterm", "mid term", "mid-term", "mid", "midterm marks", "mid marks",
                       "mids", "mid exam", "midterm exam", "mt", "mid_total", "mid total"],
            "final": ["final", "final term", "final-term", "final marks", "final exam",
                     "fin", "finals", "ft", "final_total", "final total"],
            "total": ["total", "total marks", "grand total", "sum", "overall",
                     "total_score", "total score", "aggregate", "obtained", "marks obtained"],
        }

        # ── Helper: normalize a cell value for header matching ──
        def norm(cell):
            if cell is None:
                return ""
            s = str(cell).strip().lower()
            # Remove common noise characters
            for ch in "*#.:()[]{}@$%^&_|":
                s = s.replace(ch, " ")
            # Collapse multiple spaces
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
            for key in ["name", "quiz1", "quiz2", "quiz3", "quiz4",
                       "assign1", "assign2", "assign3", "assign4", "midterm", "final"]:
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
            # Scan first 25 rows for header candidates
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

        if best_score == 0 or best_sheet is None:
            # Give user a helpful error showing what was found
            sample_headers = []
            if wb.active:
                first_rows = list(wb.active.iter_rows(values_only=True))
                if first_rows:
                    sample_headers = [str(c) for c in first_rows[0] if c is not None][:15]
            found_msg = f"Found headers: {', '.join(sample_headers)}" if sample_headers else "No headers detected"
            return JsonResponse({
                "success": False,
                "error": (
                    f"Could not find required columns in any sheet. "
                    f"Expected columns like: Student Name, Quiz 1-4, Assignment 1-4, Midterm, Final. "
                    f"{found_msg}. "
                    f"Tried {len(wb.worksheets)} sheet(s)."
                )
            }, status=400)

        # ── Extract column indices ──
        name_idx = best_indices.get("name")
        q1_idx = best_indices.get("quiz1")
        q2_idx = best_indices.get("quiz2")
        q3_idx = best_indices.get("quiz3")
        q4_idx = best_indices.get("quiz4")
        a1_idx = best_indices.get("assign1")
        a2_idx = best_indices.get("assign2")
        a3_idx = best_indices.get("assign3")
        a4_idx = best_indices.get("assign4")
        mid_idx = best_indices.get("midterm")
        fin_idx = best_indices.get("final")
        tot_idx = best_indices.get("total")

        # Report which columns are missing (non-blocking info)
        missing = []
        if name_idx is None: missing.append("Student Name / Reg No")
        if q1_idx is None: missing.append("Quiz 1")
        if q2_idx is None: missing.append("Quiz 2")
        if q3_idx is None: missing.append("Quiz 3")
        if q4_idx is None: missing.append("Quiz 4")
        if a1_idx is None: missing.append("Assignment 1")
        if a2_idx is None: missing.append("Assignment 2")
        if a3_idx is None: missing.append("Assignment 3")
        if a4_idx is None: missing.append("Assignment 4")
        if mid_idx is None: missing.append("Midterm")
        if fin_idx is None: missing.append("Final")

        if missing and len(missing) >= 6:
            return JsonResponse({
                "success": False,
                "error": f"Too many missing columns in sheet '{best_sheet_name}' (matched {best_score}/11). Missing: {', '.join(missing)}"
            }, status=400)

        # ── Safe float parser ──
        def parse_score(val):
            if val is None or str(val).strip() == "":
                return 0.0
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        # ── Parse student data rows ──
        student_records = []
        for r_idx in range(best_header_row_idx + 1, len(best_rows)):
            row = best_rows[r_idx]
            if not row:
                continue

            # Get student identifier (name or reg number)
            if name_idx is None or name_idx >= len(row):
                # Try to use first non-empty cell as identifier
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

            # Skip rows that look like headers or summary rows
            name_lower = student_name.lower().strip()
            skip_words = ["total", "average", "avg", "grade", "head wise", "student name",
                         "roll no", "reg no", "sno", "sr."]
            if any(name_lower == w or name_lower.startswith(w) for w in skip_words):
                continue

            def safe_get(idx):
                if idx is not None and idx < len(row):
                    return parse_score(row[idx])
                return 0.0

            q1 = safe_get(q1_idx)
            q2 = safe_get(q2_idx)
            q3 = safe_get(q3_idx)
            q4 = safe_get(q4_idx)
            a1 = safe_get(a1_idx)
            a2 = safe_get(a2_idx)
            a3 = safe_get(a3_idx)
            a4 = safe_get(a4_idx)
            mid = safe_get(mid_idx)
            fin = safe_get(fin_idx)

            # Calculate total: use explicit total column if available, else sum
            if tot_idx is not None and tot_idx < len(row) and row[tot_idx] is not None and str(row[tot_idx]).strip() != "":
                tot = parse_score(row[tot_idx])
            else:
                tot = q1 + q2 + q3 + q4 + a1 + a2 + a3 + a4 + mid + fin

            # Skip rows where all scores are zero (probably not a student row)
            if tot == 0.0:
                continue

            status = "Pass" if tot >= 50.0 else "Fail"

            student_records.append(StudentRecord(
                student_name=student_name,
                quiz1=q1, quiz2=q2, quiz3=q3, quiz4=q4,
                assign1=a1, assign2=a2, assign3=a3, assign4=a4,
                midterm=mid, final=fin,
                total=tot,
                status=status
            ))

        if not student_records:
            return JsonResponse({
                "success": False,
                "error": f"Found headers in sheet '{best_sheet_name}' but no valid student data rows. "
                         f"Make sure the sheet has student names and numeric scores."
            }, status=400)

        # Truncate and bulk insert
        StudentRecord.objects.all().delete()
        StudentRecord.objects.bulk_create(student_records)

        return JsonResponse({
            "success": True,
            "message": f"Successfully parsed {len(student_records)} student records from sheet '{best_sheet_name}' (matched {best_score}/11 header columns)."
        })

    except Exception as e:
        return JsonResponse({"success": False, "error": f"Failed to parse Excel file: {str(e)}"}, status=500)


def report(request):
    students = StudentRecord.objects.all()
    total_students = students.count()
    if total_students == 0:
        return JsonResponse({
            "total_students": 0,
            "passed_students_count": 0,
            "failed_students_count": 0,
            "pass_percentage": 0.0,
            "fail_percentage": 0.0,
            "average_score": 0.0,
            "highest_score": 0.0,
            "grade_distribution": {},
            "category_averages": {},
            "trend_data": [],
            "insights": [],
            "students": []
        })

    passed_count = students.filter(status="Pass").count()
    failed_count = total_students - passed_count

    pass_pct = round((passed_count / total_students) * 100, 2)
    fail_pct = round((failed_count / total_students) * 100, 2)

    # ── Additional Statistics ──
    totals = [s.total for s in students]
    average_score = round(sum(totals) / len(totals), 2) if totals else 0.0
    highest_score = round(max(totals), 2) if totals else 0.0

    # ── Grade Distribution (percentage-based, assuming 100-point scale) ──
    def get_grade(total):
        if total >= 90:
            return "A+"
        elif total >= 80:
            return "A"
        elif total >= 75:
            return "B+"
        elif total >= 70:
            return "B"
        elif total >= 60:
            return "C"
        elif total >= 50:
            return "D"
        else:
            return "F"

    grade_order = ["A+", "A", "B+", "B", "C", "D", "F"]
    grade_counts = {g: 0 for g in grade_order}
    for s in students:
        grade = get_grade(s.total)
        grade_counts[grade] += 1

    # Remove zero-count grades for cleaner display
    grade_distribution = {k: v for k, v in grade_counts.items() if v > 0}

    # ── Category Averages ──
    quiz_totals = [s.quiz1 + s.quiz2 + s.quiz3 + s.quiz4 for s in students]
    assign_totals = [s.assign1 + s.assign2 + s.assign3 + s.assign4 for s in students]
    midterm_scores = [s.midterm for s in students]
    final_scores = [s.final for s in students]

    quiz_avg = round(sum(quiz_totals) / len(quiz_totals), 2) if quiz_totals else 0.0
    assign_avg = round(sum(assign_totals) / len(assign_totals), 2) if assign_totals else 0.0
    mid_avg = round(sum(midterm_scores) / len(midterm_scores), 2) if midterm_scores else 0.0
    final_avg = round(sum(final_scores) / len(final_scores), 2) if final_scores else 0.0

    category_averages = {
        "Quizzes": quiz_avg,
        "Assignments": assign_avg,
        "Midterm": mid_avg,
        "Final Exam": final_avg,
    }

    trend_data = [quiz_avg, assign_avg, mid_avg, final_avg]

    # ── Insights ──
    insights = []
    pass_rate = round((passed_count / total_students) * 100, 1)
    insights.append(
        f"Overall pass rate is {pass_rate}% ({passed_count} out of {total_students} students)."
    )

    # Strongest / weakest category
    strongest = max(category_averages, key=category_averages.get)
    weakest = min(category_averages, key=category_averages.get)
    insights.append(
        f"Strongest assessment category: {strongest} (average: {category_averages[strongest]:.1f})."
    )
    insights.append(
        f"Weakest assessment category: {weakest} (average: {category_averages[weakest]:.1f})."
    )

    # Trend insight
    if final_avg > quiz_avg + 1:
        insights.append(
            "Performance improved from quizzes to final exam, indicating positive learning progression."
        )
    elif quiz_avg > final_avg + 1:
        insights.append(
            "Performance declined from quizzes to final exam, suggesting increasing difficulty or need for better exam preparation."
        )
    else:
        insights.append(
            "Performance remained relatively stable throughout the course."
        )

    # At-risk students (50-59 range)
    at_risk = sum(1 for s in students if 50 <= s.total < 60)
    if at_risk > 0:
        insights.append(
            f"{at_risk} student(s) are academically at-risk with borderline scores (50-59%)."
        )

    # Average score insight
    if average_score >= 70:
        insights.append(
            f"Class average score is {average_score:.1f}%, indicating strong overall performance."
        )
    elif average_score >= 50:
        insights.append(
            f"Class average score is {average_score:.1f}%, indicating moderate performance with room for improvement."
        )
    else:
        insights.append(
            f"Class average score is {average_score:.1f}%, indicating significant academic concerns."
        )

    # ── Student List ──
    student_list = []
    for s in students:
        student_list.append({
            "id": s.id,
            "student_name": s.student_name,
            "quiz1": s.quiz1,
            "quiz2": s.quiz2,
            "quiz3": s.quiz3,
            "quiz4": s.quiz4,
            "assign1": s.assign1,
            "assign2": s.assign2,
            "assign3": s.assign3,
            "assign4": s.assign4,
            "midterm": s.midterm,
            "final": s.final,
            "total": s.total,
            "status": s.status
        })

    return JsonResponse({
        "total_students": total_students,
        "passed_students_count": passed_count,
        "failed_students_count": failed_count,
        "pass_percentage": pass_pct,
        "fail_percentage": fail_pct,
        "average_score": average_score,
        "highest_score": highest_score,
        "grade_distribution": grade_distribution,
        "category_averages": category_averages,
        "trend_data": trend_data,
        "insights": insights,
        "students": student_list
    })


def chart_data(request):
    students = StudentRecord.objects.all()
    passed_count = students.filter(status="Pass").count()
    failed_count = students.filter(status="Fail").count()

    return JsonResponse({
        "labels": ["Pass", "Fail"],
        "values": [passed_count, failed_count]
    })


def download_excel(request):
    """Generate and return an Excel report with full academic insights."""
    students = StudentRecord.objects.all()
    total_students = students.count()

    if total_students == 0:
        return JsonResponse({"success": False, "error": "No data available. Upload a marksheet first."}, status=400)

    # ── Compute all statistics (same logic as report view) ──
    passed_count = students.filter(status="Pass").count()
    failed_count = total_students - passed_count
    pass_pct = round((passed_count / total_students) * 100, 2)
    fail_pct = round((failed_count / total_students) * 100, 2)

    totals = [s.total for s in students]
    average_score = round(sum(totals) / len(totals), 2)
    highest_score = round(max(totals), 2)

    def get_grade(total):
        if total >= 90: return "A+"
        elif total >= 80: return "A"
        elif total >= 75: return "B+"
        elif total >= 70: return "B"
        elif total >= 60: return "C"
        elif total >= 50: return "D"
        else: return "F"

    grade_order = ["A+", "A", "B+", "B", "C", "D", "F"]
    grade_counts = {g: 0 for g in grade_order}
    for s in students:
        grade_counts[get_grade(s.total)] += 1

    quiz_totals = [s.quiz1 + s.quiz2 + s.quiz3 + s.quiz4 for s in students]
    assign_totals = [s.assign1 + s.assign2 + s.assign3 + s.assign4 for s in students]
    midterm_scores = [s.midterm for s in students]
    final_scores = [s.final for s in students]

    quiz_avg = round(sum(quiz_totals) / len(quiz_totals), 2)
    assign_avg = round(sum(assign_totals) / len(assign_totals), 2)
    mid_avg = round(sum(midterm_scores) / len(midterm_scores), 2)
    final_avg = round(sum(final_scores) / len(final_scores), 2)

    category_averages = {
        "Quizzes": quiz_avg, "Assignments": assign_avg,
        "Midterm": mid_avg, "Final Exam": final_avg,
    }

    # ── Insights ──
    insights = []
    pass_rate = round((passed_count / total_students) * 100, 1)
    insights.append(f"Overall pass rate is {pass_rate}% ({passed_count} out of {total_students} students).")
    strongest = max(category_averages, key=category_averages.get)
    weakest = min(category_averages, key=category_averages.get)
    insights.append(f"Strongest assessment category: {strongest} (average: {category_averages[strongest]:.1f}).")
    insights.append(f"Weakest assessment category: {weakest} (average: {category_averages[weakest]:.1f}).")
    if final_avg > quiz_avg + 1:
        insights.append("Performance improved from quizzes to final exam, indicating positive learning progression.")
    elif quiz_avg > final_avg + 1:
        insights.append("Performance declined from quizzes to final exam, suggesting increasing difficulty.")
    else:
        insights.append("Performance remained relatively stable throughout the course.")
    at_risk = sum(1 for s in students if 50 <= s.total < 60)
    if at_risk > 0:
        insights.append(f"{at_risk} student(s) are academically at-risk with borderline scores (50-59%).")
    if average_score >= 70:
        insights.append(f"Class average score is {average_score:.1f}%, indicating strong overall performance.")
    elif average_score >= 50:
        insights.append(f"Class average score is {average_score:.1f}%, indicating moderate performance.")
    else:
        insights.append(f"Class average score is {average_score:.1f}%, indicating significant academic concerns.")

    # ── Build Excel Workbook ──
    wb = openpyxl.Workbook()

    # Styles
    title_font = Font(name="Calibri", size=16, bold=True, color="0D3B57")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="0D3B57", end_color="0D3B57", fill_type="solid")
    label_font = Font(name="Calibri", size=11, bold=True, color="1F2D3D")
    value_font = Font(name="Calibri", size=11, color="1F2D3D")
    section_font = Font(name="Calibri", size=13, bold=True, color="0D3B57")
    section_fill = PatternFill(start_color="E8F3FB", end_color="E8F3FB", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )

    def style_header(ws, row, cols):
        for c in range(1, cols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

    def style_row(ws, row, cols, bold=False):
        for c in range(1, cols + 1):
            cell = ws.cell(row=row, column=c)
            cell.font = label_font if bold else value_font
            cell.border = thin_border

    def auto_width(ws, min_w=10, max_w=40):
        for col_cells in ws.columns:
            col_letter = get_column_letter(col_cells[0].column)
            widths = [len(str(c.value or "")) for c in col_cells]
            ws.column_dimensions[col_letter].width = max(min_w, min(max(widths) + 3, max_w))

    # ===== Sheet 1: Academic Overview =====
    ws1 = wb.active
    ws1.title = "Academic Overview"

    ws1.merge_cells("A1:C1")
    c = ws1.cell(row=1, column=1, value="Final Academic Insights — Complete Report")
    c.font = title_font
    c.alignment = Alignment(horizontal="left", vertical="center")

    # ── Table of Contents ──
    row = 3
    ws1.merge_cells(f"A{row}:C{row}")
    toc_header = ws1.cell(row=row, column=1, value="📋  Workbook Contents  (5 Sheets)")
    toc_header.font = section_font
    toc_header.fill = section_fill
    toc_header.border = thin_border
    for c in range(2, 4):
        ws1.cell(row=row, column=c).fill = section_fill
        ws1.cell(row=row, column=c).border = thin_border
    row += 1

    toc_headers = ["Sheet #", "Sheet Name", "What's Inside"]
    for ci, h in enumerate(toc_headers, 1):
        cell = ws1.cell(row=row, column=ci, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    row += 1

    toc_data = [
        ("1", "Academic Overview", "Summary statistics — Total, Passed, Failed, Average, Highest"),
        ("2", "Grade Distribution", "Grade breakdown — A+ through F with counts / percentages"),
        ("3", "Assessment Breakdown", "Category averages — Quizzes, Assignments, Midterm, Final Exam"),
        ("4", "Academic Insights", "Auto-generated analytical insights & observations"),
        ("5", "Student Records", f"Complete marksheet — all {total_students} students with scores & grades"),
    ]
    for sheet_num, sheet_name, description in toc_data:
        ws1.cell(row=row, column=1, value=sheet_num).font = value_font
        ws1.cell(row=row, column=1).alignment = Alignment(horizontal="center")
        ws1.cell(row=row, column=1).border = thin_border
        ws1.cell(row=row, column=2, value=sheet_name).font = label_font
        ws1.cell(row=row, column=2).border = thin_border
        ws1.cell(row=row, column=3, value=description).font = value_font
        ws1.cell(row=row, column=3).border = thin_border
        row += 1

    row += 1  # blank row

    # ── Academic Overview Data ──
    ws1.merge_cells(f"A{row}:C{row}")
    data_header = ws1.cell(row=row, column=1, value="📊  Academic Overview")
    data_header.font = section_font
    data_header.fill = section_fill
    data_header.border = thin_border
    for c in range(2, 4):
        ws1.cell(row=row, column=c).fill = section_fill
        ws1.cell(row=row, column=c).border = thin_border
    row += 1

    overview_data = [
        ("Total Students", str(total_students)),
        ("Passed Students", f"{passed_count} ({pass_pct}%)"),
        ("Failed Students", f"{failed_count} ({fail_pct}%)"),
        ("Average Class Score", f"{average_score}%"),
        ("Highest Score", f"{highest_score}%"),
    ]
    for label, val in overview_data:
        ws1.cell(row=row, column=1, value=label).font = label_font
        ws1.cell(row=row, column=1).border = thin_border
        ws1.cell(row=row, column=2, value=val).font = value_font
        ws1.cell(row=row, column=2).border = thin_border
        row += 1

    # ── Navigation Hint ──
    row += 1
    ws1.merge_cells(f"A{row}:C{row}")
    hint = ws1.cell(row=row, column=1, value="👇  Use the sheet tabs at the BOTTOM of this Excel window to view all 5 sheets.")
    hint.font = Font(name="Calibri", size=11, bold=True, color="1F6F8B")
    hint.alignment = Alignment(horizontal="left", vertical="center")

    ws1.column_dimensions['A'].width = 24
    ws1.column_dimensions['B'].width = 28
    ws1.column_dimensions['C'].width = 58
    auto_width(ws1, min_w=20)

    # ===== Sheet 2: Grade Distribution =====
    ws2 = wb.create_sheet("Grade Distribution")
    ws2.cell(row=1, column=1, value="Grade Distribution").font = title_font
    row = 3
    for ci, h in enumerate(["Grade", "Students", "Percentage"], 1):
        ws2.cell(row=row, column=ci, value=h)
    style_header(ws2, row, 3)
    row += 1
    for grade in grade_order:
        cnt = grade_counts.get(grade, 0)
        pct = round((cnt / total_students) * 100, 1) if total_students else 0
        ws2.cell(row=row, column=1, value=grade)
        ws2.cell(row=row, column=2, value=cnt)
        ws2.cell(row=row, column=3, value=f"{pct}%")
        style_row(ws2, row, 3)
        row += 1
    auto_width(ws2)

    # ===== Sheet 3: Assessment Breakdown =====
    ws3 = wb.create_sheet("Assessment Breakdown")
    ws3.cell(row=1, column=1, value="Assessment Performance Breakdown").font = title_font
    row = 3
    for ci, h in enumerate(["Category", "Average Score"], 1):
        ws3.cell(row=row, column=ci, value=h)
    style_header(ws3, row, 2)
    row += 1
    for cat, avg in category_averages.items():
        ws3.cell(row=row, column=1, value=cat)
        ws3.cell(row=row, column=2, value=avg)
        style_row(ws3, row, 2)
        row += 1
    # Trend summary
    row += 1
    ws3.cell(row=row, column=1, value="Trend (Quizzes → Final Exam):").font = label_font
    ws3.cell(row=row, column=2, value=f"{quiz_avg} → {assign_avg} → {mid_avg} → {final_avg}").font = value_font
    auto_width(ws3, min_w=22)

    # ===== Sheet 4: Academic Insights =====
    ws4 = wb.create_sheet("Academic Insights")
    ws4.cell(row=1, column=1, value="Final Academic Insights").font = title_font
    row = 3
    ws4.cell(row=row, column=1, value="#").font = header_font
    ws4.cell(row=row, column=1).fill = header_fill
    ws4.cell(row=row, column=1).border = thin_border
    ws4.cell(row=row, column=2, value="Insight").font = header_font
    ws4.cell(row=row, column=2).fill = header_fill
    ws4.cell(row=row, column=2).border = thin_border
    row += 1
    for i, insight in enumerate(insights, 1):
        ws4.cell(row=row, column=1, value=i)
        ws4.cell(row=row, column=2, value=insight)
        style_row(ws4, row, 2)
        row += 1
    ws4.column_dimensions['B'].width = 80
    auto_width(ws4)

    # ===== Sheet 5: Student Records =====
    ws5 = wb.create_sheet("Student Records")
    ws5.cell(row=1, column=1, value="Student Performance Sheet").font = title_font
    row = 3
    headers = ["Student Name", "Q1", "Q2", "Q3", "Q4", "A1", "A2", "A3", "A4",
               "Midterm", "Final", "Total", "Status", "Grade"]
    for ci, h in enumerate(headers, 1):
        ws5.cell(row=row, column=ci, value=h)
    style_header(ws5, row, len(headers))
    row += 1
    for s in students.order_by("student_name"):
        vals = [s.student_name, s.quiz1, s.quiz2, s.quiz3, s.quiz4,
                s.assign1, s.assign2, s.assign3, s.assign4,
                s.midterm, s.final, s.total, s.status, get_grade(s.total)]
        for ci, val in enumerate(vals, 1):
            ws5.cell(row=row, column=ci, value=val)
        style_row(ws5, row, len(headers))
        row += 1
    auto_width(ws5)

    # ── Write to response ──
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="final_academic_insights_report.xlsx"'
    return response
