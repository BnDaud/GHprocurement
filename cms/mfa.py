"""Authenticator-app (TOTP) second factor.

Codes are checked with a +-1 step window (30 s either side, for clock drift),
each accepted step can be used only once, and five wrong codes lock the
account's second step for 15 minutes. Recovery codes are random, single-use
and stored only as hashes.
"""
import hashlib
import hmac
import secrets
import time
from datetime import timedelta

import pyotp
from django.conf import settings
from django.utils import timezone

from .models import MFADevice

ISSUER = "GH Procurement CMS"
STEP = 30
WINDOW = 1
MAX_FAILURES = 5
LOCK_MINUTES = 15
RECOVERY_COUNT = 10
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def new_secret():
    return pyotp.random_base32()


def otpauth_uri(user, secret):
    return pyotp.TOTP(secret).provisioning_uri(name=user.email or user.username, issuer_name=ISSUER)


def get_device(user, confirmed_only=True):
    device = MFADevice.objects.filter(user=user).first()
    if device and confirmed_only and not device.confirmed:
        return None
    return device


def is_enabled(user):
    return get_device(user) is not None


def _hash(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def _normalise(code):
    return "".join(str(code).upper().split()).replace("-", "")


def make_recovery_codes():
    """Returns (plain codes to show once, hashes to store)."""
    codes = [
        "".join(secrets.choice(_ALPHABET) for _ in range(10)) for _ in range(RECOVERY_COUNT)
    ]
    return [f"{c[:5]}-{c[5:]}" for c in codes], [_hash(c) for c in codes]


def is_locked(device):
    return bool(device.locked_until and device.locked_until > timezone.now())


def _fail(device):
    device.failed_attempts += 1
    if device.failed_attempts >= MAX_FAILURES:
        device.locked_until = timezone.now() + timedelta(minutes=LOCK_MINUTES)
        device.failed_attempts = 0
    device.save(update_fields=["failed_attempts", "locked_until"])
    return False


def _success(device, **changes):
    for k, v in changes.items():
        setattr(device, k, v)
    device.failed_attempts = 0
    device.locked_until = None
    device.save()
    return True


def _match_step(secret, code, now=None):
    """The time step the code belongs to (within the window), or None."""
    code = "".join(str(code).split())
    if not (code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret, interval=STEP)
    now = time.time() if now is None else now
    for offset in range(-WINDOW, WINDOW + 1):
        t = now + offset * STEP
        if hmac.compare_digest(totp.at(t), code):
            return int(t // STEP)
    return None


def check_totp(device, code, now=None):
    """True if the code is valid and not used before; records the step."""
    if is_locked(device):
        return False
    step = _match_step(device.secret, code, now)
    if step is None or (device.last_used_step is not None and step <= device.last_used_step):
        return _fail(device)
    return _success(device, last_used_step=step)


def check_recovery(device, code):
    """True and burns the code if it is one of the unused recovery codes."""
    if is_locked(device):
        return False
    digest = _hash(_normalise(code))
    for stored in device.recovery_hashes:
        if hmac.compare_digest(stored, digest):
            remaining = [h for h in device.recovery_hashes if h != stored]
            return _success(device, recovery_hashes=remaining)
    return _fail(device)


def check_code(device, code):
    """Accepts an app code or a recovery code. Returns 'totp', 'recovery' or None."""
    if is_locked(device):
        return None
    cleaned = "".join(str(code).split())
    if cleaned.isdigit() and len(cleaned) == 6:
        return "totp" if check_totp(device, cleaned) else None
    return "recovery" if check_recovery(device, code) else None
