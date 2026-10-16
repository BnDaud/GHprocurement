"""A customer can open any progress update and read the email that went with it."""
import json
from unittest import mock

from rest_framework.test import APIClient

from .models import RFQ, User
from .test_quote import ROWS, SendingQuotes
from .test_tracking import customer_client


class SeeWhatWasSent(SendingQuotes):
    def setUp(self):
        super().setUp()
        self.ada = User.objects.get(email="ada@acme.com")
        self.me = customer_client(self.ada)
        self.url = f"/api/customer/requests/{self.rfq.pk}/"

    def update(self, stage="sourcing", headline="Sourcing started", details="We found a supplier."):
        r = self.super.post(f"/api/rfqs/{self.rfq.pk}/updates/", {"stage": stage, "headline": headline, "details": details}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return self.rfq.updates.order_by("-created_at").first().pk

    def test_an_update_email_can_be_read_on_the_site(self):
        uid = self.update()
        r = self.me.get(f"{self.url}updates/{uid}/email/")
        self.assertEqual(r.status_code, 200)
        h = r.content.decode()
        for must in ("Sourcing started", "We found a supplier.", self.rfq.reference, "See my order", "data:image/png;base64"):
            self.assertIn(must, h, must)
        self.assertNotIn("cid:", h)

    def test_the_receipt_never_shows_the_private_password_link(self):
        first = self.rfq.updates.order_by("created_at").first()
        h = self.me.get(f"{self.url}updates/{first.pk}/email/").content.decode()
        self.assertIn("We have your request", h)
        self.assertNotIn("set-password", h)
        self.assertNotIn("token=", h)

    def test_a_quotation_update_shows_the_quotation(self):
        class Inline:
            def __init__(self, target, args=()):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        data = {"kind": "rfq_reply", "recipient": "ada@acme.com", "subject": "Quote", "title": "Quote", "body": "Prices below.",
                "items": json.dumps(ROWS), "currency": "NGN", "rfq": str(self.rfq.pk)}
        with mock.patch("cms.views.threading.Thread", Inline), mock.patch("cms.task._record_outcome"):
            self.assertEqual(self.super.post("/api/emails/", data, format="multipart").status_code, 200)
        upd = self.rfq.updates.filter(headline__startswith="Quotation").get()
        h = self.me.get(f"{self.url}updates/{upd.pk}/email/").content.decode()
        self.assertIn("Price breakdown", h)
        self.assertIn("Office chair", h)

    def test_nobody_else_can_read_it(self):
        uid = self.update()
        self.assertEqual(APIClient().get(f"{self.url}updates/{uid}/email/").status_code in (401, 403), True)
        self.submit(email="bob@other.com", name="Bob Eze", item="x")
        bob = customer_client(User.objects.get(email="bob@other.com"))
        self.assertEqual(bob.get(f"{self.url}updates/{uid}/email/").status_code, 404)
        self.assertEqual(self.me.get(f"{self.url}updates/not-a-uuid/email/").status_code, 404)

    def test_guest_tracking_can_read_it_with_reference_and_email(self):
        uid = self.update()
        ok = APIClient().post("/api/customer/track/email/", {"reference": self.rfq.reference, "email": "ada@acme.com", "update": str(uid)}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("Sourcing started", ok.content.decode())
        bad = APIClient().post("/api/customer/track/email/", {"reference": self.rfq.reference, "email": "wrong@x.com", "update": str(uid)}, format="json")
        self.assertEqual(bad.status_code, 404)
