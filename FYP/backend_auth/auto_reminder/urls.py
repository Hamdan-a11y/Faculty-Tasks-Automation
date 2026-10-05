"""URLs for assignment alerts (minimal scope)."""

from django.urls import path
from . import views

app_name = "auto_reminder"

urlpatterns = [
    path("assignment-alerts/", views.assignment_alerts, name="assignment_alerts"),
    path("assignment-alerts/toggle/", views.assignment_alerts_toggle, name="assignment_alerts_toggle"),
    path("assignment-alerts/run-now/", views.assignment_alerts_run_now, name="assignment_alerts_run_now"),
    path("assignment-alerts/list/", views.assignment_alerts_list, name="assignment_alerts_list"),
    path("assignment-alerts/send-now/", views.assignment_alerts_send_now, name="assignment_alerts_send_now"),
    path("assignment-alerts/create-events/", views.assignment_alerts_create_events, name="assignment_alerts_create_events"),
    path("assignment-alerts/delete-events/", views.assignment_alerts_delete_events, name="assignment_alerts_delete_events"),
    path("assignment-alerts/mark-complete/", views.assignment_alerts_mark_complete, name="assignment_alerts_mark_complete"),
    path("assignment-alerts/delete/", views.assignment_alerts_delete, name="assignment_alerts_delete"),
    path("assignment-alerts/create/", views.assignment_alerts_create, name="assignment_alerts_create"),
]
