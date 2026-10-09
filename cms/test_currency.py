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


class YearsOfExperience(AdminBase):
    """The figure goes up by one every 1 January; the owner can still set it."""

    def setUp(self):
        super().setUp()
        self.meta = MetaData.objects.create(metaIntro="a", metaDescription="b", email="x@y.com", phone="1",
                                            office="o", experience=3, experience_year=2026)

    def shown(self, year):
        with mock.patch("cms.models.current_year", return_value=year):
            return self.meta.years_of_experience

    def test_goes_up_by_one_each_january(self):
        self.assertEqual([self.shown(y) for y in (2026, 2027, 2028, 2030)], [3, 4, 5, 7])

    def test_new_rows_start_this_year(self):
        from .models import current_year
        self.assertEqual(MetaData.objects.create(metaIntro="a", metaDescription="b", email="x@y.com", phone="1", office="o").experience_year, current_year())

    def test_the_count_changes_at_midnight_on_1_january_in_lagos(self):
        from datetime import datetime, timezone as tz
        from .models import current_year
        with mock.patch("cms.models.datetime") as dt:
            dt.now.side_effect = lambda zone=None: datetime(2026, 12, 31, 22, 59, tzinfo=tz.utc).astimezone(zone)  # 23:59 in Lagos
            self.assertEqual(current_year(), 2026)
            dt.now.side_effect = lambda zone=None: datetime(2026, 12, 31, 23, 0, tzinfo=tz.utc).astimezone(zone)  # 00:00 1 Jan in Lagos
            self.assertEqual(current_year(), 2027)

    def test_public_data_shows_the_current_figure(self):
        with mock.patch("cms.models.current_year", return_value=2028), mock.patch("cms.views.FetchTwiter") as tw:
            tw.return_value.getTweets.return_value = {}
            m = self.anon.get("/api/alldata/").json()["metadata"][0]
        self.assertEqual(m["experience"], 5)
        self.assertNotIn("experience_year", m)

    def test_the_cms_shows_the_current_figure_too(self):
        with mock.patch("cms.models.current_year", return_value=2027):
            self.assertEqual(self.super.get(f"/api/metadata/{self.meta.pk}/").data["experience"], 4)

    def test_owner_can_set_it_by_hand_and_it_counts_on_from_then(self):
        with mock.patch("cms.models.current_year", return_value=2027):
            r = self.super.patch(f"/api/metadata/{self.meta.pk}/", {"experience": 10}, format="json")
            self.assertEqual(r.status_code, 200, r.data)
            self.assertEqual(r.data["experience"], 10)
        self.meta.refresh_from_db()
        self.assertEqual((self.meta.experience, self.meta.experience_year), (10, 2027))
        self.assertEqual(self.shown(2029), 12)

    def test_saving_other_settings_does_not_move_the_count(self):
        with mock.patch("cms.models.current_year", return_value=2030):
            self.super.patch(f"/api/metadata/{self.meta.pk}/", {"office": "New office"}, format="json")
        self.meta.refresh_from_db()
        self.assertEqual((self.meta.experience, self.meta.experience_year), (3, 2026))  # still counts from 2026

    def test_the_settings_form_save_keeps_the_figure_right(self):
        """The CMS sends back the whole form, including the figure it was shown."""
        with mock.patch("cms.models.current_year", return_value=2028):
            body = self.super.get(f"/api/metadata/{self.meta.pk}/").data
            self.assertEqual(body["experience"], 5)
            self.assertEqual(self.super.put(f"/api/metadata/{self.meta.pk}/", body, format="json").status_code, 200)
            self.assertEqual(self.super.get(f"/api/metadata/{self.meta.pk}/").data["experience"], 5)
        self.assertEqual(self.shown(2029), 6)

    def test_never_negative(self):
        self.assertEqual(self.shown(2020), 0)  # a clock set in the past never shows less than zero
