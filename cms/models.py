from django.db import models
from cloudinary.models import CloudinaryField
# Create your models here.
from django.contrib.auth.models import AbstractUser
from uuid import uuid4
from decimal import Decimal
from datetime import datetime
from zoneinfo import ZoneInfo
from django.utils import timezone

class User (AbstractUser):
    id = models.UUIDField(default=uuid4 , primary_key=True , editable=False)
    dp = CloudinaryField("profile_picture", folder="Dpimage")
    phone = models.CharField(max_length=55 , blank=False)
    
    def __str__(self):
          return self.username or self.email or str(self.id)

    
    
class Catalog(models.Model):
    
    
    class Categories(models.TextChoices):
        OFFICE_AND_STATIONERY = "OFFICE_AND_STATIONERY", "Office and Stationery"
        IT_AND_ELECTRONICS = "IT_AND_ELECTRONICS", "IT and Electronics"
        INDUSTRIAL_AND_MANUFACTURING = "INDUSTRIAL_AND_MANUFACTURING", "Industrial and Manufacturing"
        CONSTRUCTION_AND_BUILDING_MATERIALS = "CONSTRUCTION_AND_BUILDING_MATERIALS", "Construction and Building Materials"
        ELECTRICAL_AND_POWER = "ELECTRICAL_AND_POWER", "Electrical and Power"
        FURNITURE = "FURNITURE", "Furniture"
        MEDICAL_AND_LABORATORY = "MEDICAL_AND_LABORATORY", "Medical and Laboratory"
        FOOD_AND_CONSUMABLES = "FOOD_AND_CONSUMABLES", "Food and Consumables"
        
   

    id = models.UUIDField(default=uuid4 , editable=False, primary_key=True)
    name = models.CharField(max_length=200)
    #author = models.ForeignKey(User , on_delete=models.SET_NULL , null=True)
    description = models.TextField(max_length=1000 , blank=False)
 
    featured_image = CloudinaryField("Catalogs_image",folder="GHCatalogs", blank=True , null = True)
    min_quantity = models.IntegerField(default=10, blank=False)
    
    category = models.CharField(default="", max_length=50 , choices=Categories.choices, null = True)
 
    #status = models.CharField(default="" ,max_length=50, choices=Status.choices, null = True) 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    price = models.DecimalField(max_digits=10 , decimal_places=2 , default=Decimal("1.00"))
    
    
    def __str__(self):
       return self.name or str(self.id)
    

def current_year():
    """The year it is now in Lagos (so the count goes up at midnight on 1 January there)."""
    return datetime.now(ZoneInfo("Africa/Lagos")).year


class MetaData(models.Model):
      metaIntro = models.TextField(max_length=2000)
      
      metaDescription = models.TextField(max_length=5000)
      
      ordersCompleted= models.PositiveIntegerField(default=0)
      
      suppliers = models.PositiveIntegerField(default=0)
      # "years of experience" as the owner last typed it, and the year they typed it:
      # the number shown on the site goes up by one every 1 January by itself
      experience = models.SmallIntegerField(default=0)
      experience_year = models.PositiveSmallIntegerField(default=current_year, editable=False)
    
      email = models.EmailField()
      
      phone = models.CharField(max_length=55 , blank=False)
      
      office = models.CharField(max_length=1000)

      # the currency the catalog prices are shown in on the public site
      CURRENCIES = [
          ("USD", "US dollar ($)"),
          ("NGN", "Nigerian naira (₦)"),
          ("GBP", "British pound (£)"),
          ("EUR", "Euro (€)"),
          ("CNY", "Chinese yuan (¥)"),
          ("GHS", "Ghanaian cedi (GH₵)"),
          ("ZAR", "South African rand (R)"),
          ("AED", "UAE dirham (AED)"),
      ]
      currency = models.CharField(max_length=3, choices=CURRENCIES, default="USD")

      @property
      def years_of_experience(self):
          return max(0, self.experience + (current_year() - self.experience_year))
      
class Service(models.Model):
    
    id = models.UUIDField(default=uuid4 , editable=False , primary_key=True)
    title = models.CharField(max_length=1000 , blank=False)
    description = models.TextField(max_length=2000 , blank=False)
    
class FAQ(models.Model):
    
    id = models.UUIDField(default=uuid4 , editable=False , primary_key=True)
    question = models.CharField(max_length=1000 , blank=False)
    answer = models.TextField(max_length=2000 , blank=False)
    
    
class RFQStage(models.TextChoices):
    """The steps a quote request goes through, in order."""

    RECEIVED = "received", "Request received"
    QUOTED = "quoted", "Quoted"
    CONFIRMED = "confirmed", "Confirmed"
    SOURCING = "sourcing", "Sourcing"
    QUALITY = "quality", "Quality check"
    SHIPPED = "shipped", "Shipped"
    CUSTOMS = "customs", "Customs"
    DELIVERED = "delivered", "Delivered"


STAGE_ORDER = [value for value, _ in RFQStage.choices]


class RFQ(models.Model):
    id = models.UUIDField(default=uuid4,primary_key=True , editable=False)
    # PROTECT: deleting an account must never silently wipe its quote requests
    user = models.ForeignKey(User , related_name="rfqs" , on_delete=models.PROTECT)
    email = models.EmailField(blank=False)
    name = models.CharField(max_length=500 , blank = False) 
    phone = models.CharField(max_length=55 , blank=False , null = True)
    
    company= models.CharField(max_length = 500 , blank = False)
    item = models.TextField(max_length=5000 , blank=False)
    file = CloudinaryField("rfq_Image" , folder = "RFQ_Image", blank=True, null=True)

    # tracking: a reference such as RFQ-2026-0014, numbered per year
    reference = models.CharField(max_length=30, unique=True, null=True, blank=True)
    reference_year = models.PositiveSmallIntegerField(null=True, blank=True)
    reference_number = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=RFQStage.choices, default=RFQStage.RECEIVED)
    estimated_delivery = models.DateField(null=True, blank=True)
    delivery_address = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["reference_year", "reference_number"], name="unique_rfq_reference_per_year"),
        ]

    def __str__(self):
        return self.reference or f"{self.company}: {self.item[:40]}"


class RFQUpdate(models.Model):
    """One progress update on a quote request. The customer sees these on
    their tracking page; the newest one decides the request's status."""

    id = models.UUIDField(default=uuid4, primary_key=True, editable=False)
    rfq = models.ForeignKey(RFQ, related_name="updates", on_delete=models.CASCADE)
    stage = models.CharField(max_length=12, choices=RFQStage.choices)
    headline = models.CharField(max_length=200)
    details = models.TextField(blank=True)
    location = models.CharField(max_length=200, blank=True)
    emailed = models.BooleanField(default=False)  # the customer was emailed about it
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["rfq", "-created_at"])]

    def __str__(self):
        return f"{self.rfq_id} {self.stage}: {self.headline}"

class SentEmail(models.Model):
    """One row per email sent from the CMS. RFQ replies also get a reference
    number such as GHP-2026-0007, numbered per year."""

    class Kind(models.TextChoices):
        OUTREACH = "outreach", "Outreach / general"
        RFQ_REPLY = "rfq_reply", "Reply to a quote request"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    id = models.UUIDField(default=uuid4, primary_key=True, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OUTREACH)
    reference = models.CharField(max_length=30, unique=True, null=True, blank=True)
    reference_year = models.PositiveSmallIntegerField(null=True, blank=True)
    reference_number = models.PositiveIntegerField(null=True, blank=True)
    recipient = models.EmailField()
    recipient_name = models.CharField(max_length=200, blank=True)
    subject = models.CharField(max_length=200)
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    valid_days = models.PositiveSmallIntegerField(null=True, blank=True)
    attachments_count = models.PositiveSmallIntegerField(default=0)
    # [{"name": ..., "size": bytes, "type": mime}] -- names only, never the files
    attachments_info = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)
    provider_message_id = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # two emails can never share a reference number in the same year
            models.UniqueConstraint(
                fields=["reference_year", "reference_number"],
                name="unique_reference_per_year",
            ),
        ]

    def __str__(self):
        return f"{self.reference or self.get_kind_display()} to {self.recipient}"


class MFADevice(models.Model):
    """An authenticator-app (TOTP) second factor for one user. Optional: a user
    without a confirmed device signs in with the password alone."""

    user = models.OneToOneField(User, related_name="mfa", on_delete=models.CASCADE)
    secret = models.CharField(max_length=64)  # base32, shown once during setup
    confirmed = models.BooleanField(default=False)
    # last accepted 30-second step: the same code can never be used twice
    last_used_step = models.BigIntegerField(null=True, blank=True)
    recovery_hashes = models.JSONField(default=list, blank=True)  # hashes, never the codes
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"2FA for {self.user} ({'on' if self.confirmed else 'pending'})"


class InboxMessage(models.Model):
    """Mail that arrived at the company address (forwarded to Postmark and
    delivered here by its inbound webhook). Text and attachment names only:
    attachment files are never stored."""

    id = models.UUIDField(default=uuid4, primary_key=True, editable=False)
    message_id = models.CharField(max_length=255, unique=True)  # makes webhook retries harmless
    from_email = models.CharField(max_length=254, blank=True)
    from_name = models.CharField(max_length=200, blank=True)
    to = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    text_body = models.TextField(blank=True)
    attachments_info = models.JSONField(default=list, blank=True)  # [{"name","size","type"}]
    spam_score = models.FloatField(null=True, blank=True)
    is_spam = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    received_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["-received_at"]), models.Index(fields=["is_read"])]

    def __str__(self):
        return f"{self.from_email}: {self.subject[:40]}"


class AuditLog(models.Model):
    """Who did what in the CMS. Append-only: nothing in the API can change or
    delete a row. Never holds passwords, codes, message text or file contents;
    only who, what kind of action, which item (its name) and when."""

    class Action(models.TextChoices):
        SIGN_IN = "sign_in", "Signed in"
        SIGN_IN_FAILED = "sign_in_failed", "Failed sign-in"
        PASSWORD_CHANGED = "password_changed", "Changed password"
        TWO_STEP_ON = "two_step_on", "Turned two-step on"
        TWO_STEP_OFF = "two_step_off", "Turned two-step off"
        RECOVERY_CODES = "recovery_codes", "Made new recovery codes"
        CREATED = "created", "Created"
        UPDATED = "updated", "Edited"
        DELETED = "deleted", "Deleted"
        EMAIL_SENT = "email_sent", "Sent an email"
        QUOTE_RECEIVED = "quote_received", "Quote request received"

    id = models.UUIDField(default=uuid4, primary_key=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # SET_NULL + a copy of the email: the trail survives the account being deleted
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    target_type = models.CharField(max_length=40, blank=True)  # e.g. "catalog item", "admin"
    target_label = models.CharField(max_length=200, blank=True)  # its name at the time
    detail = models.CharField(max_length=500, blank=True)  # e.g. which fields changed

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"]), models.Index(fields=["action"])]

    def __str__(self):
        return f"{self.actor_email or 'someone'} {self.action} {self.target_type} {self.target_label}".strip()


class SocialFeed(models.Model):
    """The last good copy of an outside feed (the X/Twitter posts shown on the
    website) and when it may next be refreshed. Kept in the database, not in
    files, so a restart or a new deploy never makes the site hit the API again."""

    key = models.CharField(max_length=30, unique=True)
    payload = models.JSONField(default=dict, blank=True)
    external_id = models.CharField(max_length=40, blank=True)  # the account's id, looked up once
    fetched_at = models.DateTimeField(null=True, blank=True)
    next_try_at = models.DateTimeField(default=timezone.now)
    month = models.CharField(max_length=7, blank=True)  # "2026-10": which month the counter is for
    attempts_this_month = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)

    def __str__(self):
        return f"{self.key} (fetched {self.fetched_at})"
