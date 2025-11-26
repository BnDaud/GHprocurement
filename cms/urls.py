from rest_framework.routers import DefaultRouter

from .views import UserView , PotfolioView , BlogView , getTotalView
from django.urls import path , include
routes = DefaultRouter()
routes.register("user", UserView , basename="users")
routes.register("portfolio",PotfolioView , basename="portfolio")
routes.register("blogs", BlogView, basename="Blogs")



urlpatterns = [path("gettotal" , getTotalView , name = "getTotal")
    
]+ routes.urls