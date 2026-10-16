"""Quote requests as a list of items with optional pictures: compressed, stored on Cloudinary,
copied into the quotation, and deleted when the order is delivered."""
import io
import json
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from . import images
from .models import RFQ, RFQItem
from .test_quote import ROWS
from .test_tracking import FORM, TrackBase


def photo(name="chair.jpg", size=(3000, 2000), fmt="JPEG"):
    buf = io.BytesIO()
    Image.effect_noise(size, 80).convert("RGB").save(buf, fmt, quality=95)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


class Stub:
    """Cloudinary stand-in: remembers what was stored and deleted."""

    def __init__(self):
        self.stored, self.deleted = [], []

    def store(self, data):
        self.stored.append(data)
        return f"RFQ_Items/pic{len(self.stored)}", "111"

    def delete(self, image_id):
        self.deleted.append(image_id)
        return True


class Items(TrackBase):
    def setUp(self):
        super().setUp()
        self.cl = Stub()
        for name, fn in (("store", self.cl.store), ("delete", self.cl.delete)):
            p = mock.patch(f"cms.images.{name}", fn)
            p.start()
            self.addCleanup(p.stop)

    def post(self, rows, files=(), **extra):
        data = {k: v for k, v in FORM.items() if k != "item"}
        data.update({"items_json": json.dumps(rows), "consent": "true", **extra})
        if files:
            data["item_images"] = list(files)
        mail.outbox.clear()
        return self.anon.post("/api/rfqs/", data, format="multipart")

    def test_a_list_of_items_with_pictures_is_saved(self):
        r = self.post([{"description": "Office chair", "quantity": "20", "image": 0}, {"description": "Desk", "quantity": "5"}], [photo()])
        self.assertEqual(r.status_code, 200, r.data)
        rfq = RFQ.objects.get()
        a, b = rfq.items.all()
        self.assertEqual((a.description, f"{a.quantity.normalize():f}", a.image_id), ("Office chair", "20", "RFQ_Items/pic1"))
        self.assertEqual(b.image_id, "")
        self.assertIn("20 x Office chair", rfq.item)
        self.assertIn("5 x Desk", rfq.item)

    def test_pictures_are_compressed_to_kilobytes(self):
        big = photo()
        self.post([{"description": "Chair", "quantity": "1", "image": 0}], [big])
        stored = self.cl.stored[0]
        self.assertGreater(big.size, 1_000_000)
        self.assertLess(len(stored), 400_000)
        self.assertLessEqual(max(Image.open(io.BytesIO(stored)).size), images.MAX_SIDE)

    def test_a_note_can_go_with_the_list(self):
        self.post([{"description": "Chair", "quantity": "2"}], item="Black please")
        self.assertIn("Black please", RFQ.objects.get().item)

    def test_bad_lists_and_pictures_are_refused(self):
        bad = SimpleUploadedFile("x.jpg", b"not a picture", content_type="image/jpeg")
        cases = [([{"description": "", "quantity": "1"}], []), ([{"description": "x", "quantity": "0"}], []),
                 ([{"description": "x", "quantity": "1", "image": 3}], []), ([], []),
                 ([{"description": str(i), "quantity": "1"} for i in range(11)], []),
                 ([{"description": "x", "quantity": "1", "image": 0}], [bad])]
        for rows, files in cases:
            self.assertEqual(self.post(rows, files).status_code, 400, rows)
        self.assertEqual(RFQ.objects.count(), 0)
        self.assertEqual(self.cl.stored, [])

    def test_the_old_single_text_request_still_works(self):
        self.assertEqual(self.submit().status_code, 200)
        self.assertEqual(RFQ.objects.get().items.count(), 0)

    def test_admin_sees_the_items_with_picture_links(self):
        self.post([{"description": "Chair", "quantity": "2", "image": 0}], [photo()])
        r = self.super.get(f"/api/rfqs/{RFQ.objects.get().pk}/")
        it = r.data["items"][0]
        self.assertEqual((it["description"], it["quantity"], it["has_picture"]), ("Chair", "2", True))

    def test_delivery_deletes_the_pictures(self):
        self.post([{"description": "Chair", "quantity": "2", "image": 0}], [photo()])
        rfq = RFQ.objects.get()
        url = f"/api/rfqs/{rfq.pk}/updates/"
        self.assertEqual(self.super.post(url, {"stage": "shipped", "headline": "On its way"}, format="json").status_code, 201)
        self.assertEqual(self.cl.deleted, [])
        self.super.post(url, {"stage": "delivered", "headline": "Delivered"}, format="json")
        self.assertEqual(self.cl.deleted, ["RFQ_Items/pic1"])
        rfq.refresh_from_db()
        self.assertIsNotNone(rfq.pictures_removed_at)
        self.assertEqual(rfq.items.get().image_id, "")
        self.assertEqual(rfq.items.get().description, "Chair")  # the text stays

    def test_pictures_can_be_removed_by_hand(self):
        self.post([{"description": "Chair", "quantity": "2", "image": 0}], [photo()])
        rfq = RFQ.objects.get()
        r = self.super.post(f"/api/rfqs/{rfq.pk}/remove-pictures/")
        self.assertEqual((r.status_code, r.data["removed"], r.data["remaining"]), (200, 1, False))
        from rest_framework.test import APIClient
        self.assertIn(APIClient().post(f"/api/rfqs/{rfq.pk}/remove-pictures/").status_code, (401, 403))

    def test_a_failed_delete_is_kept_for_next_time(self):
        self.post([{"description": "Chair", "quantity": "2", "image": 0}], [photo()])
        rfq = RFQ.objects.get()
        with mock.patch("cms.images.delete", return_value=False):
            self.assertEqual(images.purge_rfq(rfq), 0)
        self.assertEqual(rfq.items.get().image_id, "RFQ_Items/pic1")
        self.assertEqual(images.purge_rfq(rfq), 1)


class QuotingFromAnOrder(Items):
    def test_order_pictures_are_copied_into_the_quotation(self):
        self.post([{"description": "Chair", "quantity": "2", "image": 0}], [photo()])
        rfq = RFQ.objects.get()
        item = rfq.items.get()
        tiny = io.BytesIO()
        Image.new("RGB", (240, 160), (200, 30, 30)).save(tiny, "JPEG")

        class Inline:
            def __init__(self, target, args=()):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        rows = [{"description": "Chair", "quantity": "2", "unit_price": "45000", "rfq_item": str(item.pk)}]
        data = {"kind": "rfq_reply", "recipient": "ada@acme.com", "subject": "Quote", "title": "Quote", "body": "Prices below.",
                "items": json.dumps(rows), "currency": "NGN", "rfq": str(rfq.pk)}
        mail.outbox.clear()
        with mock.patch("cms.images.fetch_small", return_value=tiny.getvalue()), \
                mock.patch("cms.views.threading.Thread", Inline), mock.patch("cms.task._record_outcome"):
            r = self.super.post("/api/emails/", data, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        html = mail.outbox[0].alternatives[0][0]
        self.assertEqual(html.count('src="cid:item0"'), 1)

    def test_another_orders_picture_is_never_used(self):
        self.post([{"description": "Chair", "quantity": "2", "image": 0}], [photo()])
        item = RFQItem.objects.get()
        self.submit(email="bob@other.com", name="Bob", item="x")
        other = RFQ.objects.exclude(pk=item.rfq_id).get()
        rows = [{"description": "Chair", "quantity": "2", "unit_price": "1", "rfq_item": str(item.pk)}]
        data = {"kind": "rfq_reply", "recipient": "bob@other.com", "subject": "Q", "title": "Q", "body": "b",
                "items": json.dumps(rows), "rfq": str(other.pk)}
        with mock.patch("cms.images.fetch_small", return_value=b"x") as f:
            self.super.post("/api/emails/", data, format="multipart")
        f.assert_not_called()
