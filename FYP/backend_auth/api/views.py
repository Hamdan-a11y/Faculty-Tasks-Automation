"""API views for Faculty Tasks Automation React frontend."""

from django.conf import settings
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    renderer_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from auth_app.views.helpers import SESSION_KEY
from .authentication import FacultySessionAuthentication


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@renderer_classes([JSONRenderer])
def health_check(request):
    """Public health check endpoint returning service status."""
    return Response({"status": "ok"}, status=status.HTTP_200_OK)


@ensure_csrf_cookie
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@renderer_classes([JSONRenderer])
def csrf_token_view(request):
    """Public endpoint that sets the CSRF cookie and returns the token in JSON."""
    token = get_token(request)
    return Response({"csrfToken": token}, status=status.HTTP_200_OK)


@api_view(["GET"])
@authentication_classes([FacultySessionAuthentication])
@permission_classes([IsAuthenticated])
@renderer_classes([JSONRenderer])
def current_user_api(request):
    """Protected endpoint returning the currently logged-in faculty user profile."""
    user = request.user
    return Response(
        {
            "id": user.id,
            "name": user.name,
            "email": user.email,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@authentication_classes([FacultySessionAuthentication])
@permission_classes([AllowAny])
@renderer_classes([JSONRenderer])
def logout_api(request):
    """Logout endpoint that flushes the user session and clears session cookies.
    
    When a session exists, FacultySessionAuthentication enforces CSRF validation.
    """
    request.session.flush()
    response = Response(
        {"status": "ok", "message": "Logged out successfully"},
        status=status.HTTP_200_OK,
    )
    response.delete_cookie(SESSION_KEY)
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return response
