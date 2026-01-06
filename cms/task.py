from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.html import strip_tags
import httpx
import os
from django.core.cache import cache



ZOHO_CLIENT_ID = os.getenv("CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")



async def get_access_token():
    url = "https://accounts.zoho.com/oauth/v2/token"
    data = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }

    token = cache.get("access_token")
    
    if token:
        return token

    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=data)
        response.raise_for_status()
        res = response.json()
        _token = res["access_token"]
        cache.set("access_token" , _token , 3500)
        return _token



async def sendEMailAPI(args):
    
    
    html_content = render_to_string("email.html" , args)
    subject = args.get("subject")
    


def SendRFQ(arg ):
    
    subject =  "Request Of Quote - Confirmed"
    from_ = settings.EMAIL_HOST_USER
    to_email = [arg.get("email")]
    
    html_content = render_to_string("RFQtemplate.html" , arg)
    
    text_content = strip_tags(html_content)

    email = EmailMultiAlternatives(
        subject=subject,
        body = text_content,
        from_email= from_,
        to=to_email
    )
    
    email.attach_alternative(html_content , "text/html")
    email.send()
    
def Sendemail(arg):
    subject = arg.get("subject")
    from_ = settings.EMAIL_HOST_USER
    to_ = [arg.get("recipient")]
    html_content = render_to_string("email.html" , arg)
    
    text_content = strip_tags(html_content)
    
    email = EmailMultiAlternatives(subject=subject,
                                   body= text_content, from_email= from_, 
                                   to=to_)
    
    email.attach_alternative(html_content , "text/html")
    
    email.send()