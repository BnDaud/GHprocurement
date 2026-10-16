"""Pictures on quote requests: compressed before they are stored, served from Cloudinary with
links that never change (so browsers keep them), and deleted when the order is finished."""
import io
import logging
import urllib.request

from django.utils import timezone

logger = logging.getLogger(__name__)

FOLDER = "RFQ_Items"
MAX_RAW = 8 * 1024 * 1024        # what a customer may upload (it is shrunk straight away)
MAX_SIDE = 1200                  # stored copy: longest side in pixels
THUMB = 240                      # the small copy used in emails


def compress(file):
    """A JPEG of about 100-250 KB. Raises ValueError when the file is not a real picture."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if file.size > MAX_RAW:
        raise ValueError(f"{file.name} is too large (max {MAX_RAW // (1024 * 1024)} MB).")
    try:
        img = Image.open(file)
        img.verify()
        file.seek(0)
        img = ImageOps.exif_transpose(Image.open(file))
        if img.width * img.height > 40_000_000:
            raise ValueError(f"{file.name} is too large in pixels.")
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[3])
        flat.thumbnail((MAX_SIDE, MAX_SIDE))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError(f"{file.name} is not a valid picture.")
    out = io.BytesIO()
    flat.save(out, "JPEG", quality=78, optimize=True, progressive=True)
    return out.getvalue()


def store(data):
    """Upload compressed bytes. Returns (public_id, version) or None when Cloudinary is unavailable."""
    try:
        import cloudinary.uploader
        r = cloudinary.uploader.upload(io.BytesIO(data), folder=FOLDER, resource_type="image", unique_filename=True, overwrite=False)
        return r["public_id"], str(r.get("version", ""))
    except Exception:
        logger.exception("Could not store a request picture")
        return None


def url(image_id, version="", width=None):
    """A link that never changes for the same picture, so browsers and the CDN keep it."""
    if not image_id:
        return ""
    import cloudinary
    opts = dict(secure=True, quality="auto", fetch_format="auto", version=version or None)
    if width:
        opts.update(width=width, crop="limit")
    try:
        return cloudinary.CloudinaryImage(image_id).build_url(**opts)
    except ValueError:  # Cloudinary is not configured (local and test runs)
        return ""


def fetch_small(image_id, version=""):
    """The small copy as bytes (for putting inside an email). Empty when it cannot be fetched."""
    try:
        link = url(image_id, version, THUMB)
        if not link.startswith("https://res.cloudinary.com/"):
            return b""
        with urllib.request.urlopen(link, timeout=10) as r:  # a link we built ourselves from a stored id
            return r.read(2_000_000)
    except Exception:
        logger.warning("Could not fetch picture %s", image_id)
        return b""


def delete(image_id):
    try:
        import cloudinary.uploader
        cloudinary.uploader.destroy(image_id, invalidate=True, resource_type="image")
        return True
    except Exception:
        logger.exception("Could not delete picture %s", image_id)
        return False


def purge_rfq(rfq):
    """Delete every picture of this request (item pictures and the old single picture).
    Anything that fails stays in place and is retried the next time. Returns how many were removed."""
    removed = 0
    for item in rfq.items.exclude(image_id=""):
        if delete(item.image_id):
            item.image_id, item.image_version = "", ""
            item.save(update_fields=["image_id", "image_version"])
            removed += 1
    if rfq.file:
        pid = getattr(rfq.file, "public_id", "")
        if not pid or delete(pid):
            rfq.file = None
            rfq.save(update_fields=["file"])
            removed += 1
    if not rfq.items.exclude(image_id="").exists() and not rfq.file and not rfq.pictures_removed_at:
        rfq.pictures_removed_at = timezone.now()
        rfq.save(update_fields=["pictures_removed_at"])
    return removed


def has_pictures(rfq):
    return rfq.items.exclude(image_id="").exists() or bool(rfq.file)
