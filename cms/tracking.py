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


def request_detail(rfq, admin=False):
    updates = list(rfq.updates.select_related("created_by").order_by("-created_at"))
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
                # who posted it: for the CMS only, never shown to customers
                **({"posted_by": (u.created_by.email or u.created_by.username) if u.created_by else ""} if admin else {}),
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


def _send(to, subject, title, paragraphs, button_label="", button_url="", note="", details=None,
          label="", reference="", steps=None):
    from .task import LOGO_CID, LOGO_FALLBACK_URL, LOGO_PATH, company_info

    logo = None
    try:
        # the white logo: these emails have a dark header
        with open(LOGO_PATH.replace("email-logo.png", "email-logo-white.png"), "rb") as fh:
            logo = fh.read()
    except OSError:
        pass
    info = company_info()
    context = {
        "title": title, "paragraphs": paragraphs, "button_label": button_label, "button_url": button_url,
        "note": note, "details": details or [], "label": label, "reference": reference, "steps": steps or [],
        "preheader": paragraphs[0][:110] if paragraphs else title,
        "header_image": getattr(settings, "EMAIL_HEADER_IMAGE", "https://cms.ghprocurement.com/email/header.jpg"),
        "logo_cid": LOGO_CID if logo else "", "logo_url": LOGO_FALLBACK_URL,
        "year": datetime.now().year, **info,
    }
    html = render_to_string("email_customer.html", context)
    lines = [title, ""] + ([f"Reference: {reference}", ""] if reference else []) + paragraphs + [""]
    lines += [f"{k}: {v}" for k, v in (details or [])]
    if steps:
        lines += ["", "What happens next:"] + [f"{i}. {t} - {x}" for i, (t, x) in enumerate(steps, 1)]
    if button_url:
        lines += ["", f"{button_label}: {button_url}"]
    if note:
        lines += ["", note]
    lines += ["", "Regards,", "GH Procurement"]
    msg = EmailMultiAlternatives(subject=subject, body="\n".join(lines), from_email="GH Procurement <info@ghprocurement.com>", to=[to])
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


NEXT_STEPS = [
    ("We send your quotation", "A formal quotation by email."),
    ("You confirm", "We start sourcing for you."),
    ("You follow it home", "Each step shows on your account until delivery."),
]


def email_request_received(rfq_id, new_account):
    """Sent right after a request: its reference, and (new account) the link to choose a password."""
    from .references import format_date

    try:
        rfq = RFQ.objects.select_related("user").get(pk=rfq_id)
        user = rfq.user
        details = [("Request", short_title(rfq)), ("Company", rfq.company),
                   ("Received", format_date(rfq.created_at)), ("Quotation within", "48 hours")]
        common = dict(label="Request receipt", reference=rfq.reference, steps=NEXT_STEPS, details=details)
        title = f"We have your request, {_first_name(rfq)}."
        if new_account and user.email.lower() == rfq.email.lower():
            link = f"{site_url()}/#/set-password?token={make_set_password_token(user)}"
            paragraphs = ["Thank you. We received your request and will send your quotation within 48 hours.",
                          "We made you an account so you can follow it, step by step. Choose your password (the link works for 24 hours):"]
            _send(rfq.email, f"We have your request {rfq.reference}", title, paragraphs,
                  "Choose my password", link,
                  "If you did not send this request, ignore this email. Nothing happens until the link is used.", **common)
        else:
            paragraphs = ["Thank you. We received your request and will send your quotation within 48 hours.",
                          "Sign in to follow it, step by step."]
            _send(rfq.email, f"We have your request {rfq.reference}", title, paragraphs,
                  "Track my request", f"{site_url()}/#/account/{rfq.pk}", "", **common)
    except Exception:  # noqa: BLE001
        logger.exception("could not send the request-received email for %s", rfq_id)


def email_password_link(user_id):
    """For 'send me the link again' and 'forgot password'."""
    try:
        user = User.objects.get(pk=user_id)
        link = f"{site_url()}/#/set-password?token={make_set_password_token(user)}"
        _send(user.email, "Choose your password", "Choose your password",
              [f"Hello {_first_name(user)},", "Use the button below to choose a password for your GH Procurement account. The link works once and expires after 24 hours."],
              "Choose my password", link, "If you did not ask for this, ignore this email. Your account is unchanged.",
              label="Your account")
    except Exception:  # noqa: BLE001
        logger.exception("could not send the password link to %s", user_id)


def email_update(update_id):
    """Every progress update is emailed to the customer."""
    try:
        update = RFQUpdate.objects.select_related("rfq").get(pk=update_id)
        rfq = update.rfq
        paragraphs = [f"Hello {_first_name(rfq)}, there is an update on your request."]
        if update.details:
            paragraphs.append(update.details)
        details = [("Request", short_title(rfq)), ("Stage", f"{STAGE_LABELS.get(update.stage, update.stage)} (step {step_of(update.stage)} of {len(STAGE_ORDER)})")]
        if update.location:
            details.append(("Where", update.location))
        if rfq.estimated_delivery:
            details.append(("Estimated delivery", f"{rfq.estimated_delivery.day} {rfq.estimated_delivery:%B %Y}"))
        _send(rfq.email, f"{rfq.reference}: {update.headline}", update.headline, paragraphs, "See my order",
              f"{site_url()}/#/account/{rfq.pk}", "", details, label="Progress update", reference=rfq.reference)
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


# ---------------------------------------------------------------- limits on the public request form
RFQ_WINDOW_SECONDS = 3600
RFQ_PER_EMAIL = 3  # the same address cannot be mailed more than this per hour
RFQ_PER_VISITOR = 5  # one visitor (IP address) cannot send more than this per hour
RFQ_SITE_WIDE = 100  # a ceiling for the whole site, to protect the email sending reputation


def client_ip(request):
    """The visitor's address: the first one in X-Forwarded-For when behind a proxy."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")) or "unknown"


def rfq_limit_reason(request, email):
    """Returns a message when this request must wait, else None. Counts only
    requests that were accepted, so a typo never uses up someone's allowance.
    Email and site-wide limits are counted in the database (they hold across
    servers and restarts); the visitor limit is a lighter, in-memory count."""
    import os
    from django.core.cache import cache
    from datetime import timedelta

    if os.environ.get("TEST_NO_RFQ_LIMITS"):  # local browser testing only
        return None
    since = timezone.now() - timedelta(seconds=RFQ_WINDOW_SECONDS)
    if RFQ.objects.filter(email__iexact=email, created_at__gte=since).count() >= RFQ_PER_EMAIL:
        return "This email address has sent several requests in the last hour. Please wait a while, or contact us if it is urgent."
    if RFQ.objects.filter(created_at__gte=since).count() >= RFQ_SITE_WIDE:
        return "We are receiving a lot of requests right now. Please try again in a little while."
    key = f"rfq-visitor:{client_ip(request)}"
    if cache.get(key, 0) >= RFQ_PER_VISITOR:
        return "You have sent several requests in the last hour. Please wait a while, or contact us if it is urgent."
    return None


def rfq_limit_count(request):
    from django.core.cache import cache

    key = f"rfq-visitor:{client_ip(request)}"
    cache.add(key, 0, RFQ_WINDOW_SECONDS)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, RFQ_WINDOW_SECONDS)
