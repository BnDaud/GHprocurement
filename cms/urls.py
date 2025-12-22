from rest_framework.routers import DefaultRouter

from .views import UserView , PotfolioView , CatalogView , getTotalView , AllData , ServicesView, FAQView , MetaDataView
from django.urls import path , include
routes = DefaultRouter()
routes.register("user", UserView , basename="users")
routes.register("portfolio",PotfolioView , basename="portfolio")
routes.register("catalogs", CatalogView, basename="catalogs")
routes.register("services" , ServicesView , basename="services")
routes.register("faqs" ,FAQView , basename="FAQs")
routes.register("metadata" , MetaDataView , basename="metadata")

urlpatterns = [path("gettotal" , getTotalView , name = "getTotal"),
path("alldata/" , AllData , name = "alldata" ), 
]+ routes.urls