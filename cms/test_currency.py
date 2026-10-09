"""The currency catalog prices are shown in is set in the CMS Settings."""
from unittest import mock

from .models import MetaData
from .test_admins import AdminBase


class PriceCurrency(AdminBase):
    def setUp(self):
        super().setUp()
        self.meta = MetaData.objects.create(metaIntro="a", metaDescription="b", email="x@y.com", phone="1", office="o")

    def test_defaults_to_dollars(self):
        self.assertEqual(self.meta.currency, "USD")

    def test_public_site_data_includes_it(self):
        with mock.patch("cms.views.FetchTwiter") as tw:
            tw.return_value.getTweets.return_value = {}
            r = self.anon.get("/api/alldata/")
        self.assertEqual(r.json()["metadata"][0]["currency"], "USD")

    def test_an_admin_can_change_it_and_the_public_sees_it(self):
        body = {"metaIntro": "a", "metaDescription": "b", "email": "x@y.com", "phone": "1", "office": "o",
                "ordersCompleted": 1, "suppliers": 1, "experience": 1, "currency": "NGN"}
        r = self.regular_client.put(f"/api/metadata/{self.meta.pk}/", body, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        with mock.patch("cms.views.FetchTwiter") as tw:
            tw.return_value.getTweets.return_value = {}
            self.assertEqual(self.anon.get("/api/alldata/").json()["metadata"][0]["currency"], "NGN")

    def test_only_listed_currencies_are_accepted(self):
        r = self.super.patch(f"/api/metadata/{self.meta.pk}/", {"currency": "XYZ"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.meta.refresh_from_db(); self.assertEqual(self.meta.currency, "USD")

    def test_the_public_cannot_change_it(self):
        self.assertEqual(self.anon.patch(f"/api/metadata/{self.meta.pk}/", {"currency": "GBP"}, format="json").status_code, 401)

    def test_it_is_in_the_activity_log(self):
        from .models import AuditLog
        self.super.patch(f"/api/metadata/{self.meta.pk}/", {"currency": "EUR"}, format="json")
        e = AuditLog.objects.get(action="updated", target_type="site settings")
        self.assertIn("currency", e.detail)
