from django.contrib.auth.hashers import make_password
from django.db.models import Q
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.decorators import authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .authentication import TOKEN_MAX_AGE, make_token
from .models import User
from .permissions import IsCMSAdmin


def _user_payload(user):
    return {
        "id": str(user.pk),
        "email": user.email,
        "username": user.username,
        "name": f"{user.first_name} {user.last_name}".strip(),
    }


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        email = str(request.data.get("email", "")).strip()
        password = str(request.data.get("password", ""))
        invalid = Response(
            {"detail": "Invalid email or password."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
        if not email or not password:
            return invalid

        # only active staff can sign in to the CMS; ordinary accounts (such as
        # the ones created for RFQ requesters) never can
        candidates = User.objects.filter(
            Q(is_staff=True) | Q(is_superuser=True),
            email__iexact=email,
            is_active=True,
        )
        user = next((u for u in candidates if u.check_password(password)), None)
        if user is None:
            make_password(password)  # keep timing similar whether or not the email exists
            return invalid

        return Response(
            {
                "token": make_token(user),
                "expires_in": TOKEN_MAX_AGE,
                "user": _user_payload(user),
            }
        )


class MeView(APIView):
    permission_classes = [IsCMSAdmin]

    def get(self, request):
        return Response(_user_payload(request.user))


class ChangePasswordView(APIView):
    permission_classes = [IsCMSAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password"

    def post(self, request):
        current = str(request.data.get("current_password", ""))
        new = str(request.data.get("new_password", ""))
        user = request.user

        if not user.check_password(current):
            return Response(
                {"detail": "Current password is wrong."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_password(new, user)
        except DjangoValidationError as e:
            return Response(
                {"detail": " ".join(e.messages)}, status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new)
        user.save(update_fields=["password"])
        # every older token is now invalid; hand back a fresh one for this session
        return Response(
            {
                "detail": "Password changed.",
                "token": make_token(user),
                "expires_in": TOKEN_MAX_AGE,
            }
        )
