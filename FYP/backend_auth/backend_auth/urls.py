from django.contrib import admin
from django.urls import include, path

from auth_app import views as auth_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("google/connect/", auth_views.google_services_connect, name="google-services-connect-root"),
    path("google/callback/", auth_views.google_services_callback, name="google-services-callback-root"),
    path("google/status/", auth_views.google_services_status, name="google-services-status-root"),
    path("google/drive/upload/", auth_views.google_drive_upload, name="google-drive-upload-root"),
    path("google/drive/files/", auth_views.google_drive_files, name="google-drive-files-root"),
    path("google/drive/files/<str:file_id>/", auth_views.google_drive_delete, name="google-drive-delete-root"),
    path("google/classroom/create-course/", auth_views.google_classroom_create_course, name="google-classroom-create-course-root"),
    path("google/classroom/courses/", auth_views.google_classroom_courses, name="google-classroom-courses-root"),
    path("google/classroom/coursework/", auth_views.google_classroom_coursework, name="google-classroom-coursework-root"),
    path("google/classroom/material/upload/", auth_views.google_classroom_material_upload, name="google-classroom-material-upload-root"),
    path("google/classroom/submissions/", auth_views.google_classroom_submissions, name="google-classroom-submissions-root"),
    path("auth/", include(("auth_app.urls", "auth_app"), namespace="auth_app")),
    path("class-progress/", include("class_progress.urls")),
    path("clomapper/", include("clo_mapper.urls")),
    path("auto-reminders/", include("auto_reminder.urls")),
    path("final-academic-insights/", include("final_academic_insights.urls")),
    path("academic-report/", include("academic_report.urls")),
    path("", auth_views.home_redirect, name="home"),
]
