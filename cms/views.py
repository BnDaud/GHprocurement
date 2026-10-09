from django.shortcuts import render
from .models import User ,Catalog, FAQ , MetaData , Service , RFQ , SentEmail , InboxMessage , STAGE_ORDER
from . import tracking
from .references import create_sent_email
from .serial import InboxListSerial , InboxSerial , AdminCreateSerial , AdminSerial , SentEmailListSerial , SentEmailSerial , UserSerial , CatalogSerial , MetaDataSerial , FAQSerial , ServicesSerial , RFQSerial , EmailSerial
# Create your views here.
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet, ViewSet
from rest_framework.decorators import action
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from .fetchtwitter import FetchTwiter
from .task import  sendRFQAPI , sendEMailAPI_Method
import os , threading
from django.db.models import Q
from django.core.exceptions import ValidationError as DjangoValidationError
from .permissions import IsCMSAdminOrReadOnly, IsSuperAdmin, is_super_admin
from .audit import AuditedMixin, label_of, log, A
from .models import AuditLog

class UserView(AuditedMixin, ModelViewSet):
    """Ordinary accounts (customers and contacts). Administrators are not
    listed here and cannot be changed or deleted through this endpoint; they
    are managed under /api/admins/ by a super admin."""

    audit_name = "customer account"
    serializer_class = UserSerial
    queryset = User.objects.filter(is_staff=False, is_superuser=False)

    def destroy(self, request, *args, **kwargs):
        # Accounts created from the public quote form own their quote requests.
        # Deleting such an account would delete those requests, so refuse.
        user = self.get_object()
        count = user.rfqs.count()
        if count:
            plural = "s" if count != 1 else ""
            return Response(
                {"detail": f"This account has {count} quote request{plural}, so it cannot be deleted. "
                           f"The quote request{plural} would be lost."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)
    
   # for i in PortfolioImages.objects.all():
    #    print(i.image.url)
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data = request.data)
        serializer.is_valid()
        #print({"request data": request.data , "serializer":serializer} )
        
        return super().create(request, *args, **kwargs)


    
class CatalogView(AuditedMixin, ModelViewSet):
    audit_name = "catalog item"
    permission_classes = [IsCMSAdminOrReadOnly]  # public can read, only admins can change
    queryset = Catalog.objects.all()
    serializer_class = CatalogSerial
    
class MetaDataView(AuditedMixin, ModelViewSet):
    audit_name = "site settings"
    permission_classes = [IsCMSAdminOrReadOnly]  # public can read, only admins can change
    queryset = MetaData.objects.all()
    serializer_class = MetaDataSerial
    
class FAQView(AuditedMixin, ModelViewSet):
    audit_name = "FAQ"
    permission_classes = [IsCMSAdminOrReadOnly]  # public can read, only admins can change
    queryset = FAQ.objects.all()
    serializer_class = FAQSerial
    
class ServicesView(AuditedMixin, ModelViewSet):
    audit_name = "service"
    permission_classes = [IsCMSAdminOrReadOnly]  # public can read, only admins can change
    queryset = Service.objects.all()
    serializer_class = ServicesSerial
    
    
class RFQView(AuditedMixin, ModelViewSet):
    audit_name = "quote request"
    serializer_class = RFQSerial
    queryset = RFQ.objects.all()

    def get_permissions(self):
        # the public site submits quote requests anonymously; everything else
        # (listing customers' details, editing, deleting) is admin only
        if self.action == "create":
            return [AllowAny()]
        return super().get_permissions()
    
    
    def create(self, request, *args, **kwargs):
        serial = self.get_serializer(data = request.data)
        
        if serial.is_valid() :
            waiting = tracking.rfq_limit_reason(request, serial.validated_data["email"])
            if waiting:
                return Response({"detail": waiting}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            instance = serial.save()
            tracking.rfq_limit_count(request)
            log(None, A.QUOTE_RECEIVED, "quote request", f"{instance.reference}: {instance.name} ({instance.company})")
            tracking.in_background(tracking.email_request_received, str(instance.pk), bool(getattr(instance, "_new_account", False)))
            return Response({"reference": instance.reference, "email": instance.email, "id": str(instance.pk),
                             "new_account": bool(getattr(instance, "_new_account", False))}, status=status.HTTP_200_OK)
        
        return Response({"Error":"Bad Request", "fields": serial.errors} , status=status.HTTP_400_BAD_REQUEST)

    # ---- progress updates (admin): what the customer sees on their tracking page
    def _update_body(self, request, partial=False):
        d = request.data
        stage = str(d.get("stage", "")).strip()
        headline = str(d.get("headline", "")).strip()
        errors = {}
        if stage not in STAGE_ORDER and not (partial and "stage" not in d):
            errors["stage"] = "Choose a stage."
        if (not headline) and not (partial and "headline" not in d):
            errors["headline"] = "Add a headline."
        if len(headline) > 200:
            errors["headline"] = "Keep the headline under 200 characters."
        delivery = d.get("estimated_delivery", None)
        if delivery not in (None, ""):
            try:
                import datetime as _dt
                delivery = _dt.date.fromisoformat(str(delivery))
            except ValueError:
                errors["estimated_delivery"] = "Use the date format YYYY-MM-DD."
        return d, errors, delivery

    @action(detail=True, methods=["get", "post"], url_path="updates")
    def updates(self, request, pk=None):
        rfq = self.get_object()
        if request.method == "GET":
            return Response(tracking.request_detail(rfq)["updates"])
        d, errors, delivery = self._update_body(request)
        if errors:
            return Response({"detail": " ".join(errors.values()), "fields": errors}, status=status.HTTP_400_BAD_REQUEST)
        update = tracking.add_update(rfq, d["stage"], d["headline"], str(d.get("details", "")),
                                     str(d.get("location", "")), created_by=request.user)
        if delivery not in (None, ""):
            rfq.estimated_delivery = delivery
            rfq.save(update_fields=["estimated_delivery"])
        notify = d.get("notify", True)
        if isinstance(notify, str):
            notify = notify.lower() in ("1", "true", "yes", "on")
        log(request, A.CREATED, "order update", f"{rfq.reference}: {update.headline}",
            f"stage {update.stage}" + ("" if notify else ", customer not emailed"))
        if notify:
            tracking.in_background(tracking.email_update, str(update.pk))
        return Response(tracking.request_detail(rfq), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch", "delete"], url_path=r"updates/(?P<update_id>[0-9a-fA-F-]{32,36})")
    def update_item(self, request, pk=None, update_id=None):
        rfq = self.get_object()
        update = rfq.updates.filter(pk=update_id).first()
        if update is None:
            return Response({"detail": "Update not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            label = f"{rfq.reference}: {update.headline}"
            update.delete()
            tracking.recompute_status(rfq)
            log(request, A.DELETED, "order update", label)
            return Response(tracking.request_detail(rfq))
        d, errors, delivery = self._update_body(request, partial=True)
        if errors:
            return Response({"detail": " ".join(errors.values()), "fields": errors}, status=status.HTTP_400_BAD_REQUEST)
        changed = []
        for field in ("stage", "headline", "details", "location"):
            if field in d:
                setattr(update, field, str(d[field]).strip())
                changed.append(field)
        update.save()
        tracking.recompute_status(rfq)
        log(request, A.UPDATED, "order update", f"{rfq.reference}: {update.headline}", "fields: " + ", ".join(changed))
        return Response(tracking.request_detail(rfq))
    
@api_view(["GET"])
def getTotalView(req):
    
    blogs = Catalog.objects.count()
  
    user = User.objects.filter(is_staff=False, is_superuser=False).count()  # accounts, not admins
    services = Service.objects.count()
    faq = FAQ.objects.count()
    
  
    
    return Response({"TotalBlogs": blogs , "TotalUsers":user,
    "TotalServices":services,
    "TotalFaq" : faq} )

@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def AllData(req):  # public: feeds the customer website
    bearer = os.getenv("BEARER")

    api_key = os.getenv("API_KEY")
    api_key_secret = os.getenv("API_KEY_SECRET")
    access_token = os.getenv("ACCESS_TOKEN")
    access_token_secret=os.getenv("ACCESS_TOKEN_SECRET")
    
    
    tweet = FetchTwiter(api_key=api_key , api_secret_key=api_key_secret , access_token=access_token, access_token_secret=access_token_secret)
    
    catalog = CatalogSerial(Catalog.objects.all(), many=True).data
    service = ServicesSerial(Service.objects.all() , many=True).data
    metadata = MetaData.objects.values()
    faq = FAQSerial(FAQ.objects.all() ,many=True).data
    tweets = tweet.getTweets()    
    
    context = {
        "catalogs":catalog,
        "metadata":metadata,
        "service":service,
        "faq":faq,
        "twitter": tweets
       
    }
   
    return Response( context, status=status.HTTP_200_OK)



class EmailView(APIView):
    def post(self, request):
        serializer = EmailSerial(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated_data = serializer.validated_data

        # keep a record (and, for RFQ replies, allocate the reference number)
        record = create_sent_email(
            validated_data["kind"],
            recipient=validated_data["recipient"],
            recipient_name=validated_data.get("recipient_name", ""),
            subject=validated_data["subject"],
            title=validated_data["title"],
            body=validated_data["body"],
            attachments_info=[
                {"name": f.name, "size": f.size, "type": f.content_type}
                for f in validated_data.get("attachments", [])
            ],  # names and sizes only: the files themselves are never stored
            valid_days=validated_data.get("valid_days") if validated_data["kind"] == "rfq_reply" else None,
            attachments_count=len(validated_data.get("attachments", [])),
        )
        log(request, A.EMAIL_SENT, "email", validated_data["subject"],
            f"to {validated_data['recipient']}" + (f", {record.reference}" if record.reference else ""))
        validated_data["record_id"] = str(record.pk)
        validated_data["reference"] = record.reference
        validated_data["sent_at"] = record.created_at

        # 🔥 IMPORTANT: Copy files into memory
        attachments = []
        for f in validated_data.get("attachments", []):
            attachments.append({
                "name": f.name,
                "content": f.read(),
                "content_type": f.content_type,
            })

        validated_data["attachments"] = attachments

        threading.Thread(
            target=sendEMailAPI_Method,
            args=(validated_data,)
        ).start()

        return Response(
            {"message": "Email is being sent.", "reference": record.reference},
            status=status.HTTP_200_OK
        )



class SentEmailView(ReadOnlyModelViewSet):
    """History of emails sent from the CMS (admin only, read only)."""

    queryset = SentEmail.objects.all()
    MAX_LIST = 500

    def get_serializer_class(self):
        return SentEmailSerial if self.action == "retrieve" else SentEmailListSerial

    def list(self, request, *args, **kwargs):
        newest = self.get_queryset()[: self.MAX_LIST]
        return Response(self.get_serializer(newest, many=True).data)


class AdminView(ViewSet):
    """Administrators: the accounts that can sign in to the CMS. Only a super
    admin may list, add or remove them. Super admins themselves can never be
    deleted, by anyone."""

    permission_classes = [IsSuperAdmin]

    def _admins(self):
        return User.objects.select_related("mfa").filter(Q(is_staff=True) | Q(is_superuser=True))

    def list(self, request):
        admins = self._admins().order_by("-is_superuser", "date_joined")
        return Response(AdminSerial(admins, many=True).data)

    def create(self, request):
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        data = AdminCreateSerial(data=request.data)
        data.is_valid(raise_exception=True)
        d = data.validated_data
        email = d["email"].strip()

        if User.objects.filter(email__iexact=email).exists():
            return Response({"detail": "An account with this email already exists."},
                            status=status.HTTP_400_BAD_REQUEST)

        base = (email.split("@")[0] or "admin")[:30]
        username, n = base, 1
        while User.objects.filter(username=username).exists():
            n += 1
            username = f"{base}{n}"

        candidate = User(username=username, email=email, first_name=d["first_name"], last_name=d["last_name"])
        try:
            validate_password(d["password"], candidate)
        except DjangoValidationError as e:
            return Response({"detail": " ".join(e.messages)}, status=status.HTTP_400_BAD_REQUEST)

        # a regular admin: can use the CMS, but is not a super admin
        user = User.objects.create_user(
            username=username, email=email, password=d["password"],
            first_name=d["first_name"], last_name=d["last_name"],
            is_staff=True, is_superuser=False, dp="", phone="",
        )
        log(request, A.CREATED, "admin", email)
        return Response(AdminSerial(user).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        try:
            target = self._admins().filter(pk=pk).first()
        except (ValueError, DjangoValidationError):
            target = None
        if target is None:
            return Response({"detail": "Administrator not found."}, status=status.HTTP_404_NOT_FOUND)
        if is_super_admin(target):
            return Response({"detail": "A super admin cannot be deleted."}, status=status.HTTP_403_FORBIDDEN)
        if target.is_superuser:
            # e.g. the owner's own server account: not something to remove from the CMS
            return Response({"detail": "This account has full server access, so it cannot be deleted here."},
                            status=status.HTTP_403_FORBIDDEN)
        removed = target.email or target.username
        target.delete()
        log(request, A.DELETED, "admin", removed)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InboxView(ReadOnlyModelViewSet):
    """Mail received at the company address (admin only). An admin can mark
    mail read/unread and delete it from the CMS. Messages cannot be created or
    edited here, and deleting only removes the CMS's own copy: the company
    mailbox (Zoho) is never touched."""

    queryset = InboxMessage.objects.all()
    MAX_LIST = 500

    def get_serializer_class(self):
        return InboxSerial if self.action == "retrieve" else InboxListSerial

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        if request.query_params.get("unread") in ("1", "true"):
            qs = qs.filter(is_read=False)
        return Response(self.get_serializer(qs[: self.MAX_LIST], many=True).data)

    def partial_update(self, request, *args, **kwargs):
        """PATCH {"is_read": true|false}: nothing else can be changed."""
        message = self.get_object()
        extra = set(request.data.keys()) - {"is_read"}
        if extra or "is_read" not in request.data:
            return Response({"detail": "Only is_read can be changed."}, status=status.HTTP_400_BAD_REQUEST)
        value = request.data["is_read"]
        if isinstance(value, str):
            value = value.lower() in ("1", "true", "yes")
        message.is_read = bool(value)
        message.save(update_fields=["is_read"])
        return Response(InboxListSerial(message).data)

    def destroy(self, request, *args, **kwargs):
        message = self.get_object()
        label = f"{message.subject[:80] or '(no subject)'} from {message.from_email}"
        message.delete()
        log(request, A.DELETED, "inbox mail", label)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """POST {"ids": [...]} deletes those messages; POST {"spam": true}
        deletes every message flagged as spam. Returns how many were deleted."""
        import uuid

        ids, spam = request.data.get("ids"), request.data.get("spam")
        if isinstance(spam, str):
            spam = spam.lower() in ("1", "true", "yes")
        if spam is True and not ids:
            qs = InboxMessage.objects.filter(is_spam=True)
        elif isinstance(ids, list) and 0 < len(ids) <= 500 and spam is not True:
            try:
                clean = [uuid.UUID(str(i)) for i in ids]
            except (ValueError, AttributeError):
                return Response({"detail": "Invalid message id."}, status=status.HTTP_400_BAD_REQUEST)
            qs = InboxMessage.objects.filter(pk__in=clean)
        else:
            return Response({"detail": "Send either ids (1 to 500) or spam: true."}, status=status.HTTP_400_BAD_REQUEST)
        deleted = qs.delete()[0]
        log(request, A.DELETED, "inbox mail", f"{deleted} message{'s' if deleted != 1 else ''}", "all spam" if spam is True else "selected")
        return Response({"deleted": deleted})

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        n = InboxMessage.objects.filter(is_read=False).update(is_read=True)
        return Response({"marked": n})

    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response({
            "unread": InboxMessage.objects.filter(is_read=False, is_spam=False).count(),
            "total": InboxMessage.objects.count(),
        })



class AuditView(ReadOnlyModelViewSet):
    """The activity trail. Only a super admin can read it; nobody can change it."""

    permission_classes = [IsSuperAdmin]
    queryset = AuditLog.objects.all()
    pagination_class = None
    PAGE = 100

    def get_serializer_class(self):
        from .serial import AuditSerial
        return AuditSerial

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        action = request.query_params.get("action")
        if action:
            qs = qs.filter(action__in=[a for a in action.split(",") if a in AuditLog.Action.values])
        who = request.query_params.get("actor", "").strip()
        if who:
            qs = qs.filter(actor_email__icontains=who)
        q = request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(Q(target_label__icontains=q) | Q(detail__icontains=q) | Q(target_type__icontains=q))
        try:
            offset = max(0, int(request.query_params.get("offset", 0)))
        except ValueError:
            offset = 0
        total = qs.count()
        rows = qs[offset: offset + self.PAGE]
        return Response({"total": total, "offset": offset, "results": self.get_serializer(rows, many=True).data})
