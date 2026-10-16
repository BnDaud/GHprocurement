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

    def _png(self, name="chair.png", size=(900, 600)):
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
        return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")

    def test_an_item_picture_sits_inside_the_table_not_in_the_attachments(self):
        rows = [dict(ROWS[0], image=0), ROWS[1]]
        r = self.send(items=json.dumps(rows), item_images=[self._png()])
        self.assertEqual(r.status_code, 200, r.data)
        m = mail.outbox[0]
        h = m.alternatives[0][0]
        self.assertEqual(h.count('src="cid:item0"'), 1)
        self.assertNotIn("cid:item1", h)
        cids = [p["Content-ID"] for p in m.attachments if hasattr(p, "get") and p.get("Content-ID")]
        self.assertIn("<item0>", cids)
        self.assertNotIn("chair.png", m.body)  # not listed as an attachment
        self.assertTrue(SentEmail.objects.latest("created_at").quote["rows"][0]["has_image"])

    def test_the_sent_email_can_be_viewed_and_downloaded_with_a_placeholder_for_pictures(self):
        rows = [dict(ROWS[0], image=0), ROWS[1]]
        self.assertEqual(self.send(items=json.dumps(rows), item_images=[self._png()]).status_code, 200)
        rec = SentEmail.objects.latest("created_at")
        r = self.super.get(f"/api/sent-emails/{rec.pk}/html/")
        self.assertEqual(r.status_code, 200)
        h = r.content.decode()
        for must in ("Price breakdown (NGN)", "Office chair", "₦1,427,065.19", rec.reference, "Picture", "not kept", "data:image/png;base64"):
            self.assertIn(must, h, must)
        self.assertNotIn("cid:", h)
        self.assertEqual(h.count("not kept"), 1)  # only the row that had a picture
        self.assertNotIn("attachment", r.get("Content-Disposition", ""))
        d = self.super.get(f"/api/sent-emails/{rec.pk}/html/?download=1")
        self.assertIn(f'filename="{rec.reference}.html"', d["Content-Disposition"])

    def test_only_admins_can_view_the_sent_email_html(self):
        self.send()
        rec = SentEmail.objects.latest("created_at")
        from rest_framework.test import APIClient
        self.assertIn(APIClient().get(f"/api/sent-emails/{rec.pk}/html/").status_code, (401, 403))

    def test_pictures_are_optional(self):
        r = self.send()
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("cid:item", mail.outbox[0].alternatives[0][0])

    def test_a_missing_or_fake_picture_stops_the_send(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        r = self.send(items=json.dumps([dict(ROWS[0], image=3)]), item_images=[self._png()])
        self.assertEqual(r.status_code, 400)
        fake = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        r = self.send(items=json.dumps([dict(ROWS[0], image=0)]), item_images=[fake])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

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


class Drafts(SendingQuotes):
    """Saving an email without sending it, and sending a saved draft later."""

    def save(self, **extra):
        data = {"save_draft": "1", "kind": "rfq_reply", "subject": "Half done", "items": json.dumps([{"description": "Chair", "quantity": "2", "unit_price": ""}]), **extra}
        return self.super.post("/api/emails/", data, format="multipart")

    def test_an_unfinished_draft_is_saved_without_sending(self):
        r = self.save()
        self.assertEqual(r.status_code, 200, r.data)
        rec = SentEmail.objects.get(pk=r.data["id"])
        self.assertEqual((rec.status, rec.reference, rec.recipient), ("draft", None, ""))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(json.loads(rec.draft["items"])[0]["description"], "Chair")

    def test_saving_again_updates_the_same_draft(self):
        a = self.save().data["id"]
        b = self.save(draft_id=a, subject="Better subject").data["id"]
        self.assertEqual(a, b)
        self.assertEqual(SentEmail.objects.filter(status="draft").count(), 1)
        self.assertEqual(SentEmail.objects.get(pk=a).subject, "Better subject")

    def test_a_draft_does_not_touch_the_order_or_take_a_reference(self):
        self.save(rfq=str(self.rfq.pk))
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, "received")
        self.assertEqual(SentEmail.objects.get().reference, None)

    def test_sending_a_draft_replaces_it(self):
        d = self.save().data["id"]
        r = self.send(draft_id=d)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(SentEmail.objects.filter(pk=d).exists())
        self.assertEqual(SentEmail.objects.filter(status="draft").count(), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_drafts_can_be_deleted_but_sent_mail_cannot(self):
        d = self.save().data["id"]
        self.assertEqual(self.super.post(f"/api/sent-emails/{d}/discard/").status_code, 204)
        self.send()
        sent = SentEmail.objects.exclude(status="draft").get()
        self.assertEqual(self.super.post(f"/api/sent-emails/{sent.pk}/discard/").status_code, 400)
        self.assertTrue(SentEmail.objects.filter(pk=sent.pk).exists())

    def test_a_draft_has_no_sent_email_to_view_and_bad_drafts_are_refused(self):
        d = self.save().data["id"]
        self.assertEqual(self.super.get(f"/api/sent-emails/{d}/html/").status_code, 400)
        self.assertEqual(self.save(items="not json").status_code, 400)
        self.assertEqual(self.save(draft_id="3f1c2a6e-0000-4000-8000-000000000000").status_code, 404)
        self.assertEqual(self.save(currency="XXX").status_code, 400)

    def test_the_draft_comes_back_in_the_record(self):
        d = self.save(rfq=str(self.rfq.pk), currency="NGN", shipping="500").data["id"]
        r = self.super.get(f"/api/sent-emails/{d}/").data
        self.assertEqual((r["status"], r["draft"]["currency"], r["draft"]["shipping"], str(r["rfq"])), ("draft", "NGN", "500", str(self.rfq.pk)))

    def test_only_admins_can_save_drafts(self):
        from rest_framework.test import APIClient
        self.assertIn(APIClient().post("/api/emails/", {"save_draft": "1"}).status_code, (401, 403))
