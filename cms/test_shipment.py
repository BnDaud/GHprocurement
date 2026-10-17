"""Carrier and tracking number on an order, and PDF documents for the customer to download."""
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from . import documents
from .models import OrderDocument, RFQ, User
from .test_quote import SendingQuotes
from .test_tracking import customer_client

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


def pdf(name="invoice.pdf", data=PDF):
    return SimpleUploadedFile(name, data, content_type="application/pdf")


class Shipment(SendingQuotes):
    def setUp(self):
        super().setUp()
        self.me = customer_client(User.objects.get(email="ada@acme.com"))
        self.upd = f"/api/rfqs/{self.rfq.pk}/updates/"
        self.detail = f"/api/customer/requests/{self.rfq.pk}/"

    def ship(self, **extra):
        mail.outbox.clear()
        return self.super.post(self.upd, {"stage": "shipped", "headline": "On its way", **extra}, format="json")

    def test_no_shipment_until_the_team_adds_one(self):
        self.assertIsNone(self.me.get(self.detail).data["shipment"])

    def test_carrier_and_tracking_number_reach_the_customer_page_and_email(self):
        r = self.ship(carrier="DHL Express", tracking_number="1234567890")
        self.assertEqual(r.status_code, 201, r.data)
        s = self.me.get(self.detail).data["shipment"]
        self.assertEqual((s["carrier"], s["tracking_number"]), ("DHL Express", "1234567890"))
        self.assertIn("1234567890", s["tracking_url"])  # worked out for a well-known carrier
        self.assertIsNotNone(s["shipped_at"])
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("DHL Express", html)
        self.assertIn("1234567890", html)

    def test_a_typed_link_wins_and_an_unknown_carrier_gets_none(self):
        self.ship(carrier="Local courier", tracking_number="L-77")
        self.assertEqual(self.me.get(self.detail).data["shipment"]["tracking_url"], "")
        self.ship(carrier="Local courier", tracking_number="L-77", tracking_url="https://track.example.com/L-77")
        self.assertEqual(self.me.get(self.detail).data["shipment"]["tracking_url"], "https://track.example.com/L-77")

    def test_a_bad_link_is_refused(self):
        for bad in ("javascript:alert(1)", "not a link", "ftp://x.com/y"):
            r = self.ship(carrier="X", tracking_url=bad)
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.rfq.updates.filter(stage="shipped").count(), 0)

    def test_the_team_sees_the_same_fields(self):
        self.ship(carrier="UPS", tracking_number="1Z999")
        r = self.super.get(f"/api/rfqs/{self.rfq.pk}/").data
        self.assertEqual((r["carrier"], r["tracking_number"]), ("UPS", "1Z999"))


class Documents(SendingQuotes):
    def setUp(self):
        super().setUp()
        self.me = customer_client(User.objects.get(email="ada@acme.com"))
        self.docs = f"/api/rfqs/{self.rfq.pk}/documents/"
        self.stub = mock.patch("cms.documents.store", return_value="RFQ_Docs/abc123")
        self.stub.start()
        self.addCleanup(self.stub.stop)

    def upload(self, file=None, **extra):
        return self.super.post(self.docs, {"file": file or pdf(), "kind": "invoice", **extra}, format="multipart")

    def test_the_team_adds_a_pdf_and_the_customer_sees_it(self):
        r = self.upload(title="Invoice 0042")
        self.assertEqual(r.status_code, 201, r.data)
        d = self.me.get(f"/api/customer/requests/{self.rfq.pk}/").data["documents"]
        self.assertEqual([(x["title"], x["kind"]) for x in d], [("Invoice 0042", "invoice")])
        self.assertNotIn("file_id", d[0])  # the private storage name never leaves the server

    def test_only_real_pdfs_of_sensible_size_are_accepted(self):
        self.assertEqual(self.upload(pdf("x.pdf", b"not a pdf")).status_code, 400)
        self.assertEqual(self.upload(pdf("x.txt")).status_code, 400)
        with mock.patch.object(documents, "MAX_SIZE", 10):
            self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(self.super.post(self.docs, {}, format="multipart").status_code, 400)
        self.assertEqual(OrderDocument.objects.count(), 0)

    def test_a_failed_upload_to_storage_is_reported_not_saved(self):
        with mock.patch("cms.documents.store", return_value=None):
            self.assertEqual(self.upload().status_code, 502)
        self.assertEqual(OrderDocument.objects.count(), 0)

    def test_the_customer_can_download_their_own_document(self):
        doc_id = self.upload(title="Packing list").data["id"]
        with mock.patch("cms.documents.fetch", return_value=PDF):
            r = self.me.get(f"/api/customer/requests/{self.rfq.pk}/documents/{doc_id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn('filename="Packing-list.pdf"', r["Content-Disposition"])
        self.assertEqual(r.content, PDF)

    def test_nobody_else_can_download_it(self):
        doc_id = self.upload().data["id"]
        url = f"/api/customer/requests/{self.rfq.pk}/documents/{doc_id}/"
        self.assertIn(APIClient().get(url).status_code, (401, 403))
        self.submit(email="bob@other.com", name="Bob Eze", item="x")
        bob = customer_client(User.objects.get(email="bob@other.com"))
        with mock.patch("cms.documents.fetch", return_value=PDF):
            self.assertEqual(bob.get(url).status_code, 404)
        self.assertIn(self.me.post(self.docs, {"file": pdf()}, format="multipart").status_code, (401, 403))  # a customer cannot upload

    def test_the_team_can_download_and_remove_it(self):
        doc_id = self.upload().data["id"]
        with mock.patch("cms.documents.fetch", return_value=PDF):
            self.assertEqual(self.super.get(f"{self.docs}{doc_id}/").status_code, 200)
        with mock.patch("cms.documents.delete", return_value=True) as gone:
            self.assertEqual(self.super.delete(f"{self.docs}{doc_id}/").status_code, 204)
        gone.assert_called_once_with("RFQ_Docs/abc123")
        self.assertEqual(OrderDocument.objects.count(), 0)
        self.assertEqual(self.me.get(f"/api/customer/requests/{self.rfq.pk}/").data["documents"], [])
