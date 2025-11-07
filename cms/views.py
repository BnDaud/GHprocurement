from django.shortcuts import render
from .models import User , Portfolio ,Blog
from .serial import UserSerial ,  PortfolioSerial , BlogSerial
# Create your views here.
from rest_framework.viewsets import ModelViewSet



class UserView(ModelViewSet):
    
    serializer_class = UserSerial
    queryset = User.objects.all()
    
    
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