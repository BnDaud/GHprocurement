"""Customer accounts and order tracking."""
import re
import secrets
from datetime import timedelta
from unittest import mock

from django.core import mail, signing
from django.utils import timezone
from rest_framework.test import APIClient

from . import tracking
from .authentication import make_customer_token
from .models import AuditLog, RFQ, RFQUpdate, User
from .test_admins import AdminBase
from .tests import ADMIN_EMAIL

STRONG = "Pw-" + secrets.token_urlsafe(14)
FORM = {"name": "Ada Obi", "email": "ada@acme.com", "company": "Acme Supplies", "phone": "+2348000000000", "item": "20 office chairs", "consent": True}


def customer_client(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {make_customer_token(user)}")
    return c


class TrackBase(AdminBase):
    def submit(self, **extra):
        mail.outbox.clear()
        return self.anon.post("/api/rfqs/", {**FORM, **extra}, format="json")

    def link_from_outbox(self, index=-1):
        body = mail.outbox[index].alternatives[0][0]
        m = re.search(r"set-password\?token=([^\"&<\s]+)", body)
        return m.group(1) if m else None


class RequestCreatesAnAccount(TrackBase):
    def test_new_customer_gets_an_account_a_reference_and_a_password_link(self):
        r = self.submit()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertRegex(r.data["reference"], r"^RFQ-\d{4}-[A-HJKMNP-TV-Z2-9]{6}$")
        self.assertTrue(r.data["new_account"])
        user = User.objects.get(email="ada@acme.com")
        self.assertFalse(user.has_usable_password())  # no guessable password (it used to be the company name)
        self.assertFalse(user.is_staff or user.is_superuser)
        rfq = RFQ.objects.get()
        self.assertEqual((rfq.user, rfq.status), (user, "received"))
        self.assertEqual(rfq.updates.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ada@acme.com"])
        self.assertIn(rfq.reference, mail.outbox[0].subject)
        self.assertTrue(self.link_from_outbox())

    def test_second_request_reuses_the_account_and_sends_no_password_link(self):
        self.submit()
        r = self.submit(item="500 helmets", company="Other Name Ltd")
        self.assertFalse(r.data["new_account"])
        self.assertEqual(User.objects.filter(email="ada@acme.com").count(), 1)
        self.assertNotEqual(r.data["reference"], RFQ.objects.order_by("created_at").first().reference)
        self.assertIsNone(self.link_from_outbox())

    def test_same_company_name_does_not_merge_two_people(self):
        self.submit()
        self.submit(email="bob@acme.com", name="Bob Eze")  # same company, different person
        self.assertEqual(User.objects.filter(email__in=["ada@acme.com", "bob@acme.com"]).count(), 2)

    def test_an_admin_email_never_becomes_a_customer_login(self):
        self.submit(email="regular@x.com")  # belongs to a staff account
        customer = RFQ.objects.get().user
        self.assertNotEqual(customer.pk, self.regular.pk)
        self.assertFalse(customer.is_staff)
        self.assertEqual(self.login("regular@x.com", "Regul4r-pass!!").status_code, 200)  # admin login untouched

    def test_missing_fields_are_refused_with_the_field_names(self):
        r = self.anon.post("/api/rfqs/", {"name": "x"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.data["fields"])
        self.assertEqual(RFQ.objects.count(), 0)

    def test_the_agreement_must_be_ticked(self):
        for body in ({k: v for k, v in FORM.items() if k != "consent"}, {**FORM, "consent": False}, {**FORM, "consent": "no"}):
            r = self.anon.post("/api/rfqs/", body, format="json")
            self.assertEqual(r.status_code, 400, body)
            self.assertIn("consent", r.data["fields"])
        self.assertEqual((RFQ.objects.count(), User.objects.filter(email="ada@acme.com").count()), (0, 0))  # nothing was created

    def test_an_admin_editing_a_request_does_not_need_it(self):
        self.submit()
        rfq = RFQ.objects.get()
        self.assertEqual(self.super.patch(f"/api/rfqs/{rfq.pk}/", {"item": "changed"}, format="json").status_code, 200)

    def test_it_is_recorded_without_naming_a_person(self):
        self.submit()
        e = AuditLog.objects.get(action="quote_received")
        self.assertEqual(e.actor_email, "")
        self.assertIn("RFQ-", e.target_label)


class ChoosingAPassword(TrackBase):
    def setUp(self):
        super().setUp()
        self.submit()
        self.token = self.link_from_outbox()
        self.user = User.objects.get(email="ada@acme.com")

    def test_link_sets_a_password_and_signs_in(self):
        r = self.anon.post("/api/customer/set-password/", {"token": self.token, "password": STRONG}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("token", r.data)
        self.assertEqual(r.data["user"]["email"], "ada@acme.com")
        self.assertEqual(self.anon.post("/api/customer/login/", {"email": "ADA@acme.com", "password": STRONG}, format="json").status_code, 200)

    def test_link_works_only_once(self):
        self.anon.post("/api/customer/set-password/", {"token": self.token, "password": STRONG}, format="json")
        again = self.anon.post("/api/customer/set-password/", {"token": self.token, "password": STRONG + "x"}, format="json")
        self.assertEqual(again.status_code, 400)

    def test_weak_password_is_refused_and_link_stays_usable(self):
        r = self.anon.post("/api/customer/set-password/", {"token": self.token, "password": "12345678"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.anon.post("/api/customer/set-password/", {"token": self.token, "password": STRONG}, format="json").status_code, 200)

    def test_garbage_and_expired_links_are_refused(self):
        self.assertEqual(self.anon.post("/api/customer/set-password/", {"token": "nope", "password": STRONG}, format="json").status_code, 400)
        old = signing.dumps({"uid": str(self.user.pk), "fp": tracking.password_fingerprint(self.user)}, salt=tracking.SET_PASSWORD_SALT)
        with mock.patch("cms.tracking.SET_PASSWORD_MAX_AGE", -1):
            r = self.anon.post("/api/customer/set-password/", {"token": old, "password": STRONG}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_link_cannot_set_an_admin_password(self):
        forged = signing.dumps({"uid": str(self.regular.pk), "fp": self.regular.password[-16:]}, salt=tracking.SET_PASSWORD_SALT)
        self.assertEqual(self.anon.post("/api/customer/set-password/", {"token": forged, "password": STRONG}, format="json").status_code, 400)

    def test_cannot_sign_in_before_choosing_a_password(self):
        self.assertEqual(self.anon.post("/api/customer/login/", {"email": "ada@acme.com", "password": "Acme Supplies"}, format="json").status_code, 401)
        self.assertEqual(self.anon.post("/api/customer/login/", {"email": "ada@acme.com", "password": ""}, format="json").status_code, 401)

    def test_admins_cannot_sign_in_through_the_customer_door(self):
        self.assertEqual(self.anon.post("/api/customer/login/", {"email": "regular@x.com", "password": "Regul4r-pass!!"}, format="json").status_code, 401)

    def test_request_link_answers_the_same_for_everyone(self):
        mail.outbox.clear()
        known = self.anon.post("/api/customer/request-link/", {"email": "ada@acme.com"}, format="json")
        sent = len(mail.outbox)
        unknown = self.anon.post("/api/customer/request-link/", {"email": "nobody@x.com"}, format="json")
        admin = self.anon.post("/api/customer/request-link/", {"email": "regular@x.com"}, format="json")
        self.assertEqual((known.status_code, unknown.status_code, admin.status_code), (200, 200, 200))
        self.assertEqual(known.data, unknown.data)
        self.assertEqual((sent, len(mail.outbox)), (1, 1))  # only the real customer was emailed


class CustomersSeeOnlyTheirOwn(TrackBase):
    def setUp(self):
        super().setUp()
        self.submit()
        self.submit(email="bob@other.com", name="Bob Eze", item="300 diaries")
        self.ada = User.objects.get(email="ada@acme.com")
        self.bob = User.objects.get(email="bob@other.com")
        self.ada_rfq = RFQ.objects.get(user=self.ada)
        self.bob_rfq = RFQ.objects.get(user=self.bob)

    def test_list_has_only_their_requests(self):
        r = customer_client(self.ada).get("/api/customer/requests/")
        self.assertEqual([x["reference"] for x in r.data], [self.ada_rfq.reference])

    def test_cannot_open_someone_elses_request(self):
        c = customer_client(self.ada)
        self.assertEqual(c.get(f"/api/customer/requests/{self.bob_rfq.pk}/").status_code, 404)
        self.assertEqual(c.get(f"/api/customer/requests/{self.ada_rfq.pk}/").status_code, 200)
        self.assertEqual(c.get("/api/customer/requests/not-a-uuid/").status_code, 404)

    def test_signed_out_is_refused(self):
        self.assertEqual(self.anon.get("/api/customer/requests/").status_code, 401)
        self.assertEqual(self.anon.get(f"/api/customer/requests/{self.ada_rfq.pk}/").status_code, 401)

    def test_a_customer_can_never_use_the_cms(self):
        c = customer_client(self.ada)
        for path in ("/api/rfqs/", "/api/audit/", "/api/user/", "/api/admins/", "/api/inbox/", "/api/sent-emails/", "/api/auth/me/"):
            self.assertIn(c.get(path).status_code, (401, 403), path)
        self.assertIn(c.post("/api/services/", {"title": "x", "description": "y"}, format="json").status_code, (401, 403))
        self.assertIn(c.post(f"/api/rfqs/{self.ada_rfq.pk}/updates/", {"stage": "shipped", "headline": "x"}, format="json").status_code, (401, 403))
        self.assertIn(c.patch(f"/api/rfqs/{self.ada_rfq.pk}/", {"item": "free stuff"}, format="json").status_code, (401, 403))

    def test_an_admin_token_is_not_a_customer_sign_in(self):
        self.assertEqual(self.super.get("/api/customer/requests/").status_code, 403)

    def test_customer_token_expires_after_a_day(self):
        token = make_customer_token(self.ada)
        c = APIClient(); c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        with mock.patch("cms.authentication.CUSTOMER_MAX_AGE", -1):
            self.assertEqual(c.get("/api/customer/requests/").status_code, 401)

    def test_changing_the_password_signs_out_older_sessions(self):
        c = customer_client(self.ada)
        self.ada.set_password(STRONG); self.ada.save()
        self.assertEqual(c.get("/api/customer/requests/").status_code, 401)

    def test_guest_tracking_needs_the_right_pair(self):
        ok = self.anon.post("/api/customer/track/", {"reference": self.ada_rfq.reference.lower(), "email": "ADA@acme.com"}, format="json")
        self.assertEqual(ok.status_code, 200)
        wrong_email = self.anon.post("/api/customer/track/", {"reference": self.ada_rfq.reference, "email": "bob@other.com"}, format="json")
        unknown = self.anon.post("/api/customer/track/", {"reference": "RFQ-2000-0001", "email": "ada@acme.com"}, format="json")
        self.assertEqual((wrong_email.status_code, unknown.status_code), (404, 404))
        self.assertEqual(wrong_email.data, unknown.data)
        self.assertNotIn("email", ok.data)  # contact details are not echoed


class AdminPostsUpdates(TrackBase):
    def setUp(self):
        super().setUp()
        self.submit()
        self.rfq = RFQ.objects.get()
        self.url = f"/api/rfqs/{self.rfq.pk}/updates/"

    def post(self, **body):
        mail.outbox.clear()
        return self.super.post(self.url, {"stage": "sourcing", "headline": "Supplier confirmed", **body}, format="json")

    def test_update_moves_the_status_and_emails_the_customer(self):
        r = self.post(details="Pickup in 2 days", location="Guangzhou", estimated_delivery="2026-11-20")
        self.assertEqual(r.status_code, 201, r.data)
        self.rfq.refresh_from_db()
        self.assertEqual((self.rfq.status, str(self.rfq.estimated_delivery)), ("sourcing", "2026-11-20"))
        self.assertEqual(r.data["step"], 4)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ada@acme.com"])
        self.assertIn("Supplier confirmed", mail.outbox[0].subject)
        self.assertTrue(RFQUpdate.objects.get(headline="Supplier confirmed").emailed)

    def test_every_update_is_emailed(self):
        for stage in ("quoted", "confirmed", "sourcing"):
            self.post(stage=stage, headline=f"went {stage}")
            self.assertEqual(len(mail.outbox), 1, stage)

    def test_admin_can_choose_not_to_email(self):
        self.post(notify=False)
        self.assertEqual(len(mail.outbox), 0)

    def test_customer_sees_the_update_and_the_stages(self):
        self.post(stage="quality", headline="Passed inspection")
        d = customer_client(self.rfq.user).get(f"/api/customer/requests/{self.rfq.pk}/").data
        self.assertEqual((d["status"], d["step"], d["steps"]), ("quality", 5, 8))
        self.assertEqual(d["updates"][0]["headline"], "Passed inspection")  # newest first
        states = {s["key"]: s["state"] for s in d["stages"]}
        self.assertEqual((states["received"], states["quality"], states["shipped"]), ("done", "current", "todo"))

    def test_delivered_marks_every_stage_done(self):
        self.post(stage="delivered", headline="Delivered")
        d = customer_client(self.rfq.user).get(f"/api/customer/requests/{self.rfq.pk}/").data
        self.assertTrue(all(s["state"] == "done" for s in d["stages"]))

    def test_bad_input_is_refused(self):
        self.assertEqual(self.post(stage="flying").status_code, 400)
        self.assertEqual(self.post(headline="").status_code, 400)
        self.assertEqual(self.post(estimated_delivery="soon").status_code, 400)
        self.assertEqual(self.post(headline="x" * 201).status_code, 400)
        self.assertEqual(len(mail.outbox), 0)
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, "received")

    def test_editing_and_deleting_recompute_the_status(self):
        a = self.post(stage="quoted", headline="Quoted").data["updates"][0]["id"]
        b = self.post(stage="shipped", headline="Shipped").data["updates"][0]["id"]
        self.rfq.refresh_from_db(); self.assertEqual(self.rfq.status, "shipped")
        r = self.super.delete(f"{self.url}{b}/")
        self.assertEqual(r.status_code, 200)
        self.rfq.refresh_from_db(); self.assertEqual(self.rfq.status, "quoted")
        r = self.super.patch(f"{self.url}{a}/", {"headline": "Quotation sent", "details": "Ref GHP-2026-0001"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["updates"][0]["headline"], "Quotation sent")
        self.assertEqual(self.super.patch(f"{self.url}00000000-0000-0000-0000-000000000000/", {"headline": "x"}, format="json").status_code, 404)

    def test_it_is_in_the_activity_log(self):
        self.post()
        e = AuditLog.objects.get(action="created", target_type="order update")
        self.assertEqual(e.actor_email, ADMIN_EMAIL)
        self.assertIn(self.rfq.reference, e.target_label)

    def test_regular_admins_can_post_too_and_the_public_cannot(self):
        r = self.regular_client.post(self.url, {"stage": "quoted", "headline": "Quoted"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.anon.post(self.url, {"stage": "quoted", "headline": "x"}, format="json").status_code, 401)

    def test_reference_cannot_be_edited_from_the_cms(self):
        before = self.rfq.reference
        self.super.patch(f"/api/rfqs/{self.rfq.pk}/", {"reference": "RFQ-1999-0001", "status": "delivered", "delivery_address": "12 Marina, Lagos"}, format="json")
        self.rfq.refresh_from_db()
        self.assertEqual((self.rfq.reference, self.rfq.status, self.rfq.delivery_address), (before, "received", "12 Marina, Lagos"))

    def test_quotation_reference_comes_from_the_quotation_email(self):
        from .references import create_sent_email
        create_sent_email("rfq_reply", recipient="ada@acme.com", subject="Quote", title="Quote", body="x")
        d = customer_client(self.rfq.user).get(f"/api/customer/requests/{self.rfq.pk}/").data
        self.assertRegex(d["quotation_reference"], r"^GHP-\d{4}-0001$")

    def test_a_customer_with_requests_still_cannot_be_deleted(self):
        self.assertEqual(self.super.delete(f"/api/user/{self.rfq.user.pk}/").status_code, 409)


class TheEmails(TrackBase):
    """What the customer actually reads."""

    def html(self, index=-1):
        return mail.outbox[index].alternatives[0][0]

    def test_request_received_shows_the_reference_details_steps_and_button(self):
        self.submit()
        h, m = self.html(), mail.outbox[-1]
        rfq = RFQ.objects.get()
        for must in (rfq.reference, "20 office chairs", "Acme Supplies", "Quotation within", "48 hours", "What happens next",
                     "We send your quotation", "Choose my password", "Request receipt", "We have your request, Ada."):
            self.assertIn(must, h, must)
        self.assertIn("email/header.jpg", h)  # the photo at the top
        self.assertIn(rfq.reference, m.body)  # and the plain-text version has it too
        self.assertIn("set-password?token=", m.body)

    def test_a_returning_customer_gets_a_track_button_not_a_password_link(self):
        self.submit()
        self.submit(item="more chairs")
        h = self.html()
        self.assertIn("Track my request", h)
        self.assertNotIn("set-password", h)

    def test_update_email_has_the_headline_stage_place_and_estimate(self):
        self.submit()
        rfq = RFQ.objects.get()
        mail.outbox.clear()
        self.super.post(f"/api/rfqs/{rfq.pk}/updates/", {"stage": "shipped", "headline": "Handed to the carrier", "details": "Truck leaves today",
                                                          "location": "Lagos port", "estimated_delivery": "2026-11-20"}, format="json")
        h = self.html()
        for must in ("Handed to the carrier", "Truck leaves today", "Shipped (step 6 of 8)", "Lagos port", "20 November 2026", "Progress update", "See my order", rfq.reference):
            self.assertIn(must, h, must)
        self.assertNotIn("What happens next", h)  # that block is only for the first email

    def test_customer_text_is_escaped_in_the_email(self):
        self.submit(name="Ada <script>alert(1)</script>", item="<b>x</b>")
        h = self.html()
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertNotIn("<b>x</b>", h.split("What happens next")[0])

    def test_password_link_email(self):
        self.submit()
        mail.outbox.clear()
        self.anon.post("/api/customer/request-link/", {"email": "ada@acme.com"}, format="json")
        h = self.html()
        self.assertIn("Your account", h)
        self.assertIn("Choose my password", h)


class TheFormHasLimits(TrackBase):
    """Nobody can use the public form to flood an inbox (or the site)."""

    def post_as(self, email, ip="10.0.0.1", **extra):
        c = APIClient(HTTP_X_FORWARDED_FOR=ip)
        return c.post("/api/rfqs/", {**FORM, "email": email, **extra}, format="json")

    def test_the_same_email_can_get_only_three_in_an_hour(self):
        codes = [self.post_as("victim@x.com", ip=f"10.0.0.{i}").status_code for i in range(1, 6)]  # different visitors each time
        self.assertEqual(codes, [200, 200, 200, 429, 429])
        self.assertEqual(RFQ.objects.filter(email="victim@x.com").count(), 3)

    def test_a_refused_request_sends_no_email_and_creates_nothing(self):
        for i in range(3):
            self.post_as("victim@x.com", ip=f"10.0.1.{i}")
        mail.outbox.clear()
        r = self.post_as("victim@x.com", ip="10.0.1.9")
        self.assertEqual(r.status_code, 429)
        self.assertIn("detail", r.data)
        self.assertEqual((len(mail.outbox), RFQ.objects.filter(email="victim@x.com").count()), (0, 3))

    def test_the_limit_is_per_email_case_insensitive(self):
        for i in range(3):
            self.post_as("Victim@X.com", ip=f"10.0.2.{i}")
        self.assertEqual(self.post_as("victim@x.COM", ip="10.0.2.9").status_code, 429)

    def test_it_lifts_after_an_hour(self):
        for i in range(3):
            self.post_as("victim@x.com", ip=f"10.0.3.{i}")
        RFQ.objects.filter(email="victim@x.com").update(created_at=timezone.now() - timedelta(minutes=61))
        self.assertEqual(self.post_as("victim@x.com", ip="10.0.3.9").status_code, 200)

    def test_one_visitor_can_send_five_an_hour_to_different_people(self):
        codes = [self.post_as(f"person{i}@x.com", ip="9.9.9.9").status_code for i in range(7)]
        self.assertEqual(codes, [200] * 5 + [429] * 2)
        self.assertEqual(self.post_as("other@x.com", ip="8.8.8.8").status_code, 200)  # someone else is unaffected

    def test_the_visitor_is_the_first_forwarded_address(self):
        for i in range(5):
            self.post_as(f"p{i}@x.com", ip="7.7.7.7, 10.1.1.1")
        self.assertEqual(self.post_as("q@x.com", ip="7.7.7.7, 10.2.2.2").status_code, 429)

    def test_a_site_wide_ceiling_protects_the_sender_reputation(self):
        with mock.patch("cms.tracking.RFQ_SITE_WIDE", 2):
            self.post_as("a1@x.com", ip="1.1.1.1"); self.post_as("a2@x.com", ip="1.1.1.2")
            r = self.post_as("a3@x.com", ip="1.1.1.3")
        self.assertEqual(r.status_code, 429)

    def test_typos_do_not_use_up_the_allowance(self):
        for _ in range(8):
            self.assertEqual(self.anon.post("/api/rfqs/", {"name": "x"}, format="json").status_code, 400)
        self.assertEqual(self.post_as("fine@x.com", ip="127.0.0.1").status_code, 200)

    def test_the_cms_is_not_affected(self):
        for i in range(3):
            self.post_as("victim@x.com", ip=f"10.0.4.{i}")
        rfq = RFQ.objects.filter(email="victim@x.com").first()
        self.assertEqual(self.super.patch(f"/api/rfqs/{rfq.pk}/", {"item": "edited"}, format="json").status_code, 200)


class RandomReferences(TrackBase):
    def test_references_are_random_not_counting_up(self):
        refs = []
        for i in range(12):
            refs.append(APIClient(HTTP_X_FORWARDED_FOR=f"10.9.0.{i}").post("/api/rfqs/", {**FORM, "email": f"p{i}@x.com"}, format="json").data["reference"])
        self.assertEqual(len(set(refs)), 12)
        codes = [r.split("-")[2] for r in refs]
        self.assertFalse(any(c.isdigit() for c in codes))  # none of them is a plain counting number
        self.assertNotEqual(codes, sorted(codes))  # and they do not come out in order

    def test_format_and_no_look_alike_characters(self):
        import re
        ref = self.anon.post("/api/rfqs/", FORM, format="json").data["reference"]
        self.assertRegex(ref, r"^RFQ-\d{4}-[A-Z2-9]{6}$")
        for bad in "01OIL":
            self.assertNotIn(bad, ref.split("-")[2])

    def test_a_duplicate_is_never_issued(self):
        from . import references
        with mock.patch.object(references, "REFERENCE_ALPHABET", "AB"), mock.patch.object(references, "REFERENCE_LENGTH", 3):
            made = set()
            for i in range(8):  # only 8 codes exist: it must find each free one, then give up cleanly
                made.add(references.allocate_rfq_reference(user=User.objects.create(username=f"u{i}", dp=""), email=f"e{i}@x.com", name="n",
                                                            phone="1", company="c", item="i").reference)
            self.assertEqual(len(made), 8)
            with self.assertRaises(RuntimeError):
                references.allocate_rfq_reference(user=User.objects.create(username="last", dp=""), email="l@x.com", name="n", phone="1", company="c", item="i")

    def test_tracking_accepts_any_letter_case(self):
        self.submit()
        rfq = RFQ.objects.get()
        r = self.anon.post("/api/customer/track/", {"reference": rfq.reference.lower(), "email": "ada@acme.com"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_old_counting_references_still_work(self):
        self.submit()
        rfq = RFQ.objects.get()
        RFQ.objects.filter(pk=rfq.pk).update(reference="RFQ-2026-0001")
        r = self.anon.post("/api/customer/track/", {"reference": "rfq-2026-0001", "email": "ada@acme.com"}, format="json")
        self.assertEqual(r.status_code, 200)


class EmailLinksHaveNoHash(TrackBase):
    def test_links_are_plain_addresses(self):
        self.submit()
        html = mail.outbox[-1].alternatives[0][0]
        self.assertNotIn("/#/", html)
        self.assertRegex(html, r"https?://[^\"']+/set-password\?token=")
        rfq = RFQ.objects.get()
        mail.outbox.clear()
        self.super.post(f"/api/rfqs/{rfq.pk}/updates/", {"stage": "quoted", "headline": "Quoted"}, format="json")
        update = mail.outbox[-1].alternatives[0][0]
        self.assertNotIn("/#/", update)
        self.assertIn(f"/account/{rfq.pk}", update)
        self.assertEqual(len(mail.outbox), 1)  # emailed automatically, no checkbox needed
