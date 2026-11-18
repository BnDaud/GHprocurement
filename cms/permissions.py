from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS


def is_super_admin(user):
    """The CMS super admin: an active Django superuser who is an owner account (their email is listed in
    settings.CMS_SUPER_ADMIN_EMAILS, default info@ghprocurement.com) or was made a super admin by one."""
    return bool(
        user
        and getattr(user, "is_active", False)
        and user.is_superuser
        and (is_owner_account(user) or getattr(user, "is_cms_super", False))
    )


def is_owner_account(user):
    """The accounts named in settings.CMS_SUPER_ADMIN_EMAILS: always super admins, and never demoted from the CMS."""
    emails = [e.lower() for e in getattr(settings, "CMS_SUPER_ADMIN_EMAILS", [])]
    return bool(user and (user.email or "").lower() in emails)


class IsCMSAdmin(BasePermission):
    """Signed in AND flagged as staff. Ordinary User rows (for example the
    accounts created automatically for RFQ requesters) never qualify."""

    message = "Admin access required."

    def has_permission(self, request, view):
        user = request.user
        allowed = bool(
            user
            and user.is_authenticated
            and user.is_active
            and (user.is_staff or user.is_superuser)
        )
        if allowed and getattr(settings, "CMS_REQUIRE_MFA", False) and not getattr(view, "mfa_exempt", False):
            from . import mfa
            if not mfa.is_enabled(user):
                # signed in, but two-step verification was never set up: nothing works until it is
                raise PermissionDenied({"detail": "Set up two-step verification to continue.", "code": "mfa_setup_required"})
        return allowed


class IsCMSAdminOrReadOnly(IsCMSAdmin):
    """Anyone may read; only admins may change."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().has_permission(request, view)


class IsSuperAdmin(IsCMSAdmin):
    """Only the super admin(s): the ones who can add and remove other admins."""

    message = "Only a super admin can do this."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and is_super_admin(request.user)


class IsSuperAdminOrReadOnly(IsSuperAdmin):
    """Anyone may read (the public site shows this content); only a super admin may change it."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().has_permission(request, view)
