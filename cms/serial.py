from rest_framework.serializers import ModelSerializer , SerializerMethodField , ImageField
from .models import User , Portfolio ,PortfolioImages , Catalog,Service , FAQ ,MetaData
from django.contrib.auth.hashers import make_password



class UserSerial(ModelSerializer):
    dp = SerializerMethodField()
    
    class Meta:
        model = User
        fields = ["username",
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
        
        

class PortfolioImageSerial(ModelSerializer):
    class Meta:
        model = PortfolioImages
        
        fields = "__all__"


class PortfolioSerial(ModelSerializer):
    images = PortfolioImageSerial(many=True)
    class Meta:
        model = Portfolio
        fields = "__all__"
        
    def create(self, validated_data):
        images_data = self.context['request'].FILES.getlist('images')
        
        portfolio = Portfolio.objects.create(**validated_data)
        
        for img in images_data:
            portfolio_image = PortfolioImages.objects.create(image=img)
            portfolio.images.add(portfolio_image)
        
        return portfolio
    
    

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