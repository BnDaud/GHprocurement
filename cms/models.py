from django.db import models

# Create your models here.
from django.contrib.auth.models import AbstractUser
from uuid import uuid4

class User (AbstractUser):
    id = models.UUIDField(default=uuid4 , primary_key=True , editable=False)
    dp = models.ImageField(blank=False , upload_to="Dpimage/")
    phone = models.CharField(max_length=55 , blank=False)
    
    
    
    
class PortfolioImages(models.Model):
    id = models.UUIDField(default=uuid4 , editable=False , primary_key=True ) 
    
    image = models.ImageField(upload_to="images/")  
    
    class Meta:
        verbose_name_plural = "Portfolio Images" 

class Portfolio(models.Model):
    class Categories(models.TextChoices):
        FURNITURE  = "FURNITURE" ,"FURNITURE"
        ELECTRONICS = "ELECTRONICS" , "ELECTRONICS"
    
    
    id = models.UUIDField(default=uuid4 , editable=False ,  primary_key= True) 
    title = models.CharField(max_length=50 , blank=False)
    client_name = models.CharField(max_length=50 , blank=False)
    description = models.TextField(max_length=1000 , blank=False)
    #technologies = models.CharField(max_length=1000 , blank = True )
    category = models.CharField(max_length=25 , choices=Categories.choices, null=True) 
    thumbnail = models.ImageField(upload_to="thumbnails/" , blank=True)
    images = models.ManyToManyField(PortfolioImages , related_name="portfolioimages")
    #project_url = models.URLField(default="" , blank=True)
    is_featured = models.BooleanField(default=False) #to show in home page or not
    date_completed = models.DateTimeField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    updated_at = models.DateTimeField(auto_now=True)
    
    
    def __str__(self):
        return self.title
    
    
class Blog(models.Model):
    
    
    class Categories(models.TextChoices):
        DIGITAL_MARKETING = "DIGITAL MARKETING", "DIGITAL MARKETING"
        SEO_OPTIMIZATION = "SEO OPTIMIZATION", "SEO OPTIMIZATION"
        AI_MARKETING = "AI MARKETING", "AI MARKETING"
        BRAND_STRATEGY = "BRAND STRATEGY", "BRAND STRATEGY"
        WEB_DESIGN = "WEB DESIGN", "WEB DESIGN"
        SOCIAL_MEDIA = "SOCIAL MEDIA", "SOCIAL MEDIA"
        EMAIL_CAMPAIGNS = "EMAIL CAMPAIGNS", "EMAIL CAMPAIGNS"
        INFLUENCER_MARKETING = "INFLUENCER MARKETING", "INFLUENCER MARKETING"
        CONTENT_CREATION = "CONTENT CREATION", "CONTENT CREATION"
        PAID_ADVERTISING = "PAID ADVERTISING", "PAID ADVERTISING"
        
    class Tag(models.TextChoices):
        DIGITAL_MARKETING = "digital-marketing", "Digital Marketing"
        SEO_OPTIMIZATION = "seo-optimization", "SEO Optimization"
        AI_MARKETING = "ai-marketing", "AI Marketing"
        BRAND_STRATEGY = "brand-strategy", "Brand Strategy"
        WEB_DESIGN = "web-design", "Web Design"
        SOCIAL_MEDIA = "social-media", "Social Media"
        EMAIL_CAMPAIGNS = "email-campaigns", "Email Campaigns"
        INFLUENCER_MARKETING = "influencer-marketing", "Influencer Marketing"
        CONTENT_CREATION = "content-creation", "Content Creation"
        PAID_ADVERTISING = "paid-advertising", "Paid Advertising"

    id = models.UUIDField(default=uuid4 , editable=False, primary_key=True)
    title = models.CharField(max_length=50)
    #author = models.ForeignKey(User , on_delete=models.SET_NULL , null=True)
    content = models.TextField(max_length=10000 , blank=False)
    excerpt = models.TextField(max_length=500, blank=False)
    featured_image = models.ImageField(upload_to="blogs/", blank=True)
    
    #category = models.CharField(max_length=50 , choices=Categories.choices)
    #tags = models.CharField(max_length=50 , choices=Tag.choices)
    is_published = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    views_count = models.PositiveIntegerField(default=0)
    
    
    def __str__(self):
        return f"{self.title}"
    
    