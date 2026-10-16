from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import RFQ, SentEmail

LAGOS = ZoneInfo("Africa/Lagos")
PREFIX = "GHP"


def lagos_now():
    return timezone.now().astimezone(LAGOS)


def format_date(value):
    """9 October 2026"""
    value = value.astimezone(LAGOS) if hasattr(value, "astimezone") else value
    return f"{value.day} {value:%B %Y}"


REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no 0/O, 1/I/L: nothing to mix up when read out or typed
REFERENCE_LENGTH = 6


def create_sent_email(kind, **fields):
    """Create the SentEmail row. RFQ replies get a random reference such as
    GHP-2026-K7M2Q9 (not counting up, so it reveals nothing and cannot be
    guessed). The database refuses a duplicate and we pick another."""
    import secrets

    for _ in range(30):
        ref = {}
        if kind == SentEmail.Kind.RFQ_REPLY:
            year = lagos_now().year
            code = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))
            ref = {"reference_year": year, "reference": f"{PREFIX}-{year}-{code}"}
        try:
            with transaction.atomic():
                return SentEmail.objects.create(kind=kind, **ref, **fields)
        except IntegrityError:
            if not ref:
                raise
            continue
    raise RuntimeError("Could not allocate a reference number")


def allocate_rfq_reference(**fields):
    """Create a quote request with a random reference such as RFQ-2026-K7M2Q9.

    Random, not counting up: nobody can guess another customer's reference, and
    the references do not reveal how many requests there have been. (Earlier
    requests keep their old counting numbers.) The database refuses a
    duplicate, and we simply pick another."""
    import secrets

    for _ in range(30):
        year = lagos_now().year
        code = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))
        try:
            with transaction.atomic():
                return RFQ.objects.create(reference_year=year, reference=f"RFQ-{year}-{code}", **fields)
        except IntegrityError:
            continue
    raise RuntimeError("Could not allocate a reference")
