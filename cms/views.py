from django.shortcuts import render
from .models import User , Portfolio ,Blog , PortfolioImages
from .serial import UserSerial ,  PortfolioSerial , BlogSerial
# Create your views here.
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import api_view
from rest_framework.response import Response



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
    
    
@api_view(["GET"])
def getTotalView(req):
    
    blogs = Blog.objects.count()
    portfolio = Portfolio.objects.count()
    user = User.objects.count()
    
    
    return Response({"TotalBlogs": blogs ,"TotalPortfolio": portfolio , "TotalUsers":user} )
    