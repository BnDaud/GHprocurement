"""PDF documents on an order. Kept privately on Cloudinary ("authenticated" delivery: no public link exists)
and only handed out by the API to the order's owner and the team."""
import io
import logging
import re
import urllib.request

logger = logging.getLogger(__name__)

FOLDER = "RFQ_Docs"
MAX_SIZE = 8 * 1024 * 1024


def check(file):
    """Returns an error message, or "" when the file is a real PDF of a sensible size."""
    if not file.name.lower().endswith(".pdf"):
        return "Documents must be PDF files."
    if file.size > MAX_SIZE:
        return f"That file is too large (max {MAX_SIZE // (1024 * 1024)} MB)."
    head = file.read(5)
    file.seek(0)
    if not head.startswith(b"%PDF"):
        return "That is not a valid PDF file."
    return ""


def store(file):
    """Upload privately. Returns the Cloudinary public id, or None when Cloudinary is unavailable."""
    try:
        import cloudinary.uploader
        r = cloudinary.uploader.upload(io.BytesIO(file.read()), folder=FOLDER, resource_type="raw", type="authenticated",
                                       use_filename=False, unique_filename=True, format="pdf")
        return r["public_id"]
    except Exception:
        logger.exception("Could not store a document")
        return None


def fetch(file_id):
    """The file's bytes (a signed link built by us, used only on the server). Empty when it cannot be fetched."""
    try:
        import cloudinary.utils
        url, _ = cloudinary.utils.cloudinary_url(file_id, resource_type="raw", type="authenticated", sign_url=True, secure=True)
        if not url.startswith("https://res.cloudinary.com/"):
            return b""
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.read(MAX_SIZE + 1024)
    except Exception:
        logger.warning("Could not fetch document %s", file_id)
        return b""


def delete(file_id):
    try:
        import cloudinary.uploader
        cloudinary.uploader.destroy(file_id, resource_type="raw", type="authenticated", invalidate=True)
        return True
    except Exception:
        logger.exception("Could not delete document %s", file_id)
        return False


def filename(title):
    base = re.sub(r"[^A-Za-z0-9]+", "-", title or "document").strip("-")[:60] or "document"
    return f"{base}.pdf"
