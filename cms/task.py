from django.template.loader import render_to_string
from django.conf import settings
import io
import mimetypes
from django.core.mail import EmailMultiAlternatives
import os, re, logging
from datetime import datetime
from email.mime.image import MIMEImage



logger = logging.getLogger(__name__)

LOGO_PATH = os.path.join(settings.BASE_DIR, "templates", "assets", "email-logo.png")
LOGO_CID = "ghprocurement-logo"
LOGO_FALLBACK_URL = "https://res.cloudinary.com/djasdhmmg/image/upload/v1767976601/Logo_iaaqdj.png"
SIGNATURE = "Regards,\nLawal Abdur-Razaq\nGH Procurement"


def _human_size(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _paragraphs(body):
    """Blank line = new paragraph; single newline = line break."""
    text = body.replace("\r\n", "\n").strip()
    return [p.split("\n") for p in re.split(r"\n\s*\n", text) if p.strip()]


# Shown when the CMS Settings are empty. Change them at any time under
# Settings > Contact in the CMS (they are stored in the MetaData row).
DEFAULT_OFFICE = "25 Okota Road, Isolo, Lagos"
DEFAULT_PHONE = "08057962920"
DEFAULT_EMAIL = "info@ghprocurement.com"
WEBSITE = "ghprocurement.com"


def company_info():
    """Office address, phone and email for the email header/footer, taken from
    the CMS Settings so they can be changed without touching code."""
    from .models import MetaData

    meta = MetaData.objects.first()
    pick = lambda value, default: (value or "").strip() or default
    return {
        "office": pick(getattr(meta, "office", ""), DEFAULT_OFFICE),
        "phone": pick(getattr(meta, "phone", ""), DEFAULT_PHONE),
        "contact_email": pick(getattr(meta, "email", ""), DEFAULT_EMAIL),
        "website": WEBSITE,
    }


TEMPLATES = {"outreach": "email.html", "rfq_reply": "email_rfq.html"}


def build_email(data):
    """Build (but do not send) the message: HTML + plain-text parts, an inline
    logo, and the attachments. Kept separate from sending so it can be tested."""
    from datetime import timedelta
    from .references import format_date, lagos_now

    kind = data.get("kind") or "outreach"
    title = data["title"]
    body = data["body"]
    attachments = data.get("attachments", [])
    sent_at = data.get("sent_at") or lagos_now()
    info = company_info()

    logo_bytes = None
    try:
        with open(LOGO_PATH, "rb") as fh:
            logo_bytes = fh.read()
    except OSError:
        logger.warning("Email logo %s missing; falling back to the hosted logo", LOGO_PATH)

    valid_days = data.get("valid_days") if kind == "rfq_reply" else None
    valid_until = format_date(sent_at + timedelta(days=valid_days)) if valid_days else ""

    files = [{"name": f["name"], "size": _human_size(len(f["content"]))} for f in attachments]
    context = {
        "title": title,
        "subject": data["subject"],
        "paragraphs": _paragraphs(body),
        "preheader": " ".join(body.split())[:110],
        "attachments": files,
        "year": datetime.now().year,
        "logo_cid": LOGO_CID if logo_bytes else "",
        "logo_url": LOGO_FALLBACK_URL,
        "date": format_date(sent_at),
        "reference": data.get("reference") or "",
        "prepared_for": (data.get("recipient_name") or "").strip() or data["recipient"],
        "valid_until": valid_until,
        **info,
    }
    html_content = render_to_string(TEMPLATES.get(kind, "email.html"), context)

    text_lines = []
    if kind == "rfq_reply":
        text_lines += [f"QUOTATION  {context['reference']}".strip(), f"Date: {context['date']}"]
        text_lines += [f"Prepared for: {context['prepared_for']}"]
        if valid_until:
            text_lines += [f"Valid until: {valid_until}"]
        text_lines += [""]
    text_lines += [title, "", body.strip(), ""]
    if files:
        text_lines += [f"Attachments ({len(files)}):"] + [
            f"  - {f['name']} ({f['size']})" for f in files
        ] + [""]
    text_lines += [SIGNATURE, "", f"{info['office']}", f"{info['contact_email']} | {info['phone']} | {WEBSITE}"]

    email = EmailMultiAlternatives(
        subject=data["subject"],
        body="\n".join(text_lines),
        from_email="GH Procurement <info@ghprocurement.com>",
        to=[data["recipient"]],
    )
    email.attach_alternative(html_content, "text/html")

    if logo_bytes:
        logo = MIMEImage(logo_bytes, _subtype="png")
        logo.add_header("Content-ID", f"<{LOGO_CID}>")
        logo.add_header("Content-Disposition", "inline", filename="logo.png")
        email.attach(logo)

    # attachments are DICTS (by design)
    for file in attachments:
        email.attach(
            filename=file["name"],
            content=file["content"],          # raw bytes
            mimetype=file["content_type"],
        )
    return email


def _record_outcome(record_id, **fields):
    """Update the SentEmail row from the background thread."""
    if not record_id:
        return
    from django.db import connection
    from .models import SentEmail

    try:
        SentEmail.objects.filter(pk=record_id).update(**fields)
    except Exception:
        logger.exception("Could not update SentEmail %s", record_id)
    finally:
        connection.close()  # this thread owns its own DB connection


def sendEMailAPI_Method(data):
    # this method uses postmark api to send on behalf of zoho mails because it sometimes takes attachment
    # It runs in a background thread, so the HTTP response has already gone out:
    # log the outcome so a failure is not silent.
    from django.utils import timezone

    recipient = data["recipient"]
    count = len(data.get("attachments", []))
    record_id = data.get("record_id")
    try:
        email = build_email(data)
        email.send(fail_silently=False)
    except Exception as exc:
        logger.exception("Email to %s FAILED (%d attachment(s))", recipient, count)
        _record_outcome(record_id, status="failed", error=str(exc)[:2000])
        raise
    status = getattr(email, "anymail_status", None)
    message_id = str(getattr(status, "message_id", "") or "")
    logger.info(
        "Email to %s sent (%d attachment(s)) ref=%s message_id=%s",
        recipient, count, data.get("reference"), message_id,
    )
    _record_outcome(record_id, status="sent", sent_at=timezone.now(), provider_message_id=message_id[:100])
