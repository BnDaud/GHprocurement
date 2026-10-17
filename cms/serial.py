from rest_framework import serializers
from rest_framework.serializers import ChoiceField, DecimalField, UUIDField, IntegerField, ModelSerializer , SerializerMethodField , ImageField , Serializer , CharField , EmailField ,  ListField , FileField
from .models import User , Catalog,Service , FAQ ,MetaData , RFQ , SentEmail , InboxMessage
from django.contrib.auth.hashers import make_password
from rest_framework.exceptions import ValidationError
import os



class UserSerial(ModelSerializer):
    dp = SerializerMethodField()
    
    class Meta:
        model = User
        fields = ["id","username",
                  "email",
                  "first_name",
                  "last_name",
                  "phone",
                  "dp",
                  "password"]

        extra_kwargs = {"password":{"write_only":True}}

    def get_dp(self, obj):
        if obj.dp :
            return obj.dp.url
        else:
            return None


    def create(self, validated_data):
        pword = validated_data.get("password")
        validated_data["password"] = make_password(pword)
        
       
        return super().create(validated_data)
    
    def update(self, instance, validated_data):
        instance.username = validated_data.get("username" , instance.username)
        instance.email = validated_data.get("email" , instance.email)
        instance.first_name = validated_data.get("first_name" ,instance.first_name)
        instance.last_name = validated_data.get("last_name", instance.last_name)
        instance.phone = validated_data.get("phone", instance.phone)
        instance.dp = validated_data.get("dp", instance.dp)
      
        pword = validated_data.get("password",None)
        if pword:
            instance.set_password(pword)
            
        
        instance.save()
        
        return instance
        #return super().update(instance, validated_data)
        
            

class CatalogSerial(ModelSerializer):
    featured_image = ImageField()
    featured_image_url = SerializerMethodField()
    class Meta:
        model = Catalog
        fields ="__all__"
        
    def get_featured_image_url(self, obj):
        return obj.featured_image.url if obj.featured_image else None
        

class MetaDataSerial(ModelSerializer):
    class Meta:
        model = MetaData
        exclude = ["experience_year"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["experience"] = instance.years_of_experience  # today's figure, not the one typed in
        return data

    def update(self, instance, validated_data):
        if "experience" in validated_data:
            from .models import current_year
            instance.experience_year = current_year()  # what the owner types is "this year's" figure
        return super().update(instance, validated_data)

class FAQSerial(ModelSerializer):
    class Meta:
        model = FAQ
        fields = "__all__"

class ServicesSerial(ModelSerializer) :
    class Meta:
        model = Service
        fields = "__all__"  



class RFQSerial(ModelSerializer):
    user = UserSerial(read_only = True) 
    file_url = SerializerMethodField(read_only = True)
    file = ImageField(required=False, allow_null=True)
    # the customer must tick the agreement before a NEW request is accepted
    consent = serializers.BooleanField(write_only=True, required=False)
    # what the customer wants, line by line (JSON text) with optional pictures by position
    items_json = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=20000)
    item_images = serializers.ListField(child=serializers.FileField(), write_only=True, required=False)
    items = SerializerMethodField(read_only=True)
    unread_messages = SerializerMethodField(read_only=True)
    documents = SerializerMethodField(read_only=True)
    item = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    
    class Meta:
        model = RFQ
        fields = "__all__"
        read_only_fields = ["reference", "reference_year", "reference_number", "status", "created_at"]
        extra_kwargs = {"file":{"write_only":True}}
        

    def get_file_url(self , obj):
        return obj.file.url if obj.file else None

    def get_documents(self, obj):
        from . import tracking
        return tracking.documents_json(obj) if obj.pk else []

    def get_unread_messages(self, obj):
        return obj.messages.filter(sender="customer", read=False).count() if obj.pk else 0

    def get_items(self, obj):
        from . import images
        return [{"id": str(i.id), "description": i.description, "quantity": f"{i.quantity.normalize():f}",
                 "has_picture": bool(i.image_id),
                 "picture": images.url(i.image_id, i.image_version, 1000),
                 "thumb": images.url(i.image_id, i.image_version, 160)} for i in obj.items.all()]

    def validate(self, attrs):
        import json
        from decimal import Decimal, InvalidOperation
        from . import images

        if self.instance is None and attrs.get("consent") is not True:
            raise serializers.ValidationError(
                {"consent": "Please agree so we can reply and create your account."})
        if self.instance is not None:
            return attrs
        raw = attrs.pop("items_json", "") or ""
        files = attrs.pop("item_images", []) or []
        rows = []
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except ValueError:
                raise serializers.ValidationError({"items_json": "The list could not be read."})
            if not isinstance(parsed, list) or not parsed or len(parsed) > 10 or not all(isinstance(r, dict) for r in parsed):
                raise serializers.ValidationError({"items_json": "Add between 1 and 10 items."})
            for n, r in enumerate(parsed, 1):
                desc = str(r.get("description", "")).strip()
                if not desc or len(desc) > 200:
                    raise serializers.ValidationError({"items_json": f"Item {n}: describe it in up to 200 characters."})
                try:
                    qty = Decimal(str(r.get("quantity", "1")).replace(",", "").strip() or "1")
                except InvalidOperation:
                    qty = Decimal(0)
                if not qty.is_finite() or qty <= 0 or qty > 1_000_000:
                    raise serializers.ValidationError({"items_json": f"Item {n}: enter a quantity."})
                idx = r.get("image")
                if idx is not None and (not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx >= len(files)):
                    raise serializers.ValidationError({"items_json": f"Item {n}: its picture is missing."})
                rows.append({"description": desc, "quantity": qty.quantize(Decimal("0.001")), "image": idx})
        elif not (attrs.get("item") or "").strip():
            raise serializers.ValidationError({"item": "Tell us what you need."})
        compressed = {}
        for r in rows:
            if r["image"] is not None and r["image"] not in compressed:
                try:
                    compressed[r["image"]] = images.compress(files[r["image"]])
                except ValueError as e:
                    raise serializers.ValidationError({"item_images": str(e)})
        attrs["_rows"], attrs["_pictures"] = rows, compressed
        note = (attrs.get("item") or "").strip()
        lines = [f"{r['quantity'].normalize():f} x {r['description']}" for r in rows]
        attrs["item"] = ("\n".join(lines) + (f"\n\n{note}" if note else "")) if lines else note
        return attrs
    
    def create(self, validated_data):
        from .references import allocate_rfq_reference
        from . import tracking

        validated_data.pop("consent", None)
        rows = validated_data.pop("_rows", [])
        pictures = validated_data.pop("_pictures", {})

        # the account is found (or made) from the EMAIL, never the company name;
        # a new one has no password until the customer picks one from the emailed link
        user, created = tracking.get_or_create_customer(
            validated_data["email"], validated_data["name"], validated_data.get("phone") or "")
        rfq = allocate_rfq_reference(
            user=user,
            email=validated_data["email"],
            name=validated_data["name"],
            phone=validated_data.get("phone"),
            company=validated_data["company"],
            item=validated_data["item"],
            file=validated_data.get("file"),
               )
        from . import images
        from .models import RFQItem
        stored = {}
        for n, r in enumerate(rows):
            if r["image"] is not None and r["image"] not in stored:
                stored[r["image"]] = images.store(pictures[r["image"]]) or ("", "")
            pid, ver = stored.get(r["image"], ("", ""))
            RFQItem.objects.create(rfq=rfq, position=n, description=r["description"], quantity=r["quantity"],
                                   image_id=pid, image_version=ver)
        tracking.add_update(rfq, "received", "We have your request")
        rfq._new_account = created
        return rfq
    
ALLOWED_ATTACHMENTS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
ALLOWED_ATTACHMENT_LABEL = "PDF, DOCX, XLSX, PPTX, JPG, PNG, WEBP"
MAX_ATTACHMENTS = 10
MAX_FILE_SIZE = 10 * 1024 * 1024    # per file
MAX_TOTAL_SIZE = 10 * 1024 * 1024   # per email (tuned after testing against Postmark)


def _looks_like(ext, head):
    if ext == ".pdf":
        return head.startswith(b"%PDF")
    if ext == ".png":
        return head.startswith(b"\x89PNG")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in (".docx", ".xlsx", ".pptx"):
        return head.startswith(b"PK\x03\x04")  # these are zip containers
    return False


def quote_limit():
    from .quote import MAX_ROWS
    return MAX_ROWS


MAX_ITEM_IMAGE = 8 * 1024 * 1024


def _thumbnail(f):
    """A small, safe JPEG of an item picture. Anything that is not a real photo is refused."""
    import io
    from PIL import Image, ImageOps, UnidentifiedImageError

    ext = os.path.splitext(f.name)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise ValidationError(f"{f.name}: pictures must be JPG, PNG or WEBP.")
    if f.size > MAX_ITEM_IMAGE:
        raise ValidationError(f"{f.name} is too large (max {MAX_ITEM_IMAGE // (1024 * 1024)} MB each).")
    try:
        img = Image.open(f)
        img.verify()
        f.seek(0)
        img = ImageOps.exif_transpose(Image.open(f))
        if img.width * img.height > 40_000_000:
            raise ValidationError(f"{f.name} is too large in pixels.")
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[3])
        flat.thumbnail((240, 240))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValidationError(f"{f.name} is not a valid picture.")
    out = io.BytesIO()
    flat.save(out, "JPEG", quality=82, optimize=True)
    return out.getvalue()


class EmailSerial(Serializer):
    body = CharField(max_length = 5000 , required = True)
    subject = CharField(max_length = 200 , required = True)
    title = CharField(max_length = 200 , required = True)
    recipient = EmailField(required = True)
    # which template to use: a reply to a quote request, or general outreach
    kind = ChoiceField(choices=["outreach", "rfq_reply"], required=False, default="outreach")
    # who the quotation is "prepared for" (falls back to the email address)
    recipient_name = CharField(max_length=200, required=False, allow_blank=True, default="")
    # optional: how many days the quote stays valid (RFQ replies only)
    valid_days = IntegerField(min_value=1, max_value=365, required=False, allow_null=True, default=None)
    # the price table of a quotation: rows as JSON text, added up on the server
    items = CharField(required=False, allow_blank=True, default="")
    currency = CharField(max_length=3, required=False, allow_blank=True, default="")
    shipping = CharField(required=False, allow_blank=True, default="")
    vat_percent = CharField(required=False, allow_blank=True, default="")
    # optional pictures of the items: row N points at one of these by position
    item_images = ListField(child=FileField(), required=False, allow_empty=True)
    # the order (quote request) this quotation is for
    rfq = UUIDField(required=False, allow_null=True, default=None)
    # sending a saved draft: the draft is removed once the email is on its way
    draft_id = UUIDField(required=False, allow_null=True, default=None)

    def validate_item_images(self, files):
        if len(files) > quote_limit():
            raise ValidationError("Too many item pictures.")
        thumbs = []
        for f in files:
            thumbs.append(_thumbnail(f))
        return thumbs

    def validate_currency(self, value):
        value = value.strip().upper()
        if value and value not in dict(MetaData.CURRENCIES):
            raise ValidationError("Choose one of the listed currencies.")
        return value

    def validate(self, attrs):
        from decimal import Decimal
        from . import quote

        kind = attrs.get("kind") or "outreach"
        rows = quote.parse_items(attrs.get("items"))
        if (rows or attrs.get("rfq")) and kind != "rfq_reply":
            raise ValidationError({"items": "A price table and an order can only go with a reply to a quote request."})
        shipping = quote.optional_amount(attrs.get("shipping"), "Delivery", Decimal(1_000_000_000))
        vat = quote.optional_amount(attrs.get("vat_percent"), "VAT", Decimal(100))
        if (shipping or vat) and not rows:
            raise ValidationError({"items": "Add at least one row to the price table, or clear delivery and VAT."})
        currency = attrs.get("currency") or (MetaData.objects.values_list("currency", flat=True).first() or "USD")
        thumbs = attrs.get("item_images") or []
        for n, r in enumerate(rows, 1):
            if r["image"] is not None and r["image"] >= len(thumbs):
                raise ValidationError({"items": f"Row {n}: its picture is missing."})
        order_thumbs = {}
        wanted = [(n, r["rfq_item"]) for n, r in enumerate(rows) if r["image"] is None and r["rfq_item"]]
        if wanted:
            from . import images
            from .models import RFQItem
            if not attrs.get("rfq"):
                raise ValidationError({"items": "Order pictures can only be used when the quotation is for that order."})
            for n, item_id in wanted:
                try:
                    item = RFQItem.objects.filter(pk=item_id, rfq_id=attrs["rfq"]).first()
                except Exception:
                    item = None
                data = images.fetch_small(item.image_id, item.image_version) if item and item.image_id else b""
                if data:
                    order_thumbs[n] = data
                    rows[n]["image"] = -1  # marks "has a picture" for the saved table
        attrs["quote"] = quote.build(rows, currency, shipping, vat)
        # row position -> thumbnail bytes (shown inside the table, never listed as an attachment)
        attrs["item_thumbs"] = {**{n: thumbs[r["image"]] for n, r in enumerate(rows) if r["image"] is not None and r["image"] >= 0}, **order_thumbs}
        if attrs.get("rfq"):
            from .models import RFQ
            found = RFQ.objects.filter(pk=attrs["rfq"]).first()
            if found is None:
                raise ValidationError({"rfq": "That order was not found."})
            attrs["rfq"] = found
        return attrs

    attachments = ListField(
        child=FileField(),
        required=False,
        allow_empty=True
    )

    def validate_attachments(self, files):
        if len(files) > MAX_ATTACHMENTS:
            raise ValidationError(f"Attach at most {MAX_ATTACHMENTS} files.")

        total = 0
        for file in files:
            ext = os.path.splitext(file.name)[1].lower()
            mime = ALLOWED_ATTACHMENTS.get(ext)
            if mime is None:
                raise ValidationError(
                    f"{file.name}: {ext or 'this'} files are not allowed. "
                    f"Allowed: {ALLOWED_ATTACHMENT_LABEL}."
                )
            if file.size > MAX_FILE_SIZE:
                raise ValidationError(f"{file.name} is too large (max {MAX_FILE_SIZE // (1024 * 1024)} MB each).")

            # the extension decides; the browser-reported type must not contradict it
            if file.content_type not in (mime, "application/octet-stream", ""):
                raise ValidationError(f"{file.name} does not match its {ext} file type.")
            # and the first bytes must really look like that kind of file
            head = file.read(12)
            file.seek(0)
            if not _looks_like(ext, head):
                raise ValidationError(f"{file.name} is not a valid {ext} file.")

            file.content_type = mime  # use our own canonical type from here on
            total += file.size

        if total > MAX_TOTAL_SIZE:
            raise ValidationError(
                f"Attachments total {total / (1024 * 1024):.1f} MB; "
                f"the limit is {MAX_TOTAL_SIZE // (1024 * 1024)} MB per email."
            )
        return files


class DraftSerial(Serializer):
    """A draft is allowed to be unfinished: only the shape and sizes are checked, nothing is required."""
    kind = ChoiceField(choices=["outreach", "rfq_reply"], required=False, default="outreach")
    recipient = CharField(max_length=254, required=False, allow_blank=True, default="")
    recipient_name = CharField(max_length=200, required=False, allow_blank=True, default="")
    subject = CharField(max_length=200, required=False, allow_blank=True, default="")
    title = CharField(max_length=200, required=False, allow_blank=True, default="")
    body = CharField(max_length=5000, required=False, allow_blank=True, default="")
    valid_days = IntegerField(min_value=1, max_value=365, required=False, allow_null=True, default=None)
    items = CharField(max_length=60000, required=False, allow_blank=True, default="")
    currency = CharField(max_length=3, required=False, allow_blank=True, default="")
    shipping = CharField(max_length=20, required=False, allow_blank=True, default="")
    vat_percent = CharField(max_length=20, required=False, allow_blank=True, default="")
    rfq = UUIDField(required=False, allow_null=True, default=None)
    draft_id = UUIDField(required=False, allow_null=True, default=None)

    def validate_currency(self, value):
        value = value.strip().upper()
        if value and value not in dict(MetaData.CURRENCIES):
            raise ValidationError("Choose one of the listed currencies.")
        return value

    def validate_items(self, value):
        import json
        if not value.strip():
            return ""
        try:
            rows = json.loads(value)
        except ValueError:
            raise ValidationError("The price table could not be read.")
        if not isinstance(rows, list) or len(rows) > quote_limit() or not all(isinstance(r, dict) for r in rows):
            raise ValidationError("The price table could not be read.")
        return json.dumps([{k: str(v)[:500 if k == "thumb" else 200] for k, v in r.items() if k in ("description", "quantity", "unit_price", "rfq_item", "thumb")} for r in rows])

    def validate(self, attrs):
        if attrs.get("rfq"):
            found = RFQ.objects.filter(pk=attrs["rfq"]).first()
            if found is None:
                raise ValidationError({"rfq": "That order was not found."})
            attrs["rfq"] = found
        return attrs


class SentEmailListSerial(ModelSerializer):
    """One row of the history table (no message body, to keep the list light)."""

    attachments = SerializerMethodField()

    class Meta:
        model = SentEmail
        fields = ["id", "reference", "kind", "recipient", "recipient_name", "subject",
                  "valid_days", "attachments_count", "attachments", "status",
                  "created_at", "sent_at", "order_reference", "total"]

    order_reference = SerializerMethodField()
    total = SerializerMethodField()

    def get_order_reference(self, obj):
        return obj.rfq.reference if obj.rfq_id else ""

    def get_total(self, obj):
        return (obj.quote or {}).get("total_text", "")

    def get_attachments(self, obj):
        return obj.attachments_info


class SentEmailSerial(SentEmailListSerial):
    """The full record, including what was written."""

    class Meta(SentEmailListSerial.Meta):
        fields = SentEmailListSerial.Meta.fields + ["title", "body", "error", "provider_message_id", "quote", "draft", "rfq"]


class AdminSerial(ModelSerializer):
    """How an administrator is shown in the CMS (never includes the password)."""

    name = SerializerMethodField()
    role = SerializerMethodField()
    mfa_enabled = SerializerMethodField()
    protected = SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "username", "first_name", "last_name", "name", "role",
                  "protected", "mfa_enabled", "is_active", "date_joined", "last_login"]

    def get_protected(self, obj):
        # accounts with server-level access can never be deleted from the CMS
        return bool(obj.is_superuser)

    def get_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()

    def get_role(self, obj):
        from .permissions import is_super_admin
        return "super_admin" if is_super_admin(obj) else "admin"

    def get_mfa_enabled(self, obj):
        device = getattr(obj, "mfa", None)
        return bool(device and device.confirmed)


class AdminCreateSerial(Serializer):
    email = EmailField(required=True)
    password = CharField(required=True, write_only=True, trim_whitespace=False, max_length=128)
    first_name = CharField(required=False, allow_blank=True, max_length=150, default="")
    last_name = CharField(required=False, allow_blank=True, max_length=150, default="")


class InboxListSerial(ModelSerializer):
    """One row of the inbox (no full body, to keep the list light)."""

    snippet = SerializerMethodField()
    attachments_count = SerializerMethodField()

    class Meta:
        model = InboxMessage
        fields = ["id", "from_email", "from_name", "subject", "snippet", "received_at",
                  "is_read", "is_spam", "attachments_count"]

    def get_snippet(self, obj):
        return " ".join(obj.text_body.split())[:160]

    def get_attachments_count(self, obj):
        return len(obj.attachments_info or [])


class InboxSerial(InboxListSerial):
    """A whole message, for reading."""

    attachments = SerializerMethodField()

    class Meta(InboxListSerial.Meta):
        fields = InboxListSerial.Meta.fields + ["to", "text_body", "attachments", "spam_score"]

    def get_attachments(self, obj):
        return obj.attachments_info


class AuditSerial(ModelSerializer):
    action_label = SerializerMethodField()

    class Meta:
        from .models import AuditLog as _A
        model = _A
        fields = ["id", "created_at", "actor_email", "action", "action_label", "target_type", "target_label", "detail"]

    def get_action_label(self, obj):
        return obj.get_action_display()
