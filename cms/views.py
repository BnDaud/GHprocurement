from django.shortcuts import render
from .models import User , Portfolio ,Blog , PortfolioImages , FAQ , MetaData , Service
from .serial import UserSerial ,  PortfolioSerial , BlogSerial , MetaDataSerial , FAQSerial , ServicesSerial
# Create your views here.
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .fetchtwitter import FetchTwiter

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


class PotfolioView(ModelViewSet):
    queryset = Portfolio.objects.all()
    serializer_class = PortfolioSerial
    
class BlogView(ModelViewSet):
    queryset = Blog.objects.all()
    serializer_class = BlogSerial
    
class MetaDataView(ModelViewSet):
    queryset = MetaData.objects.all()
    serializer_class = MetaDataSerial
    
class FAQView(ModelViewSet):
    queryset = FAQ.objects.all()
    serializer_class = FAQSerial
    
class ServicesView(ModelViewSet):
    queryset = Service.objects.all()
    serializer_class = ServicesSerial
    
    
@api_view(["GET"])
def getTotalView(req):
    
    blogs = Blog.objects.count()
    portfolio = Portfolio.objects.count()
    user = User.objects.count()
    services = Service.objects.count()
    faq = FAQ.objects.count()
    
  
    
    return Response({"TotalBlogs": blogs ,"TotalPortfolio": portfolio , "TotalUsers":user,
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
    
    blogs = BlogSerial(Blog.objects.filter(status = "Published") , many=True).data
    service = ServicesSerial(Service.objects.all() , many=True).data
    metadata = MetaData.objects.values()
    faq = FAQSerial(FAQ.objects.all() ,many=True).data
    tweets = tweet.getTweets()    
    
    context = {
        "blogs":blogs,
        "metadata":metadata,
        "service":service,
        "faq":faq,
        "twitter": tweets
       
    }
    
   
    return Response( context, status=status.HTTP_200_OK)