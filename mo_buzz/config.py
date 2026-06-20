"""Runtime configuration for mo_buzz, loaded from environment variables.

In Modal these are provided by the `mo-buzz` secret; locally they come from
your shell environment (see `.env.example`). Required fields have no default,
so a missing value fails fast at startup.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The arbiter repo name is fixed (see build_arbiter.py); only the owning
# user/org is configurable.
ARBITER_REPO_NAME = "reddit-post-judge"

# Which arbiter actions are worth surfacing in Slack.
FLAGGED_ACTIONS = {"respond"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # --- Modaic ---
    modaic_user_or_org: str = Field(
        description="Hub user/org that owns the arbiter, e.g. 'modaic'."
    )
    modaic_token: str = Field(
        description="Modaic API token (MODAIC_TOKEN); used by modaic_client to authenticate."
    )

    # --- Reddit (read-only 'script' app) ---
    reddit_client_id: str
    reddit_client_secret: str
    reddit_user_agent: str = "mo_buzz/0.1 (by Modaic)"

    # --- Slack ---
    slack_bot_token: str = Field(description="Slack bot token, starts with xoxb-.")
    slack_signing_secret: str = Field(
        description="Slack app signing secret, used to verify interactivity requests."
    )
    slack_channel_id: str = Field(
        description="Channel ID (e.g. C0123ABC) where flagged posts are posted."
    )

    # --- Scan tuning ---
    lookback_hours: int = 24
    posts_per_subreddit: int = 50

    @property
    def arbiter_repo(self) -> str:
        return f"{self.modaic_user_or_org}/{ARBITER_REPO_NAME}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
