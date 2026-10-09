"""Settings for running the test suite (and local end-to-end checks).

Uses SQLite instead of DATABASE_URL, so tests can never touch the real
database. Run with:  python manage.py test cms --settings=ghprocument.test_settings
"""
import os

from .settings import *  # noqa: F401,F403

SECRET_KEY = "test-only-secret-key"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("TEST_DB", ":memory:"),
    }
}
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
CORS_ALLOWED_ORIGINS = list(CORS_ALLOWED_ORIGINS) + ["http://localhost:3001"]  # noqa: F405
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {"login": "1000/min", "password": "1000/min"},
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast tests only
