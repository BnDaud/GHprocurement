"""Quote-request tracking: progress updates, the status they decide, the
customer's accounts and the emails sent to them."""
import logging
import threading
from datetime import datetime

from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import connection
from django.template.loader import render_to_string
from django.utils import timezone

from .models import RFQ, RFQUpdate, RFQStage, STAGE_ORDER, User

logger = logging.getLogger(__name__)

SET_PASSWORD_SALT = "customer-set-password"
SET_PASSWORD_MAX_AGE = 60 * 60 * 24  # the emailed link works for 24 hours
STAGE_LABELS = dict(RFQStage.choices)


# ---------------------------------------------------------------- status
def step_of(stage):
    return STAGE_ORDER.index(stage) + 1 if stage in STAGE_ORDER else 1


def recompute_status(rfq):
    """The request's status is the stage of its newest update."""
    newest = rfq.updates.order_by("-created_at").first()
    rfq.status = newest.stage if newest else RFQStage.RECEIVED
    rfq.save(update_fields=["status"])
    return rfq.status


def short_title(rfq):
    first = (rfq.item or "").strip().splitlines()[0] if (rfq.item or "").strip() else "Quote request"
    return first if len(first) <= 70 else first[:67].rstrip() + "..."


def quotation_reference(rfq):
    """The GHP-... number of the latest quotation emailed for this request."""
    from .models import SentEmail

    sent = SentEmail.objects.filter(kind="rfq_reply", recipient__iexact=rfq.email).exclude(reference=None).first()
    return sent.reference if sent else ""


def request_summary(rfq):
    latest = rfq.updates.order_by("-created_at").first()
    return {
        "id": str(rfq.pk),
        "reference": rfq.reference,
        "title": short_title(rfq),
        "created_at": rfq.created_at,
        "status": rfq.status,
        "status_label": STAGE_LABELS.get(rfq.status, rfq.status),
        "step": step_of(rfq.status),
        "steps": len(STAGE_ORDER),
        "latest": latest.headline if latest else "",
        "estimated_delivery": rfq.estimated_delivery,
        "image_url": rfq.file.url if rfq.file else None,
    }


def request_detail(rfq):
    updates = list(rfq.updates.order_by("-created_at"))
    first_seen = {}
    for u in reversed(updates):
        first_seen.setdefault(u.stage, u.created_at)
    current = step_of(rfq.status)
    stages = [
        {
            "key": key, "label": label, "step": i + 1,
            "state": "done" if i + 1 < current or rfq.status == "delivered" else ("current" if i + 1 == current else "todo"),
            "date": first_seen.get(key),
        }
        for i, (key, label) in enumerate(RFQStage.choices)
    ]
    return {
        **request_summary(rfq),
        "company": rfq.company,
        "name": rfq.name,
        "item": rfq.item,
        "delivery_address": rfq.delivery_address,
        "quotation_reference": quotation_reference(rfq),
        "stages": stages,
        "updates": [
            {
                "id": str(u.pk), "stage": u.stage, "stage_label": STAGE_LABELS.get(u.stage, u.stage),
                "headline": u.headline, "details": u.details, "location": u.location,
                "created_at": u.created_at,
            }
            for u in updates
        ],
    }


def add_update(rfq, stage, headline, details="", location="", created_by=None):
    update = RFQUpdate.objects.create(
        rfq=rfq, stage=stage, headline=headline.strip(), details=details.strip(),
        location=location.strip(), created_by=created_by,
    )
    recompute_status(rfq)
    return update


# ---------------------------------------------------------------- accounts
def username_for(email):
    base = (email.split("@")[0] or "customer")[:30]
    username, n = base, 1
    while User.objects.filter(username=username).exists():
        n += 1
        username = f"{base}{n}"
    return username


def get_or_create_customer(email, name, phone, company=""):
    """The customer's account is found by email (never by company name), and a
    new one is created with NO usable password: they choose their own through
    the emailed link. Returns (user, created)."""
    email = email.strip()
    user = User.objects.filter(email__iexact=email, is_staff=False, is_superuser=False).first()
    if user:
        return user, False
    first, _, last = (name or "").strip().partition(" ")
    user = User(username=username_for(email), email=email, first_name=first[:150], last_name=last[:150],
                phone=phone or "", dp="", is_staff=False, is_superuser=False)
    user.set_unusable_password()
    user.save()
    return user, True


def password_fingerprint(user):
    return user.password[-16:]


def make_set_password_token(user):
    return signing.dumps({"uid": str(user.pk), "fp": password_fingerprint(user)}, salt=SET_PASSWORD_SALT)


def read_set_password_token(token):
    """Returns the user, or None if the link is bad, expired or already used
    (using it changes the password, so the fingerprint no longer matches)."""
    try:
        data = signing.loads(str(token), salt=SET_PASSWORD_SALT, max_age=SET_PASSWORD_MAX_AGE)
    except signing.BadSignature:
        return None
    user = User.objects.filter(pk=data.get("uid"), is_active=True, is_staff=False, is_superuser=False).first()
    if user is None or data.get("fp") != password_fingerprint(user):
        return None
    return user


# ---------------------------------------------------------------- emails
def site_url():
    return getattr(settings, "PUBLIC_SITE_URL", "https://www.ghprocurement.com").rstrip("/")


def _send(to, subject, title, paragraphs, button_label="", button_url="", note="", details=None):
    from .task import LOGO_CID, LOGO_FALLBACK_URL, LOGO_PATH, company_info

    logo = None
    try:
        with open(LOGO_PATH, "rb") as fh:
            logo = fh.read()
    except OSError:
        pass
    info = company_info()
    context = {
        "title": title, "paragraphs": paragraphs, "button_label": button_label, "button_url": button_url,
        "note": note, "details": details or [], "preheader": paragraphs[0][:110] if paragraphs else title,
        "logo_cid": LOGO_CID if logo else "", "logo_url": LOGO_FALLBACK_URL,
        "year": datetime.now().year, **info,
    }
    html = render_to_string("email_customer.html", context)
    text = "\n\n".join([title] + paragraphs + ([f"{button_label}: {button_url}"] if button_url else []) + ([note] if note else []))
    msg = EmailMultiAlternatives(subject=subject, body=text, from_email="GH Procurement <info@ghprocurement.com>", to=[to])
    msg.attach_alternative(html, "text/html")
    if logo:
        from email.mime.image import MIMEImage
        img = MIMEImage(logo, _subtype="png")
        img.add_header("Content-ID", f"<{LOGO_CID}>")
        img.add_header("Content-Disposition", "inline", filename="logo.png")
        msg.attach(img)
    msg.send(fail_silently=False)


def _first_name(user_or_rfq):
    name = getattr(user_or_rfq, "name", "") or getattr(user_or_rfq, "first_name", "")
    return (name or "").split(" ")[0] or "there"


def email_request_received(rfq_id, new_account):
    """Sent right after a request: its reference, and (new account) the link to choose a password."""
    try:
        rfq = RFQ.objects.select_related("user").get(pk=rfq_id)
        user = rfq.user
        paragraphs = [
            f"Hello {_first_name(rfq)},",
            f"Thank you. We received your request ({rfq.reference}) and will send a quotation within 48 hours.",
        ]
        if new_account and user.email.lower() == rfq.email.lower():
            link = f"{site_url()}/#/set-password?token={make_set_password_token(user)}"
            paragraphs.append("We also made you an account so you can follow this request, step by step. Choose your password with the button below. The link works for 24 hours.")
            _send(rfq.email, f"We have your request {rfq.reference}", "Request received", paragraphs,
                  "Choose my password", link,
                  "If you did not send this request, ignore this email. Nothing happens until the link is used.",
                  [("Reference", rfq.reference), ("Item", short_title(rfq))])
        else:
            paragraphs.append("Sign in to follow it, step by step.")
            _send(rfq.email, f"We have your request {rfq.reference}", "Request received", paragraphs,
                  "Track my request", f"{site_url()}/#/account/{rfq.pk}", "",
                  [("Reference", rfq.reference), ("Item", short_title(rfq))])
    except Exception:  # noqa: BLE001
        logger.exception("could not send the request-received email for %s", rfq_id)


def email_password_link(user_id):
    """For 'send me the link again' and 'forgot password'."""
    try:
        user = User.objects.get(pk=user_id)
        link = f"{site_url()}/#/set-password?token={make_set_password_token(user)}"
        _send(user.email, "Choose your password", "Choose your password",
              [f"Hello {_first_name(user)},", "Use the button below to choose a password for your GH Procurement account. The link works once and expires after 24 hours."],
              "Choose my password", link, "If you did not ask for this, ignore this email. Your account is unchanged.")
    except Exception:  # noqa: BLE001
        logger.exception("could not send the password link to %s", user_id)


def email_update(update_id):
    """Every progress update is emailed to the customer."""
    try:
        update = RFQUpdate.objects.select_related("rfq").get(pk=update_id)
        rfq = update.rfq
        paragraphs = [f"Hello {_first_name(rfq)},", f"There is an update on your request {rfq.reference}.", update.headline]
        if update.details:
            paragraphs.append(update.details)
        details = [("Request", short_title(rfq)), ("Stage", f"{STAGE_LABELS.get(update.stage, update.stage)} (step {step_of(update.stage)} of {len(STAGE_ORDER)})")]
        if update.location:
            details.append(("Where", update.location))
        if rfq.estimated_delivery:
            details.append(("Estimated delivery", f"{rfq.estimated_delivery.day} {rfq.estimated_delivery:%B %Y}"))
        _send(rfq.email, f"{rfq.reference}: {update.headline}", update.headline, paragraphs[1:], "See my order",
              f"{site_url()}/#/account/{rfq.pk}", "", details)
        RFQUpdate.objects.filter(pk=update.pk).update(emailed=True)
    except Exception:  # noqa: BLE001
        logger.exception("could not send the update email for %s", update_id)


def in_background(fn, *args):
    """Send in a thread so the web response is not held up. (Tests set
    TRACKING_EMAILS_SYNC to run it inline.)"""
    if getattr(settings, "TRACKING_EMAILS_SYNC", False):
        fn(*args)
        return

    def run():
        try:
            fn(*args)
        finally:
            connection.close()  # the thread owns its own database connection

    threading.Thread(target=run, daemon=True).start()
