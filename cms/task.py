from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.html import strip_tags

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