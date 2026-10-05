from django.urls import path
from . import views

app_name = "api"

urlpatterns = [
    path("health/", views.health_check, name="health"),
    path("csrf/", views.csrf_token_view, name="csrf"),
    path("auth/me/", views.current_user_api, name="current_user"),
    path("auth/logout/", views.logout_api, name="logout"),
]
