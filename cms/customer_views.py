"""The customer side: sign in, choose a password, and follow quote requests.
Customers are ordinary (non-staff) accounts. Nothing here, and no customer
token, can reach the CMS: CMS endpoints require a staff account."""
import uuid

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import tracking
from .audit import A, log
from .authentication import CUSTOMER_MAX_AGE, make_customer_token
from .models import RFQ, User


class IsCustomer(BasePermission):
    message = "Sign in to see your requests."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_active and not u.is_staff and not u.is_superuser)


def _bad(detail, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": detail}, status=code)


def _payload(user):
    last = RFQ.objects.filter(user=user).order_by("-created_at").first()
    return {
        "id": str(user.pk),
        "email": user.email,
        "name": f"{user.first_name} {user.last_name}".strip(),
        "company": last.company if last else "",
    }


def _session(user):
    return {"token": make_customer_token(user), "expires_in": CUSTOMER_MAX_AGE, "user": _payload(user)}


def _customer_by_email(email):
    return User.objects.filter(email__iexact=email.strip(), is_staff=False, is_superuser=False, is_active=True).first()


class _Public(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]


class CustomerLoginView(_Public):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        email = str(request.data.get("email", "")).strip()
        password = str(request.data.get("password", ""))
        user = _customer_by_email(email) if email and password else None
        if user is None or not user.check_password(password):
            make_password(password)  # similar timing whether or not the email exists
            return _bad("Invalid email or password.", status.HTTP_401_UNAUTHORIZED)
        return Response(_session(user))


class RequestLinkView(_Public):
    """'Forgot my password' and 'send the link again'. Always answers the same
    way, so nobody can use it to find out which emails have an account."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password"

    def post(self, request):
        email = str(request.data.get("email", "")).strip()
        user = _customer_by_email(email) if email else None
        if user is not None:
            tracking.in_background(tracking.email_password_link, str(user.pk))
        return Response({"detail": "If there is an account for that email, we have sent a link to choose a password."})


class SetPasswordView(_Public):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password"

    def post(self, request):
        user = tracking.read_set_password_token(request.data.get("token", ""))
        if user is None:
            return _bad("This link has expired or was already used. Ask for a new one.", status.HTTP_400_BAD_REQUEST)
        password = str(request.data.get("password", ""))
        try:
            validate_password(password, user)
        except DjangoValidationError as e:
            return _bad(" ".join(e.messages))
        user.set_password(password)  # also makes this link (and older ones) useless
        user.save(update_fields=["password"])
        log(request, A.SIGN_IN, detail="customer chose a password", actor=user)
        return Response(_session(user))


class CustomerMeView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        return Response(_payload(request.user))


class CustomerRequestsView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        rfqs = RFQ.objects.filter(user=request.user).order_by("-created_at")
        return Response([tracking.request_summary(r) for r in rfqs])


class CustomerRequestDetailView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request, pk):
        try:
            rfq = RFQ.objects.filter(user=request.user, pk=uuid.UUID(str(pk))).first()  # only their own
        except ValueError:
            rfq = None
        if rfq is None:
            return _bad("Request not found.", status.HTTP_404_NOT_FOUND)
        return Response(tracking.request_detail(rfq))


class GuestTrackView(_Public):
    """Track with the reference and the email used for the request (no sign-in).
    A wrong pair always gets the same answer."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        reference = str(request.data.get("reference", "")).strip().upper()
        email = str(request.data.get("email", "")).strip()
        rfq = RFQ.objects.filter(reference=reference, email__iexact=email).first() if reference and email else None
        if rfq is None:
            return _bad("We could not find a request with that reference and email.", status.HTTP_404_NOT_FOUND)
        return Response(tracking.request_detail(rfq))
