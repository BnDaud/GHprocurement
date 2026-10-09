from rest_framework.serializers import ChoiceField, IntegerField, ModelSerializer , SerializerMethodField , ImageField , Serializer , CharField , EmailField ,  ListField , FileField
from .models import User , Catalog,Service , FAQ ,MetaData , RFQ , SentEmail
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
        fields = "__all__"

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
    file = ImageField()
    
    class Meta:
        model = RFQ
        fields = "__all__"
        
        extra_kwargs = {"file":{"write_only":True}}
        

    def get_file_url(self , obj):
        return obj.file.url if obj.file else None
    
    def create(self, validated_data):
        
        user , created = User.objects.get_or_create(
                        username = validated_data["company"] ,
                        defaults={
                        "email":validated_data["email"] , 
                        "phone" : validated_data["phone"] , 
                        "dp" : validated_data["file"]} )
        if created:
            user.set_password(validated_data["company"])
        
            user.save()
        rfq = RFQ.objects.create(
            user=user,
            email=validated_data["email"],
            name=validated_data["name"],
            company=validated_data["company"],
            item=validated_data["item"],
            file=validated_data.get("file"),
               )
        
        
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


class SentEmailListSerial(ModelSerializer):
    """One row of the history table (no message body, to keep the list light)."""

    attachments = SerializerMethodField()

    class Meta:
        model = SentEmail
        fields = ["id", "reference", "kind", "recipient", "recipient_name", "subject",
                  "valid_days", "attachments_count", "attachments", "status",
                  "created_at", "sent_at"]

    def get_attachments(self, obj):
        return obj.attachments_info


class SentEmailSerial(SentEmailListSerial):
    """The full record, including what was written."""

    class Meta(SentEmailListSerial.Meta):
        fields = SentEmailListSerial.Meta.fields + ["title", "body", "error", "provider_message_id"]


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
