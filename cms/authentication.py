"""Stateless signed-token authentication for the CMS.

A token is a Django-signed payload (user id + a fingerprint of the password
hash) with a maximum age. Nothing is stored in the database, so no migration
is needed. Changing a user's password, or deactivating them, invalidates every
token they hold, because the fingerprint no longer matches.
"""
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import User

TOKEN_SALT = "cms-auth-token"
TOKEN_MAX_AGE = 60 * 60 * 12  # 12 hours


def _fingerprint(user):
    return user.password[-16:]


def make_token(user):
    return signing.dumps(
        {"uid": str(user.pk), "fp": _fingerprint(user)}, salt=TOKEN_SALT
    )


class SignedTokenAuthentication(BaseAuthentication):
    keyword = b"bearer"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword:
            return None  # no credentials: let permissions decide
        if len(parts) != 2:
            raise AuthenticationFailed("Invalid authorization header.")
        try:
            token = parts[1].decode()
        except UnicodeError:
            raise AuthenticationFailed("Invalid authorization header.")

        try:
            data = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
        except signing.SignatureExpired:
            raise AuthenticationFailed("Session expired. Sign in again.")
        except signing.BadSignature:
            raise AuthenticationFailed("Invalid token.")

        try:
            user = User.objects.filter(pk=data.get("uid"), is_active=True).first()
        except (ValueError, DjangoValidationError):
            user = None
        if user is None or data.get("fp") != _fingerprint(user):
            raise AuthenticationFailed("Invalid token.")
        return (user, token)

    def authenticate_header(self, request):
        # makes DRF answer 401 (not 403) when credentials are missing or bad
        return 'Bearer realm="cms"'
