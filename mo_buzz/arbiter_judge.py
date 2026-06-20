"""Run Reddit posts through the Modaic `reddit-post-judge` arbiter.

Uses the batch predictions endpoint via `Arbiter.predict_all`: every post is
ingested as a logged example and judged server-side in one job (so each post
gets an `example_id` we can later annotate from Slack). Results are mapped back
to their posts by the unique `link`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from modaic_client import Arbiter

from config import FLAGGED_ACTIONS
from reddit_client import RedditPost

logger = logging.getLogger(__name__)

# Modaic's batch predictions endpoint accepts at most 1000 examples per call.
BATCH_SIZE = 1000


@dataclass
class JudgedPost:
    post: RedditPost
    action: str | None
    reasoning: str
    example_id: str | None
    prediction_id: str | None


def _to_example(post: RedditPost) -> dict:
    return {
        "input": {
            "subreddit": post.subreddit,
            "title": post.title,
            "body": post.body,
            "link": post.url,
        },
        "alt_id": post.post_id,
    }


def judge_posts(arbiter_repo: str, posts: list[RedditPost]) -> list[JudgedPost]:
    """Batch-judge every post and return only those worth responding to."""
    if not posts:
        return []

    arbiter = Arbiter(arbiter_repo)
    by_link = {post.url: post for post in posts}
    flagged: list[JudgedPost] = []

    for start in range(0, len(posts), BATCH_SIZE):
        chunk = posts[start : start + BATCH_SIZE]
        try:
            # wait_for="predictions" (the default) blocks and returns a
            # list[BatchExampleResult] once predictions are persisted.
            results = arbiter.predict_all(
                examples=[_to_example(post) for post in chunk],
                wait_for="predictions",
                show_progress=False,
            )
        except Exception:  # noqa: BLE001 - skip a failed chunk, keep the rest
            logger.exception("Batch prediction failed for chunk at offset %d", start)
            continue

        for result in results:
            if not result.predictions:
                continue
            pred = result.predictions[0]  # single arbiter -> single prediction
            post = by_link.get((result.input or {}).get("link"))
            if post is None:
                logger.warning("Unmatched batch result (example_id=%s)", result.example_id)
                continue
            action = getattr(pred.output, "action", None)
            if action in FLAGGED_ACTIONS:
                flagged.append(
                    JudgedPost(
                        post=post,
                        action=action,
                        reasoning=pred.reasoning or "",
                        example_id=pred.example_id,
                        prediction_id=pred.prediction_id,
                    )
                )

    return flagged
