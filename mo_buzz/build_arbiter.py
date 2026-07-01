"""Define the `reddit-post-judge` Arbiter and push it to Modaic Hub.

Run:
    uv run python build_arbiter.py

The judge's instructions and product description live in `product.md` and are
loaded via `.with_instructions()`, so you can edit the rubric without touching
code -- edit product.md and re-run this script to push a new commit.

Requires:
    - MODAIC_TOKEN set locally (https://modaic.dev/settings/tokens) so the push
      can authenticate.
    - MODAIC_USER_OR_ORG set to the Hub user/org that should own the repo,
      e.g. `modaic`. The repo name is hardcoded to `reddit-post-judge`, so the
      arbiter lands at `<MODAIC_USER_OR_ORG>/reddit-post-judge`.
    - TOGETHER_API_KEY set as an Environment Variable on Modaic Hub
      (https://modaic.dev/settings/env-vars) so future runs can execute -- this
      judge uses a `together_ai` model.
"""

import os
from pathlib import Path
from typing import Literal

import dspy
import modaic

# The repo *name* is fixed; only the owning user/org is configurable, via the
# MODAIC_USER_OR_ORG environment variable.
ARBITER_REPO_NAME = "reddit-post-judge"
PRODUCT_INSTRUCTIONS_PATH = Path(__file__).parent / "product.md"


class RedditPostJudge(dspy.Signature):
    """Triage a new Reddit post for Modaic outreach.

    The full rubric and product description are loaded from product.md at build
    time via `.with_instructions()`, so this docstring is intentionally short --
    edit product.md to change how the judge behaves.
    """

    subreddit: str = dspy.InputField(
        desc="Subreddit the post was made in (no r/ prefix)"
    )
    title: str = dspy.InputField(desc="The post title")
    body: str = dspy.InputField(
        desc="The post body / selftext (may be empty for link-only posts)"
    )
    link: str = dspy.InputField(desc="URL of the Reddit post")
    relevance: Literal["relevant", "not_relevant"] = dspy.OutputField(
        desc="Whether the post is relevant to the product and worth responding to"
    )


def main() -> None:
    org = os.environ.get("MODAIC_USER_OR_ORG")
    if not org:
        raise SystemExit(
            "MODAIC_USER_OR_ORG is not set. Set it to the Modaic Hub user/org "
            "that should own the arbiter, e.g. `export MODAIC_USER_OR_ORG=modaic`."
        )
    if not os.environ.get("MODAIC_TOKEN"):
        raise SystemExit(
            "MODAIC_TOKEN is not set. Get one at https://modaic.dev/settings/tokens."
        )

    instructions = PRODUCT_INSTRUCTIONS_PATH.read_text()
    signature = RedditPostJudge.with_instructions(instructions)

    repo = f"{org}/{ARBITER_REPO_NAME}"

    arbiter = modaic.Predict(
        signature,
        lm=dspy.LM(model="together_ai/openai/gpt-oss-120b"),
    ).as_arbiter()  # MUST be called before push_to_hub to register as an arbiter

    # No hardcoded tag: re-running this to update the rubric just pushes a new
    # commit to main. Tag a release manually (e.g. tag="v1") when you want one.
    arbiter.push_to_hub(
        repo,
        private=True,
        commit_message="reddit-post-judge: instructions from product.md",
    )
    print(f"Pushed arbiter to {repo}")


if __name__ == "__main__":
    main()
