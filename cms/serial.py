from rest_framework.serializers import ModelSerializer
from .models import User , Portfolio ,PortfolioImages , Blog
from django.contrib.auth.hashers import make_password

class UserSerial(ModelSerializer):
    
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
        images = validated_data.pop("images",None)
        portfolio = Portfolio.objects.create(**validated_data)
        print(images)
        for image in images:
            image_add = PortfolioImages.objects.create(**image)
            
            portfolio.images.add(image_add)
          
          
        portfolio.save()   
        return portfolio
    
    

class BlogSerial(ModelSerializer):
    class Meta:
        model = Blog
        fields = "__all__"
        
        