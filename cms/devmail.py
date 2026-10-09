"""Local testing only (never used in production).

Emails are written to files, EXCEPT mail addressed only to the addresses listed
in the TEST_REAL_MAIL_TO environment variable, which is really delivered through
Postmark. That lets a developer see the real email in their own inbox without
test accounts such as ada@acme.com ever receiving (or bouncing) anything.
"""
import os

from anymail.backends.postmark import EmailBackend as PostmarkBackend
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.filebased import EmailBackend as FileBackend


def allowed_addresses():
    return {a.strip().lower() for a in os.environ.get("TEST_REAL_MAIL_TO", "").split(",") if a.strip()}


class AllowlistBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.real = PostmarkBackend(fail_silently=fail_silently)
        self.file = FileBackend(file_path=settings.EMAIL_FILE_PATH, fail_silently=fail_silently)

    def send_messages(self, messages):
        allow = allowed_addresses()
        real, other = [], []
        for m in messages:
            to = [r.lower() for r in m.recipients()]
            (real if to and all(r in allow for r in to) else other).append(m)
        sent = 0
        if real:
            sent += self.real.send_messages(real) or 0
        if other:
            sent += self.file.send_messages(other) or 0
        return sent
