"""Customer accounts made by the old quote form had the company name as their
password. This makes those passwords unusable; each customer then picks a real
one from the emailed link ("Forgot your password?" on the sign-in page).

Admins (staff / superusers) are never touched.  Preview first:
    python manage.py retire_customer_passwords --dry-run
"""
from django.core.management.base import BaseCommand

from cms.models import User


class Command(BaseCommand):
    help = "Make the passwords of old customer accounts unusable."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="only count, change nothing")

    def handle(self, *args, **opts):
        customers = [u for u in User.objects.filter(is_staff=False, is_superuser=False) if u.has_usable_password()]
        for user in customers:
            self.stdout.write(f"  {user.email or user.username}")
            if not opts["dry_run"]:
                user.set_unusable_password()
                user.save(update_fields=["password"])
        verb = "would retire" if opts["dry_run"] else "retired"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(customers)} customer password(s)"))
