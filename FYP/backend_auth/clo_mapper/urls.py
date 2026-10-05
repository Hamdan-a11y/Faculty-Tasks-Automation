from django.urls import path

from . import views

app_name = "clo_mapper"

urlpatterns = [
    path("", views.index, name="index"),
    path("upload_clos/", views.upload_clos, name="upload_clos"),
    path("upload_questions/", views.upload_questions, name="upload_questions"),
    path("run_mapping/", views.run_mapping, name="run_mapping"),
    path("save_manual_mapping/", views.save_manual_mapping, name="save_manual_mapping"),
    path("state/", views.get_current_state, name="state"),
    path("clear/", views.clear_data, name="clear"),
]
