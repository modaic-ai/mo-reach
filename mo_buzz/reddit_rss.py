"""Alternate Reddit fetcher using public RSS/Atom feeds (no API creds needed).

Reddit exposes a per-subreddit Atom feed at
    https://www.reddit.com/r/<sub>/new/.rss
which requires no OAuth -- handy while waiting on API approval. It returns the
same `RedditPost` objects as `reddit_client.fetch_new_posts`, so it's a drop-in
swap in the pipeline (selected via REDDIT_SOURCE=rss).

Trade-offs vs the API: feeds only expose the newest ~25-100 items, the post
body comes back as rendered HTML (we strip tags), and Reddit rate-limits
aggressively -- so we send a descriptive User-Agent and fetch sequentially.
"""

from __future__ import annotations

import calendar
import html
import logging
import re
import time

import feedparser
import httpx

from reddit_client import RedditPost

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "mo_buzz/0.1 RSS reader (by Modaic)"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: str) -> str:
    return html.unescape(_TAG_RE.sub("", raw or "")).strip()


def _entry_epoch(entry) -> float | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return calendar.timegm(parsed) if parsed else None


def _entry_body(entry) -> str:
    if entry.get("content"):
        return _strip_html(entry["content"][0].get("value", ""))
    return _strip_html(entry.get("summary", ""))


def _fetch_feed(http: httpx.Client, url: str, max_retries: int = 4) -> httpx.Response:
    """GET a feed, retrying on 429 and honoring the Retry-After header.

    Reddit rate-limits RSS aggressively from shared/datacenter IPs, so a single
    429 is expected; we back off and retry rather than dropping the subreddit.
    """
    resp: httpx.Response | None = None
    for attempt in range(max_retries):
        resp = http.get(url)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait = min(float(retry_after) if retry_after else 2.0 * 2**attempt, 60.0)
            logger.info("429 for %s; backing off %.0fs", url, wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    assert resp is not None
    resp.raise_for_status()  # exhausted retries on 429 -> surface it
    return resp


def fetch_new_posts_rss(
    subreddits: list[str],
    lookback_hours: int = 24,
    limit_per_sub: int = 50,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 15.0,
    request_delay: float = 2.0,
) -> list[RedditPost]:
    """Return posts created within the last `lookback_hours`, via RSS feeds.

    Feeds are newest-first, so we stop reading a feed at the first stale entry.
    A failure on one subreddit (404, persistent 429, parse error) is logged and
    skipped. `request_delay` seconds are slept between subreddits to stay under
    Reddit's RSS rate limit.
    """
    cutoff = time.time() - lookback_hours * 3600
    posts: list[RedditPost] = []

    with httpx.Client(
        headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True
    ) as http:
        for i, name in enumerate(subreddits):
            if i and request_delay:
                time.sleep(request_delay)
            url = f"https://www.reddit.com/r/{name}/new/.rss?limit={limit_per_sub}"
            try:
                resp = _fetch_feed(http, url)
            except Exception as exc:  # noqa: BLE001 - skip one bad/blocked feed
                logger.warning("RSS fetch failed for r/%s: %s", name, exc)
                continue

            for entry in feedparser.parse(resp.content).entries:
                created = _entry_epoch(entry)
                if created is not None and created < cutoff:
                    break  # newest-first: the rest are older too

                post_id = (entry.get("id") or "").split("/")[-1].removeprefix("t3_")
                author = (entry.get("author") or "").removeprefix("/u/").strip() or "[deleted]"
                posts.append(
                    RedditPost(
                        subreddit=name,
                        post_id=post_id,
                        title=entry.get("title", ""),
                        body=_entry_body(entry),
                        url=entry.get("link", ""),
                        author=author,
                        created_utc=created or 0.0,
                    )
                )

    return posts
