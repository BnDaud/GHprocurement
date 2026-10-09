from django.shortcuts import render
from .models import User ,Catalog, FAQ , MetaData , Service , RFQ , SentEmail
from .references import create_sent_email
from .serial import UserSerial , CatalogSerial , MetaDataSerial , FAQSerial , ServicesSerial , RFQSerial , EmailSerial
# Create your views here.
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from .fetchtwitter import FetchTwiter
from .task import  sendRFQAPI , sendEMailAPI_Method
import os , threading
from .permissions import IsCMSAdminOrReadOnly

class UserView(ModelViewSet):
    
    serializer_class = UserSerial
    queryset = User.objects.all()
    
   # for i in PortfolioImages.objects.all():
    #    print(i.image.url)
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data = request.data)
        serializer.is_valid()
        #print({"request data": request.data , "serializer":serializer} )
        
        return super().create(request, *args, **kwargs)


    
class CatalogView(ModelViewSet):
    permission_classes = [IsCMSAdminOrReadOnly]  # public can read, only admins can change
    queryset = Catalog.objects.all()
    serializer_class = CatalogSerial
    
class MetaDataView(ModelViewSet):
    permission_classes = [IsCMSAdminOrReadOnly]  # public can read, only admins can change
    queryset = MetaData.objects.all()
    serializer_class = MetaDataSerial
    
class FAQView(ModelViewSet):
    permission_classes = [IsCMSAdminOrReadOnly]  # public can read, only admins can change
    queryset = FAQ.objects.all()
    serializer_class = FAQSerial
    
class ServicesView(ModelViewSet):
    permission_classes = [IsCMSAdminOrReadOnly]  # public can read, only admins can change
    queryset = Service.objects.all()
    serializer_class = ServicesSerial
    
    
class RFQView(ModelViewSet):
    serializer_class = RFQSerial
    queryset = RFQ.objects.all()

    def get_permissions(self):
        # the public site submits quote requests anonymously; everything else
        # (listing customers' details, editing, deleting) is admin only
        if self.action == "create":
            return [AllowAny()]
        return super().get_permissions()
    
    
    def create(self, request, *args, **kwargs):
        serial = self.get_serializer(data = request.data)
        
        data = request.data
        if serial.is_valid() :
            #print(serial.validated_data)
            instance = serial.save()
            data["image_url"] = instance.file.url
            # SendRFQ(data)
            threading.Thread(
        target=sendRFQAPI,
        args=(data,)
        ).start()
        
        
            return Response(serial.data , status=status.HTTP_200_OK)
        
        return Response({"Error":"Bad Request"} , status=status.HTTP_400_BAD_REQUEST)
    
@api_view(["GET"])
def getTotalView(req):
    
    blogs = Catalog.objects.count()
  
    user = User.objects.count()
    services = Service.objects.count()
    faq = FAQ.objects.count()
    
  
    
    return Response({"TotalBlogs": blogs , "TotalUsers":user,
    "TotalServices":services,
    "TotalFaq" : faq} )

@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def AllData(req):  # public: feeds the customer website
    bearer = os.getenv("BEARER")

    api_key = os.getenv("API_KEY")
    api_key_secret = os.getenv("API_KEY_SECRET")
    access_token = os.getenv("ACCESS_TOKEN")
    access_token_secret=os.getenv("ACCESS_TOKEN_SECRET")
    
    
    tweet = FetchTwiter(api_key=api_key , api_secret_key=api_key_secret , access_token=access_token, access_token_secret=access_token_secret)
    
    catalog = CatalogSerial(Catalog.objects.all(), many=True).data
    service = ServicesSerial(Service.objects.all() , many=True).data
    metadata = MetaData.objects.values()
    faq = FAQSerial(FAQ.objects.all() ,many=True).data
    tweets = tweet.getTweets()    
    
    context = {
        "catalogs":catalog,
        "metadata":metadata,
        "service":service,
        "faq":faq,
        "twitter": tweets
       
    }
   
    return Response( context, status=status.HTTP_200_OK)



class EmailView(APIView):
    def post(self, request):
        serializer = EmailSerial(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated_data = serializer.validated_data

        # keep a record (and, for RFQ replies, allocate the reference number)
        record = create_sent_email(
            validated_data["kind"],
            recipient=validated_data["recipient"],
            recipient_name=validated_data.get("recipient_name", ""),
            subject=validated_data["subject"],
            valid_days=validated_data.get("valid_days") if validated_data["kind"] == "rfq_reply" else None,
            attachments_count=len(validated_data.get("attachments", [])),
        )
        validated_data["record_id"] = str(record.pk)
        validated_data["reference"] = record.reference
        validated_data["sent_at"] = record.created_at

        # 🔥 IMPORTANT: Copy files into memory
        attachments = []
        for f in validated_data.get("attachments", []):
            attachments.append({
                "name": f.name,
                "content": f.read(),
                "content_type": f.content_type,
            })

        validated_data["attachments"] = attachments

        threading.Thread(
            target=sendEMailAPI_Method,
            args=(validated_data,)
        ).start()

        return Response(
            {"message": "Email is being sent.", "reference": record.reference},
            status=status.HTTP_200_OK
        )
