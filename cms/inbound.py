"""Incoming mail from Postmark's inbound webhook.

Zoho forwards a copy of every message sent to the company address to a Postmark
inbound address; Postmark POSTs it here as JSON. We keep the sender, subject, a
plain-text body and the attachment names/sizes. Attachment files and HTML are
never stored.
"""
import hashlib
import hmac
import json
import re
from datetime import timezone as dt_timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser

from django.conf import settings
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import InboxMessage

MAX_BODY_BYTES = 40 * 1024 * 1024   # Postmark itself caps inbound mail at 35 MB
MAX_TEXT_CHARS = 200_000
SPAM_THRESHOLD = 5.0                # Postmark/SpamAssassin's own default


class _TextExtractor(HTMLParser):
    """HTML -> readable plain text (drops scripts, styles and all markup)."""

    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html):
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html or "")
    text = unescape("".join(parser.parts)).replace("\xa0", " ")  # &nbsp; -> a normal space
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _header(payload, name):
    for h in payload.get("Headers") or []:
        if isinstance(h, dict) and str(h.get("Name", "")).lower() == name.lower():
            return str(h.get("Value", ""))
    return ""


def _received_at(payload):
    raw = payload.get("Date")
    if raw:
        try:
            dt = parsedate_to_datetime(str(raw))
            return dt if dt.tzinfo else dt.replace(tzinfo=dt_timezone.utc)
        except (TypeError, ValueError):
            pass
    return timezone.now()


def build_fields(payload):
    """Turn a Postmark inbound payload into InboxMessage fields."""
    full = payload.get("FromFull") if isinstance(payload.get("FromFull"), dict) else {}
    from_email = str(full.get("Email") or payload.get("From") or "")[:254]
    from_name = str(payload.get("FromName") or full.get("Name") or "")[:200]

    to = [str(t.get("Email")) for t in (payload.get("ToFull") or []) if isinstance(t, dict) and t.get("Email")]
    if not to and payload.get("To"):
        to = [str(payload["To"])]

    text = str(payload.get("TextBody") or "").strip()
    if not text:
        text = html_to_text(str(payload.get("HtmlBody") or ""))

    attachments = []
    for a in payload.get("Attachments") or []:
        if isinstance(a, dict):
            attachments.append({
                "name": str(a.get("Name") or "attachment")[:255],
                "size": int(a.get("ContentLength") or 0),
                "type": str(a.get("ContentType") or "")[:100],
            })  # the Content (base64 file) is deliberately dropped

    score = None
    try:
        score = float(_header(payload, "X-Spam-Score"))
    except ValueError:
        pass
    is_spam = _header(payload, "X-Spam-Status").strip().lower().startswith("yes") or (
        score is not None and score >= SPAM_THRESHOLD
    )

    return {
        "from_email": from_email,
        "from_name": from_name,
        "to": to,
        "subject": str(payload.get("Subject") or "")[:500],
        "text_body": text[:MAX_TEXT_CHARS],
        "attachments_info": attachments,
        "spam_score": score,
        "is_spam": is_spam,
        "received_at": _received_at(payload),
    }


@csrf_exempt
@require_POST
def postmark_inbound(request, secret):
    expected = getattr(settings, "POSTMARK_INBOUND_SECRET", "") or ""
    # unset or wrong secret: pretend the URL does not exist
    if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
        raise Http404

    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > MAX_BODY_BYTES:
        return JsonResponse({"detail": "Message too large."}, status=413)

    raw = request.read(MAX_BODY_BYTES + 1)  # read the stream directly: mail with attachments is big
    if len(raw) > MAX_BODY_BYTES:
        return JsonResponse({"detail": "Message too large."}, status=413)
    try:
        payload = json.loads(raw)
    except ValueError:
        return JsonResponse({"detail": "Invalid JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "Invalid payload."}, status=400)

    message_id = str(payload.get("MessageID") or "").strip()
    if not message_id:  # no id: derive a stable one so a retry is still recognised
        message_id = "sha256:" + hashlib.sha256(raw).hexdigest()

    _, created = InboxMessage.objects.get_or_create(message_id=message_id[:255], defaults=build_fields(payload))
    return JsonResponse({"ok": True, "duplicate": not created})
