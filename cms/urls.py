from rest_framework.routers import DefaultRouter

from .views import UserView ,  CatalogView , getTotalView , AllData , ServicesView, FAQView ,  EmailView,MetaDataView , RFQView
from django.urls import path , include
routes = DefaultRouter()
routes.register("user", UserView , basename="users")
routes.register("catalogs", CatalogView, basename="catalogs")
routes.register("services" , ServicesView , basename="services")
routes.register("faqs" ,FAQView , basename="FAQs")
routes.register("metadata" , MetaDataView , basename="metadata")
routes.register("rfqs", RFQView , basename="rfqs")

urlpatterns = [path("gettotal" , getTotalView , name = "getTotal"),
path("alldata/" , AllData , name = "alldata" ), 
path("emails/" ,EmailView.as_view() )
]+ routes.urls