"""Views package for auth_app re-exporting all views and helpers for full backwards compatibility."""

# Shared helpers and constants
from .helpers import (
    STATIC_WHITELIST,
    ALLOWED_DOMAIN_SUFFIXES,
    env_whitelist,
    WHITELISTED_EMAILS,
    SESSION_KEY,
    CHROME_PROFILE_PATH,
    PORTAL_URL,
    LOGIN_URL,
    log_auth,
    log_google,
    _parse_google_error,
    _google_error_response,
    _get_session_user,
    _is_allowed_email,
    _get_or_refresh_service_token,
    _has_required_service_scopes,
    _google_api_request,
    _is_remote_debugger_running,
    _is_port_open,
    get_driver_path,
    _wait_and_click,
    _find_chrome_executable,
    set_value_js,
    _normalize_header_text,
    _is_registration_header,
    _is_student_name_header,
    _parse_json_request_body,
)

# Drive views
from .drive import (
    google_drive_upload,
    google_drive_files,
    google_drive_delete,
)

# Auth, session, Google OAuth & portal pages
from .auth import (
    home_redirect,
    login_page,
    unauthorized_page,
    logout_view,
    google_services_page,
    google_services_connect,
    google_services_callback,
    google_services_status,
    google_services_disconnect,
    auto_reminder_page,
    auto_alert_page,
    dashboard_page,
    current_user,
    google_login,
    google_callback,
)

# Attendance exemption views
from .attendance import (
    attendance_exemption_page,
    attendance_fetch,
    attendance_process_exemption,
    attendance_close_portal,
)

# Classroom views
from .classroom import (
    google_classroom_courses,
    google_classroom_create_course,
    google_classroom_coursework,
    google_classroom_material_upload,
    google_classroom_submissions,
)

# Student outreach views
from .outreach import (
    student_outreach_assistant_page,
    _outreach_defaults,
    _outreach_students_for_run,
    _is_outreach_critical,
    _outreach_counts,
    _serialize_outreach_student,
    _compose_outreach_email,
    _dispatch_outreach_student_email,
    student_outreach_at_risk_students,
    student_outreach_preview_email,
    student_outreach_send_single,
    student_outreach_send_faculty_summary,
    student_outreach_email_history,
    student_outreach_resend_failed,
)

# Midterm evaluation views and constants
from .midterm import (
    MIDTERM_GRADE_ORDER,
    MIDTERM_PERFORMANCE_ORDER,
    MIDTERM_GRADE_SCALE,
    _coerce_midterm_numeric,
    _is_midterm_marks_header,
    _extract_marks_cap,
    _parse_midterm_students_from_file,
    _grade_for_percentage,
    _performance_band_for_score,
    _risk_status_for_score,
    _evaluate_midterm_run,
    _serialize_midterm_students,
    _resolve_midterm_run,
    midterm_academic_evaluator_page,
    midterm_upload_excel,
    midterm_extract_data,
    midterm_evaluate_data,
    midterm_generate_report,
)

# Marks bridge views and constant
from .marks_bridge import (
    ZABDESK_MARKS_TYPE_CONFIG,
    zabdesk_marks_bridge_page,
    zabdesk_auto_login,
    zabdesk_upload_excel,
    zabdesk_extract_excel_data,
    _normalize_name_for_match,
    _normalize_reg_for_match,
    _coerce_mark_value,
    _find_mark_column_index,
    _load_zabdesk_rows,
    _extract_zabdesk_students_with_marks,
    zabdesk_enter_marks_in_recap_sheet,
)

# Curriculum views
from .curriculum import (
    curriculum_page,
    parse_course_outline,
    extract_docx_fields,
    extract_smart_content,
    extract_content_by_header,
    open_portal_browser,
    process_curriculum_automation,
    upload_curriculum,
    parse_curriculum_document,
    run_curriculum_automation,
)
