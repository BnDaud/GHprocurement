from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Max
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


def create_sent_email(kind, **fields):
    """Create the SentEmail row. RFQ replies get the next reference number for
    the current year (GHP-2026-0001, 0002, ...).

    Two sends at the same moment can pick the same number; the database
    constraint rejects the second one, and we simply take the next number."""
    for _ in range(10):
        ref = {}
        if kind == SentEmail.Kind.RFQ_REPLY:
            year = lagos_now().year
            last = SentEmail.objects.filter(reference_year=year).aggregate(m=Max("reference_number"))["m"] or 0
            number = last + 1
            ref = {
                "reference_year": year,
                "reference_number": number,
                "reference": f"{PREFIX}-{year}-{number:04d}",
            }
        try:
            with transaction.atomic():
                return SentEmail.objects.create(kind=kind, **ref, **fields)
        except IntegrityError:
            if not ref:
                raise
            continue
    raise RuntimeError("Could not allocate a reference number")


def allocate_rfq_reference(**fields):
    """Create a quote request with the next reference for the year
    (RFQ-2026-0001, 0002, ...). Same retry idea as create_sent_email."""
    for _ in range(10):
        year = lagos_now().year
        last = RFQ.objects.filter(reference_year=year).aggregate(m=Max("reference_number"))["m"] or 0
        number = last + 1
        try:
            with transaction.atomic():
                return RFQ.objects.create(
                    reference_year=year, reference_number=number,
                    reference=f"RFQ-{year}-{number:04d}", **fields,
                )
        except IntegrityError:
            continue
    raise RuntimeError("Could not allocate a reference number")
