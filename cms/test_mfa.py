"""Two-step verification (authenticator app): setup, sign-in, recovery codes,
replay and brute-force protection, and turning it off."""
import io
from datetime import timedelta
from unittest import mock

import pyotp
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from . import mfa
from .authentication import make_token
from .models import MFADevice, User
from .tests import ADMIN_EMAIL, ADMIN_PASSWORD, OTHER_PASSWORD, AuthTestBase, mk

NOW = 1_800_000_000  # a fixed moment; codes for it are fully predictable


def code_at(secret, offset_seconds=0):
    return pyotp.TOTP(secret).at(NOW + offset_seconds)


class MfaBase(AuthTestBase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("cms.mfa.time.time", side_effect=lambda: self.clock)
        self.clock = NOW
        patcher.start()
        self.addCleanup(patcher.stop)

    def session(self):
        return self.client_for(self.login().data["token"])

    def enable(self):
        """Turn 2FA on for the admin. Returns (secret, recovery_codes, new_token)."""
        c = self.session()
        secret = c.post("/api/auth/mfa/setup/").data["secret"]
        r = c.post("/api/auth/mfa/confirm/", {"code": code_at(secret)}, format="json")
        assert r.status_code == 200, r.data
        self.clock = NOW + 30  # the confirm code is spent; the next step is now
        return secret, r.data["recovery_codes"], r.data["token"]

    def step_two(self, mfa_token, code):
        return self.anon.post("/api/auth/login/mfa/", {"mfa_token": mfa_token, "code": code}, format="json")


class Setup(MfaBase):
    def test_setup_needs_an_admin_session(self):
        self.assertEqual(self.anon.post("/api/auth/mfa/setup/").status_code, 401)
        self.assertEqual(self.client_for(make_token(self.customer)).post("/api/auth/mfa/setup/").status_code, 403)

    def test_setup_returns_a_secret_and_an_app_link_but_changes_nothing_yet(self):
        r = self.session().post("/api/auth/mfa/setup/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["secret"]), 32)
        self.assertTrue(r.data["otpauth_uri"].startswith("otpauth://totp/"))
        self.assertIn("GH%20Procurement%20CMS", r.data["otpauth_uri"])
        self.assertIn(r.data["secret"], r.data["otpauth_uri"])
        self.assertFalse(MFADevice.objects.get().confirmed)
        # until confirmed, signing in is still just the password
        self.assertIn("token", self.login().data)

    def test_confirm_with_a_wrong_code_fails_and_stays_pending(self):
        c = self.session()
        c.post("/api/auth/mfa/setup/")
        r = c.post("/api/auth/mfa/confirm/", {"code": "000000"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(MFADevice.objects.get().confirmed)

    def test_confirm_without_setup(self):
        self.assertEqual(self.session().post("/api/auth/mfa/confirm/", {"code": "123456"}, format="json").status_code, 400)

    def test_confirm_turns_it_on_and_hands_out_ten_recovery_codes(self):
        old_token = self.login().data["token"]
        secret, codes, new_token = self.enable()
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        for c in codes:
            self.assertRegex(c, r"^[A-HJ-NP-Z2-9]{5}-[A-HJ-NP-Z2-9]{5}$")
        self.assertTrue(MFADevice.objects.get().confirmed)
        self.assertEqual(self.client_for(new_token).get("/api/auth/me/").data["mfa_enabled"], True)
        # a session that existed before 2FA was switched on must not keep working
        self.assertEqual(self.client_for(old_token).get("/api/gettotal").status_code, 401)

    def test_recovery_codes_are_stored_only_as_hashes(self):
        _, codes, _ = self.enable()
        stored = str(MFADevice.objects.get().recovery_hashes)
        for c in codes:
            self.assertNotIn(c, stored)
            self.assertNotIn(c.replace("-", ""), stored)

    def test_setup_is_refused_while_it_is_already_on(self):
        _, _, token = self.enable()
        self.assertEqual(self.client_for(token).post("/api/auth/mfa/setup/").status_code, 400)

    def test_repeating_setup_before_confirming_replaces_the_secret(self):
        c = self.session()
        first = c.post("/api/auth/mfa/setup/").data["secret"]
        second = c.post("/api/auth/mfa/setup/").data["secret"]
        self.assertNotEqual(first, second)
        self.assertEqual(MFADevice.objects.count(), 1)
        self.assertEqual(c.post("/api/auth/mfa/confirm/", {"code": code_at(first)}, format="json").status_code, 400)
        self.assertEqual(c.post("/api/auth/mfa/confirm/", {"code": code_at(second)}, format="json").status_code, 200)


class SignIn(MfaBase):
    def setUp(self):
        super().setUp()
        self.secret, self.codes, _ = self.enable()

    def first_step(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        return r

    def test_password_alone_no_longer_gives_a_session(self):
        r = self.first_step()
        self.assertTrue(r.data["mfa_required"])
        self.assertNotIn("token", r.data)
        self.assertNotIn("user", r.data)
        self.assertTrue(r.data["mfa_token"])

    def test_wrong_password_is_still_rejected_before_any_code_is_asked(self):
        r = self.login(password="nope")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("mfa_token", r.data)

    def test_right_code_completes_sign_in(self):
        mt = self.first_step().data["mfa_token"]
        r = self.step_two(mt, code_at(self.secret, 30))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["user"]["email"], ADMIN_EMAIL)
        self.assertTrue(r.data["user"]["mfa_enabled"])
        self.assertFalse(r.data["used_recovery_code"])
        self.assertEqual(self.client_for(r.data["token"]).get("/api/gettotal").status_code, 200)

    def test_wrong_code_is_rejected(self):
        mt = self.first_step().data["mfa_token"]
        for bad in ("000000", "12345", "abcdef", "1234567"):  # four tries: the fifth would lock
            r = self.step_two(mt, bad)
            self.assertEqual(r.status_code, 401, bad)
            self.assertNotIn("token", r.data)

    def test_a_code_works_only_once(self):
        mt = self.first_step().data["mfa_token"]
        code = code_at(self.secret, 30)
        self.assertEqual(self.step_two(mt, code).status_code, 200)
        self.assertEqual(self.step_two(self.first_step().data["mfa_token"], code).status_code, 401)

    def test_the_code_used_to_confirm_setup_cannot_be_reused_to_sign_in(self):
        self.clock = NOW  # same 30-second step as the confirm code
        mt = self.first_step().data["mfa_token"]
        self.assertEqual(self.step_two(mt, code_at(self.secret, 0)).status_code, 401)

    def test_clock_drift_of_one_step_is_tolerated_but_not_two(self):
        mt = self.first_step().data["mfa_token"]
        self.clock = NOW + 90
        self.assertEqual(self.step_two(mt, code_at(self.secret, 30)).status_code, 401)  # 60 s old: too old
        self.assertEqual(self.step_two(mt, code_at(self.secret, 120)).status_code, 200)  # 30 s ahead: fine

    def test_garbage_and_foreign_tokens_are_refused(self):
        self.assertEqual(self.step_two("garbage", "123456").status_code, 401)
        self.assertTrue(self.step_two("garbage", "123456").data["restart"])
        # a normal access token is not a step token
        self.assertEqual(self.step_two(make_token(self.admin), code_at(self.secret, 30)).status_code, 401)

    def test_the_step_token_cannot_be_used_as_an_access_token(self):
        mt = self.first_step().data["mfa_token"]
        self.assertEqual(self.client_for(mt).get("/api/gettotal").status_code, 401)

    def test_the_step_token_expires(self):
        mt = self.first_step().data["mfa_token"]
        with mock.patch("cms.auth_views.MFA_STEP_MAX_AGE", -1):
            r = self.step_two(mt, code_at(self.secret, 30))
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.data["restart"])

    def test_account_that_lost_admin_rights_mid_sign_in_cannot_finish(self):
        mt = self.first_step().data["mfa_token"]
        User.objects.filter(pk=self.admin.pk).update(is_staff=False)
        self.assertEqual(self.step_two(mt, code_at(self.secret, 30)).status_code, 401)

    def test_deactivated_account_cannot_finish(self):
        mt = self.first_step().data["mfa_token"]
        User.objects.filter(pk=self.admin.pk).update(is_active=False)
        self.assertEqual(self.step_two(mt, code_at(self.secret, 30)).status_code, 401)


class RecoveryCodes(MfaBase):
    def setUp(self):
        super().setUp()
        self.secret, self.codes, _ = self.enable()

    def sign_in_with(self, code):
        return self.step_two(self.login().data["mfa_token"], code)

    def test_a_recovery_code_signs_in_once(self):
        r = self.sign_in_with(self.codes[0])
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["used_recovery_code"])
        self.assertEqual(r.data["recovery_codes_left"], 9)
        self.assertEqual(self.sign_in_with(self.codes[0]).status_code, 401)  # spent

    def test_codes_are_forgiving_about_case_spaces_and_the_dash(self):
        c = self.codes[1]
        self.assertEqual(self.sign_in_with(c.lower().replace("-", " ")).status_code, 200)

    def test_a_wrong_recovery_code_is_refused(self):
        self.assertEqual(self.sign_in_with("AAAAA-AAAAA").status_code, 401)

    def test_all_ten_work_and_the_eleventh_attempt_fails(self):
        for c in self.codes:
            self.assertEqual(self.sign_in_with(c).status_code, 200, c)
        self.assertEqual(MFADevice.objects.get().recovery_hashes, [])
        self.assertEqual(self.sign_in_with("AAAAA-AAAAA").status_code, 401)

    def test_regenerating_needs_password_and_code_and_replaces_the_old_set(self):
        token = self.login()  # step one only
        self.clock = NOW + 60
        c = self.client_for(self.step_two(token.data["mfa_token"], code_at(self.secret, 60)).data["token"])
        bad_pw = c.post("/api/auth/mfa/recovery-codes/", {"password": "x", "code": code_at(self.secret, 90)}, format="json")
        self.assertEqual(bad_pw.status_code, 400)
        self.clock = NOW + 90
        r = c.post("/api/auth/mfa/recovery-codes/", {"password": ADMIN_PASSWORD, "code": code_at(self.secret, 90)}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["recovery_codes"]), 10)
        self.assertEqual(self.sign_in_with(self.codes[0]).status_code, 401)  # old set is dead
        self.assertEqual(self.sign_in_with(r.data["recovery_codes"][0]).status_code, 200)


class BruteForce(MfaBase):
    def setUp(self):
        super().setUp()
        self.secret, self.codes, _ = self.enable()

    def test_five_wrong_codes_lock_the_second_step(self):
        mt = self.login().data["mfa_token"]
        codes = [self.step_two(mt, "000000").status_code for _ in range(5)]
        self.assertEqual(codes, [401, 401, 401, 401, 429])  # the fifth miss trips the lock
        # even the right code is refused while locked
        r = self.step_two(mt, code_at(self.secret, 30))
        self.assertEqual(r.status_code, 429)
        self.assertNotIn("token", r.data)

    def test_the_lock_ends_after_fifteen_minutes(self):
        mt = self.login().data["mfa_token"]
        for _ in range(5):
            self.step_two(mt, "000000")
        later = timezone.now() + timedelta(minutes=16)
        self.clock = NOW + 16 * 60
        with mock.patch("cms.mfa.timezone.now", return_value=later):
            mt2 = self.login().data["mfa_token"]
            self.assertEqual(self.step_two(mt2, code_at(self.secret, 16 * 60)).status_code, 200)

    def test_a_good_sign_in_resets_the_failure_count(self):
        mt = self.login().data["mfa_token"]
        for _ in range(4):
            self.step_two(mt, "000000")
        self.assertEqual(self.step_two(mt, code_at(self.secret, 30)).status_code, 200)
        self.assertEqual(MFADevice.objects.get().failed_attempts, 0)

    def test_the_lock_also_protects_recovery_codes(self):
        mt = self.login().data["mfa_token"]
        for _ in range(5):
            self.step_two(mt, "AAAAA-AAAAA")
        self.assertEqual(self.step_two(mt, self.codes[0]).status_code, 429)

    def test_confirm_during_setup_is_guarded_too(self):
        self.clock = NOW
        MFADevice.objects.all().delete()
        c = self.session()
        c.post("/api/auth/mfa/setup/")
        codes = [c.post("/api/auth/mfa/confirm/", {"code": "000000"}, format="json").status_code for _ in range(6)]
        self.assertEqual(codes[:5], [400] * 5)
        self.assertEqual(codes[5], 429)


class TurningItOff(MfaBase):
    def setUp(self):
        super().setUp()
        self.secret, self.codes, self.token = self.enable()

    def disable(self, password=ADMIN_PASSWORD, code=None):
        c = self.client_for(self.token)
        return c.post("/api/auth/mfa/disable/", {"password": password, "code": code or code_at(self.secret, 30)}, format="json")

    def test_needs_the_password(self):
        self.assertEqual(self.disable(password="nope").status_code, 400)
        self.assertTrue(MFADevice.objects.exists())

    def test_needs_a_valid_code(self):
        self.assertEqual(self.disable(code="000000").status_code, 400)
        self.assertTrue(MFADevice.objects.exists())

    def test_a_session_alone_is_not_enough(self):
        # the token alone (for example a stolen laptop) cannot switch 2FA off
        r = self.client_for(self.token).post("/api/auth/mfa/disable/", {}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(MFADevice.objects.exists())

    def test_a_recovery_code_can_be_used_to_turn_it_off(self):
        r = self.disable(code=self.codes[0])
        self.assertEqual(r.status_code, 200)
        self.assertFalse(MFADevice.objects.exists())

    def test_turning_off_returns_to_password_only_and_resets_sessions(self):
        r = self.disable()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["user"]["mfa_enabled"])
        self.assertFalse(MFADevice.objects.exists())
        self.assertEqual(self.client_for(self.token).get("/api/gettotal").status_code, 401)  # old token dead
        self.assertEqual(self.client_for(r.data["token"]).get("/api/gettotal").status_code, 200)  # fresh one works
        self.assertIn("token", self.login().data)  # password alone again

    def test_not_on_means_nothing_to_turn_off(self):
        MFADevice.objects.all().delete()
        c = self.client_for(make_token(self.admin))
        self.assertEqual(c.post("/api/auth/mfa/disable/", {"password": ADMIN_PASSWORD, "code": "123456"}, format="json").status_code, 400)

    def test_can_be_set_up_again_afterwards_with_a_new_secret(self):
        self.disable()
        c = self.session()
        again = c.post("/api/auth/mfa/setup/")
        self.assertEqual(again.status_code, 200)
        self.assertNotEqual(again.data["secret"], self.secret)


class Misc(MfaBase):
    def test_accounts_without_2fa_are_unaffected(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        self.assertIn("token", r.data)
        self.assertFalse(r.data["user"]["mfa_enabled"])

    def test_me_reports_2fa_state_and_codes_left(self):
        c = self.session()
        self.assertEqual(c.get("/api/auth/me/").data["recovery_codes_left"], 0)
        _, _, token = self.enable()
        self.assertEqual(self.client_for(token).get("/api/auth/me/").data["recovery_codes_left"], 10)

    def test_changing_the_password_keeps_2fa_on(self):
        _, _, token = self.enable()
        r = self.client_for(token).post("/api/auth/change-password/", {"current_password": ADMIN_PASSWORD, "new_password": OTHER_PASSWORD}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.login(password=OTHER_PASSWORD).data["mfa_required"])

    def test_another_admin_is_not_affected(self):
        other = mk("boss", "boss@x.com", "B0ss-passw0rd!", is_staff=True)
        self.enable()
        r = self.login("boss@x.com", "B0ss-passw0rd!")
        self.assertIn("token", r.data)
        self.assertFalse(MFADevice.objects.filter(user=other).exists())

    def test_unknown_email_still_looks_like_a_plain_failure(self):
        self.enable()
        r = self.login("nobody@x.com", "whatever")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("mfa_required", r.data)

    def test_emergency_command_turns_it_off(self):
        self.enable()
        out = io.StringIO()
        call_command("disable_mfa", ADMIN_EMAIL.upper(), stdout=out)
        self.assertFalse(MFADevice.objects.exists())
        self.assertIn("turned off", out.getvalue())
        self.assertIn("token", self.login().data)

    def test_emergency_command_rejects_unknown_email(self):
        with self.assertRaises(CommandError):
            call_command("disable_mfa", "nobody@x.com")
