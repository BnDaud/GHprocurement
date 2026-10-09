"""Administrators: only a super admin can add or remove them; the Users list
shows ordinary accounts only; a super admin can never be deleted."""
from unittest import mock

import pyotp
from django.test import override_settings
from rest_framework.test import APIClient

from .authentication import make_token
from .models import MFADevice, User
from .tests import ADMIN_EMAIL, ADMIN_PASSWORD, AuthTestBase, mk

NEW_PASSWORD = "Fresh-adm1n-pass!"
NOW = 1_800_000_000


class AdminBase(AuthTestBase):
    def setUp(self):
        super().setUp()
        # the owner account is a super admin (what `createsuperuser` makes)
        User.objects.filter(pk=self.admin.pk).update(is_superuser=True)
        self.super = APIClient()
        self.super.credentials(HTTP_AUTHORIZATION=f"Bearer {self.login().data['token']}")
        # a regular admin, who can use the CMS but is not a super admin
        self.regular = mk("regular", "regular@x.com", "Regul4r-pass!!", is_staff=True)
        self.regular_client = self.client_for(make_token(self.regular))

    def add_admin(self, client=None, **extra):
        data = {"email": "new.admin@x.com", "password": NEW_PASSWORD, "first_name": "Nia"}
        data.update(extra)
        return (client or self.super).post("/api/admins/", data, format="json")


class UsersPageShowsOnlyAccounts(AdminBase):
    def test_admins_are_not_listed_among_users(self):
        r = self.super.get("/api/user/")
        self.assertEqual(r.status_code, 200)
        emails = {u["email"] for u in r.data}
        self.assertEqual(emails, {"buyer@acme.com"})  # only the ordinary account
        self.assertNotIn(ADMIN_EMAIL, emails)
        self.assertNotIn("regular@x.com", emails)

    def test_a_new_admin_never_appears_there(self):
        self.add_admin()
        self.assertNotIn("new.admin@x.com", {u["email"] for u in self.super.get("/api/user/").data})

    def test_admins_cannot_be_read_changed_or_deleted_through_the_users_endpoint(self):
        for target in (self.admin, self.regular):
            self.assertEqual(self.super.get(f"/api/user/{target.pk}/").status_code, 404)
            self.assertEqual(self.super.patch(f"/api/user/{target.pk}/", {"first_name": "x"}, format="json").status_code, 404)
            self.assertEqual(self.super.delete(f"/api/user/{target.pk}/").status_code, 404)
            self.assertTrue(User.objects.filter(pk=target.pk).exists())

    def test_ordinary_accounts_still_work_there(self):
        r = self.super.post("/api/user/", {"username": "lead", "email": "lead@x.com", "password": "Some-pass-99", "phone": "1", "first_name": "L", "last_name": "D"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.super.delete(f"/api/user/{r.data['id']}/").status_code, 204)

    def test_an_account_made_there_can_never_be_an_admin(self):
        # extra fields in the request are ignored: no way to slip in is_staff
        r = self.super.post("/api/user/", {"username": "sneaky", "email": "s@x.com", "password": "Some-pass-99", "phone": "1",
                                          "first_name": "S", "last_name": "S", "is_staff": True, "is_superuser": True}, format="json")
        self.assertEqual(r.status_code, 201)
        u = User.objects.get(username="sneaky")
        self.assertEqual((u.is_staff, u.is_superuser), (False, False))
        self.assertEqual(self.login("s@x.com", "Some-pass-99").status_code, 401)

    def test_dashboard_user_total_counts_accounts_only(self):
        self.assertEqual(self.super.get("/api/gettotal").data["TotalUsers"], 1)


class WhoMayManageAdmins(AdminBase):
    def test_outsiders_and_customers_are_refused(self):
        self.assertEqual(self.anon.get("/api/admins/").status_code, 401)
        self.assertEqual(self.add_admin(self.anon).status_code, 401)
        self.assertEqual(self.client_for(make_token(self.customer)).get("/api/admins/").status_code, 403)

    def test_a_regular_admin_cannot_list_add_or_delete_admins(self):
        self.assertEqual(self.regular_client.get("/api/admins/").status_code, 403)
        self.assertEqual(self.add_admin(self.regular_client).status_code, 403)
        self.assertEqual(self.regular_client.delete(f"/api/admins/{self.regular.pk}/").status_code, 403)
        self.assertFalse(User.objects.filter(email="new.admin@x.com").exists())

    def test_the_super_admin_sees_everyone_with_roles_and_no_passwords(self):
        r = self.super.get("/api/admins/")
        self.assertEqual(r.status_code, 200)
        by = {a["email"]: a for a in r.data}
        self.assertEqual(set(by), {ADMIN_EMAIL, "regular@x.com"})
        self.assertEqual(by[ADMIN_EMAIL]["role"], "super_admin")
        self.assertEqual(by["regular@x.com"]["role"], "admin")
        self.assertEqual(r.data[0]["role"], "super_admin")  # super admins first
        self.assertNotIn("password", str(r.data))
        self.assertNotIn("buyer@acme.com", by)  # customers are not administrators

    def test_me_tells_the_cms_who_is_a_super_admin(self):
        self.assertTrue(self.super.get("/api/auth/me/").data["is_super_admin"])
        self.assertFalse(self.regular_client.get("/api/auth/me/").data["is_super_admin"])


class OnlyTheListedAccountIsSuperAdmin(AdminBase):
    """On the live site a second account (mac) is also a Django superuser. Only
    the account named in CMS_SUPER_ADMIN_EMAILS holds the super admin powers."""

    def setUp(self):
        super().setUp()
        self.mac = mk("mac", "mac@x.com", "M4c-passw0rd!!", is_staff=True, is_superuser=True)
        self.mac_client = self.client_for(make_token(self.mac))

    def test_a_superuser_who_is_not_listed_is_just_an_admin_in_the_cms(self):
        self.assertFalse(self.mac_client.get("/api/auth/me/").data["is_super_admin"])
        self.assertEqual(self.mac_client.get("/api/admins/").status_code, 403)
        self.assertEqual(self.add_admin(self.mac_client).status_code, 403)
        self.assertEqual(self.mac_client.get("/api/gettotal").status_code, 200)  # but can use the CMS

    def test_they_are_shown_as_an_admin_not_a_super_admin(self):
        by = {a["email"]: a["role"] for a in self.super.get("/api/admins/").data}
        self.assertEqual((by[ADMIN_EMAIL], by["mac@x.com"]), ("super_admin", "admin"))

    def test_server_level_accounts_are_flagged_protected_so_the_cms_hides_delete(self):
        by = {a["email"]: a["protected"] for a in self.super.get("/api/admins/").data}
        self.assertEqual((by[ADMIN_EMAIL], by["mac@x.com"], by["regular@x.com"]), (True, True, False))

    def test_the_unlisted_superuser_cannot_delete_the_listed_super_admin(self):
        self.assertEqual(self.mac_client.delete(f"/api/admins/{self.admin.pk}/").status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_the_email_match_ignores_case(self):
        User.objects.filter(pk=self.admin.pk).update(email="Info@GhProcurement.COM")
        c = self.client_for(make_token(User.objects.get(pk=self.admin.pk)))
        self.assertEqual(c.get("/api/admins/").status_code, 200)

    @override_settings(CMS_SUPER_ADMIN_EMAILS=[])
    def test_with_no_super_admin_configured_nobody_can_manage_admins(self):
        self.assertEqual(self.super.get("/api/admins/").status_code, 403)

    def test_being_listed_is_not_enough_without_being_a_superuser(self):
        User.objects.filter(pk=self.admin.pk).update(is_superuser=False)
        self.assertEqual(self.super.get("/api/admins/").status_code, 403)


class AddingAnAdmin(AdminBase):
    def test_creates_a_regular_admin_who_can_sign_in(self):
        r = self.add_admin()
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.data["email"], r.data["role"], r.data["name"]), ("new.admin@x.com", "admin", "Nia"))
        self.assertNotIn("password", r.data)
        u = User.objects.get(email="new.admin@x.com")
        self.assertEqual((u.is_staff, u.is_superuser, u.is_active), (True, False, True))
        self.assertNotEqual(u.password, NEW_PASSWORD)  # stored hashed
        login = self.login("new.admin@x.com", NEW_PASSWORD)
        self.assertEqual(login.status_code, 200)
        self.assertIn("token", login.data)  # 2FA is optional: not on by default

    def test_the_new_admin_can_use_the_cms_but_not_manage_admins(self):
        self.add_admin()
        c = self.client_for(self.login("new.admin@x.com", NEW_PASSWORD).data["token"])
        self.assertEqual(c.get("/api/gettotal").status_code, 200)
        self.assertEqual(c.post("/api/services/", {"title": "t", "description": "d"}, format="json").status_code, 201)
        self.assertEqual(c.get("/api/admins/").status_code, 403)
        self.assertEqual(self.add_admin(c, email="third@x.com").status_code, 403)
        self.assertEqual(c.delete(f"/api/admins/{self.admin.pk}/").status_code, 403)
        self.assertFalse(c.get("/api/auth/me/").data["is_super_admin"])

    def test_the_new_admin_can_change_their_own_password(self):
        self.add_admin()
        c = self.client_for(self.login("new.admin@x.com", NEW_PASSWORD).data["token"])
        r = c.post("/api/auth/change-password/", {"current_password": NEW_PASSWORD, "new_password": "Another-good-pass-7"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.login("new.admin@x.com", "Another-good-pass-7").status_code, 200)

    def test_weak_passwords_are_refused_with_the_reason(self):
        for weak in ("123", "password", "12345678", "short1"):
            r = self.add_admin(password=weak, email=f"w{weak}@x.com")
            self.assertEqual(r.status_code, 400, weak)
            self.assertTrue(r.data["detail"])
        self.assertFalse(User.objects.filter(email__startswith="w").exists())

    def test_a_password_like_the_email_is_refused(self):
        self.assertEqual(self.add_admin(email="nia.okafor@x.com", password="nia.okafor@x.com").status_code, 400)

    def test_missing_or_bad_fields(self):
        for bad in ({"email": ""}, {"email": "not-an-email"}, {"password": ""}):
            self.assertEqual(self.add_admin(**bad).status_code, 400, bad)
        r = self.super.post("/api/admins/", {"email": "x@x.com"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_an_email_already_in_use_is_refused_any_case(self):
        self.assertEqual(self.add_admin().status_code, 201)
        self.assertEqual(self.add_admin(email="NEW.ADMIN@x.com").status_code, 400)
        self.assertEqual(self.add_admin(email=ADMIN_EMAIL).status_code, 400)
        self.assertEqual(self.add_admin(email="buyer@acme.com").status_code, 400)  # an existing customer account
        self.assertEqual(User.objects.filter(email__iexact="new.admin@x.com").count(), 1)

    def test_usernames_never_collide(self):
        mk("nia", "taken@x.com", "Taken-pass-11")
        r = self.add_admin(email="nia@y.com")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["username"], "nia2")

    def test_privileges_cannot_be_requested(self):
        r = self.add_admin(is_superuser=True, is_staff=False)
        self.assertEqual(r.status_code, 201)
        u = User.objects.get(email="new.admin@x.com")
        self.assertEqual((u.is_staff, u.is_superuser), (True, False))

    def test_the_list_grows(self):
        self.add_admin()
        self.assertEqual(len(self.super.get("/api/admins/").data), 3)


class RemovingAnAdmin(AdminBase):
    def test_the_super_admin_can_delete_a_regular_admin(self):
        new_id = self.add_admin().data["id"]
        token = self.login("new.admin@x.com", NEW_PASSWORD).data["token"]
        self.assertEqual(self.super.delete(f"/api/admins/{new_id}/").status_code, 204)
        self.assertFalse(User.objects.filter(pk=new_id).exists())
        self.assertEqual(self.client_for(token).get("/api/gettotal").status_code, 401)  # their session ends at once
        self.assertEqual(self.login("new.admin@x.com", NEW_PASSWORD).status_code, 401)

    def test_a_super_admin_cannot_be_deleted_by_anyone(self):
        r = self.super.delete(f"/api/admins/{self.admin.pk}/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("super admin cannot be deleted", r.data["detail"])
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_a_server_level_superuser_cannot_be_deleted_from_the_cms_either(self):
        other = mk("owner", "owner@x.com", "0wner-passw0rd!", is_staff=True, is_superuser=True)
        r = self.super.delete(f"/api/admins/{other.pk}/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("full server access", r.data["detail"])
        self.assertTrue(User.objects.filter(pk=other.pk).exists())

    def test_even_a_super_admin_cannot_delete_themselves(self):
        self.assertEqual(self.super.delete(f"/api/admins/{self.admin.pk}/").status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    @override_settings(CMS_SUPER_ADMIN_EMAILS=["info@ghprocurement.com", "boss2@x.com"])
    def test_a_second_super_admin_cannot_delete_the_first_either(self):
        other = mk("boss2", "boss2@x.com", "B0ss2-passw0rd!", is_staff=True, is_superuser=True)
        c = self.client_for(make_token(other))
        self.assertEqual(c.get("/api/admins/").status_code, 200)  # a real super admin
        self.assertEqual(c.delete(f"/api/admins/{self.admin.pk}/").status_code, 403)
        self.assertEqual(c.delete(f"/api/admins/{other.pk}/").status_code, 403)  # nor themselves
        self.assertEqual(User.objects.filter(pk__in=[self.admin.pk, other.pk]).count(), 2)

    def test_a_regular_admin_cannot_delete_anyone(self):
        new_id = self.add_admin().data["id"]
        self.assertEqual(self.regular_client.delete(f"/api/admins/{new_id}/").status_code, 403)
        self.assertEqual(self.regular_client.delete(f"/api/admins/{self.admin.pk}/").status_code, 403)
        self.assertEqual(User.objects.filter(pk__in=[new_id, self.admin.pk]).count(), 2)

    def test_only_administrators_can_be_deleted_here_not_customers(self):
        self.assertEqual(self.super.delete(f"/api/admins/{self.customer.pk}/").status_code, 404)
        self.assertTrue(User.objects.filter(pk=self.customer.pk).exists())

    def test_unknown_and_malformed_ids(self):
        self.assertEqual(self.super.delete("/api/admins/00000000-0000-0000-0000-000000000000/").status_code, 404)
        self.assertEqual(self.super.delete("/api/admins/not-a-uuid/").status_code, 404)

    def test_deleting_an_admin_removes_their_two_step_setup_too(self):
        new_id = self.add_admin().data["id"]
        MFADevice.objects.create(user_id=new_id, secret=pyotp.random_base32(), confirmed=True)
        self.super.delete(f"/api/admins/{new_id}/")
        self.assertFalse(MFADevice.objects.filter(user_id=new_id).exists())


class NewAdminsCanUseTwoStep(AdminBase):
    """Two-step verification is per account, so a new admin can turn it on."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch("cms.mfa.time.time", side_effect=lambda: self.clock)
        self.clock = NOW
        patcher.start()
        self.addCleanup(patcher.stop)
        # sessions minted before the clock was frozen would look expired: sign in again
        self.super.credentials(HTTP_AUTHORIZATION=f"Bearer {self.login().data['token']}")
        self.add_admin()

    def test_set_up_and_sign_in_with_a_code(self):
        c = self.client_for(self.login("new.admin@x.com", NEW_PASSWORD).data["token"])
        secret = c.post("/api/auth/mfa/setup/").data["secret"]
        r = c.post("/api/auth/mfa/confirm/", {"code": pyotp.TOTP(secret).at(NOW)}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["recovery_codes"]), 10)
        self.clock = NOW + 30
        step1 = self.login("new.admin@x.com", NEW_PASSWORD)
        self.assertTrue(step1.data["mfa_required"])
        done = self.anon.post("/api/auth/login/mfa/", {"mfa_token": step1.data["mfa_token"], "code": pyotp.TOTP(secret).at(NOW + 30)}, format="json")
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.data["user"]["email"], "new.admin@x.com")

    def test_it_does_not_affect_anyone_elses_sign_in(self):
        c = self.client_for(self.login("new.admin@x.com", NEW_PASSWORD).data["token"])
        secret = c.post("/api/auth/mfa/setup/").data["secret"]
        c.post("/api/auth/mfa/confirm/", {"code": pyotp.TOTP(secret).at(NOW)}, format="json")
        self.assertIn("token", self.login().data)  # the super admin still signs in with just the password
        self.assertIn("token", self.login("regular@x.com", "Regul4r-pass!!").data)

    def test_the_super_admin_list_shows_who_has_it_on(self):
        c = self.client_for(self.login("new.admin@x.com", NEW_PASSWORD).data["token"])
        secret = c.post("/api/auth/mfa/setup/").data["secret"]
        c.post("/api/auth/mfa/confirm/", {"code": pyotp.TOTP(secret).at(NOW)}, format="json")
        by = {a["email"]: a["mfa_enabled"] for a in self.super.get("/api/admins/").data}
        self.assertEqual((by["new.admin@x.com"], by[ADMIN_EMAIL]), (True, False))


class SessionLength(AuthTestBase):
    """A sign-in (password, and the code if 2FA is on) lasts 12 hours."""

    def test_the_session_is_twelve_hours(self):
        from .authentication import TOKEN_MAX_AGE
        self.assertEqual(TOKEN_MAX_AGE, 12 * 3600)
        self.assertEqual(self.login().data["expires_in"], 12 * 3600)

    def test_still_valid_just_under_twelve_hours_and_dead_just_after(self):
        import time
        t0 = time.time()
        client = self.client_for(self.login().data["token"])
        with mock.patch("django.core.signing.time.time", return_value=t0 + 12 * 3600 - 120):
            self.assertEqual(client.get("/api/gettotal").status_code, 200)
        with mock.patch("django.core.signing.time.time", return_value=t0 + 12 * 3600 + 120):
            r = client.get("/api/gettotal")
        self.assertEqual(r.status_code, 401)
        self.assertIn("expired", r.data["detail"].lower())

    def test_a_session_with_two_step_on_also_lasts_twelve_hours(self):
        # the session issued after the code step has the same lifetime
        from . import mfa as mfa_module
        import time
        secret = mfa_module.new_secret()
        MFADevice.objects.create(user=self.admin, secret=secret, confirmed=True)
        step1 = self.login()
        now = time.time()
        step2 = self.anon.post("/api/auth/login/mfa/", {"mfa_token": step1.data["mfa_token"], "code": pyotp.TOTP(secret).at(now)}, format="json")
        self.assertEqual(step2.status_code, 200)
        self.assertEqual(step2.data["expires_in"], 12 * 3600)


class LastSignIn(AdminBase):
    def test_a_sign_in_is_recorded_and_shown_to_the_super_admin(self):
        self.add_admin()
        before = {a["email"]: a["last_login"] for a in self.super.get("/api/admins/").data}
        self.assertIsNone(before["new.admin@x.com"])
        self.assertEqual(self.login("new.admin@x.com", NEW_PASSWORD).status_code, 200)
        after = {a["email"]: a["last_login"] for a in self.super.get("/api/admins/").data}
        self.assertIsNotNone(after["new.admin@x.com"])

    def test_a_failed_sign_in_is_not_recorded(self):
        self.add_admin()
        self.login("new.admin@x.com", "wrong-pass")
        self.assertIsNone(User.objects.get(email="new.admin@x.com").last_login)

    def test_two_step_sign_in_is_recorded_once_the_code_is_accepted(self):
        import time
        self.add_admin()
        new = User.objects.get(email="new.admin@x.com")
        secret = pyotp.random_base32()
        MFADevice.objects.create(user=new, secret=secret, confirmed=True)
        step1 = self.login("new.admin@x.com", NEW_PASSWORD)
        self.assertIsNone(User.objects.get(pk=new.pk).last_login)  # password alone is not a sign-in yet
        self.anon.post("/api/auth/login/mfa/", {"mfa_token": step1.data["mfa_token"], "code": pyotp.TOTP(secret).at(time.time())}, format="json")
        self.assertIsNotNone(User.objects.get(pk=new.pk).last_login)


class AccountsWithQuoteRequestsAreProtected(AdminBase):
    """Deleting a customer account used to delete their quote requests too."""

    def rfq(self, user, item="Office chairs"):
        from .models import RFQ
        return RFQ.objects.create(user=user, email=user.email, name="Buyer", company="Acme", item=item, file="")

    def test_an_account_with_quote_requests_cannot_be_deleted_from_the_cms(self):
        self.rfq(self.customer)
        r = self.super.delete(f"/api/user/{self.customer.pk}/")
        self.assertEqual(r.status_code, 409)
        self.assertIn("1 quote request", r.data["detail"])
        self.assertIn("cannot be deleted", r.data["detail"])
        self.assertTrue(User.objects.filter(pk=self.customer.pk).exists())
        self.assertEqual(self.customer.rfqs.count(), 1)  # the request is still there

    def test_the_message_counts_them(self):
        self.rfq(self.customer)
        self.rfq(self.customer, "Laptops")
        r = self.super.delete(f"/api/user/{self.customer.pk}/")
        self.assertIn("2 quote requests", r.data["detail"])

    def test_an_account_without_quote_requests_is_still_deletable(self):
        other = mk("lead", "lead@x.com", "Lead-pass-2026")
        self.assertEqual(self.super.delete(f"/api/user/{other.pk}/").status_code, 204)
        self.assertFalse(User.objects.filter(pk=other.pk).exists())

    def test_one_customers_requests_do_not_block_another_customer(self):
        self.rfq(self.customer)
        other = mk("lead", "lead@x.com", "Lead-pass-2026")
        self.assertEqual(self.super.delete(f"/api/user/{other.pk}/").status_code, 204)
        self.assertTrue(User.objects.filter(pk=self.customer.pk).exists())

    def test_the_database_layer_refuses_too_so_the_django_admin_and_shell_cannot_wipe_them(self):
        from django.db.models import ProtectedError
        self.rfq(self.customer)
        with self.assertRaises(ProtectedError):
            self.customer.delete()
        with self.assertRaises(ProtectedError):
            User.objects.filter(pk=self.customer.pk).delete()
        self.assertEqual(self.customer.rfqs.count(), 1)

    def test_still_admin_only(self):
        self.rfq(self.customer)
        self.assertEqual(self.anon.delete(f"/api/user/{self.customer.pk}/").status_code, 401)
        self.assertEqual(self.client_for(make_token(self.customer)).delete(f"/api/user/{self.customer.pk}/").status_code, 403)

    def test_editing_such_an_account_still_works(self):
        self.rfq(self.customer)
        r = self.super.patch(f"/api/user/{self.customer.pk}/", {"first_name": "Renamed"}, format="json")
        self.assertEqual(r.status_code, 200)
