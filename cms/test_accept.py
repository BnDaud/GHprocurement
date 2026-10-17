"""A customer can accept their quotation on the website; the order moves to Confirmed by itself."""
from django.core import mail
from rest_framework.test import APIClient

from .models import AuditLog, RFQ, User
from .test_quote import SendingQuotes
from .test_tracking import customer_client


class AcceptQuotation(SendingQuotes):
    def setUp(self):
        super().setUp()
        self.ada = User.objects.get(email="ada@acme.com")
        self.me = customer_client(self.ada)
        self.url = f"/api/customer/requests/{self.rfq.pk}/accept/"

    def quote(self):
        self.super.post(f"/api/rfqs/{self.rfq.pk}/updates/", {"stage": "quoted", "headline": "Quotation sent"}, format="json")
        mail.outbox.clear()

    def test_accepting_moves_the_order_to_confirmed(self):
        self.quote()
        r = self.me.post(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, "confirmed")
        self.assertEqual(r.data["status"], "confirmed")
        self.assertEqual(r.data["updates"][0]["headline"], "You accepted the quotation")

    def test_the_customer_and_the_team_are_both_emailed(self):
        self.quote()
        self.me.post(self.url)
        to = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(len(mail.outbox), 2, to)
        self.assertIn("ada@acme.com", to)
        team = next(m for m in mail.outbox if m.to[0] != "ada@acme.com")
        self.assertIn("accepted", team.subject)
        self.assertIn("/orders/", team.alternatives[0][0])

    def test_it_is_written_to_the_audit_log(self):
        self.quote()
        self.me.post(self.url)
        self.assertTrue(AuditLog.objects.filter(target_label=self.rfq.reference, detail__contains="accepted").exists())

    def test_it_only_works_once_and_only_when_quoted(self):
        early = self.me.post(self.url)  # nothing quoted yet
        self.assertEqual(early.status_code, 409)
        self.quote()
        self.assertEqual(self.me.post(self.url).status_code, 200)
        self.assertEqual(self.me.post(self.url).status_code, 409)  # a double click
        self.assertEqual(self.rfq.updates.filter(stage="confirmed").count(), 1)

    def test_it_is_refused_once_the_team_has_confirmed_or_moved_on(self):
        self.quote()
        self.super.post(f"/api/rfqs/{self.rfq.pk}/updates/", {"stage": "sourcing", "headline": "Sourcing"}, format="json")
        self.assertEqual(self.me.post(self.url).status_code, 409)

    def test_the_team_can_still_confirm_by_hand(self):
        self.quote()
        r = self.super.post(f"/api/rfqs/{self.rfq.pk}/updates/", {"stage": "confirmed", "headline": "Confirmed by phone"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, "confirmed")

    def test_nobody_else_can_accept_it(self):
        self.quote()
        self.assertIn(APIClient().post(self.url).status_code, (401, 403))
        self.submit(email="bob@other.com", name="Bob Eze", item="x")
        bob = customer_client(User.objects.get(email="bob@other.com"))
        self.assertEqual(bob.post(self.url).status_code, 404)
        self.assertEqual(self.me.post("/api/customer/requests/not-a-uuid/accept/").status_code, 404)
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, "quoted")
