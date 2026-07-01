"""Surfacing policy: which judged posts reach Slack, by verdict + confidence.

The default policy lives in `surfacing.json` (baked into the image). Runtime
overrides set via the Slack slash command are stored in a `modal.Dict` so the
daily cron and the web endpoint share the same live policy. Each verdict maps
to a rule:

    {"mode": "all"}                      -> always surface
    {"mode": "none"}                     -> never surface
    {"mode": "below", "threshold": 0.5}  -> surface if confidence < 0.5
    {"mode": "above", "threshold": 0.8}  -> surface if confidence >= 0.8
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_POLICY_PATH = Path(__file__).parent / "surfacing.json"
VALID_MODES = {"all", "none", "below", "above"}
_DICT_NAME = "mo-buzz-policy"
_DICT_KEY = "policy"


def load_default_policy() -> dict:
    return json.loads(DEFAULT_POLICY_PATH.read_text())


def _policy_dict():
    import modal  # available in Modal containers and locally (declared dep)

    return modal.Dict.from_name(_DICT_NAME, create_if_missing=True)


def get_policy() -> dict:
    """Effective policy: stored overrides if present, else the JSON default."""
    try:
        stored = _policy_dict().get(_DICT_KEY)
        if stored:
            return stored
    except Exception as exc:  # noqa: BLE001 - no Modal access (e.g. local) -> default
        logger.debug("policy dict unavailable (%s); using default", exc)
    return load_default_policy()


def set_rule(verdict: str, mode: str, threshold: float | None = None) -> dict:
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
    if mode in {"below", "above"} and threshold is None:
        raise ValueError(f"mode '{mode}' requires a threshold")
    policy = get_policy()
    rule: dict = {"mode": mode}
    if threshold is not None:
        rule["threshold"] = threshold
    policy[verdict] = rule
    _policy_dict()[_DICT_KEY] = policy
    return policy


def reset_policy() -> dict:
    default = load_default_policy()
    try:
        _policy_dict()[_DICT_KEY] = default
    except Exception:  # noqa: BLE001
        pass
    return default


def should_surface(verdict: str | None, confidence: float | None, policy: dict) -> bool:
    rule = policy.get(verdict) if verdict else None
    if not rule:
        return False
    mode = rule.get("mode", "none")
    if mode == "all":
        return True
    if mode == "none":
        return False
    threshold = rule.get("threshold")
    if confidence is None or threshold is None:
        logger.warning("No confidence for verdict %r; surfacing by default", verdict)
        return True
    if mode == "below":
        return confidence < threshold
    if mode == "above":
        return confidence >= threshold
    return False


# --------------------------------------------------------------------------- #
# Slash-command interface
# --------------------------------------------------------------------------- #
def _help(command: str) -> str:
    return (
        "*mo_buzz surfacing controls*\n"
        f"• `{command} show` — show the current policy\n"
        f"• `{command} set <relevant|not_relevant> all` — surface all of that verdict\n"
        f"• `{command} set <relevant|not_relevant> none` — surface none\n"
        f"• `{command} set <relevant|not_relevant> below <pct>` — surface when confidence below pct\n"
        f"• `{command} set <relevant|not_relevant> above <pct>` — surface when confidence at/above pct\n"
        f"• `{command} reset` — restore defaults\n"
        "_pct may be written as `50` or `0.5`_"
    )


def _parse_pct(s: str) -> float | None:
    try:
        v = float(s.rstrip("%"))
    except ValueError:
        return None
    if v > 1:  # given as 0-100
        v /= 100
    return max(0.0, min(1.0, v))


def format_policy(policy: dict) -> str:
    lines = []
    for verdict, rule in policy.items():
        mode = rule.get("mode")
        if mode in {"below", "above"}:
            sym = "<" if mode == "below" else ">="
            pct = round(float(rule.get("threshold", 0)) * 100)
            lines.append(f"• `{verdict}` → surface when confidence {sym} {pct}%")
        elif mode == "all":
            lines.append(f"• `{verdict}` → surface *all*")
        elif mode == "none":
            lines.append(f"• `{verdict}` → *never* surface")
        else:
            lines.append(f"• `{verdict}` → {rule}")
    return "\n".join(lines) or "_(empty policy)_"


def apply_command(text: str, command: str = "/mobuzz") -> str:
    """Parse a slash-command body and return a human-readable result string.

    `command` is the invoked slash-command name (e.g. "/config"), used only to
    render help/usage text so it matches whatever the command was named.
    """
    parts = (text or "").split()
    # Optional namespace token so `/mobuzz config ...` works as well as bare `/mobuzz ...`.
    prefix = command
    if parts and parts[0] == "config":
        prefix = f"{command} config"
        parts = parts[1:]
    if not parts or parts[0] in {"show", "list"}:
        return "*Current surfacing policy:*\n" + format_policy(get_policy())
    sub = parts[0]
    if sub == "help":
        return _help(prefix)
    if sub == "reset":
        return "*Reset to defaults:*\n" + format_policy(reset_policy())
    if sub == "set":
        if len(parts) < 3:
            return "Usage: `set <relevant|not_relevant> <all|none|below|above> [pct]`"
        verdict, mode = parts[1], parts[2]
        threshold = None
        if mode in {"below", "above"}:
            if len(parts) < 4:
                return f"Mode `{mode}` needs a pct, e.g. `set {verdict} {mode} 50`"
            threshold = _parse_pct(parts[3])
            if threshold is None:
                return f"Couldn't parse `{parts[3]}` as a percent (try `50` or `0.5`)."
        try:
            policy = set_rule(verdict, mode, threshold)
        except ValueError as e:
            return f"⚠️ {e}"
        return f"Updated `{verdict}`.\n*Policy now:*\n" + format_policy(policy)
    return _help(prefix)
