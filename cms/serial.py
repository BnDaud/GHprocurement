from rest_framework.serializers import ModelSerializer , SerializerMethodField , ImageField , Serializer , CharField , EmailField ,  ListField , FileField
from .models import User , Catalog,Service , FAQ ,MetaData , RFQ
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
    
class EmailSerial(Serializer):
    body = CharField(max_length = 5000 , required = True)
    subject = CharField(max_length = 200 , required = True)
    title = CharField(max_length = 200 , required = True)
    recipient = EmailField(required = True)
   

    attachments = ListField(
        child=FileField(),
        required=False,
        allow_empty=True
    )

    def validate_attachments(self, files):
        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

        allowed_types = [
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "image/jpeg",
            "image/png",
            "image/webp",
        ]

        for file in files:
            if file.size > MAX_FILE_SIZE:
                raise ValidationError(f"{file.name} is too large (max 10MB).")
            if file.content_type not in allowed_types:
                raise ValidationError(
                    f"{file.name} has unsupported type {file.content_type}."
                )

        return files
