"""Write the audit trail. `log()` never raises: a problem recording an entry
must not stop the action that is being recorded."""
import logging

from .models import AuditLog

logger = logging.getLogger(__name__)

A = AuditLog.Action


def label_of(obj):
    """A short human name for an item (never a secret)."""
    for attr in ("name", "title", "question", "email", "username", "subject"):
        value = getattr(obj, attr, "")
        if value:
            return str(value)[:200]
    return str(getattr(obj, "pk", ""))[:200]


def log(request, action, target_type="", target_label="", detail="", actor=None):
    try:
        if actor is None and request is not None:
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "is_authenticated", False):
                actor = user
        AuditLog.objects.create(
            actor=actor,
            actor_email=(getattr(actor, "email", "") or "")[:254] if actor else "",
            action=action,
            target_type=target_type[:40],
            target_label=str(target_label)[:200],
            detail=str(detail)[:500],
        )
    except Exception:  # noqa: BLE001
        logger.exception("could not write audit log entry")


class AuditedMixin:
    """For ModelViewSets: records create / edit / delete by the signed-in admin.
    `audit_name` is how the item is described, e.g. "catalog item"."""

    audit_name = "item"

    def perform_create(self, serializer):
        instance = serializer.save()
        log(self.request, A.CREATED, self.audit_name, label_of(instance))

    def perform_update(self, serializer):
        instance = serializer.save()
        changed = ", ".join(sorted(k for k in serializer.validated_data if k != "password"))
        log(self.request, A.UPDATED, self.audit_name, label_of(instance), f"fields: {changed}" if changed else "")

    def perform_destroy(self, instance):
        label = label_of(instance)
        instance.delete()
        log(self.request, A.DELETED, self.audit_name, label)
