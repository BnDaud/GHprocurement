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


import requests
from django.http import HttpResponse , JsonResponse

def zoho_callback(request):
    code = request.GET.get("code")
    if not code:
        return JsonResponse({"error": "No code received"}, status=400)

    # Exchange code for tokens
    token_url = "https://accounts.zoho.com/oauth/v2/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": "1000.KMXUOSUEUGXAGMT1NNBVZN87XVOM8M",
        "client_secret": "d33283d6112f1f9f7a4ead126e92f14a4061c49053",
        "redirect_uri": "http://localhost:8000/api/zoho/callback/",
        "code": code
    }

    response = requests.post(token_url, data=data)
    if response.status_code != 200:
        return JsonResponse({"error": "Failed to get token", "details": response.text}, status=400)

    return JsonResponse(response.json())
urlpatterns = [path("gettotal" , getTotalView , name = "getTotal"),
path("alldata/" , AllData , name = "alldata" ), 
path("emails/" ,EmailView.as_view() ),
  path("zoho/callback/", zoho_callback),
]+ routes.urls