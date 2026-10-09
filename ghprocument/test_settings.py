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
CORS_ALLOWED_ORIGINS = list(CORS_ALLOWED_ORIGINS) + ["http://localhost:3001", "http://localhost:3000"]  # noqa: F405
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {"login": "1000/min", "password": "1000/min"},
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast tests only

if os.environ.get("TEST_LOCMEM_EMAIL"):  # local checks that must not send real mail
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

TRACKING_EMAILS_SYNC = True
PUBLIC_SITE_URL = os.environ.get("PUBLIC_SITE_URL", "http://localhost:3000")
if os.environ.get("TEST_FILE_EMAIL"):  # local checks: write each email to a file you can open
    EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
    EMAIL_FILE_PATH = os.environ["TEST_FILE_EMAIL"]
    if os.environ.get("TEST_REAL_MAIL_TO"):  # ...but really deliver mail to these (own) addresses
        EMAIL_BACKEND = "cms.devmail.AllowlistBackend"
TWITTER_REFRESH_SYNC = True

CUSTOMER_PORTAL_LIVE = os.environ.get("TEST_PORTAL_OFF") is None  # tests run with the portal on, unless a test turns it off
