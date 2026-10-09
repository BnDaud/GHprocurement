from rest_framework.routers import DefaultRouter
from django.http import HttpResponse
from .views import InboxView , AdminView , SentEmailView , UserView ,  CatalogView , getTotalView , AllData , ServicesView, FAQView ,  EmailView,MetaDataView , RFQView , AuditView
from .auth_views import (LoginView, LoginMfaView, MeView, ChangePasswordView, MfaSetupView,
                         MfaConfirmView, MfaDisableView, MfaRecoveryCodesView)
from .inbound import postmark_inbound
from .customer_views import (CustomerLoginView, RequestLinkView, SetPasswordView, CustomerMeView,
                             CustomerRequestsView, CustomerRequestDetailView, GuestTrackView)
from django.urls import path , include
import requests , os
from django.http import HttpResponse , JsonResponse

routes = DefaultRouter()
routes.register("user", UserView , basename="users")
routes.register("catalogs", CatalogView, basename="catalogs")
routes.register("services" , ServicesView , basename="services")
routes.register("faqs" ,FAQView , basename="FAQs")
routes.register("metadata" , MetaDataView , basename="metadata")
routes.register("rfqs", RFQView , basename="rfqs")
routes.register("sent-emails", SentEmailView , basename="sent-emails")
routes.register("admins", AdminView , basename="admins")
routes.register("inbox", InboxView , basename="inbox")
routes.register("audit", AuditView , basename="audit")





ZOHO_CLIENT_ID = os.getenv("CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")

def zoho_callback(request):
    code = request.GET.get("code")
    if not code:
        return JsonResponse({"error": "No code received"}, status=400)

    # Exchange code for tokens
    token_url = "https://accounts.zoho.com/oauth/v2/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
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
  path("inbound/<str:secret>/", postmark_inbound),
  path("customer/login/", CustomerLoginView.as_view()),
  path("customer/request-link/", RequestLinkView.as_view()),
  path("customer/set-password/", SetPasswordView.as_view()),
  path("customer/me/", CustomerMeView.as_view()),
  path("customer/requests/", CustomerRequestsView.as_view()),
  path("customer/requests/<str:pk>/", CustomerRequestDetailView.as_view()),
  path("customer/track/", GuestTrackView.as_view()),
  path("auth/login/", LoginView.as_view()),
  path("auth/login/mfa/", LoginMfaView.as_view()),
  path("auth/me/", MeView.as_view()),
  path("auth/mfa/setup/", MfaSetupView.as_view()),
  path("auth/mfa/confirm/", MfaConfirmView.as_view()),
  path("auth/mfa/disable/", MfaDisableView.as_view()),
  path("auth/mfa/recovery-codes/", MfaRecoveryCodesView.as_view()),
  path("auth/change-password/", ChangePasswordView.as_view()),
  path("zoho/callback/", zoho_callback),
]+ routes.urls