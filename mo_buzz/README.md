# mo_buzz

A daily Reddit-monitoring bot for Modaic. Every 24h it scans a list of
subreddits for new posts, runs each through a Modaic **Arbiter** to decide
which are relevant to the product and worth responding to, and posts those into
Slack with the Reddit link and the arbiter's reasoning. From Slack you can
annotate the judge (and the annotation flows back to Modaic Hub to improve it).

## Pieces

| File | Role |
|---|---|
| `subreddits.yaml` | The list of subreddits to scan (bare names, no `r/`). |
| `product.md` | The arbiter's instructions + product description (the rubric). |
| `build_arbiter.py` | Defines and pushes the `<org>/reddit-post-judge` arbiter. |
| `config.py` | Env-driven settings (`pydantic-settings`). |
| `reddit_client.py` | PRAW: fetch new posts in the last 24h. |
| `arbiter_judge.py` | Run posts through the arbiter; keep the `respond` ones. |
| `slack_app.py` | Post flagged posts; handle the annotate-button interactions. |
| `app.py` | Modal app: the daily cron + the Slack interactivity web endpoint. |

## Setup

```bash
uv sync
```

### 1. Push the arbiter

```bash
export MODAIC_TOKEN=...           # modaic.dev/settings/tokens
export MODAIC_USER_OR_ORG=modaic
uv run python build_arbiter.py
```

Also set `TOGETHER_API_KEY` as an Environment Variable on Modaic Hub
(modaic.dev/settings/env-vars) so the judge can execute. Edit `product.md` and
re-run to tune the rubric.

### 2. Reddit app

Create a **script** app at https://www.reddit.com/prefs/apps to get a client id
+ secret (read-only access to public subreddits; no user login needed).

### 3. Slack app

Create a Slack app with:
- **Bot token scopes:** `chat:write` (install to workspace -> `xoxb-` token).
- **Interactivity:** ON. Set the Request URL to the deployed web endpoint:
  `https://<your-mo-buzz-slack-interactions>.modal.run/slack/interactions`
  (get the URL from `modal deploy` output).
- Invite the bot to the target channel; grab the channel ID.

### 4. Modal secret

Bundle every env var (see `.env.example`) into one secret named `mo-buzz`:

```bash
uv run modal secret create mo-buzz \
  MODAIC_TOKEN=... MODAIC_USER_OR_ORG=modaic \
  REDDIT_CLIENT_ID=... REDDIT_CLIENT_SECRET=... "REDDIT_USER_AGENT=mo_buzz/0.1 (by Modaic)" \
  SLACK_BOT_TOKEN=xoxb-... SLACK_SIGNING_SECRET=... SLACK_CHANNEL_ID=C...
```

### 5. Deploy

```bash
uv run modal deploy app.py        # cron + interactivity endpoint go live
uv run modal run app.py           # trigger a scan right now (testing)
```

## Notes

- The cron runs daily at **13:00 UTC** (`modal.Cron("0 13 * * *")` in `app.py`).
  The 24h lookback matches the daily cadence.
- Only posts the arbiter labels **`respond`** are sent to Slack (see
  `FLAGGED_RECOMMENDATIONS` in `config.py`).
- The Slack endpoint keeps one container warm (`min_containers=1`) so it meets
  Slack's 3-second interactivity deadline — this has a small always-on cost.
