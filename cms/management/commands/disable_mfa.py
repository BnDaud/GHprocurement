from django.core.management.base import BaseCommand, CommandError

from cms.models import MFADevice, User


class Command(BaseCommand):
    help = (
        "Turn off two-step verification for an account (use it if the phone and "
        "the recovery codes are both lost). Usage: python manage.py disable_mfa EMAIL"
    )

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, email, **options):
        users = User.objects.filter(email__iexact=email)
        if not users.exists():
            raise CommandError(f"No user with email {email}")
        deleted, _ = MFADevice.objects.filter(user__in=users).delete()
        self.stdout.write(self.style.SUCCESS(
            f"Two-step verification turned off for {email}." if deleted else f"{email} did not have it on."
        ))
