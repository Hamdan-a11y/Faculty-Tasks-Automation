"""Custom session authentication for Faculty Tasks Automation API."""

from rest_framework.authentication import SessionAuthentication
from auth_app.views.helpers import _get_session_user


class AuthenticatedFacultyUser:
    """Wrapper object representing an authenticated faculty user for DRF.
    
    Provides required DRF auth properties (is_authenticated = True) without
    mutating the underlying FacultyUser model instance.
    """

    def __init__(self, faculty_user):
        self._faculty_user = faculty_user
        self.id = faculty_user.id
        self.pk = faculty_user.id
        self.name = getattr(faculty_user, "name", "")
        self.email = getattr(faculty_user, "email", "")
        self.google_id = getattr(faculty_user, "google_id", "")

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def __str__(self) -> str:
        return str(self._faculty_user)

    def __getattr__(self, name):
        return getattr(self._faculty_user, name)


class FacultySessionAuthentication(SessionAuthentication):
    """Custom DRF authentication class that authenticates via the session user
    stored under SESSION_KEY ("faculty_user_id").

    Reuses SessionAuthentication.enforce_csrf to enforce CSRF validation on
    unsafe HTTP methods (POST, PUT, PATCH, DELETE) whenever an authenticated
    session user is present.
    """

    def authenticate(self, request):
        user = _get_session_user(request)
        if not user:
            return None

        # Enforce standard Django/DRF CSRF check for unsafe HTTP methods
        self.enforce_csrf(request)

        wrapper = AuthenticatedFacultyUser(user)
        return (wrapper, None)

    def authenticate_header(self, request):
        return 'Session realm="api"'
