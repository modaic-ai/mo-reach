# mo_buzz

A Reddit-monitoring bot for Modaic GTM workflows. Every day it scans configured
subreddits for recent posts, runs them through a Modaic Arbiter with calibrated
confidence, applies a surfacing policy, and posts the surfaced items into Slack.
From Slack, teammates can annotate the judge; those annotations flow back to
Modaic Hub.

## How It Works

1. `app.py` runs a Modal cron every day at 13:00 UTC.
2. It reads `subreddits.yaml` from the Modal image.
3. It fetches recent Reddit posts using either:
   - `REDDIT_SOURCE=rss`: public Reddit Atom feeds, no Reddit credentials.
   - `REDDIT_SOURCE=api`: PRAW and a Reddit script app client ID/secret.
4. `arbiter_judge.py` batches posts through `<org>/reddit-post-judge` with
   `compute_confidence=True`.
5. `policy.py` decides what reaches Slack using `surfacing.json` plus any live
   overrides stored in the Modal Dict named `mo-buzz-policy`.
6. `slack_app.py` posts surfaced items and handles Slack annotation callbacks.

The default surfacing policy posts all `relevant` predictions and also posts
low-confidence `not_relevant` predictions below 50% confidence, so humans can
review uncertain misses.

## Pieces

| File | Role |
|---|---|
| `subreddits.yaml` | The deployed subreddit list (bare names, no `r/`). Copy from `subreddits.example.yaml` if needed. |
| `subreddits.example.yaml` | Example subreddit list. |
| `product.md` | Arbiter instructions and product rubric. |
| `build_arbiter.py` | Defines and pushes the `<org>/reddit-post-judge` Arbiter. |
| `config.py` | Environment-driven settings. |
| `reddit_client.py` | PRAW fetcher for `REDDIT_SOURCE=api`. |
| `reddit_rss.py` | Public Atom/RSS fetcher for `REDDIT_SOURCE=rss`. |
| `arbiter_judge.py` | Batch prediction with confidence, then surfacing policy filtering. |
| `surfacing.json` | Default confidence policy baked into the Modal image. |
| `policy.py` | Policy evaluator plus Slack slash-command parser. |
| `slack_app.py` | Slack posting, annotation interactivity, and slash-command endpoints. |
| `app.py` | Modal app: daily cron plus Slack web endpoints. |
| `smoke.py` | Local smoke tests for RSS, Reddit API, Arbiter, Slack, policy, and pipeline. |

## Setup

```bash
uv sync
cp subreddits.example.yaml subreddits.yaml
cp .env.example .env
```

Fill in `.env` for local smoke tests. In Modal, the same values live in a
secret named `mo-buzz`.

### 1. Push the Arbiter

```bash
export MODAIC_TOKEN=...           # modaic.dev/settings/tokens
export MODAIC_USER_OR_ORG=modaic
uv run python build_arbiter.py
```

Also set `TOGETHER_API_KEY` as an environment variable on Modaic Hub
(modaic.dev/settings/env-vars), because the judge uses a Together AI model at
runtime. Edit `product.md` and re-run the build script to tune the rubric.

### 2. Choose a Reddit Source

For the lowest-friction setup, use RSS:

```bash
REDDIT_SOURCE=rss
REDDIT_USER_AGENT="mo_buzz/0.1 (by Modaic)"
```

RSS requires no Reddit app credentials. It is useful while waiting on Reddit API
approval, but feeds are limited and can rate-limit shared infrastructure.

For API mode, create a script app at https://www.reddit.com/prefs/apps and set:

```bash
REDDIT_SOURCE=api
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT="mo_buzz/0.1 (by Modaic)"
```

### 3. Create the Slack App

Create a Slack app with:

- Bot token scope: `chat:write`, then install it to the workspace.
- Interactivity enabled with request URL:
  `https://<your-mo-buzz-slack-interactions>.modal.run/slack/interactions`
- A slash command such as `/mobuzz` with request URL:
  `https://<your-mo-buzz-slack-interactions>.modal.run/slack/commands`

Invite the bot to the target channel and copy the channel ID.

### 4. Create the Modal Secret

Bundle every runtime env var into one secret named `mo-buzz`:

```bash
uv run modal secret create mo-buzz \
  MODAIC_TOKEN=... MODAIC_USER_OR_ORG=modaic \
  REDDIT_SOURCE=rss "REDDIT_USER_AGENT=mo_buzz/0.1 (by Modaic)" \
  SLACK_BOT_TOKEN=xoxb-... SLACK_SIGNING_SECRET=... SLACK_CHANNEL_ID=C...
```

If you use `REDDIT_SOURCE=api`, also include `REDDIT_CLIENT_ID` and
`REDDIT_CLIENT_SECRET`.

### 5. Deploy

```bash
uv run modal deploy app.py        # cron + Slack endpoints go live
uv run modal run app.py           # trigger one scan immediately
```

For local endpoint testing:

```bash
uv run modal serve app.py
```

## Surfacing Policy

The default policy is in `surfacing.json`:

```json
{
  "relevant": { "mode": "all" },
  "not_relevant": { "mode": "below", "threshold": 0.5 }
}
```

Supported modes are:

- `all`: always surface this verdict.
- `none`: never surface this verdict.
- `below`: surface when confidence is below a threshold.
- `above`: surface when confidence is at or above a threshold.

Use the Slack slash command to inspect or change the live policy:

```text
/mobuzz show
/mobuzz set relevant all
/mobuzz set not_relevant below 50
/mobuzz reset
```

Slash-command changes are stored in Modal Dict `mo-buzz-policy`, so the web
endpoint and daily cron share the same effective policy.

## Smoke Tests

Load local env vars first:

```bash
set -a; source .env; set +a
```

Then run focused checks:

```bash
uv run python smoke.py rss LLMDevs
uv run python smoke.py reddit LLMDevs
uv run python smoke.py arbiter
uv run python smoke.py slack <example_id>
uv run python smoke.py policy show
uv run python smoke.py pipeline
```

## Notes

- The cron runs daily at 13:00 UTC with a default 24-hour lookback.
- `POSTS_PER_SUBREDDIT` defaults to 50.
- `arbiter_judge.py` sends examples in batches of up to 1000.
- The Slack endpoint keeps one Modal container warm (`min_containers=1`) to meet
  Slack's 3-second interactivity deadline. This has a small always-on cost.
