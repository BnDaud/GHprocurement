from rest_framework.permissions import BasePermission, SAFE_METHODS


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
