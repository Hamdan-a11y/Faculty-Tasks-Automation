from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt

from auth_app.models import FacultyUser
from .models import CLO, CLOMapping, Question
from .services import (
    ExtractionError,
    MappingError,
    extract_clos_from_docx,
    extract_clos_from_pdf,
    extract_questions_from_docx,
    extract_questions_from_image,
    extract_questions_from_pdf,
    extract_questions_from_text,
    map_questions_to_clos,
)

SESSION_KEY = "faculty_user_id"
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
logger = logging.getLogger("clo_mapper")


def _require_session(request):
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None, redirect("auth_app:login")
    user = FacultyUser.objects.filter(id=user_id).first()
    if not user:
        return None, redirect("auth_app:login")
    return user, None


def index(request):
    user, redirect_resp = _require_session(request)
    if redirect_resp:
        return redirect_resp
    context = {"user_name": user.name or user.email}
    return render(request, "clo_mapper/index.html", context)


def _validate_file(uploaded_file, allowed_exts: set[str]):
    if not uploaded_file:
        return "File is required"
    if uploaded_file.size == 0:
        return "File is empty"
    if uploaded_file.size > MAX_UPLOAD_SIZE:
        return "File size exceeds 5 MB limit"
    ext = Path(uploaded_file.name).suffix.lower()
    if ext not in allowed_exts:
        return f"Unsupported file type: {ext}"
    return None


def _serialize_clo(clo: CLO):
    return {
        "id": clo.id,
        "code": clo.code,
        "description": clo.description,
        "course": clo.course,
        "domain": clo.domain,
        "bt_level": clo.bt_level,
    }


def _serialize_question(q: Question):
    mapping = q.mappings.order_by("-updated_at").first()
    return {
        "id": q.id,
        "text": q.text,
        "assessment_type": q.assessment_type,
        "source_filename": q.source_filename,
        "mapping": {
            "clo_id": mapping.clo.id if mapping else None,
            "clo_code": mapping.clo.code if mapping else None,
            "clo_description": mapping.clo.description if mapping else None,
            "score": mapping.score if mapping else None,
            "auto_mapped": mapping.auto_mapped if mapping else None,
        }
        if mapping
        else None,
    }


@csrf_exempt
def upload_clos(request):
    user, redirect_resp = _require_session(request)
    if redirect_resp:
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    file = request.FILES.get("file")
    logger.info("CLO upload attempt | module=clo_mapper | category=file_upload | name=%s", getattr(file, "name", "<none>"))
    validation_error = _validate_file(file, {".docx", ".pdf"})
    if validation_error:
        # Defensive: reject invalid uploads without crashing; user can retry.
        logger.warning("CLO upload rejected | category=file_upload | reason=%s", validation_error)
        return JsonResponse({"error": validation_error}, status=400)

    course = (request.POST.get("course") or "").strip()

    file_bytes = file.read()
    stream = io.BytesIO(file_bytes)
    clos_data: list[dict]
    ext = Path(file.name).suffix.lower()
    try:
        if ext == ".docx":
            clos_data = extract_clos_from_docx(stream)
        else:
            clos_data = extract_clos_from_pdf(stream)
    except ExtractionError as exc:
        logger.warning("CLO upload failed | extraction | %s", exc.user_message)
        return JsonResponse({"error": exc.user_message, "code": exc.code, "category": exc.category}, status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("CLO upload failed | unexpected error: %s", exc)
        return JsonResponse({"error": "CLO extraction failed. Please retry with a clean file."}, status=500)

    if not clos_data:
        # No CLO statements found in the content; explain next steps.
        logger.info("CLO extraction empty | category=extraction | reason=no_clos")
        return JsonResponse({"error": "No CLOs detected in file. Ensure the document contains CLO statements."}, status=400)

    saved = []
    try:
        with transaction.atomic():
            for row in clos_data:
                code = row.get("code", "").strip()
                desc = row.get("description", "").strip()
                domain = row.get("domain", "").strip()
                bt_level = row.get("bt_level", "").strip()
                row_course = row.get("course", "").strip()
                clo_course = row_course or course
                if not code or not desc:
                    continue
                clo, _ = CLO.objects.update_or_create(
                    code=code,
                    defaults={
                        "description": desc,
                        "course": clo_course,
                        "domain": domain,
                        "bt_level": bt_level,
                    },
                )
                saved.append(_serialize_clo(clo))
    except Exception as exc:  # noqa: BLE001
        logger.exception("CLO save failed | db | %s", exc)
        return JsonResponse({"error": "Database error while saving CLOs. Please retry."}, status=500)

    logger.info("CLO upload success | category=extraction | count=%s", len(saved))
    return JsonResponse({"clos": saved})


@csrf_exempt
def upload_questions(request):
    user, redirect_resp = _require_session(request)
    if redirect_resp:
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    assessment_type = request.POST.get("assessment_type", "unspecified")
    logger.info("Question upload attempt | module=clo_mapper | category=file_upload")
    allowed_types = {"quiz", "assignment", "midterm", "final", "unspecified"}
    if assessment_type not in allowed_types:
        # Prevent invalid assessment types from corrupting mappings.
        return JsonResponse({"error": "Invalid assessment type. Please choose a valid type."}, status=400)
    text_payload = request.POST.get("text")
    files = request.FILES.getlist("files") or ([] if request.FILES else [])
    questions: list[str] = []
    source_filename = None

    if text_payload:
        questions.extend(extract_questions_from_text(text_payload))
        source_filename = "paste"
    elif files:
        for upload in files:
            validation_error = _validate_file(upload, {".docx", ".pdf", ".png", ".jpg", ".jpeg"})
            if validation_error:
                return JsonResponse({"error": validation_error}, status=400)
            file_bytes = upload.read()
            stream = io.BytesIO(file_bytes)
            ext = Path(upload.name).suffix.lower()
            try:
                if ext == ".docx":
                    extracted = extract_questions_from_docx(stream)
                elif ext == ".pdf":
                    extracted = extract_questions_from_pdf(stream)
                else:
                    extracted = extract_questions_from_image(stream)
            except ExtractionError as exc:
                logger.warning("Question upload failed | extraction | %s", exc.user_message)
                return JsonResponse({"error": exc.user_message, "code": exc.code, "category": exc.category}, status=400)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Question upload failed | unexpected error: %s", exc)
                return JsonResponse({"error": "Question extraction failed. Please retry with a clean file."}, status=500)
            questions.extend(extracted)
            source_filename = upload.name
    else:
        return JsonResponse({"error": "Provide text or at least one file"}, status=400)

    if not questions:
        logger.info("Question extraction empty | category=extraction | reason=no_questions")
        return JsonResponse({"error": "No questions detected in the provided input."}, status=400)

    # Detect duplicate questions (to avoid noisy mappings)
    # Detect duplicates to avoid mapping noise and preserve FCAR integrity.
    normalized = [q.strip().lower() for q in questions]
    duplicates = {q for q in normalized if normalized.count(q) > 1}
    if duplicates:
        logger.info("Duplicate questions detected | category=extraction | count=%s", len(duplicates))
        return JsonResponse({
            "error": "Duplicate questions detected. Please remove duplicates and retry.",
            "details": list(duplicates)[:5],
        }, status=400)

    saved_questions = []
    try:
        with transaction.atomic():
            for q_text in questions:
                q = Question.objects.create(
                    text=q_text,
                    assessment_type=assessment_type,
                    source_filename=source_filename,
                )
                saved_questions.append(_serialize_question(q))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Question save failed | db | %s", exc)
        return JsonResponse({"error": "Database error while saving questions. Please retry."}, status=500)

    logger.info("Question upload success | category=extraction | count=%s", len(saved_questions))
    return JsonResponse({"questions": saved_questions})


@csrf_exempt
def run_mapping(request):
    user, redirect_resp = _require_session(request)
    if redirect_resp:
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    assessment_type = request.POST.get("assessment_type")
    course = (request.POST.get("course") or "").strip()
    map_all = request.POST.get("map_all", "false").lower() in {"1", "true", "yes", "on"}

    questions_qs = Question.objects.all()
    if assessment_type:
        questions_qs = questions_qs.filter(assessment_type=assessment_type)
    if not map_all:
        questions_qs = questions_qs.filter(mappings__isnull=True)

    questions = list(questions_qs)
    clos_qs = CLO.objects.all()
    if course:
        filtered = clos_qs.filter(course__iexact=course)
        clos_qs = filtered if filtered.exists() else clos_qs  # fallback to all if none match
    clos = list(clos_qs)

    try:
        mapping_results = map_questions_to_clos(
            questions,
            clos,
            course_name=course,
        )
    except MappingError as exc:
        logger.warning("Mapping skipped | %s", exc.user_message)
        return JsonResponse({"error": exc.user_message, "code": exc.code, "category": exc.category}, status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Mapping failed | unexpected error: %s", exc)
        return JsonResponse({"error": "Mapping failed. Please retry."}, status=500)

    payload = []
    try:
        with transaction.atomic():
            for result in mapping_results:
                manual_exists = CLOMapping.objects.filter(question=result.question, auto_mapped=False).exists()
                if manual_exists:
                    payload.append({
                        "question_id": result.question.id,
                        "question_text": result.question.text,
                        "clo_id": None,
                        "clo_code": None,
                        "clo_description": None,
                        "score": None,
                        "auto_mapped": False,
                        "status": "manual_override",
                        "reason": "Manual mapping already set; auto-mapping skipped.",
                    })
                    continue

                if result.status == "mapped" and result.clo:
                    CLOMapping.objects.update_or_create(
                        question=result.question,
                        auto_mapped=True,
                        defaults={
                            "clo": result.clo,
                            "score": result.score or 0.0,
                            "auto_mapped": True,
                        }
                    )

                payload.append(
                    {
                        "question_id": result.question.id,
                        "question_text": result.question.text,
                        "clo_id": result.clo.id if result.clo else None,
                        "clo_code": result.clo.code if result.clo else None,
                        "clo_description": result.clo.description if result.clo else None,
                        "score": result.score,
                        "auto_mapped": result.status == "mapped",
                        "status": result.status,
                        "reason": result.reason,
                    }
                )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Mapping save failed | db | %s", exc)
        return JsonResponse({"error": "Database error while saving mappings. Please retry."}, status=500)
    logger.info("Mapping completed | category=mapping | total=%s", len(payload))
    return JsonResponse({"results": payload})


@csrf_exempt
def save_manual_mapping(request):
    user, redirect_resp = _require_session(request)
    if redirect_resp:
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    data = request.POST or None
    if not data or not data.dict():
        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception:
            data = {}

    question_id = data.get("question_id")
    clo_id = data.get("clo_id")
    score = data.get("score")

    if not question_id or not clo_id:
        return JsonResponse({"error": "question_id and clo_id are required"}, status=400)

    try:
        question = Question.objects.get(id=question_id)
        clo = CLO.objects.get(id=clo_id)
    except (Question.DoesNotExist, CLO.DoesNotExist):
        return JsonResponse({"error": "Invalid question or CLO"}, status=404)

    try:
        numeric_score = float(score) if score is not None else 1.0
    except (TypeError, ValueError):
        numeric_score = 1.0

    try:
        with transaction.atomic():
            mapping, _ = CLOMapping.objects.update_or_create(
                question=question,
                auto_mapped=False,
                defaults={
                    "clo": clo,
                    "score": numeric_score,
                    "auto_mapped": False,
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Manual mapping save failed | db | %s", exc)
        return JsonResponse({"error": "Database error while saving mapping. Please retry."}, status=500)
    # Manual override is preserved to keep faculty control over borderline mappings.
    logger.info("Manual override saved | category=manual_override | question=%s clo=%s", question.id, clo.code)

    return JsonResponse(
        {
            "status": "saved",
            "question_id": question.id,
            "clo_id": clo.id,
            "clo_code": clo.code,
            "score": numeric_score,
        }
    )


@csrf_exempt
def clear_data(request):
    user, redirect_resp = _require_session(request)
    if redirect_resp:
        return JsonResponse({"detail": "Unauthorized"}, status=401)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    scope = request.POST.get("scope", "all")
    try:
        with transaction.atomic():
            CLOMapping.objects.all().delete()
            if scope in {"all", "questions"}:
                Question.objects.all().delete()
            if scope in {"all", "clos"}:
                CLO.objects.all().delete()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Clear data failed | db | %s", exc)
        return JsonResponse({"error": "Database error while clearing data. Please retry."}, status=500)

    logger.info("CLO mapper cleared | category=db | scope=%s", scope)
    return JsonResponse({"status": "cleared", "scope": scope})


def get_current_state(request):
    user, redirect_resp = _require_session(request)
    if redirect_resp:
        return JsonResponse({"detail": "Unauthorized"}, status=401)

    clos = [_serialize_clo(clo) for clo in CLO.objects.all()]
    questions = [_serialize_question(q) for q in Question.objects.all()]
    return JsonResponse({"clos": clos, "questions": questions})
