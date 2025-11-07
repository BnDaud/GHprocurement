from rest_framework.routers import DefaultRouter

from .views import UserView , PotfolioView , BlogView
from django.urls import path , include
routes = DefaultRouter()
routes.register("user", UserView , basename="users")
routes.register("portfolio",PotfolioView , basename="portfolio")
routes.register("blogs", BlogView, basename="Blogs")



urlpatterns = [
    
]+ routes.urls