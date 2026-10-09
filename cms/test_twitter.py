"""The Twitter feed must never call the API per visitor, and must back off."""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import fetchtwitter
from .fetchtwitter import FetchTwiter
from .models import SocialFeed

KEYS = ("k", "ks", "t", "ts")
GOOD = {"data": [{"id": "1", "text": "hello"}], "includes": {"media": []}}


def reply(status=200, body=None, headers=None):
    r = mock.Mock()
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = body if body is not None else {}
    return r


class FakeSession:
    """Stands in for OAuth1Session; records every call so we can count them."""

    calls = []
    script = []  # replies handed out in order; the last one repeats

    def __init__(self, *a, **k):
        pass

    def get(self, url, **kw):
        FakeSession.calls.append(url)
        i = min(len(FakeSession.calls) - 1, len(FakeSession.script) - 1)
        return FakeSession.script[i]


def tweets_calls():
    return [u for u in FakeSession.calls if u.endswith("/tweets")]


class TwitterFeed(TestCase):
    def setUp(self):
        FakeSession.calls = []
        FakeSession.script = [reply(200, {"data": {"id": "42"}}), reply(200, GOOD)]
        p = mock.patch.object(fetchtwitter, "OAuth1Session", FakeSession)
        p.start()
        self.addCleanup(p.stop)
        self.api = FetchTwiter(*KEYS)

    def feed(self):
        return SocialFeed.objects.get(key="twitter")

    def due(self):
        SocialFeed.objects.filter(key="twitter").update(next_try_at=timezone.now() - timedelta(seconds=1))

    # -- the basics
    def test_first_visit_fetches_once_and_saves_the_posts(self):
        data = self.api.getTweets()
        self.assertIsInstance(data, dict)
        self.assertEqual(len(FakeSession.calls), 2)  # account id lookup + the posts
        self.assertEqual(self.feed().payload, GOOD)
        self.assertEqual(self.feed().external_id, "42")

    def test_visitors_inside_the_window_never_call_the_api(self):
        self.api.getTweets()
        FakeSession.calls.clear()
        for _ in range(50):
            self.assertEqual(self.api.getTweets(), GOOD)
        self.assertEqual(FakeSession.calls, [])

    def test_refresh_waits_the_configured_hours(self):
        self.api.getTweets()
        gap = self.feed().next_try_at - self.feed().fetched_at
        self.assertAlmostEqual(gap.total_seconds(), 12 * 3600, delta=5)
        with mock.patch.dict("os.environ", {"TWITTER_REFRESH_HOURS": "24"}):
            self.due(); FakeSession.calls.clear(); FakeSession.script = [reply(200, GOOD)]
            self.api.getTweets()
        self.assertAlmostEqual((self.feed().next_try_at - self.feed().fetched_at).total_seconds(), 24 * 3600, delta=5)

    def test_account_id_is_looked_up_only_once(self):
        self.api.getTweets()
        self.due(); FakeSession.calls.clear()
        self.api.getTweets()
        self.assertEqual(len(tweets_calls()), 1)
        self.assertEqual([u for u in FakeSession.calls if u.endswith("/me")], [])

    def test_a_brand_new_database_still_shows_the_posts_that_ship_with_the_code(self):
        FakeSession.script = [reply(500)]
        data = self.api.getTweets()
        self.assertTrue(data.get("data"))  # from social/twitter_post.json, not "Unable to fetch post"

    # -- failures must back off
    def test_failure_keeps_the_old_posts_and_waits_before_trying_again(self):
        self.api.getTweets()
        self.due()
        FakeSession.script = [reply(500)]; FakeSession.calls.clear()
        self.assertEqual(self.api.getTweets(), GOOD)  # old copy still served
        made = len(FakeSession.calls)
        for _ in range(30):
            self.api.getTweets()
        self.assertEqual(len(FakeSession.calls), made)  # no retry storm
        self.assertEqual(self.feed().last_error, "HTTP 500")
        gap = self.feed().next_try_at - timezone.now()
        self.assertTrue(timedelta(minutes=55) < gap <= timedelta(minutes=61))

    def test_rate_limit_waits_until_the_time_x_names(self):
        self.api.getTweets()
        self.due()
        reset = int((timezone.now() + timedelta(hours=3)).timestamp())
        FakeSession.script = [reply(429, headers={"x-rate-limit-reset": str(reset)})]; FakeSession.calls.clear()
        self.api.getTweets()
        self.assertTrue(abs((self.feed().next_try_at - timezone.now()) - timedelta(hours=3)) < timedelta(minutes=1))

    def test_not_allowed_or_quota_reached_waits_a_day(self):
        for status in (401, 402, 403):
            SocialFeed.objects.all().delete()
            FakeSession.calls.clear(); FakeSession.script = [reply(status)]
            self.api.getTweets()
            gap = self.feed().next_try_at - timezone.now()
            self.assertTrue(timedelta(hours=23) < gap <= timedelta(hours=24, minutes=1), status)

    def test_network_error_is_survived(self):
        class Boom:
            def __init__(self, *a, **k): pass
            def get(self, *a, **k): raise ConnectionError("down")
        with mock.patch.object(fetchtwitter, "OAuth1Session", Boom):
            data = self.api.getTweets()
        self.assertIsInstance(data, dict)
        self.assertIn("down", self.feed().last_error)
        self.assertTrue(self.feed().next_try_at > timezone.now())  # still waits

    def test_garbage_answer_does_not_replace_the_good_copy(self):
        self.api.getTweets()
        self.due()
        FakeSession.script = [reply(200, {"data": "oops"})]
        self.assertEqual(self.api.getTweets(), GOOD)
        self.assertEqual(self.feed().payload, GOOD)

    def test_a_404_makes_it_look_the_account_up_again(self):
        self.api.getTweets()
        self.due()
        FakeSession.script = [reply(404)]
        self.api.getTweets()
        self.assertEqual(self.feed().external_id, "")

    # -- caps and races
    def test_only_one_caller_can_claim_a_refresh(self):
        self.api.getTweets()
        self.due()
        feed = self.feed()
        results = [self.api._claim(feed) for _ in range(5)]  # five visitors at the same instant
        self.assertEqual(results.count(True), 1)

    def test_monthly_cap_stops_refreshes_until_next_month(self):
        with mock.patch.dict("os.environ", {"TWITTER_MAX_FETCHES_PER_MONTH": "3"}):
            for _ in range(3):
                self.due(); self.api.getTweets()
            FakeSession.calls.clear()
            self.due(); self.api.getTweets()
            self.assertEqual(FakeSession.calls, [])
            self.assertEqual(self.feed().last_error, "monthly limit reached")
            self.assertEqual(self.feed().next_try_at.day, 1)  # wakes up on the 1st
            # a new month starts the count again
            SocialFeed.objects.filter(key="twitter").update(month="2000-01", next_try_at=timezone.now() - timedelta(seconds=1))
            self.api.getTweets()
            self.assertTrue(FakeSession.calls)

    def test_missing_keys_never_call_the_api(self):
        FakeSession.calls.clear()
        data = FetchTwiter(None, None, None, None).getTweets()
        self.assertIsInstance(data, dict)
        self.assertEqual(FakeSession.calls, [])

    def test_the_public_endpoint_always_answers(self):
        with mock.patch("cms.views.FetchTwiter") as tw:
            tw.return_value.getTweets.return_value = GOOD
            r = self.client.get("/api/alldata/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["twitter"], GOOD)
