"""Fetch recent Reddit posts via PRAW (read-only).

Uses an installed/script app's client id + secret for read-only access to
public subreddits -- no user login required.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import praw

logger = logging.getLogger(__name__)


@dataclass
class RedditPost:
    subreddit: str
    post_id: str
    title: str
    body: str
    url: str  # full https URL to the post
    author: str
    created_utc: float


def make_reddit(client_id: str, client_secret: str, user_agent: str) -> praw.Reddit:
    """Build a read-only PRAW client."""
    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        check_for_async=False,
    )


def fetch_new_posts(
    reddit: praw.Reddit,
    subreddits: list[str],
    lookback_hours: int = 24,
    limit_per_sub: int = 50,
) -> list[RedditPost]:
    """Return posts created within the last `lookback_hours` across `subreddits`.

    `.new()` yields newest-first, so we stop scanning a subreddit as soon as we
    hit a post older than the cutoff. A failure on one subreddit (e.g. a typo'd
    name or a private sub) is logged and skipped, never fatal.
    """
    cutoff = time.time() - lookback_hours * 3600
    posts: list[RedditPost] = []

    for name in subreddits:
        try:
            for submission in reddit.subreddit(name).new(limit=limit_per_sub):
                if submission.created_utc < cutoff:
                    break  # newest-first: everything after this is older too
                posts.append(
                    RedditPost(
                        subreddit=name,
                        post_id=submission.id,
                        title=submission.title or "",
                        body=submission.selftext or "",
                        url=f"https://www.reddit.com{submission.permalink}",
                        author=str(submission.author) if submission.author else "[deleted]",
                        created_utc=float(submission.created_utc),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - one bad sub shouldn't kill the run
            logger.warning("Failed to fetch r/%s: %s", name, exc)
            continue

    return posts
