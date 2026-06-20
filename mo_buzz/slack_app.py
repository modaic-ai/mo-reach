"""Slack integration: post flagged posts and handle annotation interactions.

Outbound (called by the daily cron):
    post_flagged(jp) -> posts a Block Kit message with the Reddit link, the
    arbiter's action + reasoning, and a row of "annotate" buttons.

Inbound (served by the Modal web endpoint):
    build_fastapi_app() -> a FastAPI app exposing POST /slack/interactions.
    Clicking an annotate button opens a modal asking for the annotation
    reasoning; submitting it writes the annotation back to Modaic via
    Arbiter.annotate_example(...) and replies in-thread with that reasoning.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs

from modaic_client import Arbiter
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

from arbiter_judge import JudgedPost
from config import get_settings

logger = logging.getLogger(__name__)

# Buttons let a human assert the *correct* action for the post.
ACTION_BUTTONS = [
    ("respond", "✅ respond", "primary"),
    ("ignore", "🚫 ignore", "danger"),
]
ANNOTATE_ACTION_PREFIX = "annotate_"
ANNOTATION_MODAL_CALLBACK = "annotation_submit"


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# Outbound message
# --------------------------------------------------------------------------- #
def build_message_blocks(jp: JudgedPost) -> list[dict]:
    post = jp.post
    body_preview = _truncate(post.body, 600) if post.body else "_(link post / no body)_"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"r/{post.subreddit} · worth a response"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{post.url}|{_truncate(post.title, 280)}>*\nby u/{post.author}",
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": body_preview}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Arbiter:* `{jp.action}`\n*Why it's relevant:* {_truncate(jp.reasoning, 1200)}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔗 Open in Reddit"},
                    "url": post.url,
                    "action_id": "open_reddit",
                }
            ],
        },
    ]

    # Only offer annotation if we have a logged example to annotate.
    if jp.example_id:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Annotate the judge — what's the correct call?"}]}
        )
        blocks.append(
            {
                "type": "actions",
                "block_id": "annotate",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": label},
                        "action_id": f"{ANNOTATE_ACTION_PREFIX}{value}",
                        "value": jp.example_id,
                        **({"style": style} if style else {}),
                    }
                    for value, label, style in ACTION_BUTTONS
                ],
            }
        )

    return blocks


def post_flagged(jp: JudgedPost) -> None:
    settings = get_settings()
    client = WebClient(token=settings.slack_bot_token)
    client.chat_postMessage(
        channel=settings.slack_channel_id,
        blocks=build_message_blocks(jp),
        text=f"r/{jp.post.subreddit}: {jp.post.title}",  # notification fallback
    )


# --------------------------------------------------------------------------- #
# Inbound interactivity (FastAPI app served by Modal)
# --------------------------------------------------------------------------- #
def _annotation_modal(example_id: str, ground_truth: str, channel_id: str, message_ts: str) -> dict:
    return {
        "type": "modal",
        "callback_id": ANNOTATION_MODAL_CALLBACK,
        "private_metadata": json.dumps(
            {
                "example_id": example_id,
                "ground_truth": ground_truth,
                "channel_id": channel_id,
                "message_ts": message_ts,
            }
        ),
        "title": {"type": "plain_text", "text": "Annotate post"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"Marking the correct call as `{ground_truth}`."},
            },
            {
                "type": "input",
                "block_id": "reasoning",
                "label": {"type": "plain_text", "text": "Reasoning for this annotation"},
                "element": {
                    "type": "plain_text_input",
                    "multiline": True,
                    "action_id": "reasoning_input",
                },
            },
        ],
    }


def build_fastapi_app():
    from fastapi import FastAPI, Request, Response

    settings = get_settings()
    verifier = SignatureVerifier(signing_secret=settings.slack_signing_secret)
    slack = WebClient(token=settings.slack_bot_token)
    arbiter = Arbiter(settings.arbiter_repo)

    web = FastAPI(title="mo-buzz-slack")

    @web.get("/health")
    async def health():
        return {"ok": True}

    @web.post("/slack/interactions")
    async def interactions(request: Request):
        raw = await request.body()
        if not verifier.is_valid_request(raw, dict(request.headers)):
            return Response(status_code=403)

        form = parse_qs(raw.decode())
        payload = json.loads(form["payload"][0])
        ptype = payload.get("type")

        # 1) Button click -> open a modal to capture the annotation reasoning.
        if ptype == "block_actions":
            action = payload["actions"][0]
            action_id = action.get("action_id", "")
            if action_id.startswith(ANNOTATE_ACTION_PREFIX):
                ground_truth = action_id[len(ANNOTATE_ACTION_PREFIX) :]
                try:
                    slack.views_open(
                        trigger_id=payload["trigger_id"],
                        view=_annotation_modal(
                            example_id=action["value"],
                            ground_truth=ground_truth,
                            channel_id=payload["channel"]["id"],
                            message_ts=payload["message"]["ts"],
                        ),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to open annotation modal")
            return Response(status_code=200)

        # 2) Modal submitted -> annotate on Modaic + reply in-thread.
        if ptype == "view_submission" and payload["view"]["callback_id"] == ANNOTATION_MODAL_CALLBACK:
            meta = json.loads(payload["view"]["private_metadata"])
            reasoning = (
                payload["view"]["state"]["values"]["reasoning"]["reasoning_input"].get("value") or ""
            )
            user_id = payload["user"]["id"]

            try:
                arbiter.annotate_example(
                    meta["example_id"],
                    ground_truth=meta["ground_truth"],
                    ground_reasoning=reasoning,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to annotate example %s", meta.get("example_id"))

            try:
                slack.chat_postMessage(
                    channel=meta["channel_id"],
                    thread_ts=meta["message_ts"],
                    text=f"Annotated as `{meta['ground_truth']}` by <@{user_id}> — {reasoning}",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to post annotation reply")

            return Response(content=json.dumps({"response_action": "clear"}), media_type="application/json")

        return Response(status_code=200)

    return web
