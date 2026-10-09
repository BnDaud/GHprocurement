"""The X (Twitter) posts shown on the public website.

The API only has small quotas, so the site must never call it per visitor:

* visitors are always served the saved copy (kept in the database);
* the copy is refreshed at most every TWITTER_REFRESH_HOURS (default 12);
* only ONE request can claim a refresh (an atomic update), so a burst of
  visitors cannot cause a burst of API calls;
* a failed refresh waits before trying again, and the wait is longer when X
  says "too many requests" (it names the time) or "not allowed / cap reached";
* a hard cap on refreshes per month (TWITTER_MAX_FETCHES_PER_MONTH, default 30);
* the account id is looked up once and remembered.
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import connection
from django.utils import timezone
from requests_oauthlib import OAuth1Session

from .models import SocialFeed

logger = logging.getLogger(__name__)

FEED_KEY = "twitter"
API = "https://api.x.com/2/users/"
RETRY_MINUTES = 60  # after a failure
BLOCKED_HOURS = 24  # after "not allowed" / quota reached
TIMEOUT = 10
SEED_FILE = os.path.join(settings.BASE_DIR, "social", "twitter_post.json")
UNAVAILABLE = {"data": "Unable to fetch post"}


def _env_number(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def refresh_hours():
    return _env_number("TWITTER_REFRESH_HOURS", 12)


def max_per_month():
    return int(_env_number("TWITTER_MAX_FETCHES_PER_MONTH", 30))


def _next_month_start(now):
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


def _seed_payload():
    """The copy shipped with the code, so a brand new database has posts to show."""
    import json

    try:
        with open(SEED_FILE) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class FetchTwiter:
    def __init__(self, api_key, api_secret_key, access_token, access_token_secret):
        self.credentials = (api_key, api_secret_key, access_token, access_token_secret)

    # ----------------------------------------------------------- public
    def getTweets(self):
        """Always returns the saved copy (a dict). May start ONE background
        refresh if one is due. Never raises."""
        try:
            feed, _ = SocialFeed.objects.get_or_create(key=FEED_KEY, defaults={"payload": _seed_payload()})
            if self._claim(feed):
                self._start_refresh(feed.pk)
                if getattr(settings, "TWITTER_REFRESH_SYNC", False):
                    feed.refresh_from_db()  # (tests) the refresh already finished
            return feed.payload or UNAVAILABLE
        except Exception:  # noqa: BLE001
            logger.exception("could not read the saved tweets")
            return UNAVAILABLE

    # ----------------------------------------------------------- claim
    def _claim(self, feed):
        """True for exactly one caller when a refresh is due. The wait before
        the next try is written BEFORE the API is called, so even a crash in
        the middle cannot cause a retry storm."""
        now = timezone.now()
        if feed.next_try_at > now:
            return False
        if not all(self.credentials):
            SocialFeed.objects.filter(pk=feed.pk).update(
                next_try_at=now + timedelta(hours=BLOCKED_HOURS), last_error="Twitter keys are not set")
            return False

        month = f"{now:%Y-%m}"
        used = feed.attempts_this_month if feed.month == month else 0
        if used >= max_per_month():
            SocialFeed.objects.filter(pk=feed.pk).update(
                next_try_at=_next_month_start(now), last_error="monthly limit reached", month=month, attempts_this_month=used)
            return False

        claimed = SocialFeed.objects.filter(pk=feed.pk, next_try_at__lte=now).update(
            next_try_at=now + timedelta(minutes=RETRY_MINUTES), month=month, attempts_this_month=used + 1)
        return claimed == 1

    def _start_refresh(self, pk):
        if getattr(settings, "TWITTER_REFRESH_SYNC", False):
            self._refresh(pk)
            return

        def run():
            try:
                self._refresh(pk)
            finally:
                connection.close()

        threading.Thread(target=run, daemon=True).start()

    # ----------------------------------------------------------- refresh
    def _refresh(self, pk):
        feed = SocialFeed.objects.get(pk=pk)
        session = OAuth1Session(*self.credentials)
        try:
            if not feed.external_id:
                res = session.get(f"{API}me", timeout=TIMEOUT)
                if res.status_code != 200:
                    return self._failed(feed, res)
                feed.external_id = str(res.json()["data"]["id"])
                feed.save(update_fields=["external_id"])  # looked up once, then remembered
            res = session.get(
                f"{API}{feed.external_id}/tweets",
                params={
                    "expansions": "attachments.media_keys",
                    "media.fields": "url,type,width,height,preview_image_url,variants",
                    "tweet.fields": "attachments",
                },
                timeout=TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001  (network trouble: keep the old copy, wait)
            logger.warning("tweet refresh failed: %s", exc)
            SocialFeed.objects.filter(pk=pk).update(last_error=str(exc)[:300])
            return
        if res.status_code != 200:
            return self._failed(feed, res)
        try:
            data = res.json()
        except ValueError:
            return self._failed(feed, res)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            return self._failed(feed, res)

        now = timezone.now()
        SocialFeed.objects.filter(pk=pk).update(
            payload=data, fetched_at=now, last_error="", next_try_at=now + timedelta(hours=refresh_hours()))

    def _failed(self, feed, res):
        """Keep serving the old copy and wait, for as long as X asks."""
        now = timezone.now()
        status = res.status_code
        wait = timedelta(minutes=RETRY_MINUTES)
        if status == 429:
            try:
                reset = datetime.fromtimestamp(int(res.headers.get("x-rate-limit-reset", 0)), tz=dt_timezone.utc)
            except (TypeError, ValueError, OverflowError):
                reset = now
            wait = max(wait, reset - now)
        elif status in (401, 402, 403):
            wait = timedelta(hours=BLOCKED_HOURS)
        elif status == 404 and feed.external_id:
            SocialFeed.objects.filter(pk=feed.pk).update(external_id="")  # look the account up again next time
        logger.warning("tweet refresh answered %s; trying again in %s", status, wait)
        SocialFeed.objects.filter(pk=feed.pk).update(next_try_at=now + wait, last_error=f"HTTP {status}")
