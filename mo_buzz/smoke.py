"""Local smoke tests for mo_buzz — run one layer at a time, cheapest first.

Usage:
    uv run python smoke.py rss [subreddit ...]      # no creds needed (public feeds)
    uv run python smoke.py reddit [subreddit ...]   # needs REDDIT_* only
    uv run python smoke.py arbiter                   # needs MODAIC_* (arbiter must be pushed)
    uv run python smoke.py slack [example_id]        # needs SLACK_BOT_TOKEN + SLACK_CHANNEL_ID
    uv run python smoke.py policy [show|set ...]      # view/tune the surfacing policy
    uv run python smoke.py pipeline                  # how to run the full Modal job

Load your env first, e.g.:  set -a; source .env; set +a
"""

import os
import sys

SAMPLE = {
    "subreddit": "LLMDevs",
    "title": "How do I grade my LLM outputs reliably?",
    "body": "Looking for an LLM-as-a-judge with calibrated confidence, not just vibes.",
    "link": "https://www.reddit.com/r/LLMDevs/comments/example/",
}


def _load_dotenv(filename: str = ".env") -> None:
    """Load KEY=VALUE lines from a sibling .env into os.environ (real env wins)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if val.startswith("#"):
                val = ""  # leftover placeholder comment, not a real value
            elif len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                val = val[1:-1]  # fully quoted value
            else:
                val = val.split(" #", 1)[0].rstrip().strip("\"'")  # drop inline comment + stray quotes
            os.environ.setdefault(key, val)


def _require(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)} (see .env.example)")


def cmd_rss(args: list[str]) -> None:
    # No credentials required — public Atom feeds.
    from reddit_rss import fetch_new_posts_rss

    subs = args or ["LLMDevs", "LocalLLaMA"]
    posts = fetch_new_posts_rss(subs, lookback_hours=48, limit_per_sub=10)
    print(f"Fetched {len(posts)} posts via RSS from {subs}")
    for p in posts[:10]:
        print(f"  r/{p.subreddit}  {p.title[:70]!r}  {p.url}")


def cmd_reddit(args: list[str]) -> None:
    _require("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
    from reddit_client import fetch_new_posts, make_reddit

    subs = args or ["LLMDevs", "LocalLLaMA"]
    reddit = make_reddit(
        os.environ["REDDIT_CLIENT_ID"],
        os.environ["REDDIT_CLIENT_SECRET"],
        os.environ["REDDIT_USER_AGENT"],
    )
    posts = fetch_new_posts(reddit, subs, lookback_hours=48, limit_per_sub=5)
    print(f"Fetched {len(posts)} posts from {subs}")
    for p in posts[:10]:
        print(f"  r/{p.subreddit}  {p.title[:70]!r}  {p.url}")


def cmd_arbiter(args: list[str]) -> None:
    _require("MODAIC_TOKEN", "MODAIC_USER_OR_ORG")
    from modaic_client import Arbiter

    repo = f"{os.environ['MODAIC_USER_OR_ORG']}/reddit-post-judge"
    arbiter = Arbiter(repo)

    print(f"Single predict against {repo} ...")
    pred = arbiter(**SAMPLE, compute_confidence=True)
    print("  relevance  =", pred.output.relevance)
    print("  confidence =", pred.confidence)
    print("  reasoning  =", pred.reasoning)
    print("  example_id =", pred.example_id, "  <- reuse this for `smoke.py slack`")

    print("\nBatch predict_all (2 examples, with confidence) ...")
    results = arbiter.predict_all(
        examples=[
            {"input": SAMPLE},
            {"input": {**SAMPLE, "title": "Best AI meme of the day", "body": "lol"}},
        ],
        compute_confidence=True,
        wait_for="scores",
        show_progress=False,
    )
    for row in results:
        p = row.predictions[0]
        print("  ", row.example_id, "->", p.output.relevance, f"(confidence {p.confidence})")


def cmd_slack(args: list[str]) -> None:
    # Mirrors slack_app.post_flagged but reads channel/token directly so this
    # layer needs only the Slack creds (not the full Settings).
    _require("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID")
    from slack_sdk import WebClient

    from arbiter_judge import JudgedPost
    from reddit_client import RedditPost
    from slack_app import build_message_blocks

    example_id = args[0] if args else None
    if not example_id:
        print("No example_id given -> posting WITHOUT annotate buttons.")
        print("Run `uv run python smoke.py arbiter` first and pass its example_id.")

    jp = JudgedPost(
        post=RedditPost(
            subreddit=SAMPLE["subreddit"],
            post_id="t3_smoke",
            title=SAMPLE["title"],
            body=SAMPLE["body"],
            url=SAMPLE["link"],
            author="smoke_user",
            created_utc=0.0,
        ),
        relevance="relevant",
        reasoning="Asking about LLM-as-a-judge + calibrated confidence — core Modaic use case.",
        confidence=0.87,
        example_id=example_id,
        prediction_id=None,
    )
    WebClient(token=os.environ["SLACK_BOT_TOKEN"]).chat_postMessage(
        channel=os.environ["SLACK_CHANNEL_ID"],
        blocks=build_message_blocks(jp),
        text="mo_buzz smoke test",
    )
    print("Posted to channel", os.environ["SLACK_CHANNEL_ID"])


def cmd_policy(args: list[str]) -> None:
    # Show or tune the surfacing policy. `set`/`reset` write to the shared
    # modal.Dict, so they affect the deployed cron too (needs Modal auth).
    import policy

    print(policy.apply_command(" ".join(args)))


def cmd_pipeline(args: list[str]) -> None:
    print("Run the full pipeline in Modal (needs the `mo-buzz` secret to exist):")
    print("    uv run modal run app.py        # triggers daily_scan once")
    print("    uv run modal app logs mo-buzz  # follow output")
    print("\nFor the Slack interactivity endpoint:")
    print("    uv run modal serve app.py      # live URL -> set as Slack Request URL")


COMMANDS = {
    "rss": cmd_rss,
    "reddit": cmd_reddit,
    "arbiter": cmd_arbiter,
    "slack": cmd_slack,
    "policy": cmd_policy,
    "pipeline": cmd_pipeline,
}


def main() -> None:
    _load_dotenv()
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"Usage: python smoke.py [{'|'.join(COMMANDS)}] [args...]")
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
