"""The page an uptime monitor pings."""
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient


class Health(TestCase):
    def setUp(self):
        self.c = APIClient()

    def test_open_to_everyone_no_sign_in(self):
        r = self.c.get("/api/health/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_works_for_head_requests_too(self):
        self.assertEqual(self.c.head("/api/health/").status_code, 200)

    def test_a_bad_or_expired_token_does_not_make_it_fail(self):
        self.c.credentials(HTTP_AUTHORIZATION="Bearer garbage")
        self.assertEqual(self.c.get("/api/health/").status_code, 200)

    def test_never_cached_and_reveals_nothing_else(self):
        r = self.c.get("/api/health/")
        self.assertEqual(r["Cache-Control"], "no-store")
        self.assertEqual(set(r.json()), {"status"})

    def test_reports_down_when_the_database_does(self):
        with mock.patch("django.db.backends.utils.CursorWrapper.execute", side_effect=Exception("db gone")):
            r = self.c.get("/api/health/")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {"status": "down"})

    def test_it_cannot_be_used_to_change_anything(self):
        self.assertEqual(self.c.post("/api/health/", {}, format="json").status_code, 405)

    def test_the_api_root_still_needs_a_sign_in(self):
        self.assertEqual(self.c.get("/api/").status_code, 401)
