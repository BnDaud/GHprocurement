"""The audit trail: who did what, readable by the super admin only, never
editable, and never containing secrets."""
import secrets
from unittest import mock

from rest_framework.test import APIClient

from .authentication import make_token
from .models import AuditLog, Catalog, InboxMessage, User
from .test_admins import AdminBase
from .tests import ADMIN_EMAIL, ADMIN_PASSWORD, OTHER_PASSWORD, USER_PASSWORD, mk


class AuditBase(AdminBase):
    def entries(self, **filters):
        return list(AuditLog.objects.filter(**filters).order_by("created_at"))

    def one(self, **filters):
        rows = self.entries(**filters)
        self.assertEqual(len(rows), 1, rows)
        return rows[0]


class SignInsAreRecorded(AuditBase):
    def test_sign_in(self):
        AuditLog.objects.all().delete()
        self.login()
        e = self.one(action="sign_in")
        self.assertEqual(e.actor_email, ADMIN_EMAIL)

    def test_failed_sign_in_for_a_real_admin_names_them(self):
        self.login(password="nope-nope-1")
        e = self.one(action="sign_in_failed")
        self.assertEqual(e.actor_email, ADMIN_EMAIL)
        self.assertEqual(e.detail, "wrong password")

    def test_failed_sign_in_for_unknown_email_stores_no_typed_text(self):
        typed = "maybe-this-is-a-password@x"
        self.login(email=typed, password="whatever-1")
        e = self.one(action="sign_in_failed")
        self.assertEqual(e.actor_email, "")
        self.assertEqual(e.detail, "unknown email")
        self.assertNotIn(typed, f"{e.actor_email}{e.target_label}{e.detail}")

    def test_customer_account_cannot_sign_in_and_is_not_named(self):
        self.login(email="buyer@acme.com", password="Acme")
        e = self.one(action="sign_in_failed")
        self.assertEqual(e.actor_email, "")

    def test_password_change(self):
        c = self.client_for(make_token(self.admin))
        r = c.post("/api/auth/change-password/", {"current_password": ADMIN_PASSWORD, "new_password": OTHER_PASSWORD}, format="json")
        self.assertEqual(r.status_code, 200)
        e = self.one(action="password_changed")
        self.assertEqual(e.actor_email, ADMIN_EMAIL)
        self.assertNotIn(OTHER_PASSWORD, str(AuditLog.objects.values()))
        self.assertNotIn(ADMIN_PASSWORD, str(AuditLog.objects.values()))


class ChangesAreRecorded(AuditBase):
    def test_content_create_edit_delete(self):
        r = self.super.post("/api/services/", {"title": "Sourcing", "description": "We source"}, format="json")
        self.assertEqual(r.status_code, 201)
        sid = r.data["id"]
        created = self.one(action="created", target_type="service")
        self.assertEqual((created.actor_email, created.target_label), (ADMIN_EMAIL, "Sourcing"))

        self.super.patch(f"/api/services/{sid}/", {"description": "New text"}, format="json")
        edited = self.one(action="updated", target_type="service")
        self.assertIn("description", edited.detail)
        self.assertNotIn("New text", edited.detail)  # field names only, never values

        self.super.delete(f"/api/services/{sid}/")
        deleted = self.one(action="deleted", target_type="service")
        self.assertEqual(deleted.target_label, "Sourcing")

    def test_a_regular_admin_is_recorded_under_their_own_email(self):
        self.regular_client.post("/api/faqs/", {"question": "Do you ship?", "answer": "Yes"}, format="json")
        self.assertEqual(self.one(action="created", target_type="FAQ").actor_email, "regular@x.com")

    def test_refused_changes_leave_no_entry(self):
        before = AuditLog.objects.count()
        self.anon.post("/api/services/", {"title": "x", "description": "y"}, format="json")
        self.assertEqual(AuditLog.objects.count(), before)

    def test_admin_added_and_deleted(self):
        r = self.add_admin()
        self.assertEqual(self.one(action="created", target_type="admin").target_label, "new.admin@x.com")
        self.assertNotIn("password", str(AuditLog.objects.values()).lower().replace("password_changed", ""))
        self.super.delete(f"/api/admins/{r.data['id']}/")
        self.assertEqual(self.one(action="deleted", target_type="admin").target_label, "new.admin@x.com")

    def test_customer_account_changes(self):
        r = self.super.post("/api/user/", {"username": "lead", "email": "lead@x.com", "password": USER_PASSWORD, "phone": "1", "first_name": "L", "last_name": "D"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        e = self.one(action="created", target_type="customer account")
        self.assertNotIn(USER_PASSWORD, str(AuditLog.objects.values()))
        self.super.delete(f"/api/user/{r.data['id']}/")
        self.assertEqual(self.one(action="deleted", target_type="customer account").target_label, e.target_label)

    def test_site_settings_edit(self):
        from .models import MetaData
        m = MetaData.objects.create(metaIntro="a", metaDescription="b", email="x@y.com", phone="1", office="o")
        self.super.patch(f"/api/metadata/{m.pk}/", {"office": "25 Okota Road"}, format="json")
        e = self.one(action="updated", target_type="site settings")
        self.assertIn("office", e.detail)

    def test_inbox_deletes(self):
        from django.utils import timezone
        a = InboxMessage.objects.create(message_id="a", from_email="ada@x.com", subject="Hello", received_at=timezone.now())
        b = InboxMessage.objects.create(message_id="b", from_email="s@x.com", subject="Junk", is_spam=True, received_at=timezone.now())
        self.super.delete(f"/api/inbox/{a.pk}/")
        e = self.one(action="deleted", target_type="inbox mail", target_label="Hello from ada@x.com")
        self.assertNotIn("body", e.detail)
        self.super.post("/api/inbox/bulk-delete/", {"spam": True}, format="json")
        self.assertEqual(len(self.entries(action="deleted", target_type="inbox mail")), 2)

    def test_email_sent_records_who_and_to_whom_without_the_text(self):
        with mock.patch("cms.views.threading.Thread"):
            r = self.super.post("/api/emails/", {"kind": "outreach", "recipient": "client@x.com", "subject": "Hi there",
                                                 "title": "Hi", "body": "private words here"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        e = self.one(action="email_sent")
        self.assertEqual(e.actor_email, ADMIN_EMAIL)
        self.assertIn("client@x.com", e.detail)
        self.assertNotIn("private words", str(AuditLog.objects.values()))

    def test_public_quote_request_is_recorded_without_an_actor(self):
        r = self.anon.post("/api/rfqs/", {"name": "Buyer One", "email": "buyer1@acme.com", "company": "Acme", "phone": "+234 1", "item": "20 chairs"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        e = self.one(action="quote_received")
        self.assertEqual(e.actor_email, "")
        self.assertIn("Buyer One (Acme)", e.target_label)
        self.assertIn(r.data["reference"], e.target_label)


class TwoStepIsRecorded(AuditBase):
    def test_on_and_off(self):
        import pyotp
        c = self.client_for(make_token(self.admin))
        secret = c.post("/api/auth/mfa/setup/").data["secret"]
        r = c.post("/api/auth/mfa/confirm/", {"code": pyotp.TOTP(secret).now()}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.one(action="two_step_on").actor_email, ADMIN_EMAIL)
        self.assertNotIn(secret, str(AuditLog.objects.values()))


class ReadingTheTrail(AuditBase):
    def test_only_the_super_admin_can_read_it(self):
        self.assertEqual(self.anon.get("/api/audit/").status_code, 401)
        self.assertEqual(self.regular_client.get("/api/audit/").status_code, 403)
        self.assertEqual(self.super.get("/api/audit/").status_code, 200)

    def test_newest_first_with_filters_and_paging(self):
        AuditLog.objects.all().delete()
        for i in range(130):
            AuditLog.objects.create(actor_email="regular@x.com", action="created", target_type="FAQ", target_label=f"Q{i}")
        AuditLog.objects.create(actor_email=ADMIN_EMAIL, action="deleted", target_type="service", target_label="Old")
        r = self.super.get("/api/audit/").data
        self.assertEqual((r["total"], len(r["results"])), (131, 100))
        self.assertEqual(r["results"][0]["target_label"], "Old")  # newest first
        self.assertEqual(len(self.super.get("/api/audit/?offset=100").data["results"]), 31)
        self.assertEqual(self.super.get("/api/audit/?action=deleted").data["total"], 1)
        self.assertEqual(self.super.get("/api/audit/?actor=regular").data["total"], 130)
        self.assertEqual(self.super.get("/api/audit/?q=Q12").data["total"], 11)
        self.assertEqual(self.super.get("/api/audit/?offset=abc").status_code, 200)

    def test_cannot_be_written_or_deleted_through_the_api(self):
        e = AuditLog.objects.create(actor_email="x@x.com", action="created")
        for method in ("post", "put", "patch", "delete"):
            r = getattr(self.super, method)(f"/api/audit/{e.pk}/" if method != "post" else "/api/audit/", {}, format="json")
            self.assertEqual(r.status_code, 405, method)
        self.assertTrue(AuditLog.objects.filter(pk=e.pk).exists())

    def test_entry_survives_the_account_being_deleted(self):
        r = self.add_admin()
        self.client_for(make_token(User.objects.get(email="new.admin@x.com"))).post("/api/faqs/", {"question": "q", "answer": "a"}, format="json")
        self.super.delete(f"/api/admins/{r.data['id']}/")
        e = self.one(action="created", target_type="FAQ")
        self.assertIsNone(e.actor)
        self.assertEqual(e.actor_email, "new.admin@x.com")

    def test_logging_failure_never_breaks_the_action(self):
        with mock.patch("cms.audit.AuditLog.objects.create", side_effect=RuntimeError("db down")):
            r = self.super.post("/api/faqs/", {"question": "q", "answer": "a"}, format="json")
        self.assertEqual(r.status_code, 201)
