"""Modal app for mo_buzz: a daily Reddit scan + Slack annotation loop.

Deploy (cron + interactivity endpoint go live):
    uv run modal deploy app.py

Trigger a scan immediately (for testing):
    uv run modal run app.py

Two functions:
  - daily_scan: runs on a daily cron -> reads subreddits.yaml -> fetches new
    posts -> judges them with the reddit-post-judge arbiter -> posts the ones
    worth responding to into Slack.
  - slack_interactions: a web endpoint receiving Slack button/modal callbacks
    and writing annotations back to Modaic. Set this URL as the app's
    "Interactivity" Request URL in Slack: https://<...>.modal.run/slack/interactions
"""

import logging

import modal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mo_buzz")

app = modal.App("mo-buzz")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .uv_pip_install(
        "praw>=7.7",
        "feedparser>=6.0",
        "pyyaml>=6",
        # Lightweight client only (no dspy/litellm) -> fast cold start for the
        # latency-sensitive Slack endpoint. Bump to match your hub version.
        "modaic-client>=0.42.1",
        "slack-sdk>=3.27",
        "pydantic-settings>=2.6",
        "fastapi>=0.115",
    )
    .add_local_file("subreddits.yaml", "/root/subreddits.yaml")
    .add_local_file("surfacing.json", "/root/surfacing.json")
    .add_local_python_source(
        "config", "reddit_client", "reddit_rss", "arbiter_judge", "slack_app", "policy"
    )
)

# One Modal secret holds every env var; see .env.example for the keys.
secret = modal.Secret.from_name("mo-buzz")


def load_subreddits(path: str = "/root/subreddits.yaml") -> list[str]:
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return [s for s in data.get("subreddits", []) if s]


@app.function(
    image=image,
    secrets=[secret],
    schedule=modal.Cron("0 13 * * *"),  # daily at 13:00 UTC
    timeout=3600,
)
def daily_scan() -> dict:
    from arbiter_judge import judge_posts
    from config import get_settings
    from slack_app import post_flagged

    settings = get_settings()
    subreddits = load_subreddits()

    if settings.reddit_source == "rss":
        from reddit_rss import fetch_new_posts_rss

        posts = fetch_new_posts_rss(
            subreddits, settings.lookback_hours, settings.posts_per_subreddit, settings.reddit_user_agent
        )
    else:
        from reddit_client import fetch_new_posts, make_reddit

        if not (settings.reddit_client_id and settings.reddit_client_secret):
            raise RuntimeError(
                "REDDIT_SOURCE=api requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET "
                "(or set REDDIT_SOURCE=rss to use public feeds)."
            )
        reddit = make_reddit(
            settings.reddit_client_id, settings.reddit_client_secret, settings.reddit_user_agent
        )
        posts = fetch_new_posts(
            reddit, subreddits, settings.lookback_hours, settings.posts_per_subreddit
        )

    logger.info("Fetched %d new posts across %d subreddits", len(posts), len(subreddits))

    flagged = judge_posts(settings.arbiter_repo, posts)
    logger.info("Flagged %d posts worth responding to", len(flagged))

    for jp in flagged:
        try:
            post_flagged(jp)
        except Exception:
            logger.exception("Failed to post to Slack: %s", jp.post.url)

    return {"scanned": len(posts), "flagged": len(flagged)}


@app.function(image=image, secrets=[secret], timeout=60, min_containers=1)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def slack_interactions():
    # Kept warm (min_containers=1) so Slack's 3s interactivity deadline is met.
    from slack_app import build_fastapi_app

    return build_fastapi_app()


@app.local_entrypoint()
def main():
    result = daily_scan.remote()
    print(result)
