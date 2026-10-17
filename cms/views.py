from django.shortcuts import render
from .models import User ,Catalog, FAQ , MetaData , Service , RFQ , SentEmail , InboxMessage , STAGE_ORDER
from . import tracking
from .references import create_sent_email
from .serial import InboxListSerial , InboxSerial , AdminCreateSerial , AdminSerial , SentEmailListSerial , SentEmailSerial , UserSerial , CatalogSerial , MetaDataSerial , FAQSerial , ServicesSerial , RFQSerial , EmailSerial
# Create your views here.
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet, ViewSet
from rest_framework.decorators import action
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.utils import timezone
from rest_framework import status
from .fetchtwitter import FetchTwiter
from .task import  sendEMailAPI_Method
import os , threading
from django.conf import settings
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
            # one confirmation email, sent through Postmark (never Zoho)
            tracking.in_background(tracking.email_request_received, str(instance.pk), bool(getattr(instance, "_new_account", False)))
            return Response({"reference": instance.reference, "email": instance.email, "id": str(instance.pk),
                             "new_account": bool(getattr(instance, "_new_account", False))}, status=status.HTTP_200_OK)
        
        return Response({"Error":"Bad Request", "fields": serial.errors} , status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["get", "post"], url_path="documents")
    def documents(self, request, pk=None):
        """PDFs for the customer to download (invoice, packing list, airway bill...)."""
        from . import documents as docs
        from .models import OrderDocument
        rfq = self.get_object()
        if request.method == "GET":
            return Response(tracking.documents_json(rfq))
        file = request.FILES.get("file")
        if file is None:
            return Response({"detail": "Choose a PDF file."}, status=status.HTTP_400_BAD_REQUEST)
        problem = docs.check(file)
        if problem:
            return Response({"detail": problem}, status=status.HTTP_400_BAD_REQUEST)
        if rfq.documents.count() >= 20:
            return Response({"detail": "An order can have up to 20 documents."}, status=status.HTTP_400_BAD_REQUEST)
        kind = str(request.data.get("kind", "other"))
        if kind not in OrderDocument.Kind.values:
            kind = "other"
        title = str(request.data.get("title", "")).strip()[:120] or dict(OrderDocument.Kind.choices)[kind]
        file_id = docs.store(file)
        if not file_id:
            return Response({"detail": "The file could not be saved. Please try again."}, status=status.HTTP_502_BAD_GATEWAY)
        doc = OrderDocument.objects.create(rfq=rfq, kind=kind, title=title, file_id=file_id, size=file.size, uploaded_by=request.user)
        log(request, A.CREATED, "order document", rfq.reference, f"added {title}")
        return Response(tracking.documents_json(rfq)[-1] | {"id": str(doc.pk)}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "delete"], url_path=r"documents/(?P<doc_id>[0-9a-fA-F-]{32,36})")
    def document_item(self, request, pk=None, doc_id=None):
        from django.http import HttpResponse
        from . import documents as docs
        rfq = self.get_object()
        doc = rfq.documents.filter(pk=doc_id).first()
        if doc is None:
            return Response({"detail": "Document not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            docs.delete(doc.file_id)
            log(request, A.DELETED, "order document", rfq.reference, f"removed {doc.title}")
            doc.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        data = docs.fetch(doc.file_id)
        if not data:
            return Response({"detail": "The file could not be loaded."}, status=status.HTTP_502_BAD_GATEWAY)
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{docs.filename(doc.title)}"'
        response["Cache-Control"] = "private, no-store"
        return response

    @action(detail=True, methods=["get", "post"], url_path="messages")
    def messages(self, request, pk=None):
        """The thread with the customer. Reading it marks the customer's messages as read."""
        from .models import OrderMessage
        rfq = self.get_object()
        if request.method == "GET":
            rfq.messages.filter(sender="customer", read=False).update(read=True)
            return Response([tracking.message_json(m, admin=True) for m in rfq.messages.select_related("author")])
        body = str(request.data.get("body", "")).strip()
        if not body:
            return Response({"detail": "Write a reply first."}, status=status.HTTP_400_BAD_REQUEST)
        if len(body) > tracking.MESSAGE_MAX:
            return Response({"detail": f"Keep it under {tracking.MESSAGE_MAX} characters."}, status=status.HTTP_400_BAD_REQUEST)
        m = OrderMessage.objects.create(rfq=rfq, sender="team", author=request.user, body=body, read=False)
        rfq.messages.filter(sender="customer", read=False).update(read=True)  # answering means they were seen
        log(request, A.CREATED, "order message", rfq.reference, "replied to the customer")
        tracking.in_background(tracking.email_customer_message, str(m.pk))
        return Response(tracking.message_json(m, admin=True), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="remove-pictures")
    def remove_pictures(self, request, pk=None):
        """Delete the customer's pictures of this request now (they are also deleted after delivery)."""
        from . import images
        rfq = self.get_object()
        count = images.purge_rfq(rfq)
        log(request, A.DELETED, "quote request", f"{rfq.reference}: {count} picture(s) removed")
        rfq.refresh_from_db()
        return Response({"removed": count, "remaining": images.has_pictures(rfq)}, status=status.HTTP_200_OK)

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
        for key, limit in (("carrier", 120), ("tracking_number", 120)):
            if len(str(d.get(key, "")).strip()) > limit:
                errors[key] = f"Keep it under {limit} characters."
        link = str(d.get("tracking_url", "")).strip()
        if link:
            from urllib.parse import urlparse
            if len(link) > 500 or urlparse(link).scheme not in ("http", "https") or not urlparse(link).netloc:
                errors["tracking_url"] = "Use a full web address starting with https://."
        return d, errors, delivery

    @action(detail=True, methods=["get", "post"], url_path="updates")
    def updates(self, request, pk=None):
        rfq = self.get_object()
        if request.method == "GET":
            return Response(tracking.request_detail(rfq, admin=True)["updates"])
        d, errors, delivery = self._update_body(request)
        if errors:
            return Response({"detail": " ".join(errors.values()), "fields": errors}, status=status.HTTP_400_BAD_REQUEST)
        before = rfq.status
        shipment = {k: str(d.get(k, "")).strip() for k in ("carrier", "tracking_number", "tracking_url") if str(d.get(k, "")).strip()}
        if shipment:  # saved first, so the email for this update carries them
            for k, v in shipment.items():
                setattr(rfq, k, v)
            rfq.save(update_fields=list(shipment))
        update = tracking.add_update(rfq, d["stage"], d["headline"], str(d.get("details", "")),
                                     str(d.get("location", "")), created_by=request.user)
        if delivery not in (None, ""):
            rfq.estimated_delivery = delivery
            rfq.save(update_fields=["estimated_delivery"])
        notify = d.get("notify", True)
        if isinstance(notify, str):
            notify = notify.lower() in ("1", "true", "yes", "on")
        emailed = bool(notify)  # the CMS always sends it: the customer is emailed on every update
        moved = tracking.STAGE_LABELS.get(before) != tracking.STAGE_LABELS.get(update.stage)
        what = (f"moved from {tracking.STAGE_LABELS.get(before)} to {tracking.STAGE_LABELS.get(update.stage)}" if moved
                else f"note at {tracking.STAGE_LABELS.get(update.stage)}")
        log(request, A.CREATED, "order update", f"{rfq.reference}: {update.headline}",
            f"{what}; " + ("customer emailed" if emailed else "customer not emailed"))
        if emailed:
            tracking.in_background(tracking.email_update, str(update.pk))
        return Response({**tracking.request_detail(rfq, admin=True), "emailed": emailed}, status=status.HTTP_201_CREATED)

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
            log(request, A.DELETED, "order update", label, f"stage was {tracking.STAGE_LABELS.get(update.stage)}")
            return Response(tracking.request_detail(rfq, admin=True))
        d, errors, delivery = self._update_body(request, partial=True)
        if errors:
            return Response({"detail": " ".join(errors.values()), "fields": errors}, status=status.HTTP_400_BAD_REQUEST)
        changed = []
        old_stage = update.stage
        for field in ("stage", "headline", "details", "location"):
            if field in d:
                setattr(update, field, str(d[field]).strip())
                changed.append(field)
        update.save()
        tracking.recompute_status(rfq)
        stage_note = (f"; stage changed from {tracking.STAGE_LABELS.get(old_stage)} to {tracking.STAGE_LABELS.get(update.stage)}"
                      if update.stage != old_stage else "")
        log(request, A.UPDATED, "order update", f"{rfq.reference}: {update.headline}", "fields: " + ", ".join(changed) + stage_note)
        return Response(tracking.request_detail(rfq, admin=True))
    
STALLED_DAYS = 7


@api_view(["GET"])
def attention(request):
    """What a customer is waiting for, worst first: requests with no quotation yet (a quotation is promised within 48
    hours), accepted quotations, unanswered questions, and orders that have not moved for a while."""
    from datetime import timedelta
    from django.db.models import Count, Max, Q
    now = timezone.now()
    items = []

    def add(kind, rfq, title, detail, since, severity, hours=None):
        items.append({"kind": kind, "rfq_id": str(rfq.pk), "reference": rfq.reference, "who": rfq.name, "company": rfq.company,
                      "title": title, "detail": detail, "since": since, "severity": severity,
                      "hours": hours if hours is not None else int((now - since).total_seconds() // 3600)})

    # no quotation yet
    for r in RFQ.objects.filter(status="received"):
        age = (now - r.created_at).total_seconds() / 3600
        if age >= 24:
            add("quote", r, "Quotation not sent yet", tracking.short_title(r), r.created_at, "red" if age >= 48 else "amber")
    # accepted, waiting for the next step
    for r in RFQ.objects.filter(status="confirmed"):
        last = r.updates.order_by("-created_at").first()
        if last and last.headline == "You accepted the quotation":
            add("accepted", r, "Accepted: check the payment", tracking.short_title(r), last.created_at, "green")
    # unanswered questions
    for r in RFQ.objects.annotate(n=Count("messages", filter=Q(messages__sender="customer", messages__read=False)),
                                  last=Max("messages__created_at", filter=Q(messages__sender="customer", messages__read=False))).filter(n__gt=0):
        latest = r.messages.filter(sender="customer", read=False).order_by("-created_at").first()
        add("question", r, "Customer asked a question" if r.n == 1 else f"{r.n} unanswered questions", (latest.body[:90] if latest else ""), r.last, "amber")
    # nothing has moved for a while (from Confirmed to before Delivered)
    cutoff = now - timedelta(days=STALLED_DAYS)
    for r in RFQ.objects.exclude(status__in=["received", "quoted", "delivered"]):
        last = r.updates.order_by("-created_at").first()
        if last and last.created_at < cutoff and not (r.status == "confirmed" and last.headline == "You accepted the quotation"):
            days = (now - last.created_at).days
            add("stalled", r, f"No update for {days} days", f"{tracking.short_title(r)} · {tracking.STAGE_LABELS.get(r.status, r.status)}", last.created_at, "amber")
    rank = {"red": 0, "amber": 1, "green": 2}
    items.sort(key=lambda i: (rank[i["severity"]], i["since"]))
    return Response({"count": len(items), "items": items[:30]})


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
    metadata = MetaDataSerial(MetaData.objects.all(), many=True).data
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
    def _save_draft(self, request):
        """Save the form as it is, without sending. Sending the same draft later replaces it."""
        from .serial import DraftSerial
        serializer = DraftSerial(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        fields = dict(
            kind=d["kind"], recipient=d["recipient"].strip(), recipient_name=d["recipient_name"], subject=d["subject"],
            title=d["title"], body=d["body"], valid_days=d["valid_days"] if d["kind"] == "rfq_reply" else None,
            rfq=d["rfq"], draft={"items": d["items"], "currency": d["currency"], "shipping": d["shipping"], "vat_percent": d["vat_percent"]},
        )
        record = None
        if d.get("draft_id"):
            record = SentEmail.objects.filter(pk=d["draft_id"], status=SentEmail.Status.DRAFT).first()
            if record is None:
                return Response({"detail": "That draft no longer exists."}, status=status.HTTP_404_NOT_FOUND)
            for k, v in fields.items():
                setattr(record, k, v)
            record.save()
        else:
            record = SentEmail.objects.create(status=SentEmail.Status.DRAFT, **fields)
        return Response({"id": str(record.pk), "status": "draft", "saved_at": timezone.now()}, status=status.HTTP_200_OK)

    def post(self, request):
        if str(request.data.get("save_draft", "")).lower() in ("1", "true"):
            return self._save_draft(request)
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
            quote=validated_data.get("quote") or {},
            rfq=validated_data.get("rfq"),
        )
        if validated_data.get("draft_id"):
            SentEmail.objects.filter(pk=validated_data["draft_id"], status=SentEmail.Status.DRAFT).delete()
        order = validated_data.get("rfq")
        log(request, A.EMAIL_SENT, "email", validated_data["subject"],
            f"to {validated_data['recipient']}" + (f", {record.reference}" if record.reference else "")
            + (f", total {validated_data['quote']['total_text']}" if validated_data.get("quote") else "")
            + (f", for order {order.reference}" if order else ""))
        if order:
            # sending the quotation moves the order to "Quoted" and records it, without a second email to the customer
            validated_data["order_reference"] = order.reference
            validated_data["order_id"] = str(order.pk)
            quoted_at = STAGE_ORDER.index("quoted")
            stage = "quoted" if STAGE_ORDER.index(order.status) < quoted_at else order.status
            before = order.status
            update = tracking.add_update(order, stage, f"Quotation {record.reference} sent",
                                         "Your quotation has been emailed to you.", created_by=request.user)
            moved = tracking.STAGE_LABELS.get(before) != tracking.STAGE_LABELS.get(stage)
            log(request, A.CREATED, "order update", f"{order.reference}: {update.headline}",
                (f"moved from {tracking.STAGE_LABELS.get(before)} to {tracking.STAGE_LABELS.get(stage)}" if moved
                 else f"note at {tracking.STAGE_LABELS.get(stage)}") + "; quotation emailed")
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
        validated_data.pop("rfq", None)

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

    @action(detail=True, methods=["post"], url_path="discard")
    def discard(self, request, pk=None):
        """Delete a draft. Emails that were sent are history and cannot be removed."""
        record = self.get_object()
        if record.status != SentEmail.Status.DRAFT:
            return Response({"detail": "Only drafts can be deleted."}, status=status.HTTP_400_BAD_REQUEST)
        record.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="html")
    def html(self, request, pk=None):
        """The email as it was sent (rebuilt from the record). ?download=1 saves it as a file."""
        from django.http import HttpResponse
        from .task import render_sent_html

        record = self.get_object()
        if record.status == SentEmail.Status.DRAFT:
            return Response({"detail": "A draft has not been sent yet."}, status=status.HTTP_400_BAD_REQUEST)
        response = HttpResponse(render_sent_html(record), content_type="text/html; charset=utf-8")
        if request.query_params.get("download"):
            name = (record.reference or f"email-{str(record.pk)[:8]}") + ".html"
            response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "no-store"
        return response


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
        target = request.query_params.get("target", "").strip()
        if target:
            qs = qs.filter(target_type=target)
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



@api_view(["GET", "HEAD"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def health(request):
    """For uptime monitors: public, no sign-in, nothing private in the answer.
    200 when the site and its database answer, 503 when the database does not."""
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        up = True
    except Exception:  # noqa: BLE001
        up = False
    response = Response({"status": "ok" if up else "down"}, status=200 if up else 503)
    response["Cache-Control"] = "no-store"
    return response
