"""Staff identity and permissions, the logistic fee and service charge, recipient suggestions, and autosaved drafts."""
import json
from decimal import Decimal
from unittest import mock

from django.core import mail

from . import quote
from .models import Designation, InboxMessage, RFQ, SentEmail, User
from .test_admins import AdminBase, NEW_PASSWORD
from .test_quote import ROWS, SendingQuotes
from .tests import REGULAR_PASSWORD, fresh_password, mk


class Inline:
    def __init__(self, target, args=()):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class QuoteMoney(AdminBase):
    """Subtotal, Logistic Fee, Service Charge, VAT and the total."""

    def rows(self):
        return quote.parse_items(json.dumps([{"description": "Book 1", "quantity": "1", "unit_price": "50000"},
                                             {"description": "Book 2", "quantity": "1", "unit_price": "30000"}]))

    def test_the_example_from_the_brief(self):
        q = quote.build(self.rows(), "NGN", Decimal(5000), None, Decimal(10000), "fixed")
        self.assertEqual((q["subtotal"], q["shipping"], q["service_charge"], q["total"]), ("80000.00", "5000.00", "10000.00", "95000.00"))
        self.assertEqual(q["total_text"], "₦95,000.00")
        self.assertEqual(q["service_text"], "₦10,000.00")

    def test_a_percentage_service_charge_is_taken_from_the_goods(self):
        q = quote.build(self.rows(), "NGN", Decimal(5000), None, Decimal(10), "percent")
        self.assertEqual((q["service_charge"], q["service_percent"], q["total"]), ("8000.00", "10", "93000.00"))

    def test_vat_applies_to_goods_logistic_fee_and_service_charge(self):
        q = quote.build(self.rows(), "NGN", Decimal(5000), Decimal("10"), Decimal(10000), "fixed")
        self.assertEqual((q["vat"], q["total"]), ("9500.00", "104500.00"))

    def test_nothing_extra_changes_nothing(self):
        q = quote.build(self.rows(), "NGN")
        self.assertEqual((q["total"], q["service_text"], q["shipping_text"]), ("80000.00", "", ""))


class QuoteEmail(SendingQuotes):
    def test_the_email_shows_logistic_fee_and_service_charge_lines(self):
        r = self.send(service_charge="10", service_mode="percent")
        self.assertEqual(r.status_code, 200, r.data)
        html, text = mail.outbox[-1].alternatives[0][0], mail.outbox[-1].body
        self.assertIn("Logistic Fee", html)
        self.assertIn("Service Charge (10%)", html)
        self.assertNotIn(">Delivery<", html)
        self.assertIn("Logistic Fee:", text)
        self.assertIn("Service Charge (10%):", text)

    def test_a_percentage_over_100_is_refused(self):
        self.assertEqual(self.send(service_charge="150", service_mode="percent").status_code, 400)

    def test_a_service_charge_without_a_table_is_refused(self):
        r = self.send(items="[]", service_charge="500")
        self.assertEqual(r.status_code, 400)

    def test_the_saved_record_keeps_the_service_charge(self):
        self.send(service_charge="2500")
        rec = SentEmail.objects.exclude(status="draft").first()
        self.assertEqual(rec.quote["service_charge"], "2500.00")
        self.assertEqual(rec.quote["service_mode"], "fixed")


class SenderIdentity(AdminBase):
    def send(self, client):
        with mock.patch("cms.views.threading.Thread", Inline), mock.patch("cms.task._record_outcome"):
            r = client.post("/api/emails/", {"kind": "outreach", "recipient": "c@x.com", "subject": "Hi", "title": "Hi", "body": "Hello"}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        return mail.outbox[-1]

    def test_each_person_signs_with_their_own_name_and_position(self):
        User.objects.filter(pk=self.admin.pk).update(first_name="Abdul", last_name="Razaq", designation="Chief Executive Officer")
        User.objects.filter(pk=self.regular.pk).update(first_name="Jane", last_name="Doe", designation="Procurement Officer")
        ceo = self.send(self.super)
        jane = self.send(self.regular_client)
        self.assertIn("Abdul Razaq", ceo.alternatives[0][0]); self.assertIn("Chief Executive Officer", ceo.alternatives[0][0])
        self.assertIn("Jane Doe", jane.alternatives[0][0]); self.assertIn("Procurement Officer", jane.alternatives[0][0])
        self.assertNotIn("Abdul Razaq", jane.alternatives[0][0]); self.assertNotIn("Lawal Abdur-Razaq", jane.alternatives[0][0])
        self.assertIn("Jane Doe\nProcurement Officer", jane.body)

    def test_a_changed_position_shows_in_the_next_email(self):
        User.objects.filter(pk=self.regular.pk).update(first_name="Jane", last_name="Doe", designation="Procurement Officer")
        self.send(self.regular_client)
        User.objects.filter(pk=self.regular.pk).update(designation="Operations Manager")
        again = self.send(self.regular_client)
        self.assertIn("Operations Manager", again.alternatives[0][0])

    def test_an_old_email_keeps_the_name_it_was_sent_with(self):
        User.objects.filter(pk=self.regular.pk).update(first_name="Jane", last_name="Doe", designation="Recruiter")
        self.send(self.regular_client)
        rec = SentEmail.objects.exclude(status="draft").first()
        User.objects.filter(pk=self.regular.pk).update(first_name="Janet", designation="Director")
        r = self.super.get(f"/api/sent-emails/{rec.pk}/html/")
        self.assertIn("Jane Doe", r.content.decode()); self.assertNotIn("Janet", r.content.decode())

    def test_a_record_from_before_the_change_still_reads_as_it_did(self):
        rec = SentEmail.objects.create(kind="outreach", recipient="c@x.com", subject="Old", title="Old", body="b", status="sent")
        self.assertIn("Lawal Abdur-Razaq", self.super.get(f"/api/sent-emails/{rec.pk}/html/").content.decode())

    def test_without_a_name_the_username_is_used(self):
        User.objects.filter(pk=self.regular.pk).update(first_name="", last_name="")
        self.assertIn("regular", self.send(self.regular_client).alternatives[0][0])


class StaffManagement(AdminBase):
    def setUp(self):
        super().setUp()
        self.p = Designation.objects.get(name="Procurement Officer")

    def test_a_starting_list_of_positions_exists_for_every_admin_to_read(self):
        names = [d["name"] for d in self.regular_client.get("/api/designations/").data]
        for n in ("Chief Executive Officer", "Logistics Coordinator", "Administrator"):
            self.assertIn(n, names)

    def test_only_a_super_admin_manages_the_list_of_positions(self):
        self.assertEqual(self.regular_client.post("/api/designations/", {"name": "Intern"}, format="json").status_code, 403)
        r = self.super.post("/api/designations/", {"name": "Warehouse Supervisor"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.regular_client.delete(f"/api/designations/{r.data['id']}/").status_code, 403)
        self.assertEqual(self.super.delete(f"/api/designations/{r.data['id']}/").status_code, 204)

    def test_a_position_in_use_cannot_be_removed(self):
        User.objects.filter(pk=self.regular.pk).update(designation="Procurement Officer")
        self.assertEqual(self.super.delete(f"/api/designations/{self.p.pk}/").status_code, 409)

    def test_a_new_admin_gets_a_position_and_is_an_ordinary_admin_whatever_it_is(self):
        r = self.add_admin(designation="Chief Executive Officer", first_name="Abdul", last_name="Razaq")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["designation"], r.data["role"]), ("Chief Executive Officer", "admin"))   # title does not grant rights
        self.assertFalse(User.objects.get(email="new.admin@x.com").is_superuser)

    def test_a_position_must_come_from_the_list(self):
        self.assertEqual(self.add_admin(designation="Supreme Leader").status_code, 400)

    def test_the_super_admin_corrects_a_registered_name(self):
        u = User.objects.get(pk=self.add_admin(first_name="Abdulrazak").data["id"])
        r = self.super.patch(f"/api/admins/{u.pk}/", {"first_name": "Abdul", "last_name": "Razaq"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        u.refresh_from_db()
        self.assertEqual(u.full_name, "Abdul Razaq")

    def test_a_correction_reaches_the_next_email(self):
        u = User.objects.get(pk=self.add_admin(first_name="Abdulrazak").data["id"])
        self.super.patch(f"/api/admins/{u.pk}/", {"first_name": "Abdul", "last_name": "Razaq", "designation": "Chief Executive Officer"}, format="json")
        c = self.client_for(__import__("cms.authentication", fromlist=["make_token"]).make_token(u))
        with mock.patch("cms.views.threading.Thread", Inline), mock.patch("cms.task._record_outcome"):
            c.post("/api/emails/", {"kind": "outreach", "recipient": "c@x.com", "subject": "Hi", "title": "Hi", "body": "Hello"}, format="multipart")
        html = mail.outbox[-1].alternatives[0][0]
        self.assertIn("Abdul Razaq", html); self.assertIn("Chief Executive Officer", html)

    def test_an_ordinary_admin_cannot_use_the_staff_endpoints_at_all(self):
        u = self.regular
        for method, url, body in (("patch", f"/api/admins/{u.pk}/", {"first_name": "Hacked"}), ("patch", f"/api/admins/{u.pk}/", {"role": "super_admin"}),
                                  ("post", "/api/admins/", {"email": "z@x.com", "password": NEW_PASSWORD, "role": "super_admin"}), ("get", "/api/admins/", None)):
            r = getattr(self.regular_client, method)(url, body, format="json") if body else getattr(self.regular_client, method)(url)
            self.assertEqual(r.status_code, 403, (method, url))
        u.refresh_from_db()
        self.assertEqual((u.first_name, u.is_superuser), ("", False))

    def test_nobody_can_change_their_own_permission_level(self):
        r = self.super.patch(f"/api/admins/{self.admin.pk}/", {"role": "admin"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_the_super_admin_can_promote_and_demote_an_admin(self):
        self.assertEqual(self.super.patch(f"/api/admins/{self.regular.pk}/", {"role": "super_admin"}, format="json").data["role"], "super_admin")
        promoted = self.client_for(__import__("cms.authentication", fromlist=["make_token"]).make_token(self.regular))
        self.assertEqual(promoted.get("/api/admins/").status_code, 200)   # now allowed
        self.assertEqual(self.super.patch(f"/api/admins/{self.regular.pk}/", {"role": "admin"}, format="json").data["role"], "admin")
        self.assertEqual(promoted.get("/api/admins/").status_code, 403)

    def test_a_job_title_never_decides_permissions(self):
        self.super.patch(f"/api/admins/{self.regular.pk}/", {"designation": "Chief Executive Officer"}, format="json")
        self.assertEqual(self.regular_client.get("/api/admins/").status_code, 403)
        self.assertEqual(self.regular_client.get("/api/auth/me/").data["is_super_admin"], False)

    def test_the_owner_account_stays_a_super_admin(self):
        owner = mk("boss", "info2@x.com", fresh_password(), is_staff=True, is_superuser=True, is_cms_super=True)
        with self.settings(CMS_SUPER_ADMIN_EMAILS=["info2@x.com"]):
            r = self.super.patch(f"/api/admins/{owner.pk}/", {"role": "admin"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_deactivating_blocks_sign_in_and_cannot_target_yourself(self):
        self.assertEqual(self.super.patch(f"/api/admins/{self.admin.pk}/", {"is_active": False}, format="json").status_code, 403)
        self.assertEqual(self.super.patch(f"/api/admins/{self.regular.pk}/", {"is_active": False}, format="json").status_code, 200)
        self.assertEqual(self.login("regular@x.com", REGULAR_PASSWORD).status_code, 401)

    def test_an_email_already_in_use_is_refused(self):
        self.assertEqual(self.super.patch(f"/api/admins/{self.regular.pk}/", {"email": "buyer@acme.com"}, format="json").status_code, 400)

    def test_the_session_carries_the_position(self):
        User.objects.filter(pk=self.regular.pk).update(designation="Logistics Coordinator")
        self.assertEqual(self.regular_client.get("/api/auth/me/").data["designation"], "Logistics Coordinator")


class RecipientSuggestions(AdminBase):
    def setUp(self):
        super().setUp()
        User.objects.filter(pk=self.regular.pk).update(first_name="Lawal", last_name="Sulaimon")
        mk("lawal2", "lawal.ade@acme.com", "x", first_name="Lawal", last_name="Adeyemi")
        mk("other", "bola@lawalfoods.com", "x", first_name="Bola", last_name="Obi")
        for i in range(3):
            SentEmail.objects.create(kind="outreach", recipient="lawal.ade@acme.com", subject="s", status="sent")
        InboxMessage.objects.create(message_id="m1", from_email="sales@lawalstore.ng", from_name="Lawal Store", subject="s", received_at="2026-01-01T00:00:00Z")

    def ask(self, q, client=None):
        return (client or self.regular_client).get("/api/contacts/", {"q": q})

    def test_typing_part_of_a_name_finds_matches(self):
        emails = [c["email"] for c in self.ask("lawal").data]
        for e in ("regular@x.com", "lawal.ade@acme.com", "bola@lawalfoods.com", "sales@lawalstore.ng"):
            self.assertIn(e, emails)

    def test_partial_email_matches_and_frequent_contacts_come_first(self):
        data = self.ask("lawal").data
        starts = [c for c in data if c["email"].startswith("lawal") or c["name"].lower().startswith("lawal")]
        self.assertEqual(data[0]["email"], "lawal.ade@acme.com")   # starts with it, and emailed most
        self.assertTrue(set(c["email"] for c in starts) <= set(c["email"] for c in data[: len(starts) + 1]))
        self.assertEqual(self.ask("acme").data[0]["email"].split("@")[1], "acme.com")

    def test_an_exact_match_beats_a_frequent_one(self):
        self.assertEqual(self.ask("bola@lawalfoods.com").data[0]["email"], "bola@lawalfoods.com")

    def test_nothing_typed_gives_nothing_and_strangers_get_nothing(self):
        self.assertEqual(self.ask("").data, [])
        self.assertEqual(self.client_for("bad").get("/api/contacts/", {"q": "lawal"}).status_code, 401)
        self.assertEqual(self.anon.get("/api/contacts/", {"q": "lawal"}).status_code, 401)


class AutosavedDrafts(AdminBase):
    def save(self, **extra):
        data = {"save_draft": "1", "kind": "outreach", "recipient": "c@x.com", "subject": "Hello", "body": "first", **extra}
        return self.regular_client.post("/api/emails/", data, format="multipart")

    def test_saving_again_updates_the_same_draft(self):
        first = self.save()
        self.assertEqual(first.status_code, 200, first.data)
        second = self.save(draft_id=first.data["id"], body="first, and then more")
        self.assertEqual(second.data["id"], first.data["id"])
        self.assertEqual(SentEmail.objects.filter(status="draft").count(), 1)
        self.assertEqual(SentEmail.objects.get(status="draft").body, "first, and then more")

    def test_a_half_written_email_is_accepted_as_a_draft(self):
        self.assertEqual(self.save(recipient="", subject="", body="just a few words").status_code, 200)

    def test_the_draft_keeps_the_quote_fields(self):
        r = self.save(kind="rfq_reply", service_charge="12", service_mode="percent", shipping="500", items=json.dumps([{"description": "x", "quantity": "1", "unit_price": "10"}]))
        d = SentEmail.objects.get(pk=r.data["id"]).draft
        self.assertEqual((d["service_charge"], d["service_mode"], d["shipping"]), ("12", "percent", "500"))


class SuggestionsAreCheap(RecipientSuggestions):
    def test_a_few_queries_and_the_message_text_is_never_loaded(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.ask("lawal").status_code, 200)
        self.assertLessEqual(len(ctx), 8)
        self.assertFalse(any('"body"' in q["sql"] for q in ctx.captured_queries))

    def test_the_same_text_again_is_answered_from_memory(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        first = self.ask("lawal").data
        with CaptureQueriesContext(connection) as ctx:
            again = self.ask("lawal").data
        self.assertEqual(first, again)
        self.assertLessEqual(len(ctx), 2)   # only the sign-in check


class NewStaffNeedPositionAndLevel(AdminBase):
    def test_both_are_required_when_creating(self):
        base = {"email": "z@x.com", "password": NEW_PASSWORD, "first_name": "Zed"}
        for missing, extra in (("both", {}), ("level", {"designation": "Administrator"}), ("position", {"role": "admin"}),
                               ("blank position", {"designation": "", "role": "admin"}), ("bad level", {"designation": "Administrator", "role": "root"})):
            r = self.super.post("/api/admins/", {**base, **extra}, format="json")
            self.assertEqual(r.status_code, 400, missing)
        self.assertFalse(User.objects.filter(email="z@x.com").exists())

    def test_the_position_must_be_on_the_list(self):
        self.assertEqual(self.add_admin(designation="Supreme Leader").status_code, 400)

    def test_with_both_given_it_works_and_the_level_is_as_chosen(self):
        r = self.add_admin(designation="Software Developer", role="super_admin", email="dev@x.com")
        self.assertEqual((r.status_code, r.data["role"], r.data["designation"]), (201, "super_admin", "Software Developer"))

    def test_an_existing_account_without_a_position_can_still_be_edited_but_not_blanked(self):
        self.assertEqual(self.super.patch(f"/api/admins/{self.regular.pk}/", {"first_name": "Rex"}, format="json").status_code, 200)   # untouched position
        self.super.patch(f"/api/admins/{self.regular.pk}/", {"designation": "Accountant"}, format="json")
        self.assertEqual(self.super.patch(f"/api/admins/{self.regular.pk}/", {"designation": ""}, format="json").status_code, 400)
