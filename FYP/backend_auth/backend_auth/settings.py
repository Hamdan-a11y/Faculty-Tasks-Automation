"""
Django settings for backend_auth project.

Session-based Google OAuth2 authentication only.
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def parse_hosts(raw: str | None) -> list[str]:
    if not raw:
        return ["localhost", "127.0.0.1"]
    return [item.strip() for item in raw.split(",") if item.strip()]


SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("The SECRET_KEY environment variable must be set.")
DEBUG = get_bool("DEBUG", True)
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5500")
ALLOWED_HOSTS = parse_hosts(os.getenv("ALLOWED_HOSTS")) + ["testserver"]
CSRF_TRUSTED_ORIGINS = [FRONTEND_ORIGIN]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "auth_app",
    "class_progress",
    "clo_mapper",
    "auto_reminder",
    "final_academic_insights",
    "academic_report",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "backend_auth.urls"

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            # Explicitly add the auth_app templates folder
            os.path.join(BASE_DIR, 'auth_app', 'templates'),
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = "backend_auth.wsgi.application"
ASGI_APPLICATION = "backend_auth.asgi.application"


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ImproperlyConfigured("The DATABASE_URL environment variable must be set.")

db_config = dj_database_url.parse(
    DATABASE_URL,
    conn_max_age=600,
    conn_health_checks=True,
)

# Enforce sslmode=require when connecting to non-local databases
db_host = (db_config.get("HOST") or "").lower()
if db_host and db_host not in ("localhost", "127.0.0.1"):
    db_config.setdefault("OPTIONS", {})["sslmode"] = "require"

# Supabase transaction pooler (port 6543) requires disabling server-side cursors
if str(db_config.get("PORT")) == "6543":
    db_config["DISABLE_SERVER_SIDE_CURSORS"] = True

DATABASES = {
    "default": db_config,
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Karachi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "auth_app" / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# CORS_ALLOWED_ORIGINS = [FRONTEND_ORIGIN]
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = "Lax"

LOGIN_URL = "/auth/login/"
LOGIN_REDIRECT_URL = "/auth/dashboard/"
LOGOUT_REDIRECT_URL = "/auth/login/"

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
REQUIRED_GOOGLE_SERVICE_SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me",
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/classroom.announcements",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials",
    "https://www.googleapis.com/auth/drive.file",
    # Added for calendar event creation and Gmail API send
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]
default_scopes_raw = " ".join(REQUIRED_GOOGLE_SERVICE_SCOPES)
env_scopes_raw = os.getenv("GOOGLE_SERVICE_SCOPES", default_scopes_raw)

def _unique_scopes(scopes_str: str) -> list[str]:
    seen = set()
    ordered = []
    for scope in scopes_str.split():
        if scope and scope not in seen:
            seen.add(scope)
            ordered.append(scope)
    for req in REQUIRED_GOOGLE_SERVICE_SCOPES:
        if req not in seen:
            ordered.append(req)
            seen.add(req)
    return ordered

GOOGLE_SERVICE_SCOPES = _unique_scopes(env_scopes_raw)
GOOGLE_SERVICES_REDIRECT_URI = os.getenv("GOOGLE_SERVICES_REDIRECT_URI", GOOGLE_REDIRECT_URI)

# Auto reminder email-only switch (skip Classroom announcements when True)
AUTO_REMINDER_EMAIL_ONLY = get_bool("AUTO_REMINDER_EMAIL_ONLY", False)

# ============================================================================
# EMAIL CONFIGURATION (GMAIL SMTP for Auto Reminder)
# ============================================================================

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USE_TLS = get_bool("EMAIL_USE_TLS", True)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", EMAIL_HOST_USER)

# Student Outreach Assistant (Mailtrap sandbox SMTP)
MAILTRAP_SMTP_HOST = os.getenv("MAILTRAP_SMTP_HOST", "")
MAILTRAP_SMTP_PORT = int(os.getenv("MAILTRAP_SMTP_PORT", 2525))
MAILTRAP_SMTP_USER = os.getenv("MAILTRAP_SMTP_USER", "")
MAILTRAP_SMTP_PASSWORD = os.getenv("MAILTRAP_SMTP_PASSWORD", "")
MAILTRAP_SMTP_TLS = get_bool("MAILTRAP_SMTP_TLS", True)
MAILTRAP_FROM_EMAIL = os.getenv("MAILTRAP_FROM_EMAIL", "no-reply@szabist-isb.edu.pk")
OUTREACH_FACULTY_SUMMARY_EMAIL = os.getenv("OUTREACH_FACULTY_SUMMARY_EMAIL", EMAIL_HOST_USER)
OUTREACH_STUDENT_EMAIL_DOMAIN = os.getenv("OUTREACH_STUDENT_EMAIL_DOMAIN", "szabist-isb.edu.pk")
OUTREACH_INSTITUTION_NAME = os.getenv("OUTREACH_INSTITUTION_NAME", "SZABIST Islamabad")

# Logging configuration
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "file": {
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "logs" / "auto_reminder.log",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "auto_reminder": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "clo_mapper": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
