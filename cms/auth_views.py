from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import mfa
from .authentication import TOKEN_MAX_AGE, make_token
from .models import MFADevice, User
from .permissions import IsCMSAdmin, is_super_admin

MFA_STEP_SALT = "cms-mfa-step"
MFA_STEP_MAX_AGE = 5 * 60  # five minutes to type the code after the password


def _user_payload(user):
    return {
        "id": str(user.pk),
        "email": user.email,
        "username": user.username,
        "is_super_admin": is_super_admin(user),
        "name": f"{user.first_name} {user.last_name}".strip(),
        "mfa_enabled": mfa.is_enabled(user),
    }


def _session(user, **extra):
    return {
        "token": make_token(user),
        "expires_in": TOKEN_MAX_AGE,
        "user": _user_payload(user),
        **extra,
    }


def _bad(detail, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": detail}, status=code)


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
        candidates = User.objects.select_related("mfa").filter(
            Q(is_staff=True) | Q(is_superuser=True),
            email__iexact=email,
            is_active=True,
        )
        user = next((u for u in candidates if u.check_password(password)), None)
        if user is None:
            make_password(password)  # keep timing similar whether or not the email exists
            return invalid

        if mfa.is_enabled(user):
            # password is right, but no session yet: the app code comes next
            step_token = signing.dumps({"uid": str(user.pk)}, salt=MFA_STEP_SALT)
            return Response({"mfa_required": True, "mfa_token": step_token})

        update_last_login(None, user)  # so the super admin can see who has been active
        return Response(_session(user))


class LoginMfaView(APIView):
    """Step two of sign-in for accounts with 2FA on."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        expired = Response(
            {"detail": "That sign-in has timed out. Enter your password again.", "restart": True},
            status=status.HTTP_401_UNAUTHORIZED,
        )
        try:
            data = signing.loads(
                str(request.data.get("mfa_token", "")), salt=MFA_STEP_SALT, max_age=MFA_STEP_MAX_AGE
            )
        except signing.BadSignature:
            return expired

        user = User.objects.select_related("mfa").filter(pk=data.get("uid"), is_active=True).first()
        device = mfa.get_device(user) if user else None
        # rights are re-checked: an account that lost admin access in the last
        # few minutes cannot finish signing in
        if user is None or device is None or not (user.is_staff or user.is_superuser):
            return expired

        if mfa.is_locked(device):
            return _bad("Too many wrong codes. Try again in a few minutes.", status.HTTP_429_TOO_MANY_REQUESTS)

        used = mfa.check_code(device, request.data.get("code", ""))
        if used is None:
            if mfa.is_locked(device):
                return _bad("Too many wrong codes. Try again in 15 minutes.", status.HTTP_429_TOO_MANY_REQUESTS)
            return _bad("That code is not right. Check the code in your app and try again.", status.HTTP_401_UNAUTHORIZED)

        device.refresh_from_db()
        update_last_login(None, user)
        return Response(_session(user, used_recovery_code=used == "recovery", recovery_codes_left=len(device.recovery_hashes)))


class MeView(APIView):
    permission_classes = [IsCMSAdmin]

    def get(self, request):
        device = mfa.get_device(request.user)
        return Response({**_user_payload(request.user), "recovery_codes_left": len(device.recovery_hashes) if device else 0})


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


# ---------------------------------------------------------------------------
# Turning two-step verification on, off, and managing recovery codes
# ---------------------------------------------------------------------------
class MfaSetupView(APIView):
    """Start setup: makes a new secret (not active until confirmed)."""

    permission_classes = [IsCMSAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password"

    def post(self, request):
        user = request.user
        if mfa.is_enabled(user):
            return _bad("Two-step verification is already on. Turn it off first to set it up again.")
        secret = mfa.new_secret()
        MFADevice.objects.update_or_create(
            user=user,
            defaults={"secret": secret, "confirmed": False, "last_used_step": None,
                      "recovery_hashes": [], "failed_attempts": 0, "locked_until": None},
        )
        return Response({"secret": secret, "otpauth_uri": mfa.otpauth_uri(user, secret)})


class MfaConfirmView(APIView):
    """Finish setup by proving the app shows the right code. Returns the
    recovery codes (this one time only) and a fresh session token, because
    every older token stops working once 2FA is on."""

    permission_classes = [IsCMSAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password"

    def post(self, request):
        user = request.user
        device = mfa.get_device(user, confirmed_only=False)
        if device is None or device.confirmed:
            return _bad("Start the setup first.")
        if mfa.is_locked(device):
            return _bad("Too many wrong codes. Try again in a few minutes.", status.HTTP_429_TOO_MANY_REQUESTS)
        if not mfa.check_totp(device, request.data.get("code", "")):
            return _bad("That code is not right. Check the code in your app and try again.")

        codes, hashes = mfa.make_recovery_codes()
        device.confirmed = True
        device.recovery_hashes = hashes
        device.save(update_fields=["confirmed", "recovery_hashes"])
        user.refresh_from_db()
        return Response(_session(user, recovery_codes=codes))


class _NeedsPasswordAndCode(APIView):
    """Sensitive 2FA changes need the password and a current code."""

    permission_classes = [IsCMSAdmin]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password"

    def verify(self, request):
        """Returns (device, error_response)."""
        user = request.user
        device = mfa.get_device(user)
        if device is None:
            return None, _bad("Two-step verification is not on.")
        if not user.check_password(str(request.data.get("password", ""))):
            return None, _bad("Password is wrong.")
        if mfa.is_locked(device):
            return None, _bad("Too many wrong codes. Try again in a few minutes.", status.HTTP_429_TOO_MANY_REQUESTS)
        if mfa.check_code(device, request.data.get("code", "")) is None:
            return None, _bad("That code is not right. Check the code in your app and try again.")
        return device, None


class MfaDisableView(_NeedsPasswordAndCode):
    def post(self, request):
        device, error = self.verify(request)
        if error:
            return error
        device.delete()
        request.user.refresh_from_db()
        return Response(_session(request.user, detail="Two-step verification is off."))


class MfaRecoveryCodesView(_NeedsPasswordAndCode):
    """Make a fresh set of recovery codes (the old ones stop working)."""

    def post(self, request):
        device, error = self.verify(request)
        if error:
            return error
        codes, hashes = mfa.make_recovery_codes()
        device.recovery_hashes = hashes
        device.save(update_fields=["recovery_hashes"])
        return Response({"recovery_codes": codes})
