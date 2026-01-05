from django.shortcuts import render
from .models import User ,Catalog, FAQ , MetaData , Service , RFQ
from .serial import UserSerial , CatalogSerial , MetaDataSerial , FAQSerial , ServicesSerial , RFQSerial , EmailSerial
# Create your views here.
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .fetchtwitter import FetchTwiter
from .task import SendRFQ , Sendemail
import os

class UserView(ModelViewSet):
    
    serializer_class = UserSerial
    queryset = User.objects.all()
    
   # for i in PortfolioImages.objects.all():
    #    print(i.image.url)
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data = request.data)
        if serializer.is_valid():
        #print({"request data": request.data , "serializer":serializer} )
        
            return super().create(request, *args, **kwargs)


    
class CatalogView(ModelViewSet):
    queryset = Catalog.objects.all()
    serializer_class = CatalogSerial
    
class MetaDataView(ModelViewSet):
    queryset = MetaData.objects.all()
    serializer_class = MetaDataSerial
    
class FAQView(ModelViewSet):
    queryset = FAQ.objects.all()
    serializer_class = FAQSerial
    
class ServicesView(ModelViewSet):
    queryset = Service.objects.all()
    serializer_class = ServicesSerial
    
    
class RFQView(ModelViewSet):
    serializer_class = RFQSerial
    queryset = RFQ.objects.all()
    
    
    def create(self, request, *args, **kwargs):
        serial = self.get_serializer(data = request.data)
        
        data = request.data
        serial.is_valid()
        instance = serial.save()
        data["image_url"] = instance.file.url
        SendRFQ(data)
        
        
        
        return Response(serial.data , status=status.HTTP_200_OK)
    
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
def AllData(req):
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
  def post(self , request):
      serial = EmailSerial(data = request.data)
      serial.is_valid()
      
      Sendemail(serial.validated_data)
      

      
      return Response(serial.validated_data , status=status.HTTP_200_OK)