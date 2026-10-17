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

from .models import RFQ, RFQUpdate, RFQStage, OrderMessage, STAGE_ORDER, User

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
    if rfq.status == RFQStage.DELIVERED:
        in_background(purge_pictures, str(rfq.pk))  # the order is complete: its pictures are no longer needed
    return rfq.status


def purge_pictures(rfq_id):
    """Delete the customer's pictures of a finished order (retried the next time if Cloudinary fails)."""
    from . import images
    rfq = RFQ.objects.filter(pk=rfq_id).first()
    if rfq and images.has_pictures(rfq):
        images.purge_rfq(rfq)


def short_title(rfq):
    first = (rfq.item or "").strip().splitlines()[0] if (rfq.item or "").strip() else "Quote request"
    return first if len(first) <= 70 else first[:67].rstrip() + "..."


def quotation_reference(rfq):
    """The GHP-... number of the latest quotation emailed for this request."""
    from .models import SentEmail

    sent = (SentEmail.objects.filter(kind="rfq_reply", rfq=rfq).exclude(reference=None).first()
            or SentEmail.objects.filter(kind="rfq_reply", recipient__iexact=rfq.email, rfq__isnull=True).exclude(reference=None).first())
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


CARRIER_LINKS = [
    ("dhl", "https://www.dhl.com/global-en/home/tracking.html?tracking-id={n}"),
    ("fedex", "https://www.fedex.com/fedextrack/?trknbr={n}"),
    ("ups", "https://www.ups.com/track?tracknum={n}"),
    ("aramex", "https://www.aramex.com/track/results?ShipmentNumber={n}"),
]


def tracking_link(rfq):
    """The customer's link to the carrier: the one the team typed, else one worked out for a well-known carrier."""
    if rfq.tracking_url:
        return rfq.tracking_url
    from urllib.parse import quote as q
    name = (rfq.carrier or "").lower()
    if rfq.tracking_number:
        for key, pattern in CARRIER_LINKS:
            if key in name:
                return pattern.format(n=q(rfq.tracking_number.strip()))
    return ""


def shipment_json(rfq):
    """Carrier, tracking number and dates, or None until the team has added any."""
    if not (rfq.carrier or rfq.tracking_number or rfq.tracking_url):
        return None
    shipped = rfq.updates.filter(stage="shipped").order_by("created_at").first()
    return {"carrier": rfq.carrier, "tracking_number": rfq.tracking_number, "tracking_url": tracking_link(rfq),
            "shipped_at": shipped.created_at if shipped else None, "estimated_delivery": rfq.estimated_delivery}


def documents_json(rfq):
    return [{"id": str(d.pk), "kind": d.kind, "kind_label": d.get_kind_display(), "title": d.title, "size": d.size,
             "created_at": d.created_at} for d in rfq.documents.all()]


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
        "unread_messages": unread_for_team(rfq) if admin else unread_for_customer(rfq),
        "shipment": shipment_json(rfq),
        "documents": documents_json(rfq),
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


def _white_logo():
    from .task import LOGO_PATH
    try:
        # the white logo: these emails have a dark header
        with open(LOGO_PATH.replace("email-logo.png", "email-logo-white.png"), "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _html(title, paragraphs, button_label, button_url, note, details, label, reference, steps, logo_cid, logo_url):
    from .task import company_info

    context = {
        "title": title, "paragraphs": paragraphs, "button_label": button_label, "button_url": button_url,
        "note": note, "details": details or [], "label": label, "reference": reference, "steps": steps or [],
        "preheader": paragraphs[0][:110] if paragraphs else title,
        "header_image": getattr(settings, "EMAIL_HEADER_IMAGE", "https://cms.ghprocurement.com/email/header.jpg"),
        "logo_cid": logo_cid, "logo_url": logo_url,
        "year": datetime.now().year, **company_info(),
    }
    return render_to_string("email_customer.html", context)


def _send(to, subject, title, paragraphs, button_label="", button_url="", note="", details=None,
          label="", reference="", steps=None):
    from .task import LOGO_CID, LOGO_FALLBACK_URL

    logo = _white_logo()
    html = _html(title, paragraphs, button_label, button_url, note, details, label, reference, steps,
                 LOGO_CID if logo else "", LOGO_FALLBACK_URL)
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
    """Sent right after a request: its reference, and (new account) the link to choose a password, or (known
    customer) the link to track it."""
    from .references import format_date

    try:
        rfq = RFQ.objects.select_related("user").get(pk=rfq_id)
        user = rfq.user
        details = [("Request", short_title(rfq)), ("Company", rfq.company),
                   ("Received", format_date(rfq.created_at)), ("Quotation within", "48 hours")]
        common = dict(label="Request receipt", reference=rfq.reference, details=details, steps=NEXT_STEPS)
        title = f"We have your request, {_first_name(rfq)}."
        thanks = "Thank you. We received your request and will send your quotation within 48 hours."
        if new_account and user.email.lower() == rfq.email.lower():
            link = f"{site_url()}/set-password?token={make_set_password_token(user)}"
            _send(rfq.email, f"We have your request {rfq.reference}", title,
                  [thanks, "We made you an account so you can follow it, step by step. Choose your password (the link works for 24 hours):"],
                  "Choose my password", link,
                  "If you did not send this request, ignore this email. Nothing happens until the link is used.", **common)
        else:
            _send(rfq.email, f"We have your request {rfq.reference}", title,
                  [thanks, "Sign in to follow it, step by step."],
                  "Track my request", f"{site_url()}/account/{rfq.pk}", "", **common)
    except Exception:  # noqa: BLE001
        logger.exception("could not send the request-received email for %s", rfq_id)


def email_password_link(user_id):
    """For 'send me the link again' and 'forgot password'."""
    try:
        user = User.objects.get(pk=user_id)
        link = f"{site_url()}/set-password?token={make_set_password_token(user)}"
        _send(user.email, "Choose your password", "Choose your password",
              [f"Hello {_first_name(user)},", "Use the button below to choose a password for your GH Procurement account. The link works once and expires after 24 hours."],
              "Choose my password", link, "If you did not ask for this, ignore this email. Your account is unchanged.",
              label="Your account")
    except Exception:  # noqa: BLE001
        logger.exception("could not send the password link to %s", user_id)


def _update_parts(update):
    """What an update email says (shared by the sending and by the customer's 'see what we sent' view)."""
    rfq = update.rfq
    paragraphs = [f"Hello {_first_name(rfq)}, there is an update on your request."]
    if update.details:
        paragraphs.append(update.details)
    details = [("Request", short_title(rfq)), ("Stage", f"{STAGE_LABELS.get(update.stage, update.stage)} (step {step_of(update.stage)} of {len(STAGE_ORDER)})")]
    if update.location:
        details.append(("Where", update.location))
    if rfq.carrier:
        details.append(("Shipped by", rfq.carrier))
    if rfq.tracking_number:
        details.append(("Tracking number", rfq.tracking_number))
    if rfq.estimated_delivery:
        details.append(("Estimated delivery", f"{rfq.estimated_delivery.day} {rfq.estimated_delivery:%B %Y}"))
    return dict(subject=f"{rfq.reference}: {update.headline}", title=update.headline, paragraphs=paragraphs,
                button_label="See my order", button_url=f"{site_url()}/account/{rfq.pk}", note="", details=details,
                label="Progress update", reference=rfq.reference, steps=None)


def email_update(update_id):
    """Every progress update is emailed to the customer."""
    try:
        update = RFQUpdate.objects.select_related("rfq").get(pk=update_id)
        p = _update_parts(update)
        _send(update.rfq.email, p["subject"], p["title"], p["paragraphs"], p["button_label"], p["button_url"], p["note"],
              p["details"], label=p["label"], reference=p["reference"])
        RFQUpdate.objects.filter(pk=update.pk).update(emailed=True)
    except Exception:  # noqa: BLE001
        logger.exception("could not send the update email for %s", update_id)


# ---------------------------------------------------------------- messages on an order
MESSAGE_MAX = 2000
CUSTOMER_MESSAGES_PER_HOUR = 30


def message_json(m, admin=False):
    out = {"id": str(m.pk), "sender": m.sender, "body": m.body, "created_at": m.created_at, "read": m.read}
    if admin and m.sender == "team":
        out["author"] = (m.author.email or m.author.username) if m.author else ""
    return out


def unread_for_customer(rfq):
    return rfq.messages.filter(sender="team", read=False).count()


def unread_for_team(rfq):
    return rfq.messages.filter(sender="customer", read=False).count()


def email_team_message(message_id):
    """Tell the team a customer wrote on an order."""
    try:
        from .task import company_info
        m = OrderMessage.objects.select_related("rfq").get(pk=message_id)
        rfq = m.rfq
        _send(company_info()["contact_email"], f"{rfq.reference}: {rfq.name} sent a message", "A customer sent a message",
              [f"{rfq.name} ({rfq.company}) wrote on {short_title(rfq)}:", m.body[:600] + ("..." if len(m.body) > 600 else "")],
              "Reply in the CMS", f"https://cms.ghprocurement.com/orders/{rfq.pk}", "", [("Customer", rfq.email), ("Order", rfq.reference)],
              label="New message", reference=rfq.reference)
    except Exception:  # noqa: BLE001
        logger.exception("could not tell the team about message %s", message_id)


def email_customer_message(message_id):
    """Tell the customer the team replied."""
    try:
        m = OrderMessage.objects.select_related("rfq").get(pk=message_id)
        rfq = m.rfq
        _send(rfq.email, f"{rfq.reference}: a reply from GH Procurement", "You have a reply",
              [f"Hello {_first_name(rfq)}, we replied to your question about {short_title(rfq)}:", m.body[:600] + ("..." if len(m.body) > 600 else "")],
              "Read and reply", f"{site_url()}/account/{rfq.pk}", "", [("Order", rfq.reference)],
              label="Message from us", reference=rfq.reference)
    except Exception:  # noqa: BLE001
        logger.exception("could not email the reply %s", message_id)


def email_staff_accepted(rfq_id):
    """Tell the team a customer accepted their quotation on the website."""
    try:
        from .task import company_info
        rfq = RFQ.objects.get(pk=rfq_id)
        ref = quotation_reference(rfq)
        _send(company_info()["contact_email"], f"{rfq.reference}: the customer accepted the quotation",
              "A customer accepted a quotation",
              [f"{rfq.name} ({rfq.company}) accepted the quotation{f' {ref}' if ref else ''} for {short_title(rfq)}.",
               "The order has moved to Confirmed. Check the payment terms with them, then carry on from the order page."],
              "Open the order", f"https://cms.ghprocurement.com/orders/{rfq.pk}", "", [("Customer", rfq.email), ("Phone", rfq.phone or "-")],
              label="Quotation accepted", reference=rfq.reference)
    except Exception:  # noqa: BLE001
        logger.exception("could not tell the team that %s accepted", rfq_id)


def render_update_email(update):
    """The email the customer got for this update, as HTML (for the tracking page). The first update shows the
    receipt without its password link (that link is private and single-use); a quotation shows the quotation."""
    import base64
    import re
    from .models import SentEmail
    from .references import format_date
    from .task import LOGO_FALLBACK_URL, render_sent_html

    rfq = update.rfq
    m = re.search(r"GHP-\d{4}-[A-Z0-9]+", update.headline or "")
    if m:
        record = SentEmail.objects.filter(rfq=rfq, reference=m.group(0)).exclude(status="draft").first()
        if record:
            return render_sent_html(record)
    logo = _white_logo()
    logo_url = "data:image/png;base64," + base64.b64encode(logo).decode() if logo else LOGO_FALLBACK_URL
    first = rfq.updates.order_by("created_at").first()
    if first and first.pk == update.pk and update.stage == RFQStage.RECEIVED:
        details = [("Request", short_title(rfq)), ("Company", rfq.company),
                   ("Received", format_date(rfq.created_at)), ("Quotation within", "48 hours")]
        return _html(f"We have your request, {_first_name(rfq)}.",
                     ["Thank you. We received your request and will send your quotation within 48 hours.",
                      "Sign in to follow it, step by step."], "Track my request", f"{site_url()}/account/{rfq.pk}", "",
                     details, "Request receipt", rfq.reference, NEXT_STEPS, "", logo_url)
    p = _update_parts(update)
    return _html(p["title"], p["paragraphs"], p["button_label"], p["button_url"], p["note"], p["details"], p["label"],
                 p["reference"], p["steps"], "", logo_url)


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
