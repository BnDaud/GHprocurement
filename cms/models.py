from django.db import models
from cloudinary.models import CloudinaryField
# Create your models here.
from django.contrib.auth.models import AbstractUser
from uuid import uuid4

class User (AbstractUser):
    id = models.UUIDField(default=uuid4 , primary_key=True , editable=False)
    dp = CloudinaryField("profile_picture", folder="Dpimage")
    phone = models.CharField(max_length=55 , blank=False)
    
    def __str__(self):
          return self.username or self.email or str(self.id)

    
    
    
    
class PortfolioImages(models.Model):
    id = models.UUIDField(default=uuid4 , editable=False , primary_key=True ) 
    
    image = CloudinaryField("portfolio_image" , folder = "Port_images")  
    
    
    
    def __str__(self):
         return str(self.id)
    
    class Meta:
        verbose_name_plural = "Portfolio Images" 

class Portfolio(models.Model):
    class Categories(models.TextChoices):
        FURNITURE  = "FURNITURE" ,"FURNITURE"
        ELECTRONICS = "ELECTRONICS" , "ELECTRONICS"
    
    class Status(models.TextChoices):
        DRAFT = "Draft" , "Draft"
        PUBLISHED = "Published" , "Published"
    
    id = models.UUIDField(default=uuid4 , editable=False ,  primary_key= True) 
    title = models.CharField(max_length=200 , blank=False)
    client_name = models.CharField(max_length=200 , blank=False)
    description = models.TextField(max_length=1000 , blank=False)
    #technologies = models.CharField(max_length=1000 , blank = True )
    category = models.CharField(max_length=25 , choices=Categories.choices, null=True) 
    thumbnail = CloudinaryField("thumbnail",folder="GHProthumbnails" , blank=True)
    images = models.ManyToManyField(PortfolioImages , related_name="GHProportfolioimages")
   
    status = models.CharField(default="" ,max_length=50, choices=Status.choices, null = True) #to show in home page or not
    date_completed = models.DateTimeField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    updated_at = models.DateTimeField(auto_now=True)
    
    
    def __str__(self):
         return self.title or str(self.id)
    
    
class Blog(models.Model):
    
    
    class Categories(models.TextChoices):
        FURNITURE  = "FURNITURE" ,"FURNITURE"
        ELECTRONICS = "ELECTRONICS" , "ELECTRONICS"
        
    class Status(models.TextChoices):
        DRAFT = "Draft" , "Draft"
        PUBLISHED = "Published" , "Published"

    id = models.UUIDField(default=uuid4 , editable=False, primary_key=True)
    title = models.CharField(max_length=200)
    #author = models.ForeignKey(User , on_delete=models.SET_NULL , null=True)
    content = models.TextField(max_length=10000 , blank=False)
    excerpt = models.TextField(max_length=500, blank=False)
    featured_image = CloudinaryField("blog_image",folder="GHProblogs", blank=True , null = True)
    
    category = models.CharField(default="", max_length=50 , choices=Categories.choices, null = True)
 
    status = models.CharField(default="" ,max_length=50, choices=Status.choices, null = True) 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    views_count = models.PositiveIntegerField(default=0)
    
    
    def __str__(self):
       return self.title or str(self.id)
    
    