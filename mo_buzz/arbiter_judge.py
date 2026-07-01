"""Run Reddit posts through the Modaic `reddit-post-judge` arbiter.

Uses the batch predictions endpoint via `Arbiter.predict_all` with confidence
scoring enabled, then applies the surfacing policy (see policy.py) to decide
which posts reach Slack. Each post becomes a logged example (annotatable later)
and carries a calibrated confidence score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from modaic_client import Arbiter

from policy import get_policy, should_surface
from reddit_client import RedditPost

logger = logging.getLogger(__name__)

# Modaic's batch predictions endpoint accepts at most 1000 examples per call.
BATCH_SIZE = 1000


@dataclass
class JudgedPost:
    post: RedditPost
    relevance: str | None
    reasoning: str
    confidence: float | None
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
    """Batch-judge every post (with confidence) and return those the policy surfaces."""
    if not posts:
        return []

    arbiter = Arbiter(arbiter_repo)
    policy = get_policy()
    by_link = {post.url: post for post in posts}
    surfaced: list[JudgedPost] = []

    for start in range(0, len(posts), BATCH_SIZE):
        chunk = posts[start : start + BATCH_SIZE]
        try:
            # compute_confidence + wait_for="scores" blocks until calibrated
            # confidence is available on every prediction.
            results = arbiter.predict_all(
                examples=[_to_example(post) for post in chunk],
                compute_confidence=True,
                wait_for="scores",
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
            relevance = getattr(pred.output, "relevance", None)
            confidence = pred.confidence
            if should_surface(relevance, confidence, policy):
                surfaced.append(
                    JudgedPost(
                        post=post,
                        relevance=relevance,
                        reasoning=pred.reasoning or "",
                        confidence=confidence,
                        example_id=pred.example_id,
                        prediction_id=pred.prediction_id,
                    )
                )

    return surfaced
