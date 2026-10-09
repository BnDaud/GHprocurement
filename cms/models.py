from django.db import models
from cloudinary.models import CloudinaryField
# Create your models here.
from django.contrib.auth.models import AbstractUser
from uuid import uuid4
from decimal import Decimal

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
       return self.title or str(self.id)
    

class MetaData(models.Model):
      metaIntro = models.TextField(max_length=2000)
      
      metaDescription = models.TextField(max_length=5000)
      
      ordersCompleted= models.PositiveIntegerField(default=0)
      
      suppliers = models.PositiveIntegerField(default=0)
      experience = models.SmallIntegerField(default=0)
    
      email = models.EmailField()
      
      phone = models.CharField(max_length=55 , blank=False)
      
      office = models.CharField(max_length=1000)
      
class Service(models.Model):
    
    id = models.UUIDField(default=uuid4 , editable=False , primary_key=True)
    title = models.CharField(max_length=1000 , blank=False)
    description = models.TextField(max_length=2000 , blank=False)
    
class FAQ(models.Model):
    
    id = models.UUIDField(default=uuid4 , editable=False , primary_key=True)
    question = models.CharField(max_length=1000 , blank=False)
    answer = models.TextField(max_length=2000 , blank=False)
    
    
class RFQ(models.Model):
    id = models.UUIDField(default=uuid4,primary_key=True , editable=False)
    # PROTECT: deleting an account must never silently wipe its quote requests
    user = models.ForeignKey(User , related_name="rfqs" , on_delete=models.PROTECT)
    email = models.EmailField(blank=False)
    name = models.CharField(max_length=500 , blank = False) 
    phone = models.CharField(max_length=55 , blank=False , null = True)
    
    company= models.CharField(max_length = 500 , blank = False)
    item = models.TextField(max_length=5000 , blank=False)
    file = CloudinaryField("rfq_Image" , folder = "RFQ_Image")

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
