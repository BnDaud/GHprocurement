from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.html import strip_tags
import httpx
import os, requests
from django.core.cache import cache



ZOHO_CLIENT_ID = os.getenv("CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")



def get_access_token():
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
    
    response = requests.post(url, data=data)
    response.raise_for_status()
    res = response.json()
    _token = res["access_token"]
    cache.set("access_token" , _token , 3500)
    return _token



def get_account_id():
    access_token = get_access_token()

    url = "https://mail.zoho.com/api/organization/909482271/accounts"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()["data"]
    # Usually first account is the primary mail account
    account_id = data[0]["accountId"]
    
    print(f"ACCOUNT ID FETCHED , {account_id}")
    return account_id



def get_cached_account_id():
    account_id = cache.get("zoho_account_id")
    if account_id:
        return account_id

    account_id = get_account_id()
    cache.set("zoho_account_id", account_id, None)  # store forever
    return account_id


def sendEMailAPI(args):
    
    access_token =  get_access_token()
    account_id =  get_cached_account_id()
    
    html_content = render_to_string("email.html" , args)
    subject = args.get("subject")
    recipient = args.get("recipient")
    from_email = settings.EMAIL_HOST_USER
    
    url = f"https://mail.zoho.com/api/accounts/{account_id}/messages"

    
    headers = {
        "Authorization" :f"Zoho-oauthtoken {access_token}",
        "Content-Type":"application/json"
    }
    
    payload = {
        "fromAddress":from_email,
        "toAddress": recipient,
        "subject" : subject,
        "content":html_content
    }
    
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    print(response.json())
    return response.json()
       
      


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