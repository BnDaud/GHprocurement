"""Questions and replies on an order, and the 'needs attention' list."""
from datetime import timedelta

from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AuditLog, MetaData, OrderMessage, RFQ, RFQUpdate, User, Catalog
from .test_quote import SendingQuotes
from .test_tracking import customer_client


class Messages(SendingQuotes):
    def setUp(self):
        super().setUp()
        self.ada = User.objects.get(email="ada@acme.com")
        self.me = customer_client(self.ada)
        self.cust = f"/api/customer/requests/{self.rfq.pk}/messages/"
        self.team = f"/api/rfqs/{self.rfq.pk}/messages/"

    def test_a_customer_can_ask_and_the_team_is_emailed(self):
        mail.outbox.clear()
        r = self.me.post(self.cust, {"body": "Can the chairs be dark grey?"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["sender"], r.data["body"]), ("customer", "Can the chairs be dark grey?"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("sent a message", mail.outbox[0].subject)
        self.assertIn("dark grey", mail.outbox[0].alternatives[0][0])
        self.assertIn("/orders/", mail.outbox[0].alternatives[0][0])

    def test_an_empty_or_huge_message_is_refused(self):
        self.assertEqual(self.me.post(self.cust, {"body": "   "}, format="json").status_code, 400)
        self.assertEqual(self.me.post(self.cust, {"body": "x" * 2001}, format="json").status_code, 400)
        self.assertEqual(OrderMessage.objects.count(), 0)

    def test_the_team_reply_reaches_the_customer_by_email_and_on_the_page(self):
        self.me.post(self.cust, {"body": "Question?"}, format="json")
        mail.outbox.clear()
        r = self.super.post(self.team, {"body": "Yes, it is possible."}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(mail.outbox[0].to, ["ada@acme.com"])
        self.assertIn("a reply from GH Procurement", mail.outbox[0].subject)
        detail = self.me.get(f"/api/customer/requests/{self.rfq.pk}/").data
        self.assertEqual(detail["unread_messages"], 1)
        thread = self.me.get(self.cust).data
        self.assertEqual([m["sender"] for m in thread], ["customer", "team"])
        self.assertEqual(self.me.get(f"/api/customer/requests/{self.rfq.pk}/").data["unread_messages"], 0)  # reading marks them read
        self.assertTrue(AuditLog.objects.filter(target_type="order message").exists())

    def test_customers_never_see_which_admin_replied(self):
        self.super.post(self.team, {"body": "Hi"}, format="json")
        self.assertNotIn("author", self.me.get(self.cust).data[0])
        self.assertIn("author", self.super.get(self.team).data[0])

    def test_the_team_sees_unread_questions_and_reading_clears_them(self):
        self.me.post(self.cust, {"body": "One"}, format="json")
        self.me.post(self.cust, {"body": "Two"}, format="json")
        self.assertEqual(self.super.get(f"/api/rfqs/{self.rfq.pk}/").data["unread_messages"], 2)
        self.super.get(self.team)
        self.assertEqual(self.super.get(f"/api/rfqs/{self.rfq.pk}/").data["unread_messages"], 0)

    def test_nobody_else_can_read_or_write_a_thread(self):
        self.me.post(self.cust, {"body": "Private"}, format="json")
        self.assertIn(APIClient().get(self.cust).status_code, (401, 403))
        self.submit(email="bob@other.com", name="Bob Eze", item="x")
        bob = customer_client(User.objects.get(email="bob@other.com"))
        self.assertEqual(bob.get(self.cust).status_code, 404)
        self.assertEqual(bob.post(self.cust, {"body": "hi"}, format="json").status_code, 404)
        self.assertIn(self.me.get(self.team).status_code, (401, 403))  # a customer is not an admin
        self.assertIn(self.me.post(self.team, {"body": "x"}, format="json").status_code, (401, 403))

    def test_a_flood_of_messages_is_slowed_down(self):
        for _ in range(30):
            self.assertEqual(self.me.post(self.cust, {"body": "hello"}, format="json").status_code, 201)
        self.assertEqual(self.me.post(self.cust, {"body": "hello"}, format="json").status_code, 429)


class Attention(SendingQuotes):
    def items(self):
        r = self.super.get("/api/attention/")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["items"]

    def age(self, hours):
        RFQ.objects.filter(pk=self.rfq.pk).update(created_at=timezone.now() - timedelta(hours=hours))

    def test_a_fresh_request_is_not_listed(self):
        self.assertEqual(self.items(), [])

    def test_a_request_without_a_quotation_turns_amber_then_red(self):
        self.age(30)
        item = self.items()[0]
        self.assertEqual((item["kind"], item["severity"], item["reference"]), ("quote", "amber", self.rfq.reference))
        self.age(60)
        self.assertEqual(self.items()[0]["severity"], "red")

    def test_a_quoted_request_leaves_the_list(self):
        self.age(60)
        self.super.post(f"/api/rfqs/{self.rfq.pk}/updates/", {"stage": "quoted", "headline": "Quotation sent"}, format="json")
        self.assertEqual([i for i in self.items() if i["kind"] == "quote"], [])

    def test_an_accepted_quotation_is_listed_until_the_team_moves_on(self):
        self.super.post(f"/api/rfqs/{self.rfq.pk}/updates/", {"stage": "quoted", "headline": "Quotation sent"}, format="json")
        customer_client(User.objects.get(email="ada@acme.com")).post(f"/api/customer/requests/{self.rfq.pk}/accept/")
        self.assertEqual([i["kind"] for i in self.items()], ["accepted"])
        self.super.post(f"/api/rfqs/{self.rfq.pk}/updates/", {"stage": "sourcing", "headline": "Sourcing"}, format="json")
        self.assertEqual([i for i in self.items() if i["kind"] == "accepted"], [])

    def test_an_unanswered_question_is_listed_until_it_is_read(self):
        customer_client(User.objects.get(email="ada@acme.com")).post(f"/api/customer/requests/{self.rfq.pk}/messages/", {"body": "Dark grey?"}, format="json")
        item = [i for i in self.items() if i["kind"] == "question"][0]
        self.assertIn("Dark grey", item["detail"])
        self.super.post(f"/api/rfqs/{self.rfq.pk}/messages/", {"body": "Yes."}, format="json")
        self.assertEqual([i for i in self.items() if i["kind"] == "question"], [])

    def test_an_order_that_has_not_moved_for_a_week_is_listed(self):
        self.super.post(f"/api/rfqs/{self.rfq.pk}/updates/", {"stage": "sourcing", "headline": "Sourcing"}, format="json")
        RFQUpdate.objects.filter(rfq=self.rfq).update(created_at=timezone.now() - timedelta(days=9))
        item = [i for i in self.items() if i["kind"] == "stalled"][0]
        self.assertIn("9 days", item["title"])

    def test_only_admins_can_see_it(self):
        self.assertIn(APIClient().get("/api/attention/").status_code, (401, 403))
        self.assertIn(customer_client(User.objects.get(email="ada@acme.com")).get("/api/attention/").status_code, (401, 403))


class EstimatorData(SendingQuotes):
    def test_the_estimator_settings_and_product_weights_reach_the_public_site(self):
        MetaData.objects.all().delete()
        MetaData.objects.create(metaIntro="x", metaDescription="y", email="a@b.com", phone="+2348057962920", office="Lagos",
                                air_rate_per_kg="6.50", sea_rate_per_kg="1.20", duty_percent="20", estimate_range_percent=10,
                                whatsapp_number="+2348000000009")
        Catalog.objects.create(name="Power Bank", description="d", min_quantity=200, price="15.00", weight_kg="0.350")
        d = APIClient().get("/api/alldata/?format=json").json()
        m = d["metadata"][0]
        self.assertEqual((m["air_rate_per_kg"], m["sea_rate_per_kg"], m["duty_percent"], m["estimate_range_percent"], m["whatsapp_number"]),
                         ("6.50", "1.20", "20.00", 10, "+2348000000009"))
        self.assertEqual(d["catalogs"][0]["weight_kg"], "0.350")
