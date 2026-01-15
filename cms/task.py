from django.template.loader import render_to_string
from django.conf import settings
import io
import mimetypes
from django.core.mail import EmailMultiAlternatives
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
        #print(f"from access => token {token}")
        return token
    
    response = requests.post(url, data=data)
    response.raise_for_status()
    res = response.json()
    _token = res["access_token"]
    #print(f"from access => token {_token}")
    cache.set("access_token" , _token , 3500)
    return _token



def get_account_id():
    access_token = get_access_token()

    url = "https://mail.zoho.com/api/accounts"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()["data"]
    #print(data)
    account_id = data[0]["accountId"]
    
    #print(f"ACCOUNT ID FETCHED , {account_id}")
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
    #print(response.json())
    return response.json()
       

def sendRFQAPI(args):
    access_token =  get_access_token()
    account_id =  get_cached_account_id()
    
    html_content = render_to_string("RFQtemplate.html" , args)
    subject = "Request Of Quote - Confirmed"
   
    recipient = args.get("email")
  
    from_email = settings.DEFAULT_FROM_EMAIL 
    
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
    #print(response.json())
    return response.json()
    


def sendEMailAPI_Method(data):
    subject = data["subject"]
    recipient = data["recipient"]
    attachments = data.get("attachments", [])

    html_content = render_to_string("email.html", {
        "title": data["title"],
        "body": data["body"],
    })

    email = EmailMultiAlternatives(
        subject=subject,
        body="This email requires HTML support",
        from_email="GH Procurement <info@ghprocurement.com>",
        to=[recipient],
    )

    email.attach_alternative(html_content, "text/html")

    # ✅ attachments are DICTS (by design)
    for file in attachments:
        email.attach(
            filename=file["name"],
            content=file["content"],          # raw bytes
            mimetype=file["content_type"],
        )

    email.send(fail_silently=False)