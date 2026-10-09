"""The customer portal switch, and what admins see in the CMS."""
from unittest import mock

from django.core import mail
from django.test import override_settings

from .models import AuditLog, RFQ
from .test_tracking import FORM, TrackBase, customer_client


class PortalNotLive(TrackBase):
    """The new customer site is not live: the live (old) public form must keep working."""

    def setUp(self):
        super().setUp()
        p = override_settings(CUSTOMER_PORTAL_LIVE=False)
        p.enable()
        self.addCleanup(p.disable)

    def old_form_post(self):
        body = {k: v for k, v in FORM.items() if k != "consent"}  # the old site never sends consent
        mail.outbox.clear()
        return self.anon.post("/api/rfqs/", body, format="json")

    def test_the_old_form_still_works_without_the_agreement_box(self):
        r = self.old_form_post()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(RFQ.objects.count(), 1)

    def test_the_confirmation_goes_by_postmark_and_never_touches_zoho(self):
        with mock.patch("cms.task.requests") as zoho:
            self.old_form_post()
            zoho.post.assert_not_called()
            zoho.get.assert_not_called()
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["ada@acme.com"])
        self.assertRegex(m.subject, r"^We have your request RFQ-\d{4}-[A-Z2-9]{6}$")

    def test_it_has_no_links_to_customer_pages_that_do_not_exist_yet(self):
        self.old_form_post()
        html = mail.outbox[0].alternatives[0][0]
        for gone in ("set-password", "/account", "Choose my password", "Track my request", "followed on your account"):
            self.assertNotIn(gone, html, gone)
        self.assertIn("We have your request, Ada.", html)
        self.assertIn("Quotation within", html)
        self.assertIn("We source, check and ship", html)

    def test_the_account_is_still_made_safely(self):
        self.old_form_post()
        user = RFQ.objects.get().user
        self.assertFalse(user.has_usable_password())  # never the company name again

    def test_progress_updates_are_saved_but_the_customer_is_not_emailed(self):
        self.old_form_post()
        rfq = RFQ.objects.get()
        mail.outbox.clear()
        r = self.super.post(f"/api/rfqs/{rfq.pk}/updates/", {"stage": "sourcing", "headline": "Supplier confirmed", "notify": True}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertIs(r.data["emailed"], False)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("customer not emailed", AuditLog.objects.get(target_type="order update").detail)

    def test_the_cms_is_told_the_portal_is_off(self):
        self.assertIs(self.super.get("/api/auth/me/").data["customer_portal"], False)


class PortalLive(TrackBase):
    def test_the_cms_is_told_the_portal_is_on(self):
        self.assertIs(self.super.get("/api/auth/me/").data["customer_portal"], True)

    def test_audit_says_who_moved_which_order_from_what_to_what(self):
        self.submit()
        rfq = RFQ.objects.get()
        mail.outbox.clear()
        r = self.regular_client.post(f"/api/rfqs/{rfq.pk}/updates/", {"stage": "shipped", "headline": "Handed to the carrier"}, format="json")
        self.assertIs(r.data["emailed"], True)
        e = AuditLog.objects.get(target_type="order update")
        self.assertEqual(e.actor_email, "regular@x.com")  # any admin can do it, and it is on their name
        self.assertEqual(e.target_label, f"{rfq.reference}: Handed to the carrier")
        self.assertEqual(e.detail, "moved from Request received to Shipped; customer emailed")

    def test_a_note_at_the_same_stage_is_described_as_a_note(self):
        self.submit()
        rfq = RFQ.objects.get()
        self.super.post(f"/api/rfqs/{rfq.pk}/updates/", {"stage": "received", "headline": "Still checking", "notify": False}, format="json")
        self.assertEqual(AuditLog.objects.get(target_type="order update").detail, "note at Request received; customer not emailed")

    def test_changing_the_stage_of_an_update_is_logged_with_both_stages(self):
        self.submit()
        rfq = RFQ.objects.get()
        uid = self.super.post(f"/api/rfqs/{rfq.pk}/updates/", {"stage": "quoted", "headline": "Quoted", "notify": False}, format="json").data["updates"][0]["id"]
        self.super.patch(f"/api/rfqs/{rfq.pk}/updates/{uid}/", {"stage": "confirmed"}, format="json")
        e = AuditLog.objects.get(action="updated", target_type="order update")
        self.assertIn("stage changed from Quoted to Confirmed", e.detail)

    def test_deleting_an_update_is_logged(self):
        self.submit()
        rfq = RFQ.objects.get()
        uid = self.super.post(f"/api/rfqs/{rfq.pk}/updates/", {"stage": "quoted", "headline": "Quoted", "notify": False}, format="json").data["updates"][0]["id"]
        self.super.delete(f"/api/rfqs/{rfq.pk}/updates/{uid}/")
        self.assertEqual(AuditLog.objects.get(action="deleted", target_type="order update").detail, "stage was Quoted")

    def test_the_cms_sees_who_posted_each_update_but_customers_never_do(self):
        self.submit()
        rfq = RFQ.objects.get()
        self.regular_client.post(f"/api/rfqs/{rfq.pk}/updates/", {"stage": "quoted", "headline": "Quoted", "notify": False}, format="json")
        mine = self.super.get(f"/api/rfqs/{rfq.pk}/updates/").data
        self.assertEqual({u["headline"]: u["posted_by"] for u in mine}["Quoted"], "regular@x.com")
        theirs = customer_client(rfq.user).get(f"/api/customer/requests/{rfq.pk}/").data["updates"]
        self.assertTrue(theirs and all("posted_by" not in u for u in theirs))
        guest = self.anon.post("/api/customer/track/", {"reference": rfq.reference, "email": "ada@acme.com"}, format="json").data["updates"]
        self.assertTrue(all("posted_by" not in u for u in guest))

    def test_activity_can_be_filtered_to_orders_only(self):
        self.submit()
        rfq = RFQ.objects.get()
        self.super.post("/api/services/", {"title": "S", "description": "d"}, format="json")
        self.super.post(f"/api/rfqs/{rfq.pk}/updates/", {"stage": "quoted", "headline": "Quoted", "notify": False}, format="json")
        r = self.super.get("/api/audit/?target=order update").data
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["results"][0]["target_type"], "order update")
        # and a reference finds everything that happened to one order
        refs = self.super.get(f"/api/audit/?q={rfq.reference}").data
        self.assertGreaterEqual(refs["total"], 2)
