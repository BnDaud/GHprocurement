from django.conf import settings
from rest_framework.permissions import BasePermission, SAFE_METHODS


def is_super_admin(user):
    """The CMS super admin: an active Django superuser whose email is listed in
    settings.CMS_SUPER_ADMIN_EMAILS (default info@ghprocurement.com)."""
    emails = [e.lower() for e in getattr(settings, "CMS_SUPER_ADMIN_EMAILS", [])]
    return bool(
        user
        and getattr(user, "is_active", False)
        and user.is_superuser
        and (user.email or "").lower() in emails
    )


class IsCMSAdmin(BasePermission):
    """Signed in AND flagged as staff. Ordinary User rows (for example the
    accounts created automatically for RFQ requesters) never qualify."""

    message = "Admin access required."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and (user.is_staff or user.is_superuser)
        )


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
