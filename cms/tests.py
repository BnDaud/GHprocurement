import secrets
from unittest import mock

from django.core import signing
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from .authentication import TOKEN_SALT, make_token
from .models import User

ADMIN_EMAIL = "info@ghprocurement.com"
# Fresh random passwords each run, so no password literal lives in the repo.
ADMIN_PASSWORD = "Pw-" + secrets.token_urlsafe(14)
OTHER_PASSWORD = "Pw-" + secrets.token_urlsafe(14)
USER_PASSWORD = "Pw-" + secrets.token_urlsafe(14)


def mk(username, email, password, **extra):
    return User.objects.create_user(
        username=username, email=email, password=password, dp="", **extra
    )


class AuthTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = mk("admin", ADMIN_EMAIL, ADMIN_PASSWORD, is_staff=True)
        # what the RFQ form creates for a customer: password == company name
        self.customer = mk("Acme", "buyer@acme.com", "Acme")
        self.anon = APIClient()

    def client_for(self, token):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return c

    def login(self, email=ADMIN_EMAIL, password=ADMIN_PASSWORD):
        return self.anon.post("/api/auth/login/", {"email": email, "password": password}, format="json")


class PublicSiteStillWorks(AuthTestBase):
    """The customer website only uses GET /api/alldata/ and POST /api/rfqs/."""

    def test_alldata_is_public(self):
        with mock.patch("cms.views.FetchTwiter") as tw:
            tw.return_value.getTweets.return_value = []
            r = self.anon.get("/api/alldata/")
        self.assertEqual(r.status_code, 200)
        for key in ("catalogs", "metadata", "service", "faq", "twitter"):
            self.assertIn(key, r.data)

    def test_alldata_ignores_a_bad_authorization_header(self):
        with mock.patch("cms.views.FetchTwiter") as tw:
            tw.return_value.getTweets.return_value = []
            c = self.client_for("garbage")
            self.assertEqual(c.get("/api/alldata/").status_code, 200)

    def test_rfq_submission_is_not_blocked(self):
        r = self.anon.post("/api/rfqs/", {}, format="json")
        self.assertNotIn(r.status_code, (401, 403))  # 400 = validation, i.e. it got through

    def test_public_can_read_catalog_services_faqs_metadata(self):
        for path in ("catalogs", "services", "faqs", "metadata"):
            self.assertEqual(self.anon.get(f"/api/{path}/").status_code, 200, path)


class AnonymousIsLockedOut(AuthTestBase):
    def test_cannot_write_content(self):
        self.assertEqual(self.anon.post("/api/services/", {"title": "x", "description": "y"}, format="json").status_code, 401)
        self.assertEqual(self.anon.post("/api/faqs/", {"question": "x", "answer": "y"}, format="json").status_code, 401)
        self.assertEqual(self.anon.put("/api/metadata/1/", {}, format="json").status_code, 401)
        self.assertEqual(self.anon.post("/api/catalogs/", {}, format="json").status_code, 401)
        self.assertEqual(self.anon.delete("/api/services/00000000-0000-0000-0000-000000000000/").status_code, 401)

    def test_cannot_read_private_data(self):
        for path in ("user/", "rfqs/", "gettotal", "auth/me/"):
            self.assertEqual(self.anon.get(f"/api/{path}").status_code, 401, path)

    def test_cannot_send_email_or_create_users(self):
        self.assertEqual(self.anon.post("/api/emails/", {}, format="json").status_code, 401)
        self.assertEqual(self.anon.post("/api/user/", {"username": "evil", "password": "x"}, format="json").status_code, 401)

    def test_401_has_www_authenticate(self):
        r = self.anon.get("/api/user/")
        self.assertIn("Bearer", r.headers.get("WWW-Authenticate", ""))


class Login(AuthTestBase):
    def test_success_returns_token_and_user(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["token"])
        self.assertEqual(r.data["user"]["email"], ADMIN_EMAIL)

    def test_email_is_case_insensitive(self):
        self.assertEqual(self.login("  INFO@GhProcurement.com ".strip()).status_code, 200)

    def test_wrong_password_and_unknown_email_look_the_same(self):
        a = self.login(password="nope")
        b = self.login(email="nobody@x.com")
        self.assertEqual((a.status_code, a.data), (b.status_code, b.data))
        self.assertEqual(a.status_code, 401)

    def test_missing_fields(self):
        self.assertEqual(self.anon.post("/api/auth/login/", {}, format="json").status_code, 401)

    def test_non_staff_account_cannot_sign_in_even_with_right_password(self):
        # an RFQ customer's password is their company name; this must never work
        self.assertEqual(self.login("buyer@acme.com", "Acme").status_code, 401)

    def test_inactive_staff_cannot_sign_in(self):
        self.admin.is_active = False
        self.admin.save()
        self.assertEqual(self.login().status_code, 401)

    def test_superuser_can_sign_in(self):
        mk("root", "root@x.com", "R00t-passw0rd!", is_superuser=True)
        self.assertEqual(self.login("root@x.com", "R00t-passw0rd!").status_code, 200)

    def test_login_is_throttled(self):
        with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "3/min"}):
            cache.clear()
            codes = [self.login(password="bad").status_code for _ in range(5)]
        self.assertEqual(codes[:3], [401, 401, 401])
        self.assertEqual(codes[3], 429)


class TokenUse(AuthTestBase):
    def token(self):
        return self.login().data["token"]

    def test_token_unlocks_the_cms(self):
        c = self.client_for(self.token())
        self.assertEqual(c.get("/api/gettotal").status_code, 200)
        self.assertEqual(c.get("/api/user/").status_code, 200)
        self.assertEqual(c.get("/api/auth/me/").data["email"], ADMIN_EMAIL)
        r = c.post("/api/services/", {"title": "Sourcing", "description": "We source"}, format="json")
        self.assertEqual(r.status_code, 201)
        sid = r.data["id"]
        self.assertEqual(c.patch(f"/api/services/{sid}/", {"title": "Sourcing 2"}, format="json").status_code, 200)
        self.assertEqual(c.delete(f"/api/services/{sid}/").status_code, 204)

    def test_tampered_token_rejected(self):
        t = self.token()
        self.assertEqual(self.client_for(t[:-3] + "xxx").get("/api/gettotal").status_code, 401)

    def test_expired_token_rejected(self):
        t = self.token()
        with mock.patch("cms.authentication.TOKEN_MAX_AGE", -1):
            r = self.client_for(t).get("/api/gettotal")
        self.assertEqual(r.status_code, 401)
        self.assertIn("expired", r.data["detail"].lower())

    def test_token_signed_for_another_purpose_rejected(self):
        t = signing.dumps({"uid": str(self.admin.pk), "fp": self.admin.password[-16:]}, salt="something-else")
        self.assertEqual(self.client_for(t).get("/api/gettotal").status_code, 401)

    def test_non_staff_token_is_forbidden(self):
        r = self.client_for(make_token(self.customer)).get("/api/gettotal")
        self.assertEqual(r.status_code, 403)
        r = self.client_for(make_token(self.customer)).post("/api/services/", {"title": "x", "description": "y"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_deactivated_user_token_rejected(self):
        t = self.token()
        self.admin.is_active = False
        self.admin.save()
        self.assertEqual(self.client_for(t).get("/api/gettotal").status_code, 401)

    def test_malformed_header(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION="Bearer")
        self.assertEqual(c.get("/api/gettotal").status_code, 401)
        c.credentials(HTTP_AUTHORIZATION="Basic abc")
        self.assertEqual(c.get("/api/gettotal").status_code, 401)


class ChangePassword(AuthTestBase):
    def post(self, client, current, new):
        return client.post("/api/auth/change-password/", {"current_password": current, "new_password": new}, format="json")

    def test_requires_sign_in(self):
        self.assertEqual(self.post(self.anon, "a", "b").status_code, 401)

    def test_wrong_current_password(self):
        c = self.client_for(self.login().data["token"])
        r = self.post(c, "wrong", OTHER_PASSWORD)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Current password", r.data["detail"])

    def test_weak_new_password_rejected_with_reason(self):
        c = self.client_for(self.login().data["token"])
        for weak in ("123", "password", "12345678"):
            r = self.post(c, ADMIN_PASSWORD, weak)
            self.assertEqual(r.status_code, 400, weak)
            self.assertTrue(r.data["detail"])

    def test_change_invalidates_old_tokens_and_keeps_session_alive(self):
        old = self.login().data["token"]
        r = self.post(self.client_for(old), ADMIN_PASSWORD, OTHER_PASSWORD)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client_for(old).get("/api/gettotal").status_code, 401)  # old token dead
        self.assertEqual(self.client_for(r.data["token"]).get("/api/gettotal").status_code, 200)  # new one works
        self.assertEqual(self.login().status_code, 401)  # old password dead
        self.assertEqual(self.login(password=OTHER_PASSWORD).status_code, 200)


from django.core.files.uploadedfile import SimpleUploadedFile
from .serial import EmailSerial, MAX_ATTACHMENTS, MAX_TOTAL_SIZE
from rest_framework.exceptions import ValidationError as DRFValidationError

PDF = b"%PDF-1.4\n" + b"x" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
JPG = b"\xff\xd8\xff\xe0" + b"x" * 100
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"x" * 100
ZIP = b"PK\x03\x04" + b"x" * 100


def up(name, content, ctype="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=ctype)


def check(files):
    s = EmailSerial(data={"body": "b", "subject": "s", "title": "t", "recipient": "a@b.com", "attachments": files})
    ok = s.is_valid()
    return ok, s


class EmailAttachmentRules(TestCase):
    def test_every_allowed_type_is_accepted(self):
        files = [up("a.pdf", PDF, "application/pdf"), up("b.docx", ZIP), up("c.xlsx", ZIP), up("d.pptx", ZIP),
                 up("e.jpg", JPG, "image/jpeg"), up("f.jpeg", JPG), up("g.png", PNG, "image/png"), up("h.webp", WEBP, "image/webp")]
        ok, s = check(files)
        self.assertTrue(ok, s.errors)

    def test_extension_is_case_insensitive(self):
        ok, s = check([up("SCAN.PDF", PDF, "application/pdf")])
        self.assertTrue(ok, s.errors)

    def test_missing_browser_type_is_fine_and_type_is_canonicalised(self):
        f = up("a.pdf", PDF, "")
        ok, s = check([f])
        self.assertTrue(ok, s.errors)
        self.assertEqual(s.validated_data["attachments"][0].content_type, "application/pdf")

    def test_disallowed_extensions_rejected(self):
        for name in ("run.exe", "notes.txt", "data.csv", "old.doc", "old.xls", "bundle.zip", "noext", "page.html", "x.pdf.exe"):
            ok, s = check([up(name, PDF)])
            self.assertFalse(ok, name)

    def test_spoofed_files_rejected(self):
        # an exe renamed to .pdf, claiming to be a pdf
        ok, _ = check([up("evil.pdf", b"MZ\x90\x00" + b"x" * 50, "application/pdf")])
        self.assertFalse(ok)
        # a pdf renamed to .png
        ok, _ = check([up("fake.png", PDF, "image/png")])
        self.assertFalse(ok)
        # right bytes, contradicting claimed type
        ok, _ = check([up("a.pdf", PDF, "image/png")])
        self.assertFalse(ok)

    def test_size_and_count_limits(self):
        ok, _ = check([up(f"f{i}.pdf", PDF) for i in range(MAX_ATTACHMENTS)])
        self.assertTrue(ok)
        ok, s = check([up(f"f{i}.pdf", PDF) for i in range(MAX_ATTACHMENTS + 1)])
        self.assertFalse(ok)
        big = PDF + b"x" * (MAX_TOTAL_SIZE // 2 + 1)
        ok, s = check([up("a.pdf", big), up("b.pdf", big)])
        self.assertFalse(ok)
        self.assertIn("limit", str(s.errors))

    def test_title_and_recipient_still_required(self):
        s = EmailSerial(data={"body": "b", "subject": "s", "recipient": "a@b.com"})
        self.assertFalse(s.is_valid())
        self.assertIn("title", s.errors)


class EmailTemplate(TestCase):
    def build(self, **kw):
        from .task import build_email
        data = {"subject": "S", "title": "A title", "recipient": "x@y.com", "body": "Hello\n\nSecond para\nline two", "attachments": []}
        data.update(kw)
        return build_email(data)

    def test_html_and_plain_parts_and_inline_logo(self):
        m = self.build()
        html = m.alternatives[0][0]
        self.assertIn("A title", html)
        self.assertIn("cid:ghprocurement-logo", html)
        self.assertNotIn("Regrads", html)
        import datetime
        self.assertIn(str(datetime.datetime.now().year), html)  # footer year was blank before
        self.assertIn("Second para", m.body)
        self.assertIn("A title", m.body)
        self.assertTrue(any(getattr(a, "get", lambda *_: None)("Content-ID") == "<ghprocurement-logo>" for a in m.attachments))

    def test_paragraphs_and_line_breaks(self):
        html = self.build().alternatives[0][0]
        self.assertEqual(html.count("<p "), 2)
        self.assertIn("Second para<br />line two", html)

    def test_html_in_body_is_escaped(self):
        html = self.build(body="<script>alert(1)</script> & <b>x</b>").alternatives[0][0]
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_attachment_list_shown_with_sizes(self):
        files = [{"name": "a.pdf", "content": b"x" * 2048, "content_type": "application/pdf"},
                 {"name": "b.pdf", "content": b"x" * 10, "content_type": "application/pdf"}]
        m = self.build(attachments=files)
        html = m.alternatives[0][0]
        self.assertIn("Attachments (2)", html)
        self.assertIn("a.pdf", html)
        self.assertIn("2.0 KB", html)
        names = [a[0] for a in m.attachments if isinstance(a, tuple)]
        self.assertEqual(names, ["a.pdf", "b.pdf"])
        self.assertIn("a.pdf (2.0 KB)", m.body)

    def test_reply_button_prefilled_with_subject(self):
        html = self.build(subject="Quote & terms").alternatives[0][0]
        self.assertIn("mailto:info@ghprocurement.com?subject=Re%3A%20Quote%20%26%20terms", html)

    def test_no_attachment_block_when_none(self):
        html = self.build().alternatives[0][0]
        self.assertNotIn("attachment", html.lower())


# ---------------------------------------------------------------------------
# reference numbers, templates by kind, and the send flow
# ---------------------------------------------------------------------------
import datetime as _dt
from django.db import IntegrityError
from .models import MetaData, SentEmail
from .references import create_sent_email, LAGOS


def _rec(kind="rfq_reply", **kw):
    base = dict(recipient="a@b.com", subject="S")
    base.update(kw)
    return create_sent_email(kind, **base)


class ReferenceNumbers(TestCase):
    def test_rfq_replies_get_sequential_references(self):
        year = _dt.datetime.now(LAGOS).year
        refs = [_rec().reference for _ in range(3)]
        self.assertEqual(refs, [f"GHP-{year}-0001", f"GHP-{year}-0002", f"GHP-{year}-0003"])

    def test_outreach_gets_no_reference_and_does_not_use_up_numbers(self):
        year = _dt.datetime.now(LAGOS).year
        self.assertIsNone(_rec("outreach").reference)
        _rec("outreach")
        self.assertEqual(_rec().reference, f"GHP-{year}-0001")

    def test_numbering_restarts_each_year(self):
        with mock.patch("cms.references.lagos_now", return_value=_dt.datetime(2027, 1, 2, tzinfo=LAGOS)):
            a = _rec().reference
            b = _rec().reference
        self.assertEqual((a, b), ("GHP-2027-0001", "GHP-2027-0002"))
        with mock.patch("cms.references.lagos_now", return_value=_dt.datetime(2028, 6, 1, tzinfo=LAGOS)):
            self.assertEqual(_rec().reference, "GHP-2028-0001")

    def test_five_digit_numbers_keep_counting(self):
        year = _dt.datetime.now(LAGOS).year
        SentEmail.objects.create(kind="rfq_reply", recipient="a@b.com", subject="S", reference=f"GHP-{year}-9999",
                                 reference_year=year, reference_number=9999)
        self.assertEqual(_rec().reference, f"GHP-{year}-10000")

    def test_database_refuses_a_duplicate_number(self):
        r = _rec()
        with self.assertRaises(IntegrityError):
            SentEmail.objects.create(kind="rfq_reply", recipient="x@y.com", subject="S",
                                     reference="GHP-OTHER", reference_year=r.reference_year, reference_number=r.reference_number)

    def test_collision_is_retried_with_the_next_number(self):
        # another request already took 0001 but this one read a stale "last number = 0"
        from django.db.models.query import QuerySet
        year = _dt.datetime.now(LAGOS).year
        SentEmail.objects.create(kind="rfq_reply", recipient="z@z.com", subject="other", reference=f"GHP-{year}-0001",
                                 reference_year=year, reference_number=1)
        real = QuerySet.aggregate
        calls = {"n": 0}

        def stale_once(self, *a, **kw):
            calls["n"] += 1
            return {"m": 0} if calls["n"] == 1 else real(self, *a, **kw)

        with mock.patch.object(QuerySet, "aggregate", stale_once):
            r = _rec()
        self.assertEqual(r.reference, f"GHP-{year}-0002")
        self.assertEqual(calls["n"], 2)


class TemplatesByKind(TestCase):
    def build(self, **kw):
        from .task import build_email
        data = {"subject": "Re: your RFQ", "title": "Quote", "recipient": "buyer@acme.com", "body": "Hello\n\nHere it is.", "attachments": []}
        data.update(kw)
        return build_email(data)

    def html(self, **kw):
        return self.build(**kw).alternatives[0][0]

    def test_outreach_uses_the_card_template_without_quote_details(self):
        html = self.html(kind="outreach", reference="GHP-2026-0001", valid_days=30)
        self.assertIn("Reply to this email", html)
        self.assertNotIn("Reference", html)
        self.assertNotIn("Valid until", html)
        self.assertNotIn("Quotation", html)

    def test_rfq_reply_uses_the_formal_template_with_reference_and_date(self):
        sent = _dt.datetime(2026, 10, 9, 15, 0, tzinfo=LAGOS)
        m = self.build(kind="rfq_reply", reference="GHP-2026-0007", sent_at=sent, recipient_name="Sulaimon")
        html = m.alternatives[0][0]
        self.assertIn("GHP-2026-0007", html)
        self.assertIn("9 October 2026", html)
        self.assertIn("Prepared for", html)
        self.assertIn("Sulaimon", html)
        self.assertIn("Quotation", html)
        self.assertNotIn("Reply to this email", html)
        self.assertIn("GHP-2026-0007", m.body)  # plain-text part too
        self.assertIn("9 October 2026", m.body)

    def test_prepared_for_falls_back_to_the_email_address(self):
        self.assertIn("buyer@acme.com", self.html(kind="rfq_reply"))

    def test_validity_is_optional_and_computed_from_the_send_date(self):
        sent = _dt.datetime(2026, 10, 9, 15, 0, tzinfo=LAGOS)
        none = self.html(kind="rfq_reply", sent_at=sent)
        self.assertNotIn("Valid until", none)
        some = self.build(kind="rfq_reply", sent_at=sent, valid_days=30)
        self.assertIn("Valid until", some.alternatives[0][0])
        self.assertIn("8 November 2026", some.alternatives[0][0])
        self.assertIn("Valid until: 8 November 2026", some.body)

    def test_unknown_kind_falls_back_to_the_card_template(self):
        self.assertIn("Reply to this email", self.html(kind="nonsense"))

    def test_office_comes_from_cms_settings_with_a_default(self):
        self.assertIn("25 Okota Road, Isolo, Lagos", self.html(kind="outreach"))
        self.assertIn("25 Okota Road, Isolo, Lagos", self.html(kind="rfq_reply"))
        MetaData.objects.create(metaIntro="i", metaDescription="d", email="hello@gh.com", phone="0800", office="7 New Street, Abuja")
        for kind in ("outreach", "rfq_reply"):
            html = self.html(kind=kind)
            self.assertIn("7 New Street, Abuja", html)
            self.assertNotIn("Okota", html)
            self.assertIn("hello@gh.com", html)
            self.assertIn("0800", html)

    def test_blank_cms_office_uses_the_default(self):
        MetaData.objects.create(metaIntro="i", metaDescription="d", email="", phone="", office="   ")
        self.assertIn("25 Okota Road, Isolo, Lagos", self.html(kind="outreach"))


class SendFlow(AuthTestBase):
    """POST /api/emails/ end to end, with the background thread run inline and
    the email provider mocked."""

    def post(self, client, **extra):
        data = {"recipient": "buyer@acme.com", "subject": "Quote", "title": "Quote", "body": "Hello"}
        data.update(extra)
        return client.post("/api/emails/", data, format="multipart")

    def run_inline(self):
        class Inline:
            def __init__(self, target, args): self.target, self.args = target, args
            def start(self): self.target(*self.args)
        return mock.patch("cms.views.threading.Thread", Inline)

    def send_patch(self):
        return mock.patch("cms.task.EmailMultiAlternatives.send", return_value=1)

    def test_rfq_reply_is_recorded_with_a_reference_and_marked_sent(self):
        c = self.client_for(self.login().data["token"])
        with self.run_inline(), self.send_patch() as send:
            r = self.post(c, kind="rfq_reply", recipient_name="Sulaimon", valid_days="30")
        self.assertEqual(r.status_code, 200)
        self.assertRegex(r.data["reference"], r"^GHP-\d{4}-0001$")
        rec = SentEmail.objects.get()
        self.assertEqual((rec.kind, rec.reference, rec.recipient, rec.recipient_name, rec.valid_days), ("rfq_reply", r.data["reference"], "buyer@acme.com", "Sulaimon", 30))
        self.assertEqual(rec.status, "sent")
        self.assertIsNotNone(rec.sent_at)
        send.assert_called_once()

    def test_outreach_is_recorded_without_a_reference_and_ignores_validity(self):
        c = self.client_for(self.login().data["token"])
        with self.run_inline(), self.send_patch():
            r = self.post(c, kind="outreach", valid_days="30")
        self.assertIsNone(r.data["reference"])
        rec = SentEmail.objects.get()
        self.assertEqual((rec.kind, rec.reference, rec.valid_days), ("outreach", None, None))

    def test_kind_defaults_to_outreach(self):
        c = self.client_for(self.login().data["token"])
        with self.run_inline(), self.send_patch():
            self.post(c)
        self.assertEqual(SentEmail.objects.get().kind, "outreach")

    def test_failed_send_is_recorded(self):
        c = self.client_for(self.login().data["token"])
        with self.run_inline(), mock.patch("cms.task.EmailMultiAlternatives.send", side_effect=RuntimeError("provider down")):
            with self.assertRaises(RuntimeError):
                self.post(c, kind="rfq_reply")
        rec = SentEmail.objects.get()
        self.assertEqual(rec.status, "failed")
        self.assertIn("provider down", rec.error)

    def test_references_increase_across_sends(self):
        c = self.client_for(self.login().data["token"])
        with self.run_inline(), self.send_patch():
            a = self.post(c, kind="rfq_reply").data["reference"]
            b = self.post(c, kind="rfq_reply").data["reference"]
        self.assertTrue(a.endswith("0001") and b.endswith("0002"))

    def test_bad_input_creates_no_record(self):
        c = self.client_for(self.login().data["token"])
        for bad in ({"kind": "spam"}, {"valid_days": "0"}, {"valid_days": "400"}, {"valid_days": "abc"}, {"recipient": "not-an-email"}):
            r = self.post(c, **bad)
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(SentEmail.objects.count(), 0)

    def test_blank_validity_is_accepted(self):
        c = self.client_for(self.login().data["token"])
        with self.run_inline(), self.send_patch():
            r = self.post(c, kind="rfq_reply", valid_days="")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(SentEmail.objects.get().valid_days)

    def test_still_requires_admin_sign_in(self):
        self.assertEqual(self.post(self.anon, kind="rfq_reply").status_code, 401)
        self.assertEqual(SentEmail.objects.count(), 0)


# ---------------------------------------------------------------------------
# sent-mail history: what is kept, and that it is admin-only and read-only
# ---------------------------------------------------------------------------
class SentMailHistory(SendFlow):
    def send(self, client, files=None, **extra):
        data = {"recipient": "buyer@acme.com", "subject": "Quote", "title": "Your quote",
                "body": "Hello there.\n\nSecond paragraph.", "kind": "rfq_reply", "recipient_name": "Sulaimon"}
        data.update(extra)
        if files:
            data["attachments"] = files
        with self.run_inline(), self.send_patch():
            return client.post("/api/emails/", data, format="multipart")

    def admin_client(self):
        return self.client_for(self.login().data["token"])

    def test_keeps_the_text_and_attachment_names_and_sizes_but_not_the_files(self):
        c = self.admin_client()
        files = [up("price-list.pdf", PDF + b"x" * 900, "application/pdf"), up("photo.png", PNG + b"y" * 300, "image/png")]
        self.assertEqual(self.send(c, files).status_code, 200)
        rec = SentEmail.objects.get()
        self.assertEqual((rec.title, rec.body), ("Your quote", "Hello there.\n\nSecond paragraph."))
        self.assertEqual(rec.attachments_count, 2)
        self.assertEqual([a["name"] for a in rec.attachments_info], ["price-list.pdf", "photo.png"])
        self.assertEqual([a["size"] for a in rec.attachments_info], [len(PDF) + 900, len(PNG) + 300])
        self.assertEqual({a["type"] for a in rec.attachments_info}, {"application/pdf", "image/png"})
        # nothing in the stored row contains the file bytes
        stored = repr(rec.attachments_info) + rec.body + rec.title + rec.error
        self.assertNotIn("xxxxxxxxxx", stored)
        self.assertNotIn("%PDF", stored)

    def test_no_attachments_is_an_empty_list(self):
        self.send(self.admin_client())
        rec = SentEmail.objects.get()
        self.assertEqual((rec.attachments_count, rec.attachments_info), (0, []))

    def test_list_is_newest_first_and_leaves_out_the_body(self):
        c = self.admin_client()
        self.send(c, subject="first")
        self.send(c, subject="second")
        r = c.get("/api/sent-emails/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([row["subject"] for row in r.data], ["second", "first"])
        self.assertNotIn("body", r.data[0])
        self.assertEqual(r.data[0]["recipient"], "buyer@acme.com")
        self.assertRegex(r.data[0]["reference"], r"^GHP-\d{4}-0002$")

    def test_detail_has_the_full_text_and_attachment_names(self):
        c = self.admin_client()
        self.send(c, [up("terms.pdf", PDF, "application/pdf")])
        rec = SentEmail.objects.get()
        r = c.get(f"/api/sent-emails/{rec.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["body"], "Hello there.\n\nSecond paragraph.")
        self.assertEqual(r.data["title"], "Your quote")
        self.assertEqual(r.data["attachments"][0]["name"], "terms.pdf")
        self.assertEqual(r.data["status"], "sent")

    def test_failed_send_shows_up_with_its_error(self):
        c = self.admin_client()
        with self.run_inline(), mock.patch("cms.task.EmailMultiAlternatives.send", side_effect=RuntimeError("provider down")):
            with self.assertRaises(RuntimeError):
                c.post("/api/emails/", {"recipient": "a@b.com", "subject": "S", "title": "T", "body": "B"}, format="multipart")
        r = c.get(f"/api/sent-emails/{SentEmail.objects.get().pk}/")
        self.assertEqual((r.data["status"], "provider down" in r.data["error"]), ("failed", True))

    def test_list_is_capped(self):
        from .views import SentEmailView
        for i in range(5):
            _rec("outreach", subject=f"s{i}")
        with mock.patch.object(SentEmailView, "MAX_LIST", 3):
            r = self.admin_client().get("/api/sent-emails/")
        self.assertEqual(len(r.data), 3)

    def test_admin_only(self):
        self.send(self.admin_client())
        pk = SentEmail.objects.get().pk
        self.assertEqual(self.anon.get("/api/sent-emails/").status_code, 401)
        self.assertEqual(self.anon.get(f"/api/sent-emails/{pk}/").status_code, 401)
        customer = self.client_for(make_token(self.customer))
        self.assertEqual(customer.get("/api/sent-emails/").status_code, 403)

    def test_read_only_through_the_api(self):
        c = self.admin_client()
        self.send(c)
        pk = SentEmail.objects.get().pk
        self.assertEqual(c.post("/api/sent-emails/", {}, format="json").status_code, 405)
        self.assertEqual(c.put(f"/api/sent-emails/{pk}/", {}, format="json").status_code, 405)
        self.assertEqual(c.patch(f"/api/sent-emails/{pk}/", {"subject": "edited"}, format="json").status_code, 405)
        self.assertEqual(c.delete(f"/api/sent-emails/{pk}/").status_code, 405)
        self.assertEqual(SentEmail.objects.get().subject, "Quote")
