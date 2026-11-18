"""Two-step verification is compulsory for staff: without it a sign-in only reaches the setup steps."""
import pyotp
from django.test import override_settings

from .authentication import make_token
from .models import MFADevice, User
from .test_admins import AdminBase, NEW_PASSWORD
from .test_mfa import NOW


@override_settings(CMS_REQUIRE_MFA=True)
class NoTwoStepNoAccess(AdminBase):
    def test_a_staff_member_without_it_can_only_reach_setup(self):
        c = self.regular_client
        self.assertEqual(c.get("/api/auth/me/").status_code, 200)                 # who am I: needed to show the setup screen
        self.assertEqual(c.get("/api/auth/me/").data["mfa_enabled"], False)
        self.assertEqual(c.post("/api/auth/mfa/setup/").status_code, 200)          # the setup itself
        for url in ("/api/gettotal", "/api/attention/", "/api/rfqs/", "/api/sent-emails/", "/api/inbox/", "/api/contacts/?q=a", "/api/designations/"):
            r = c.get(url)
            self.assertEqual(r.status_code, 403, url)
            self.assertEqual(r.data["code"], "mfa_setup_required", url)
        self.assertEqual(c.post("/api/emails/", {"kind": "outreach", "recipient": "a@x.com", "subject": "s", "title": "t", "body": "b"}, format="multipart").status_code, 403)

    def test_the_super_admin_is_held_to_the_same_rule(self):
        r = self.super.get("/api/admins/")
        self.assertEqual((r.status_code, r.data["code"]), (403, "mfa_setup_required"))

    def test_writes_are_refused_too(self):
        self.assertEqual(self.regular_client.post("/api/services/", {"title": "t", "description": "d"}, format="json").status_code, 403)

    def test_the_public_site_is_not_affected(self):
        self.assertEqual(self.anon.get("/api/alldata/").status_code, 200)
        self.assertEqual(self.regular_client.get("/api/alldata/").status_code, 200)

    def test_setting_it_up_opens_everything_at_once(self):
        import unittest.mock as mock
        with mock.patch("cms.mfa.time.time", return_value=NOW):   # a fixed moment, so the code is predictable
            c = self.client_for(make_token(self.regular))
            secret = c.post("/api/auth/mfa/setup/").data["secret"]
            self.assertEqual(c.get("/api/gettotal").status_code, 403)
            r = c.post("/api/auth/mfa/confirm/", {"code": pyotp.TOTP(secret).at(NOW)}, format="json")
            self.assertEqual(r.status_code, 200, r.data)
            self.assertEqual(c.get("/api/gettotal").status_code, 401)   # the old token stops working once two-step is on...
            fresh = self.client_for(r.data["token"])                    # ...and the answer carries a new one
            self.assertEqual(fresh.get("/api/gettotal").status_code, 200)

    def test_someone_who_already_has_it_is_unaffected(self):
        MFADevice.objects.create(user=self.regular, secret=pyotp.random_base32(), confirmed=True)
        self.assertEqual(self.client_for(make_token(User.objects.get(pk=self.regular.pk))).get("/api/gettotal").status_code, 200)

    def test_a_half_finished_setup_does_not_count(self):
        MFADevice.objects.create(user=self.regular, secret=pyotp.random_base32(), confirmed=False)
        self.assertEqual(self.regular_client.get("/api/gettotal").status_code, 403)

    def test_a_new_staff_member_is_held_until_they_set_it_up(self):
        r = self.add_admin(email="fresh@x.com")
        self.assertEqual(r.status_code, 403)   # the super admin has none either in this test; add with one on
        MFADevice.objects.create(user=self.admin, secret=pyotp.random_base32(), confirmed=True)
        self.super = self.client_for(make_token(User.objects.get(pk=self.admin.pk)))
        self.assertEqual(self.add_admin(email="fresh@x.com").status_code, 201)
        fresh = self.client_for(make_token(User.objects.get(email="fresh@x.com")))
        self.assertEqual(fresh.get("/api/gettotal").status_code, 403)

    def test_customers_are_not_staff_and_not_affected(self):
        self.assertEqual(self.client_for(make_token(self.customer)).get("/api/customer/me/").status_code, 200)


class EmergencySwitch(AdminBase):
    @override_settings(CMS_REQUIRE_MFA=False)
    def test_the_rule_can_be_turned_off_in_an_emergency(self):
        self.assertEqual(self.regular_client.get("/api/gettotal").status_code, 200)


class TrustedComputer(AdminBase):
    """Signing in again on the same computer within 3 hours of a full sign-in does not ask for the code."""

    def setUp(self):
        super().setUp()
        import unittest.mock as mock
        patcher = mock.patch("cms.mfa.time.time", side_effect=lambda: self.clock)   # one fixed clock for codes and tokens alike
        self.clock = NOW
        patcher.start()
        self.addCleanup(patcher.stop)
        self.secret = pyotp.random_base32()
        MFADevice.objects.create(user=self.regular, secret=self.secret, confirmed=True)
        self.creds = {"email": "regular@x.com", "password": "Regul4r-pass!!"}

    def full_sign_in(self):
        step = self.anon.post("/api/auth/login/", self.creds, format="json")
        self.assertTrue(step.data["mfa_required"])
        return self.anon.post("/api/auth/login/mfa/", {"mfa_token": step.data["mfa_token"], "code": pyotp.TOTP(self.secret).at(NOW)}, format="json")

    def test_a_full_sign_in_hands_out_a_trust_token(self):
        ok = self.full_sign_in()
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertTrue(ok.data["device_token"])

    def test_with_the_token_the_code_is_not_asked(self):
        token = self.full_sign_in().data["device_token"]
        again = self.anon.post("/api/auth/login/", {**self.creds, "device_token": token}, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.data.get("token") and again.data["trusted_computer"])
        self.assertNotIn("mfa_required", again.data)

    def test_without_the_token_or_with_a_wrong_one_it_is_asked(self):
        token = self.full_sign_in().data["device_token"]
        for t in (None, "", "garbage", token[:-3] + "xxx"):
            r = self.anon.post("/api/auth/login/", {**self.creds, **({"device_token": t} if t is not None else {})}, format="json")
            self.assertTrue(r.data.get("mfa_required"), repr(t))

    def test_the_password_is_always_needed(self):
        token = self.full_sign_in().data["device_token"]
        r = self.anon.post("/api/auth/login/", {**self.creds, "password": "wrong-password", "device_token": token}, format="json")
        self.assertEqual(r.status_code, 401)

    def test_after_three_hours_it_is_asked_again(self):
        token = self.full_sign_in().data["device_token"]
        self.clock = NOW + 3 * 3600 - 60
        self.assertEqual(self.anon.post("/api/auth/login/", {**self.creds, "device_token": token}, format="json").data.get("trusted_computer"), True)   # just inside
        self.clock = NOW + 3 * 3600 + 60
        r = self.anon.post("/api/auth/login/", {**self.creds, "device_token": token}, format="json")
        self.assertTrue(r.data.get("mfa_required"))

    def test_a_password_change_ends_the_trust(self):
        token = self.full_sign_in().data["device_token"]
        self.regular.set_password("Brand-new-pass-77!"); self.regular.save()
        r = self.anon.post("/api/auth/login/", {**self.creds, "password": "Brand-new-pass-77!", "device_token": token}, format="json")
        self.assertTrue(r.data.get("mfa_required"))

    def test_a_token_belongs_to_one_account(self):
        token = self.full_sign_in().data["device_token"]
        other_secret = pyotp.random_base32()
        MFADevice.objects.create(user=self.admin, secret=other_secret, confirmed=True)
        r = self.anon.post("/api/auth/login/", {"email": "info@ghprocurement.com", "password": "x", "device_token": token}, format="json")
        self.assertEqual(r.status_code, 401)   # wrong password for that account anyway
        self.assertNotIn("token", r.data)
