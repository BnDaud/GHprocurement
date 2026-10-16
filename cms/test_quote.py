"""Quotations: the price table, and sending one for an order."""
import json
from decimal import Decimal
from unittest import mock

from django.core import mail

from . import quote
from .models import AuditLog, RFQ, SentEmail
from .test_tracking import TrackBase

ROWS = [{"description": "Office chair", "quantity": "20", "unit_price": "45000"},
        {"description": "Desk, 1.4m", "quantity": "5", "unit_price": "82500.50"}]


class Arithmetic(TrackBase):
    def test_adds_up_with_delivery_and_vat(self):
        rows = quote.parse_items(json.dumps(ROWS))
        q = quote.build(rows, "NGN", Decimal("15000"), Decimal("7.5"))
        self.assertEqual(q["rows"][0]["line_total"], "900000.00")
        self.assertEqual(q["rows"][1]["line_total"], "412502.50")
        self.assertEqual(q["subtotal"], "1312502.50")
        self.assertEqual(q["vat"], "99562.69")  # (1,312,502.50 + 15,000) x 7.5%, rounded half up
        self.assertEqual(q["total"], "1427065.19")
        self.assertEqual(q["total_text"], "₦1,427,065.19")

    def test_no_delivery_or_vat_means_no_such_lines(self):
        q = quote.build(quote.parse_items(ROWS), "USD")
        self.assertEqual((q["shipping_text"], q["vat_text"], q["total"]), ("", "", q["subtotal"]))

    def test_decimal_quantities_and_rounding(self):
        q = quote.build(quote.parse_items([{"description": "Cable (m)", "quantity": "2.5", "unit_price": "3.33"}]), "GBP")
        self.assertEqual((q["rows"][0]["quantity"], q["rows"][0]["line_total"]), ("2.5", "8.33"))  # 8.325 rounds up

    def test_symbols(self):
        self.assertEqual([quote.money(1234.5, c) for c in ("USD", "NGN", "GBP", "EUR")], ["$1,234.50", "₦1,234.50", "£1,234.50", "€1,234.50"])

    def test_bad_rows_are_refused_with_a_clear_message(self):
        bad = [({"description": "", "quantity": "1", "unit_price": "1"}, "describe the item"),
               ({"description": "x", "quantity": "0", "unit_price": "1"}, "quantity"),
               ({"description": "x", "quantity": "abc", "unit_price": "1"}, "not a number"),
               ({"description": "x", "quantity": "1", "unit_price": "-5"}, "unit price"),
               ({"description": "x", "quantity": "1", "unit_price": "NaN"}, "unit price"),
               ({"description": "x" * 201, "quantity": "1", "unit_price": "1"}, "under 200")]
        for row, word in bad:
            with self.assertRaises(Exception) as cm:
                quote.parse_items([row])
            self.assertIn(word, str(cm.exception), row)
        with self.assertRaises(Exception):
            quote.parse_items([ROWS[0]] * 51)
        with self.assertRaises(Exception):
            quote.parse_items("not json")


class SendingQuotes(TrackBase):
    def setUp(self):
        super().setUp()
        self.submit()
        self.rfq = RFQ.objects.get()
        mail.outbox.clear()

    def send(self, **extra):
        """Posts to the Mail endpoint. The sending thread is run inline (and its database
        bookkeeping skipped) so the test can read the email it produced."""
        class Inline:
            def __init__(self, target, args=()):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        data = {"kind": "rfq_reply", "recipient": "ada@acme.com", "recipient_name": "Ada Obi", "subject": "Your quotation",
                "title": "Your quotation", "body": "Please find our prices below.", "items": json.dumps(ROWS), "currency": "NGN",
                "shipping": "15000", "vat_percent": "7.5", **extra}
        with mock.patch("cms.views.threading.Thread", Inline), mock.patch("cms.task._record_outcome"):
            return self.super.post("/api/emails/", data, format="multipart")

    def test_the_email_contains_the_table_and_the_correct_total(self):
        r = self.send()
        self.assertEqual(r.status_code, 200, r.data)
        h = mail.outbox[0].alternatives[0][0]
        for must in ("Price breakdown (NGN)", "Office chair", "₦45,000.00", "₦900,000.00", "Subtotal", "Delivery", "VAT (7.5%)", "₦1,427,065.19", "Prepared for"):
            self.assertIn(must, h, must)
        self.assertIn("TOTAL: ₦1,427,065.19", mail.outbox[0].body)  # the plain-text version too
        self.assertIn("email/header.jpg", h)

    def test_the_sent_record_keeps_the_table_and_total(self):
        self.send()
        rec = SentEmail.objects.get()
        self.assertEqual(rec.quote["total"], "1427065.19")
        row = self.super.get("/api/sent-emails/").data[0]
        self.assertEqual(row["total"], "₦1,427,065.19")
        self.assertEqual(len(self.super.get(f"/api/sent-emails/{rec.pk}/").data["quote"]["rows"]), 2)

    def test_currency_defaults_to_the_one_in_settings(self):
        from .models import MetaData
        MetaData.objects.create(metaIntro="a", metaDescription="b", email="x@y.com", phone="1", office="o", currency="GBP")
        self.send(currency="")
        self.assertIn("£", mail.outbox[0].alternatives[0][0])

    def test_nothing_unsafe_reaches_the_email(self):
        self.send(items=json.dumps([{"description": "<script>alert(1)</script>", "quantity": "1", "unit_price": "5"}]), shipping="", vat_percent="")
        h = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<script>alert(1)</script>", h)
        self.assertIn("&lt;script&gt;", h)

    def test_totals_come_from_the_server_not_the_browser(self):
        self.send(total="1")  # a made-up 'total' field is ignored
        self.assertIn("₦1,427,065.19", mail.outbox[0].alternatives[0][0])

    def test_a_bad_table_stops_the_send(self):
        r = self.send(items=json.dumps([{"description": "x", "quantity": "-1", "unit_price": "1"}]))
        self.assertEqual(r.status_code, 400)
        self.assertEqual((len(mail.outbox), SentEmail.objects.count()), (0, 0))  # no reference number used up either
        self.assertEqual(self.send(currency="XYZ").status_code, 400)
        self.assertEqual(self.send(items="", vat_percent="7.5").status_code, 400)  # VAT with no rows makes no sense

    def test_a_table_only_goes_with_a_quotation(self):
        self.assertEqual(self.send(kind="outreach").status_code, 400)

    def test_without_a_table_it_is_the_plain_quotation(self):
        self.assertEqual(self.send(items="", shipping="", vat_percent="").status_code, 200)
        self.assertNotIn("Price breakdown", mail.outbox[0].alternatives[0][0])

    # ---- from the Orders page
    def test_a_quotation_for_an_order_moves_it_to_quoted_and_is_traceable(self):
        r = self.send(rfq=str(self.rfq.pk))
        self.assertEqual(r.status_code, 200, r.data)
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, "quoted")
        rec = SentEmail.objects.get()
        self.assertEqual(rec.rfq, self.rfq)
        self.assertEqual(len(mail.outbox), 1)  # the quotation itself: no second 'update' email
        h = mail.outbox[0].alternatives[0][0]
        self.assertIn(self.rfq.reference, h)
        self.assertIn(f"/account/{self.rfq.pk}", h)
        self.assertIn("See my order", h)
        self.assertIn("Quotation", self.rfq.updates.first().headline)
        sent = AuditLog.objects.get(action="email_sent")
        self.assertIn(f"for order {self.rfq.reference}", sent.detail)
        self.assertIn("total ₦1,427,065.19", sent.detail)
        step = AuditLog.objects.get(target_type="order update")
        self.assertEqual(step.detail, "moved from Request received to Quoted; quotation emailed")

    def test_the_customer_sees_the_quotation_number_on_their_order(self):
        from .test_tracking import customer_client
        self.send(rfq=str(self.rfq.pk))
        d = customer_client(self.rfq.user).get(f"/api/customer/requests/{self.rfq.pk}/").data
        self.assertRegex(d["quotation_reference"], r"^GHP-\d{4}-[A-Z2-9]{6}$")
        self.assertEqual(d["status"], "quoted")

    def test_a_later_quotation_never_moves_the_order_backwards(self):
        self.super.post(f"/api/rfqs/{self.rfq.pk}/updates/", {"stage": "shipped", "headline": "Shipped", "notify": False}, format="json")
        self.send(rfq=str(self.rfq.pk))
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, "shipped")

    def test_unknown_order_is_refused(self):
        self.assertEqual(self.send(rfq="00000000-0000-0000-0000-000000000000").status_code, 400)

    def test_only_admins_can_send(self):
        r = self.anon.post("/api/emails/", {"kind": "rfq_reply", "recipient": "a@b.com", "subject": "s", "title": "t", "body": "b"}, format="multipart")
        self.assertEqual(r.status_code, 401)
