from django.core.mail import send_mail
from django.conf import settings


def SendRFQ():
    send_mail(
        subject = "Request For Quote",
        message= "Hello Gh Procurement",
        from_email= settings.EMAIL_HOST_USER,
        recipient_list=["lawalsulaimon70@gmail.com" , "vectoredmatrix@gmail.com"],
        fail_silently= False 
    )
    