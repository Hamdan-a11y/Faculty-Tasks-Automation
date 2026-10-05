from django.urls import path
from . import views

app_name = 'class_progress'

urlpatterns = [
    path('', views.tracker_view, name='tracker'),
    path('login-zabdesk/', views.login_view, name='login_zabdesk'),
    path('auto-fill/', views.auto_fill_view, name='auto_fill'),
    path('extract-noticeboard/', views.extract_noticeboard_view, name='extract_noticeboard'),
]
