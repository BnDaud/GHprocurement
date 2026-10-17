"""The showcase: photos of delivered orders and customer testimonials for the home page."""
import io
from unittest import mock

from cloudinary import CloudinaryResource
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient, APITestCase

from .models import Showcase, User


def photo(size=(3000, 2000)):
    buf = io.BytesIO()
    Image.effect_noise(size, 80).convert("RGB").save(buf, "JPEG", quality=95)
    return SimpleUploadedFile("shipment.jpg", buf.getvalue(), content_type="image/jpeg")


class ShowcaseTests(APITestCase):
    def setUp(self):
        admin = User.objects.create_user(username="boss", email="boss@x.com", password="x", is_staff=True, is_superuser=True)
        from .authentication import make_token
        self.admin = APIClient()
        self.admin.credentials(HTTP_AUTHORIZATION=f"Bearer {make_token(admin)}")
        self.anon = APIClient()
        stub = mock.patch("cloudinary.uploader.upload_resource",
                          return_value=CloudinaryResource("Showcase/abc", version="1", format="jpg", type="upload", resource_type="image"))
        self.upload = stub.start()
        self.addCleanup(stub.stop)

    def test_the_team_adds_a_testimonial_and_a_delivered_photo(self):
        t = self.admin.post("/api/showcase/", {"kind": "testimonial", "quote": "They delivered on time.", "person": "Ada Obi", "company": "Acme"}, format="json")
        self.assertEqual(t.status_code, 201, t.data)
        d = self.admin.post("/api/showcase/", {"kind": "delivery", "title": "500 power banks, Lagos", "image": photo()}, format="multipart")
        self.assertEqual(d.status_code, 201, d.data)
        self.assertEqual(Showcase.objects.count(), 2)

    def test_the_photo_is_shrunk_before_it_is_stored(self):
        self.admin.post("/api/showcase/", {"kind": "delivery", "image": photo()}, format="multipart")
        stored = self.upload.call_args.args[0]
        stored.seek(0)
        img = Image.open(io.BytesIO(stored.read()))
        self.assertLessEqual(max(img.size), 1200)
        self.assertEqual(img.format, "JPEG")

    def test_each_kind_needs_its_main_content(self):
        self.assertEqual(self.admin.post("/api/showcase/", {"kind": "testimonial", "quote": "  "}, format="json").status_code, 400)
        self.assertEqual(self.admin.post("/api/showcase/", {"kind": "delivery", "title": "No photo"}, format="json").status_code, 400)
        bad = SimpleUploadedFile("x.jpg", b"not a picture", content_type="image/jpeg")
        self.assertEqual(self.admin.post("/api/showcase/", {"kind": "delivery", "image": bad}, format="multipart").status_code, 400)
        self.assertEqual(Showcase.objects.count(), 0)

    def test_visitors_see_only_what_is_published(self):
        Showcase.objects.create(kind="testimonial", quote="Public one", person="A", published=True)
        Showcase.objects.create(kind="testimonial", quote="Hidden draft", person="B", published=False)
        listed = [x["quote"] for x in self.anon.get("/api/showcase/").json()]
        self.assertEqual(listed, ["Public one"])
        feed = self.anon.get("/api/alldata/?format=json").json()["showcase"]
        self.assertEqual([x["quote"] for x in feed], ["Public one"])
        self.assertEqual(len(self.admin.get("/api/showcase/").json()), 2)  # the team sees both

    def test_only_admins_can_change_it(self):
        entry = Showcase.objects.create(kind="testimonial", quote="Hello", person="A")
        self.assertIn(self.anon.post("/api/showcase/", {"kind": "testimonial", "quote": "x"}, format="json").status_code, (401, 403))
        self.assertIn(self.anon.delete(f"/api/showcase/{entry.pk}/").status_code, (401, 403))
        self.assertEqual(self.admin.patch(f"/api/showcase/{entry.pk}/", {"published": False}, format="json").status_code, 200)
        self.assertEqual(self.admin.delete(f"/api/showcase/{entry.pk}/").status_code, 204)
