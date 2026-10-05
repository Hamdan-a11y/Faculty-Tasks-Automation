from django.urls import path
from . import views

app_name = "final_academic_insights"

urlpatterns = [
    path("", views.index, name="index"),
    path("upload-excel/", views.upload_excel, name="upload_excel"),
    path("report/", views.report, name="report"),
    path("chart-data/", views.chart_data, name="chart_data"),
    path("download-excel/", views.download_excel, name="download_excel"),
]
