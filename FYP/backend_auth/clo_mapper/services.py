"""Extraction and mapping utilities for the CLO Mapper module."""

from __future__ import annotations

import io
import re
import string
from dataclasses import dataclass
from typing import Sequence

from docx import Document
from docx.oxml.exceptions import InvalidXmlError
from pdfminer.high_level import extract_text
from PIL import Image
import pytesseract
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

try:
    from nltk.corpus import stopwords
except Exception:  # pragma: no cover - optional dependency errors handled below
    stopwords = None  # type: ignore

from .models import CLO, Question


class ExtractionError(Exception):
    """Raised when CLO/Question extraction fails with a user-friendly reason."""

    def __init__(self, code: str, message: str, category: str = "extraction"):
        super().__init__(message)
        self.code = code
        self.category = category
        self.user_message = message


class MappingError(Exception):
    """Raised when mapping cannot proceed safely."""

    def __init__(self, code: str, message: str, category: str = "mapping"):
        super().__init__(message)
        self.code = code
        self.category = category
        self.user_message = message

STOPWORDS: set[str] = set()
if stopwords:
    try:  # pragma: no cover - runtime initialization
        STOPWORDS = set(stopwords.words("english"))
    except Exception:
        STOPWORDS = set()


@dataclass
class MappingResult:
    question: Question
    clo: CLO | None
    score: float | None
    status: str
    reason: str | None
    candidates: list[tuple[CLO, float]] | None


CLO_PATTERN = re.compile(r"^(CLO\s*\d+|CLO\d+)", re.IGNORECASE)
QUESTION_START_PATTERN = re.compile(r"^(q?\d+\s*[\).:-]|q\s?\d+\s*)", re.IGNORECASE)
INLINE_QUESTION_PATTERN = re.compile(r"q?\d+\s*[\).:-]", re.IGNORECASE)


def _clean_text(text: str) -> str:
    cleaned = text.replace("\r", "\n")
    cleaned = cleaned.lower()
    cleaned = cleaned.translate(str.maketrans("", "", string.punctuation))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _split_questions(raw_text: str) -> list[str]:
    text = raw_text.replace("\r", "\n")
    raw_lines = text.split("\n")
    lines = [ln.strip() for ln in raw_lines if ln.strip()]
    questions: list[str] = []
    buffer: list[str] = []

    for line in lines:
        if QUESTION_START_PATTERN.match(line):
            if buffer:
                questions.append(" ".join(buffer).strip())
                buffer = []
            cleaned_line = QUESTION_START_PATTERN.sub("", line).strip()
            if cleaned_line:
                buffer.append(cleaned_line)
        else:
            buffer.append(line)
    if buffer:
        questions.append(" ".join(buffer).strip())

    # If a single-line payload contains multiple numbered prompts (e.g., "Q1... Q2..."), split by markers.
    if len(questions) <= 1:
        spans = list(INLINE_QUESTION_PATTERN.finditer(text))
        if len(spans) > 1:
            questions = []
            for idx, match in enumerate(spans):
                start = match.end()
                end = spans[idx + 1].start() if idx + 1 < len(spans) else len(text)
                chunk = text[start:end].strip()
                if chunk:
                    questions.append(chunk)

    # If still one blob but multiple non-empty lines were provided, treat each line as a question.
    if len(questions) <= 1 and len(lines) > 1:
        questions = lines

    if not questions:
        raw_split = re.split(r"\?\s+", text)
        questions = [item.strip() for item in raw_split if item.strip()]
    return [q for q in questions if len(q) > 2]


def extract_clos_from_docx(file_obj: io.BytesIO) -> list[dict]:
    file_obj.seek(0)
    try:
        doc = Document(file_obj)
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("docx_parse_failed", "DOCX parsing failed. Please upload a valid Word file.") from exc
    clos: list[dict] = []

    def _is_clo_header(row_cells) -> bool:
        headers = [c.text.strip().lower() for c in row_cells]
        return any("clo" in h for h in headers) and any("desc" in h for h in headers)

    # Look for the first table that actually contains CLO rows.
    for table in doc.tables:
        table_clos: list[dict] = []
        try:
            rows = table.rows
            start_idx = 1 if rows and _is_clo_header(rows[0].cells) else 0
            for row in rows[start_idx:]:
                cells = row.cells
                if len(cells) < 2:
                    continue
                code = cells[0].text.strip()
                description = cells[1].text.strip()
                domain = cells[2].text.strip() if len(cells) > 2 else ""
                bt_level = cells[3].text.strip() if len(cells) > 3 else ""
                if code and description and CLO_PATTERN.match(code):
                    table_clos.append(
                        {
                            "code": code,
                            "description": description,
                            "domain": domain,
                            "bt_level": bt_level,
                        }
                    )
        except InvalidXmlError as exc:
            raise ExtractionError("docx_table_corrupt", "DOCX table data is corrupted. Please re-export the file.") from exc

        if table_clos:
            clos = table_clos
            break

    if clos:
        return clos

    # Fallback: scan paragraphs
    for para in doc.paragraphs:
        text = para.text.strip()
        if CLO_PATTERN.match(text):
            parts = text.split(":", 1)
            code = parts[0].strip()
            description = parts[1].strip() if len(parts) > 1 else text
            clos.append({"code": code, "description": description})
    return clos


def extract_clos_from_pdf(file_obj: io.BytesIO) -> list[dict]:
    file_obj.seek(0)
    try:
        text = extract_text(file_obj) or ""
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("pdf_parse_failed", "PDF parsing failed. Please upload a valid PDF file.") from exc
    clos: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if CLO_PATTERN.match(line):
            parts = re.split(r"\s{2,}|\t|:", line, maxsplit=3)
            code = parts[0].strip()
            description = parts[1].strip() if len(parts) > 1 else line
            domain = parts[2].strip() if len(parts) > 2 else ""
            bt_level = parts[3].strip() if len(parts) > 3 else ""
            clos.append({"code": code, "description": description, "domain": domain, "bt_level": bt_level})
    return clos


def extract_questions_from_docx(file_obj: io.BytesIO) -> list[str]:
    file_obj.seek(0)
    try:
        doc = Document(file_obj)
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("docx_parse_failed", "DOCX parsing failed. Please upload a valid Word file.") from exc
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    text = "\n".join(paragraphs)
    return _split_questions(text)


def extract_questions_from_pdf(file_obj: io.BytesIO) -> list[str]:
    file_obj.seek(0)
    try:
        text = extract_text(file_obj) or ""
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("pdf_parse_failed", "PDF parsing failed. Please upload a valid PDF file.") from exc
    return _split_questions(text)


def extract_questions_from_image(file_obj: io.BytesIO) -> list[str]:
    file_obj.seek(0)
    try:
        image = Image.open(file_obj)
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("image_unreadable", "Image is unreadable. Please upload a clear image.") from exc
    try:
        raw_text = pytesseract.image_to_string(image)
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("ocr_failed", "OCR failed to read the image. Try a clearer scan.") from exc
    questions = _split_questions(raw_text)
    if not questions:
        raise ExtractionError("ocr_no_text", "OCR found no readable text. Try a clearer image.")
    return questions


def extract_questions_from_text(raw_text: str) -> list[str]:
    return _split_questions(raw_text)


def _extract_keywords(text: str) -> set[str]:
    return {tok for tok in re.split(r"[^a-zA-Z0-9]+", text.lower()) if len(tok) > 2}


def _build_course_signals(clos: Sequence[CLO], course_name: str = "") -> set[str]:
    keywords: set[str] = set()
    for clo in clos:
        keywords |= _extract_keywords(clo.description or "")
        keywords |= _extract_keywords(clo.code or "")
        keywords |= _extract_keywords(clo.course or "")
    keywords |= _extract_keywords(course_name or "")
    return {kw for kw in keywords if kw}


def map_questions_to_clos(
    questions: Sequence[Question],
    clos: Sequence[CLO],
    course_name: str = "",
    min_similarity: float = 0.05,
    ambiguous_delta: float = 0.02,
    fallback_prog_similarity: float = 0.0,
    off_topic_floor: float = 0.0,
) -> list[MappingResult]:
    """Return mappings when similarity is meaningful and question is in-scope.

    - Off-topic questions (no programming signal and very low similarity) are skipped.
    - Clearly programming questions can map with a lower fallback threshold to avoid
      dropping basic prompts like "what is programming" while still avoiding noise.
    """
    if not questions:
        raise MappingError("no_questions", "No questions available for mapping.")
    if not clos:
        raise MappingError("no_clos", "No CLOs available for mapping.")

    def _clo_text(clo: CLO) -> str:
        parts = [clo.code or "", clo.description or "", clo.domain or "", clo.bt_level or "", clo.course or ""]
        return _clean_text(" ".join(parts))

    clo_texts = [_clo_text(clo) for clo in clos]
    question_texts = [_clean_text(q.text) for q in questions]

    # If every string cleans down to empty, bail early to avoid zero-vectors.
    if not any(clo_texts) or not any(question_texts):
        return []

    stop_words = None  # keep all tokens to avoid over-pruning small texts
    vectorizer = TfidfVectorizer(stop_words=stop_words, ngram_range=(1, 2), max_df=1.0, min_df=1)
    tfidf = vectorizer.fit_transform(clo_texts + question_texts)
    clo_matrix = tfidf[: len(clo_texts)]
    question_matrix = tfidf[len(clo_texts) :]
    similarity = cosine_similarity(question_matrix, clo_matrix)

    course_signals = _build_course_signals(clos, course_name)

    results: list[MappingResult] = []
    assigned_clos: set[int] = set()
    pending: list[dict] = []

    for idx, row in enumerate(similarity):
        if row.size == 0:
            continue
        raw_question = questions[idx].text or ""
        # If no course signals could be derived, treat as in-course to avoid over-filtering.
        has_course_signal = True if not course_signals else bool(_extract_keywords(raw_question) & course_signals)

        best_idx = int(row.argmax())
        best_score = float(row[best_idx])

        # Minimal gating: only skip when explicitly off-topic (no signals) and zero similarity.
        if not has_course_signal and best_score <= off_topic_floor:
            results.append(
                MappingResult(
                    question=questions[idx],
                    clo=None,
                    score=float(best_score),
                    status="unmapped",
                    reason="Off-topic or very low similarity.",
                    candidates=None,
                )
            )
            continue

        # If the question appears in-course, allow mapping even with very low similarity.
        effective_min = 0.0 if has_course_signal else min_similarity
        ranked = sorted(enumerate(row.tolist()), key=lambda item: item[1], reverse=True)
        pending.append(
            {
                "idx": idx,
                "ranked": ranked,
                "effective_min": effective_min,
            }
        )

    # First pass: try to distribute across distinct CLOs when possible.
    pending.sort(key=lambda item: item["ranked"][0][1] if item["ranked"] else 0.0, reverse=True)
    unassigned: list[dict] = []
    for item in pending:
        assigned = False
        ranked = item["ranked"]
        best_idx, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        if best_score < item["effective_min"]:
            results.append(
                MappingResult(
                    question=questions[item["idx"]],
                    clo=None,
                    score=float(best_score),
                    status="low_confidence",
                    reason="Similarity too low for auto-mapping.",
                    candidates=None,
                )
            )
            continue
        if abs(best_score - second_score) <= ambiguous_delta:
            candidates = [(clos[best_idx], float(best_score)), (clos[ranked[1][0]], float(second_score))]
            results.append(
                MappingResult(
                    question=questions[item["idx"]],
                    clo=None,
                    score=float(best_score),
                    status="ambiguous",
                    reason="Two CLOs have similar confidence.",
                    candidates=candidates,
                )
            )
            continue
        for clo_idx, score in item["ranked"]:
            if score < item["effective_min"]:
                break
            if clo_idx in assigned_clos:
                continue
            assigned_clos.add(clo_idx)
            results.append(
                MappingResult(
                    question=questions[item["idx"]],
                    clo=clos[clo_idx],
                    score=float(score),
                    status="mapped",
                    reason=None,
                    candidates=None,
                )
            )
            assigned = True
            break
        if not assigned:
            unassigned.append(item)

    # Second pass: allow reuse once all CLOs are taken or no unique match exists.
    for item in unassigned:
        ranked = item["ranked"]
        if not ranked:
            results.append(
                MappingResult(
                    question=questions[item["idx"]],
                    clo=None,
                    score=None,
                    status="unmapped",
                    reason="No viable CLO match found.",
                    candidates=None,
                )
            )
            continue
        best_idx, best_score = ranked[0]
        if best_score < item["effective_min"]:
            results.append(
                MappingResult(
                    question=questions[item["idx"]],
                    clo=None,
                    score=float(best_score),
                    status="low_confidence",
                    reason="Similarity too low for auto-mapping.",
                    candidates=None,
                )
            )
            continue
        results.append(
            MappingResult(
                question=questions[item["idx"]],
                clo=clos[best_idx],
                score=float(best_score),
                status="duplicate_mapping",
                reason="CLO already used; needs manual confirmation.",
                candidates=None,
            )
        )

    return results
