"""Student Outreach Assistant views and email dispatch."""

import os
import json
import uuid

from django.conf import settings
from django.db.models import Q
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from auth_app.models import (
    FacultyUser,
    MidtermEvaluationRun,
    MidtermEvaluationStudent,
    StudentOutreachEmailLog,
)
from auth_app.outreach_email_service import (
    build_generated_student_email,
    build_student_context,
    get_default_outreach_templates,
    render_outreach_template,
    send_outreach_email,
)
from .helpers import (
    _get_session_user,
    _parse_json_request_body,
    SESSION_KEY,
)
from .midterm import (
    _resolve_midterm_run,
    _evaluate_midterm_run,
)

def student_outreach_assistant_page(request):
    user_id = request.session.get(SESSION_KEY)
    user_name = None
    if user_id:
        try:
            user = FacultyUser.objects.get(id=user_id)
            user_name = user.name or user.email
        except FacultyUser.DoesNotExist:
            pass
    context = {"user_name": user_name}
    return render(request, "auth/student_outreach_assistant.html", context)


def _outreach_defaults(user, run):
    templates = get_default_outreach_templates()
    return {
        "course_name": run.source_sheet_name or run.source_file_name or "Midterm Course",
        "faculty_name": (user.name or user.email) if user else "Faculty Member",
        "institution_name": getattr(settings, "OUTREACH_INSTITUTION_NAME", "SZABIST Islamabad"),
        "subject_template": templates["subject_template"],
        "body_template": templates["body_template"],
    }


def _outreach_students_for_run(run):
    if not run:
        return []

    return list(
        run.students.filter(
            Q(performance_band__in=["At Risk", "Critical"])
            | Q(risk_status__istartswith="at risk")
            | Q(risk_status__istartswith="critical")
        ).order_by("row_number", "id")
    )


def _is_outreach_critical(student):
    risk_lower = (student.risk_status or "").strip().lower()
    band_lower = (student.performance_band or "").strip().lower()
    return band_lower == "critical" or risk_lower.startswith("critical")


def _outreach_counts(students):
    critical_count = 0
    at_risk_count = 0

    for student in students:
        if _is_outreach_critical(student):
            critical_count += 1
        else:
            at_risk_count += 1

    return {
        "critical": critical_count,
        "at_risk": at_risk_count,
        "total": critical_count + at_risk_count,
    }


def _serialize_outreach_student(student):
    risk_level = (student.risk_status or student.performance_band or "At Risk").strip()
    if _is_outreach_critical(student):
        risk_level = "Critical"
    elif not risk_level.lower().startswith("at risk"):
        risk_level = "At Risk"

    return {
        "id": student.id,
        "row_number": student.row_number,
        "student_name": student.student_name,
        "registration_no": student.registration_no,
        "midterm_marks": student.midterm_marks,
        "grade_letter": student.grade_letter,
        "risk_status": risk_level,
        "performance_band": student.performance_band,
        "generated_email": build_generated_student_email(student.student_name, student.registration_no),
    }


def _compose_outreach_email(
    student,
    subject_template,
    body_template,
    course_name,
    faculty_name,
    institution_name,
):
    risk_status = (student.risk_status or student.performance_band or "At Risk").strip()
    context = build_student_context(
        student_name=student.student_name,
        registration_no=student.registration_no,
        midterm_marks=student.midterm_marks,
        risk_status=risk_status,
        grade_letter=student.grade_letter,
        course_name=course_name,
        faculty_name=faculty_name,
        institution_name=institution_name,
    )

    subject = render_outreach_template(subject_template, context).strip()
    body = render_outreach_template(body_template, context).strip()

    if not subject:
        subject = f"Academic Alert - {context['student_name']}"
    if not body:
        body = "This is an academic alert from Faculty Tasks Automation. Please contact your faculty advisor."

    return subject, body, context


def _dispatch_outreach_student_email(
    user,
    run,
    student,
    subject_template,
    body_template,
    course_name,
    faculty_name,
    institution_name,
    dispatch_batch_id,
):
    generated_email = build_generated_student_email(student.student_name, student.registration_no)
    subject, body, context = _compose_outreach_email(
        student,
        subject_template,
        body_template,
        course_name,
        faculty_name,
        institution_name,
    )

    status_label = "sent"
    error_message = ""

    try:
        send_outreach_email(generated_email, subject, body)
    except Exception as exc:
        status_label = "failed"
        error_message = str(exc)

    log_id = None
    created_at = timezone.now().isoformat()

    try:
        log_entry = StudentOutreachEmailLog.objects.create(
            user=user,
            run=run,
            student=student,
            dispatch_batch_id=dispatch_batch_id or "",
            recipient_type="student",
            student_name=student.student_name or "",
            registration_no=student.registration_no or "",
            email_address=generated_email,
            midterm_marks=student.midterm_marks,
            grade_letter=student.grade_letter or "",
            risk_level=context.get("risk_status", ""),
            email_subject=subject,
            email_body=body,
            status=status_label,
            error_message=error_message,
        )
        log_id = log_entry.id
        created_at = log_entry.created_at.isoformat()
    except Exception as exc:
        if status_label == "sent":
            status_label = "failed"
        if error_message:
            error_message = f"{error_message}; Log write failed: {exc}"
        else:
            error_message = f"Log write failed: {exc}"

    return {
        "log_id": log_id,
        "student_id": student.id,
        "student_name": student.student_name,
        "registration_no": student.registration_no,
        "email": generated_email,
        "risk_status": context.get("risk_status"),
        "status": status_label,
        "error": error_message,
        "subject": subject,
        "created_at": created_at,
    }


@csrf_exempt
def student_outreach_at_risk_students(request):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    run_id = request.GET.get("run_id")
    user, run = _resolve_midterm_run(request, run_id)
    if not run:
        return JsonResponse(
            {"error": "No midterm evaluation run found. Please upload and evaluate midterm data first."},
            status=400,
        )

    try:
        _evaluate_midterm_run(run)
        defaults = _outreach_defaults(user, run)
        students = _outreach_students_for_run(run)
        serialized_students = [_serialize_outreach_student(student) for student in students]
        counts = _outreach_counts(students)

        preview = None
        if students:
            subject_preview, body_preview, _ = _compose_outreach_email(
                students[0],
                defaults["subject_template"],
                defaults["body_template"],
                defaults["course_name"],
                defaults["faculty_name"],
                defaults["institution_name"],
            )
            preview = {
                "student_id": students[0].id,
                "subject": subject_preview,
                "body": body_preview,
            }

        return JsonResponse(
            {
                "status": "success",
                "run_id": run.id,
                "course_name": defaults["course_name"],
                "faculty_name": defaults["faculty_name"],
                "institution_name": defaults["institution_name"],
                "templates": {
                    "subject_template": defaults["subject_template"],
                    "body_template": defaults["body_template"],
                },
                "counts": counts,
                "students": serialized_students,
                "preview": preview,
            }
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse(
            {
                "error": "Failed to fetch at-risk students.",
                "detail": str(exc),
            },
            status=500,
        )


@csrf_exempt
def student_outreach_preview_email(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    payload = _parse_json_request_body(request)
    if payload is None:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    run_id = payload.get("run_id")
    student_id = payload.get("student_id")
    if not student_id:
        return JsonResponse({"error": "student_id is required."}, status=400)

    user, run = _resolve_midterm_run(request, run_id)
    if not run:
        return JsonResponse({"error": "No midterm evaluation run found."}, status=400)

    student = run.students.filter(id=student_id).first()
    if not student:
        return JsonResponse({"error": "Student not found in selected run."}, status=404)

    defaults = _outreach_defaults(user, run)
    subject_template = payload.get("subject_template") or defaults["subject_template"]
    body_template = payload.get("body_template") or defaults["body_template"]
    course_name = payload.get("course_name") or defaults["course_name"]
    faculty_name = payload.get("faculty_name") or defaults["faculty_name"]
    institution_name = payload.get("institution_name") or defaults["institution_name"]

    try:
        subject, body, context = _compose_outreach_email(
            student,
            subject_template,
            body_template,
            course_name,
            faculty_name,
            institution_name,
        )
        return JsonResponse(
            {
                "status": "success",
                "preview": {
                    "student_id": student.id,
                    "subject": subject,
                    "body": body,
                    "generated_email": build_generated_student_email(student.student_name, student.registration_no),
                    "context": context,
                },
            }
        )
    except Exception as exc:
        return JsonResponse(
            {
                "error": "Unable to render email preview.",
                "detail": str(exc),
            },
            status=500,
        )


@csrf_exempt
def student_outreach_send_single(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    payload = _parse_json_request_body(request)
    if payload is None:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    run_id = payload.get("run_id")
    student_id = payload.get("student_id")
    if not student_id:
        return JsonResponse({"error": "student_id is required."}, status=400)

    user, run = _resolve_midterm_run(request, run_id)
    if not run:
        return JsonResponse({"error": "No midterm evaluation run found."}, status=400)

    student = run.students.filter(id=student_id).first()
    if not student:
        return JsonResponse({"error": "Student not found in selected run."}, status=404)

    defaults = _outreach_defaults(user, run)
    subject_template = payload.get("subject_template") or defaults["subject_template"]
    body_template = payload.get("body_template") or defaults["body_template"]
    course_name = payload.get("course_name") or defaults["course_name"]
    faculty_name = payload.get("faculty_name") or defaults["faculty_name"]
    institution_name = payload.get("institution_name") or defaults["institution_name"]
    dispatch_batch_id = payload.get("dispatch_batch_id") or uuid.uuid4().hex

    try:
        dispatch_result = _dispatch_outreach_student_email(
            user=user,
            run=run,
            student=student,
            subject_template=subject_template,
            body_template=body_template,
            course_name=course_name,
            faculty_name=faculty_name,
            institution_name=institution_name,
            dispatch_batch_id=dispatch_batch_id,
        )
        return JsonResponse(
            {
                "status": dispatch_result["status"],
                "run_id": run.id,
                "dispatch_batch_id": dispatch_batch_id,
                "result": dispatch_result,
            }
        )
    except Exception as exc:
        return JsonResponse(
            {
                "error": "Failed to send outreach email.",
                "detail": str(exc),
            },
            status=500,
        )


@csrf_exempt
def student_outreach_send_faculty_summary(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    payload = _parse_json_request_body(request)
    if payload is None:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    run_id = payload.get("run_id")
    user, run = _resolve_midterm_run(request, run_id)
    if not run:
        return JsonResponse({"error": "No midterm evaluation run found."}, status=400)

    try:
        _evaluate_midterm_run(run)
        students = _outreach_students_for_run(run)
        counts = _outreach_counts(students)

        defaults = _outreach_defaults(user, run)
        course_name = payload.get("course_name") or defaults["course_name"]
        faculty_name = payload.get("faculty_name") or defaults["faculty_name"]
        institution_name = payload.get("institution_name") or defaults["institution_name"]
        dispatch_batch_id = payload.get("dispatch_batch_id") or uuid.uuid4().hex

        recipient = str(getattr(settings, "OUTREACH_FACULTY_SUMMARY_EMAIL", "") or "").strip()
        if not recipient:
            return JsonResponse(
                {
                    "error": "OUTREACH_FACULTY_SUMMARY_EMAIL is not configured in .env.",
                },
                status=400,
            )

        subject = (
            f"[Faculty Tasks Automation] Outreach Summary - {counts['critical']} Critical, "
            f"{counts['at_risk']} At Risk"
        )

        lines = [
            "Student Outreach Summary",
            "",
            f"Course: {course_name}",
            f"Faculty: {faculty_name}",
            f"Institution: {institution_name}",
            f"Total Alerts Sent: {counts['total']}",
            f"Critical: {counts['critical']}",
            f"At Risk: {counts['at_risk']}",
            "",
            "Student List:",
        ]

        if not students:
            lines.append("No at-risk or critical students were found in this run.")
        else:
            for index, student in enumerate(students, start=1):
                risk_level = (student.risk_status or student.performance_band or "At Risk").strip()
                generated_email = build_generated_student_email(student.student_name, student.registration_no)
                lines.append(
                    (
                        f"{index}. {student.student_name} ({student.registration_no}) | "
                        f"Marks: {student.midterm_marks if student.midterm_marks is not None else 'N/A'} | "
                        f"Grade: {student.grade_letter or 'N/A'} | "
                        f"Risk: {risk_level} | Email: {generated_email}"
                    )
                )

        lines.extend(
            [
                "",
                "Recommended faculty action:",
                "1. Arrange support meeting for critical students this week.",
                "2. Share targeted study plan with at-risk students.",
                "3. Track follow-up in the next assessment cycle.",
            ]
        )
        body = "\n".join(lines)

        status_label = "sent"
        error_message = ""
        try:
            send_outreach_email(recipient, subject, body)
        except Exception as exc:
            status_label = "failed"
            error_message = str(exc)

        StudentOutreachEmailLog.objects.create(
            user=user,
            run=run,
            dispatch_batch_id=dispatch_batch_id,
            recipient_type="faculty_summary",
            student_name="Faculty Summary",
            registration_no="",
            email_address=recipient,
            midterm_marks=None,
            grade_letter="",
            risk_level=f"Critical={counts['critical']} | AtRisk={counts['at_risk']}",
            email_subject=subject,
            email_body=body,
            status=status_label,
            error_message=error_message,
        )

        return JsonResponse(
            {
                "status": status_label,
                "run_id": run.id,
                "dispatch_batch_id": dispatch_batch_id,
                "recipient": recipient,
                "counts": counts,
                "error": error_message,
            }
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse(
            {
                "error": "Failed to send faculty summary email.",
                "detail": str(exc),
            },
            status=500,
        )


@csrf_exempt
def student_outreach_email_history(request):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    run_id = request.GET.get("run_id")
    user = _get_session_user(request)

    queryset = StudentOutreachEmailLog.objects.all()
    if user:
        queryset = queryset.filter(user=user)
    if run_id:
        queryset = queryset.filter(run_id=run_id)

    logs = list(queryset.select_related("student", "run").order_by("-created_at")[:500])

    payload = [
        {
            "id": log.id,
            "run_id": log.run_id,
            "student_id": log.student_id,
            "recipient_type": log.recipient_type,
            "student_name": log.student_name,
            "registration_no": log.registration_no,
            "email_address": log.email_address,
            "midterm_marks": log.midterm_marks,
            "grade_letter": log.grade_letter,
            "risk_level": log.risk_level,
            "email_subject": log.email_subject,
            "status": log.status,
            "error_message": log.error_message,
            "dispatch_batch_id": log.dispatch_batch_id,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]

    failed_count = sum(1 for row in payload if (row["status"] or "").lower() == "failed")

    return JsonResponse(
        {
            "status": "success",
            "history": payload,
            "total": len(payload),
            "failed": failed_count,
            "sent": len(payload) - failed_count,
        }
    )


@csrf_exempt
def student_outreach_resend_failed(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    payload = _parse_json_request_body(request)
    if payload is None:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    run_id = payload.get("run_id")
    user, run = _resolve_midterm_run(request, run_id)
    if not run:
        return JsonResponse({"error": "No midterm evaluation run found."}, status=400)

    defaults = _outreach_defaults(user, run)
    subject_template = payload.get("subject_template") or defaults["subject_template"]
    body_template = payload.get("body_template") or defaults["body_template"]
    course_name = payload.get("course_name") or defaults["course_name"]
    faculty_name = payload.get("faculty_name") or defaults["faculty_name"]
    institution_name = payload.get("institution_name") or defaults["institution_name"]
    dispatch_batch_id = payload.get("dispatch_batch_id") or uuid.uuid4().hex

    latest_by_student = {}
    log_queryset = StudentOutreachEmailLog.objects.filter(
        run=run,
        recipient_type="student",
    ).order_by("student_id", "-created_at", "-id")

    for log in log_queryset:
        key = log.student_id or f"{log.registration_no}|{log.email_address}"
        if key in latest_by_student:
            continue
        latest_by_student[key] = log

    resend_targets = [
        log
        for log in latest_by_student.values()
        if (log.status or "").lower() == "failed"
    ]

    if not resend_targets:
        return JsonResponse(
            {
                "status": "success",
                "run_id": run.id,
                "dispatch_batch_id": dispatch_batch_id,
                "message": "No failed outreach emails found to resend.",
                "sent": 0,
                "failed": 0,
                "results": [],
            }
        )

    sent_count = 0
    failed_count = 0
    results = []

    for target in resend_targets:
        student = target.student
        if not student and target.registration_no:
            student = run.students.filter(registration_no=target.registration_no).first()
        if not student and target.student_name:
            student = run.students.filter(student_name=target.student_name).first()

        if not student:
            failed_count += 1
            message = "Student record no longer exists in this run."
            StudentOutreachEmailLog.objects.create(
                user=user,
                run=run,
                dispatch_batch_id=dispatch_batch_id,
                recipient_type="student",
                student_name=target.student_name or "",
                registration_no=target.registration_no or "",
                email_address=target.email_address,
                midterm_marks=target.midterm_marks,
                grade_letter=target.grade_letter,
                risk_level=target.risk_level,
                email_subject=target.email_subject,
                email_body=target.email_body,
                status="failed",
                error_message=message,
            )
            results.append(
                {
                    "student_id": target.student_id,
                    "student_name": target.student_name,
                    "registration_no": target.registration_no,
                    "email": target.email_address,
                    "status": "failed",
                    "error": message,
                }
            )
            continue

        dispatch_result = _dispatch_outreach_student_email(
            user=user,
            run=run,
            student=student,
            subject_template=subject_template,
            body_template=body_template,
            course_name=course_name,
            faculty_name=faculty_name,
            institution_name=institution_name,
            dispatch_batch_id=dispatch_batch_id,
        )

        if dispatch_result["status"] == "sent":
            sent_count += 1
        else:
            failed_count += 1
        results.append(dispatch_result)

    return JsonResponse(
        {
            "status": "success",
            "run_id": run.id,
            "dispatch_batch_id": dispatch_batch_id,
            "sent": sent_count,
            "failed": failed_count,
            "results": results,
        }
    )

