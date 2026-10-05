"""Utility helpers for Student Outreach Assistant email composition and SMTP delivery."""

import re
import unicodedata

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

DEFAULT_OUTREACH_SUBJECT_TEMPLATE = (
    "Academic Support Alert - {course_name} | {student_name} ({registration_no})"
)

DEFAULT_OUTREACH_BODY_TEMPLATE = """Dear {student_name} ({registration_no}),

We hope you are doing well.

Our midterm performance review indicates that your current academic standing is "{risk_status}".
Your current marks: {midterm_marks}
Current grade: {grade_letter}

This message is sent with care to encourage you to seek academic support as early as possible.
Please contact your course instructor, attend consultation hours, and prepare a focused recovery plan.

Recommended immediate actions:
1. Meet your faculty advisor this week.
2. Review weak topics and complete pending practice.
3. Join peer study sessions and request mentoring support.

Course: {course_name}
Faculty: {faculty_name}
Institution: {institution_name}

Regards,
Student Outreach Assistant
Faculty Tasks Automation
"""

_TEMPLATE_KEY_PATTERN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}|\{([a-z_]+)\}")


def _normalize_token(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    token = re.sub(r"[^a-zA-Z0-9]+", "", ascii_value.lower())
    return token


def build_generated_student_email(student_name, registration_no):
    """Build deterministic dummy student email for sandbox testing."""
    cleaned_name = " ".join(str(student_name or "").split())
    name_parts = [piece for piece in cleaned_name.split(" ") if piece]

    first_name = _normalize_token(name_parts[0]) if name_parts else "student"
    last_name = _normalize_token(name_parts[-1]) if len(name_parts) > 1 else "student"
    reg = _normalize_token(registration_no) or "unknown"

    domain = getattr(settings, "OUTREACH_STUDENT_EMAIL_DOMAIN", "szabist-isb.edu.pk")
    domain = str(domain or "szabist-isb.edu.pk").strip().lower().lstrip("@")
    return f"{first_name}.{last_name}.{reg}@{domain}"


def build_student_context(
    student_name,
    registration_no,
    midterm_marks,
    risk_status,
    grade_letter,
    course_name,
    faculty_name,
    institution_name,
):
    if midterm_marks is None:
        marks_label = "N/A"
    else:
        marks_label = str(round(float(midterm_marks), 2))

    return {
        "student_name": str(student_name or "Student").strip(),
        "registration_no": str(registration_no or "N/A").strip(),
        "midterm_marks": marks_label,
        "risk_status": str(risk_status or "At Risk").strip(),
        "grade_letter": str(grade_letter or "N/A").strip(),
        "course_name": str(course_name or "Course").strip(),
        "faculty_name": str(faculty_name or "Faculty").strip(),
        "institution_name": str(institution_name or "SZABIST Islamabad").strip(),
    }


def render_outreach_template(template_text, context):
    """Render a lightweight placeholder template using {key} or {{key}} patterns."""
    source = str(template_text or "")

    def _replace(match):
        key = match.group(1) or match.group(2)
        return str(context.get(key, ""))

    return _TEMPLATE_KEY_PATTERN.sub(_replace, source)


def get_default_outreach_templates():
    return {
        "subject_template": DEFAULT_OUTREACH_SUBJECT_TEMPLATE,
        "body_template": DEFAULT_OUTREACH_BODY_TEMPLATE,
    }


def _mailtrap_config():
    host = str(getattr(settings, "MAILTRAP_SMTP_HOST", "") or "").strip()
    username = str(getattr(settings, "MAILTRAP_SMTP_USER", "") or "").strip()
    password = str(getattr(settings, "MAILTRAP_SMTP_PASSWORD", "") or "").strip()

    if not host or not username or not password:
        raise ValueError(
            "Mailtrap SMTP is not configured. Set MAILTRAP_SMTP_HOST, MAILTRAP_SMTP_USER, and "
            "MAILTRAP_SMTP_PASSWORD in backend_auth/.env."
        )

    return {
        "host": host,
        "port": int(getattr(settings, "MAILTRAP_SMTP_PORT", 2525)),
        "use_tls": bool(getattr(settings, "MAILTRAP_SMTP_TLS", True)),
        "username": username,
        "password": password,
        "from_email": str(
            getattr(settings, "MAILTRAP_FROM_EMAIL", "")
            or getattr(settings, "EMAIL_FROM_ADDRESS", "")
            or username
        ).strip(),
    }


def get_outreach_mail_connection():
    cfg = _mailtrap_config()
    return get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend",
        host=cfg["host"],
        port=cfg["port"],
        username=cfg["username"],
        password=cfg["password"],
        use_tls=cfg["use_tls"],
        fail_silently=False,
    )


def send_outreach_email(recipient_email, subject, body, connection=None):
    cfg = _mailtrap_config()
    recipient = str(recipient_email or "").strip()
    if not recipient:
        raise ValueError("Recipient email is required.")

    email = EmailMultiAlternatives(
        subject=str(subject or "Academic Alert"),
        body=str(body or ""),
        from_email=cfg["from_email"],
        to=[recipient],
        connection=connection or get_outreach_mail_connection(),
    )
    email.send(fail_silently=False)
    return True
