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
    user = models.ForeignKey(User , related_name="rfqs" , on_delete=models.CASCADE)
    email = models.EmailField(blank=False)
    name = models.CharField(max_length=500 , blank = False) 
    phone = models.CharField(max_length=55 , blank=False , null = True)
    
    company= models.CharField(max_length = 500 , blank = False)
    item = models.TextField(max_length=5000 , blank=False)
    file = CloudinaryField("rfq_Image" , folder = "RFQ_Image")