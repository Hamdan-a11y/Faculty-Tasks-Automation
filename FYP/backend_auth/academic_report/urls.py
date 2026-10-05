from django.urls import path

from . import views

app_name = "academic_report"

urlpatterns = [
    path("", views.index, name="index"),
    path("upload-excel/", views.upload_excel, name="upload_excel"),
    path("report/<int:run_id>/", views.report, name="report"),
    path("download-pdf/<int:run_id>/", views.download_pdf, name="download_pdf"),
    path("download-excel/<int:run_id>/", views.download_excel, name="download_excel"),
]
