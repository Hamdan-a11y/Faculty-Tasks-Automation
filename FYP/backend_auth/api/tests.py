"""Tests for api application endpoints."""

from django.test import Client, TestCase
from django.urls import reverse
from auth_app.models import FacultyUser
from auth_app.views.helpers import SESSION_KEY


class ApiEndpointsTestCase(TestCase):
    def setUp(self):
        self.user = FacultyUser.objects.create(
            google_id="1234567890",
            email="testfaculty@szabist-isb.pk",
            name="Dr. Test Faculty",
        )

    def test_health_check(self):
        """GET /api/health/ should return 200 and status ok."""
        client = Client()
        response = client.get(reverse("api:health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_csrf_token_endpoint(self):
        """GET /api/csrf/ should return 200, return csrfToken, and set the csrftoken cookie."""
        client = Client()
        response = client.get(reverse("api:csrf"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("csrfToken", data)
        self.assertTrue(len(data["csrfToken"]) > 0)
        self.assertIn("csrftoken", response.cookies)

    def test_me_returns_401_when_logged_out(self):
        """GET /api/auth/me/ should return 401 when no session exists."""
        client = Client()
        response = client.get(reverse("api:current_user"))
        self.assertEqual(response.status_code, 401)
        self.assertIn("detail", response.json())

    def test_me_returns_correct_data_when_logged_in(self):
        """GET /api/auth/me/ should return user info when authenticated via session."""
        client = Client()
        session = client.session
        session[SESSION_KEY] = str(self.user.id)
        session.save()

        response = client.get(reverse("api:current_user"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], self.user.id)
        self.assertEqual(data["name"], self.user.name)
        self.assertEqual(data["email"], self.user.email)

    def test_logout_without_csrf_token_rejected_when_session_exists(self):
        """POST /api/auth/logout/ must be rejected (403) when logged in but no CSRF token provided."""
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session[SESSION_KEY] = str(self.user.id)
        session.save()

        response = client.post(reverse("api:logout"))
        self.assertEqual(response.status_code, 403)

    def test_logout_with_csrf_token_succeeds(self):
        """POST /api/auth/logout/ succeeds when a valid CSRF token is supplied."""
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session[SESSION_KEY] = str(self.user.id)
        session.save()

        # Fetch CSRF token
        csrf_resp = client.get(reverse("api:csrf"))
        csrf_token = csrf_resp.json()["csrfToken"]

        # Call logout with CSRF token in header
        response = client.post(reverse("api:logout"), HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

        # Session should be flushed
        self.assertNotIn(SESSION_KEY, client.session)
