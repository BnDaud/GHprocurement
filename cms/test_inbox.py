"""The inbox: Postmark's inbound webhook (public but secret), and the
read-only admin API on top of the stored mail."""
import base64
import json
from datetime import timezone as tz

from django.test import Client, override_settings
from django.utils import timezone

from . import inbound
from .authentication import make_token
from .models import InboxMessage
from .tests import AuthTestBase, mk

SECRET = "inbox-test-secret-0123456789"
URL = f"/api/inbound/{SECRET}/"


def payload(**over):
    """A realistic Postmark inbound payload (trimmed)."""
    p = {
        "FromName": "Ada Buyer",
        "MessageStream": "inbound",
        "From": "ada@acme.com",
        "FromFull": {"Email": "ada@acme.com", "Name": "Ada Buyer", "MailboxHash": ""},
        "To": "info@ghprocurement.com",
        "ToFull": [{"Email": "info@ghprocurement.com", "Name": "", "MailboxHash": ""}],
        "OriginalRecipient": "abc123@inbound.postmarkapp.com",
        "Subject": "Quote for 20 office chairs",
        "MessageID": "73e6d360-66eb-11e1-8e72-a8904824019b",
        "Date": "Fri, 9 Oct 2026 10:30:00 +0100",
        "TextBody": "Hello,\n\nCould you quote for 20 office chairs?\n\nThanks,\nAda",
        "HtmlBody": "<p>Hello,</p><p>Could you quote for 20 office chairs?</p>",
        "Headers": [{"Name": "X-Spam-Status", "Value": "No"}, {"Name": "X-Spam-Score", "Value": "-0.1"}],
        "Attachments": [
            {"Name": "spec.pdf", "Content": base64.b64encode(b"%PDF-1.4 secret file bytes").decode(),
             "ContentType": "application/pdf", "ContentLength": 26, "ContentID": ""},
        ],
    }
    p.update(over)
    return p


def post(data, url=URL, client=None, **extra):
    body = data if isinstance(data, (str, bytes)) else json.dumps(data)
    return (client or Client()).post(url, body, content_type="application/json", **extra)


@override_settings(POSTMARK_INBOUND_SECRET=SECRET)
class Webhook(AuthTestBase):
    def test_stores_the_message_text_and_attachment_names_only(self):
        r = post(payload())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "duplicate": False})
        m = InboxMessage.objects.get()
        self.assertEqual((m.from_email, m.from_name, m.subject), ("ada@acme.com", "Ada Buyer", "Quote for 20 office chairs"))
        self.assertEqual(m.to, ["info@ghprocurement.com"])
        self.assertIn("20 office chairs", m.text_body)
        self.assertEqual(m.attachments_info, [{"name": "spec.pdf", "size": 26, "type": "application/pdf"}])
        self.assertFalse(m.is_read)
        self.assertFalse(m.is_spam)

    def test_attachment_files_are_never_stored(self):
        post(payload())
        m = InboxMessage.objects.get()
        everything = json.dumps([m.text_body, m.subject, m.attachments_info, m.to, m.from_email])
        self.assertNotIn(base64.b64encode(b"%PDF-1.4 secret file bytes").decode(), everything)
        self.assertNotIn("secret file bytes", everything)

    def test_the_date_is_taken_from_the_message_and_stored_in_utc(self):
        post(payload())
        self.assertEqual(InboxMessage.objects.get().received_at, timezone.datetime(2026, 10, 9, 9, 30, tzinfo=tz.utc))

    def test_a_missing_or_bad_date_falls_back_to_now(self):
        for i, d in enumerate((None, "not a date", "")):
            post(payload(MessageID=f"id-{i}", Date=d))
        self.assertEqual(InboxMessage.objects.count(), 3)
        for m in InboxMessage.objects.all():
            self.assertLess(abs((timezone.now() - m.received_at).total_seconds()), 60)

    def test_a_retry_with_the_same_message_id_does_not_duplicate(self):
        post(payload())
        r = post(payload(Subject="changed on retry"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["duplicate"])
        self.assertEqual(InboxMessage.objects.count(), 1)
        self.assertEqual(InboxMessage.objects.get().subject, "Quote for 20 office chairs")

    def test_without_a_message_id_the_same_message_is_still_recognised(self):
        p = payload()
        del p["MessageID"]
        raw = json.dumps(p)
        post(raw)
        post(raw)
        self.assertEqual(InboxMessage.objects.count(), 1)

    def test_wrong_secret_pretends_the_page_does_not_exist(self):
        r = post(payload(), url="/api/inbound/wrong-secret/")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(InboxMessage.objects.count(), 0)

    def test_the_secret_must_match_exactly(self):
        for bad in (SECRET[:-1], SECRET + "x", SECRET.upper()):
            self.assertEqual(post(payload(), url=f"/api/inbound/{bad}/").status_code, 404)
        self.assertEqual(InboxMessage.objects.count(), 0)

    @override_settings(POSTMARK_INBOUND_SECRET="")
    def test_switched_off_when_no_secret_is_configured(self):
        self.assertEqual(post(payload(), url="/api/inbound/anything/").status_code, 404)
        self.assertEqual(post(payload(), url="/api/inbound/ /").status_code, 404)
        self.assertEqual(InboxMessage.objects.count(), 0)

    def test_only_post_is_accepted(self):
        c = Client()
        self.assertEqual(c.get(URL).status_code, 405)
        self.assertEqual(c.put(URL, "{}", content_type="application/json").status_code, 405)

    def test_bad_bodies_are_refused_cleanly(self):
        self.assertEqual(post("not json").status_code, 400)
        self.assertEqual(post("").status_code, 400)
        self.assertEqual(post("[1,2,3]").status_code, 400)
        self.assertEqual(post('"text"').status_code, 400)
        self.assertEqual(InboxMessage.objects.count(), 0)

    def test_a_nearly_empty_message_is_still_kept(self):
        self.assertEqual(post({"MessageID": "bare-1"}).status_code, 200)
        m = InboxMessage.objects.get()
        self.assertEqual((m.from_email, m.subject, m.text_body, m.attachments_info), ("", "", "", []))

    def test_sender_and_recipient_fall_back_to_the_plain_fields(self):
        p = payload()
        del p["FromFull"], p["ToFull"]
        post(p)
        m = InboxMessage.objects.get()
        self.assertEqual((m.from_email, m.to), ("ada@acme.com", ["info@ghprocurement.com"]))

    def test_an_html_only_message_becomes_readable_text(self):
        html = ("<html><head><title>x</title><style>p{color:red}</style></head><body>"
                "<script>alert('xss')</script><p>Hello <b>Sulaimon</b>,</p>"
                "<p>Price&nbsp;list &amp; terms attached.</p><div>Line one<br>Line two</div></body></html>")
        post(payload(TextBody="", HtmlBody=html))
        t = InboxMessage.objects.get().text_body
        self.assertIn("Hello Sulaimon,", t)
        self.assertIn("Price list & terms attached.", t)
        self.assertIn("Line one\nLine two", t)
        for bad in ("<", "alert", "color:red", "script", "<b>"):
            self.assertNotIn(bad, t)

    def test_the_plain_text_part_is_preferred_when_there_is_one(self):
        post(payload(TextBody="Plain version", HtmlBody="<p>HTML version</p>"))
        self.assertEqual(InboxMessage.objects.get().text_body, "Plain version")

    def test_hostile_text_is_kept_as_plain_text_not_interpreted(self):
        evil = '<img src=x onerror=alert(1)> <script>alert(2)</script>'
        post(payload(Subject=evil, TextBody=evil))
        m = InboxMessage.objects.get()
        self.assertEqual((m.subject, m.text_body), (evil, evil))  # shown escaped by the CMS (React)

    def test_spam_flags(self):
        post(payload(MessageID="a", Headers=[{"Name": "X-Spam-Status", "Value": "Yes, score=7.1"}, {"Name": "X-Spam-Score", "Value": "7.1"}]))
        post(payload(MessageID="b", Headers=[{"Name": "X-Spam-Score", "Value": "5.5"}]))
        post(payload(MessageID="c", Headers=[{"Name": "X-Spam-Status", "Value": "No"}, {"Name": "X-Spam-Score", "Value": "0.3"}]))
        post(payload(MessageID="d", Headers=[]))
        flags = {m.message_id: m.is_spam for m in InboxMessage.objects.all()}
        self.assertEqual(flags, {"a": True, "b": True, "c": False, "d": False})
        self.assertEqual(InboxMessage.objects.get(message_id="c").spam_score, 0.3)

    def test_very_long_text_is_cut(self):
        post(payload(TextBody="x" * (inbound.MAX_TEXT_CHARS + 5000)))
        self.assertEqual(len(InboxMessage.objects.get().text_body), inbound.MAX_TEXT_CHARS)

    def test_a_large_message_with_a_big_attachment_is_accepted(self):
        # bigger than Django's 2.5 MB form-data limit: mail with attachments often is
        big = base64.b64encode(b"x" * 4_000_000).decode()
        r = post(payload(Attachments=[{"Name": "big.zip", "Content": big, "ContentType": "application/zip", "ContentLength": 4_000_000}]))
        self.assertEqual(r.status_code, 200)
        m = InboxMessage.objects.get()
        self.assertEqual(m.attachments_info[0]["size"], 4_000_000)
        self.assertLess(len(json.dumps(m.attachments_info)), 300)  # just the name and numbers

    def test_a_message_over_the_cap_is_refused(self):
        r = post(payload(), CONTENT_LENGTH=str(inbound.MAX_BODY_BYTES + 1))
        self.assertEqual(r.status_code, 413)
        self.assertEqual(InboxMessage.objects.count(), 0)

    def test_no_sign_in_is_needed_because_the_secret_is_the_key(self):
        self.assertEqual(post(payload(), client=Client()).status_code, 200)

    def test_a_cms_session_token_does_not_open_the_webhook(self):
        c = Client(HTTP_AUTHORIZATION=f"Bearer {make_token(self.admin)}")
        self.assertEqual(post(payload(), url="/api/inbound/whatever/", client=c).status_code, 404)


@override_settings(POSTMARK_INBOUND_SECRET=SECRET)
class InboxApi(AuthTestBase):
    def setUp(self):
        super().setUp()
        post(payload(MessageID="m1", Subject="First", Date="Fri, 9 Oct 2026 08:00:00 +0000", TextBody="  Hello \n\n there   friend "))
        post(payload(MessageID="m2", Subject="Second", Date="Fri, 9 Oct 2026 09:00:00 +0000"))
        post(payload(MessageID="m3", Subject="Spammy", Date="Fri, 9 Oct 2026 09:30:00 +0000",
                     Headers=[{"Name": "X-Spam-Status", "Value": "Yes"}]))
        self.c = self.client_for(self.login().data["token"])

    def test_outsiders_and_customers_cannot_read_it(self):
        self.assertEqual(self.anon.get("/api/inbox/").status_code, 401)
        self.assertEqual(self.anon.get("/api/inbox/summary/").status_code, 401)
        self.assertEqual(self.client_for(make_token(self.customer)).get("/api/inbox/").status_code, 403)

    def test_list_is_newest_first_and_light(self):
        r = self.c.get("/api/inbox/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([m["subject"] for m in r.data], ["Spammy", "Second", "First"])
        self.assertNotIn("text_body", r.data[0])
        self.assertEqual(r.data[2]["snippet"], "Hello there friend")  # whitespace tidied
        self.assertEqual((r.data[0]["is_spam"], r.data[1]["is_spam"]), (True, False))
        self.assertEqual(r.data[1]["attachments_count"], 1)

    def test_unread_filter(self):
        first = InboxMessage.objects.get(message_id="m1")
        self.c.patch(f"/api/inbox/{first.pk}/", {"is_read": True}, format="json")
        r = self.c.get("/api/inbox/?unread=1")
        self.assertEqual({m["subject"] for m in r.data}, {"Second", "Spammy"})

    def test_a_message_opens_in_full(self):
        m = InboxMessage.objects.get(message_id="m2")
        r = self.c.get(f"/api/inbox/{m.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("20 office chairs", r.data["text_body"])
        self.assertEqual(r.data["to"], ["info@ghprocurement.com"])
        self.assertEqual(r.data["attachments"], [{"name": "spec.pdf", "size": 26, "type": "application/pdf"}])
        self.assertEqual(r.data["from_email"], "ada@acme.com")
        self.assertFalse(r.data["is_read"])  # reading by GET does not change anything

    def test_mark_read_and_unread(self):
        m = InboxMessage.objects.get(message_id="m2")
        r = self.c.patch(f"/api/inbox/{m.pk}/", {"is_read": True}, format="json")
        self.assertEqual((r.status_code, r.data["is_read"]), (200, True))
        self.assertTrue(InboxMessage.objects.get(pk=m.pk).is_read)
        self.assertFalse(self.c.patch(f"/api/inbox/{m.pk}/", {"is_read": False}, format="json").data["is_read"])

    def test_nothing_but_the_read_flag_can_be_changed(self):
        m = InboxMessage.objects.get(message_id="m2")
        for body in ({"subject": "edited"}, {"is_read": True, "subject": "edited"}, {}):
            self.assertEqual(self.c.patch(f"/api/inbox/{m.pk}/", body, format="json").status_code, 400, body)
        self.assertEqual(InboxMessage.objects.get(pk=m.pk).subject, "Second")

    def test_messages_cannot_be_created_replaced_or_deleted(self):
        m = InboxMessage.objects.get(message_id="m2")
        self.assertEqual(self.c.post("/api/inbox/", {"subject": "x"}, format="json").status_code, 405)
        self.assertEqual(self.c.put(f"/api/inbox/{m.pk}/", {"subject": "x"}, format="json").status_code, 405)
        self.assertEqual(self.c.delete(f"/api/inbox/{m.pk}/").status_code, 405)
        self.assertEqual(InboxMessage.objects.count(), 3)

    def test_summary_counts_unread_excluding_spam(self):
        r = self.c.get("/api/inbox/summary/")
        self.assertEqual(r.data, {"unread": 2, "total": 3})  # the spam one is not counted as unread
        first = InboxMessage.objects.get(message_id="m1")
        self.c.patch(f"/api/inbox/{first.pk}/", {"is_read": True}, format="json")
        self.assertEqual(self.c.get("/api/inbox/summary/").data["unread"], 1)

    def test_mark_all_read(self):
        r = self.c.post("/api/inbox/mark-all-read/")
        self.assertEqual(r.data, {"marked": 3})
        self.assertEqual(self.c.get("/api/inbox/summary/").data["unread"], 0)
        self.assertEqual(self.c.post("/api/inbox/mark-all-read/").data, {"marked": 0})

    def test_the_list_is_capped(self):
        from unittest import mock
        from .views import InboxView
        with mock.patch.object(InboxView, "MAX_LIST", 2):
            self.assertEqual(len(self.c.get("/api/inbox/").data), 2)

    def test_a_regular_admin_can_read_it_too(self):
        regular = mk("regular", "regular@x.com", "Regul4r-pass!!", is_staff=True)
        self.assertEqual(self.client_for(make_token(regular)).get("/api/inbox/").status_code, 200)

    def test_unknown_message(self):
        self.assertEqual(self.c.get("/api/inbox/00000000-0000-0000-0000-000000000000/").status_code, 404)
