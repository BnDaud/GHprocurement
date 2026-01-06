from rest_framework.routers import DefaultRouter
from django.http import HttpResponse
from .views import UserView ,  CatalogView , getTotalView , AllData , ServicesView, FAQView ,  EmailView,MetaDataView , RFQView
from django.urls import path , include
routes = DefaultRouter()
routes.register("user", UserView , basename="users")
routes.register("catalogs", CatalogView, basename="catalogs")
routes.register("services" , ServicesView , basename="services")
routes.register("faqs" ,FAQView , basename="FAQs")
routes.register("metadata" , MetaDataView , basename="metadata")
routes.register("rfqs", RFQView , basename="rfqs")




def zoho_callback(request):
    return HttpResponse(request.GET.get("code", "No code received"))

urlpatterns = [path("gettotal" , getTotalView , name = "getTotal"),
path("alldata/" , AllData , name = "alldata" ), 
path("emails/" ,EmailView.as_view() ),
  path("zoho/callback/", zoho_callback),
]+ routes.urls