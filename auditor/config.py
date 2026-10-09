"""Load .env and treat blank entries (e.g. `AWS_PROFILE=`) as unset, so a copied .env.example
with empty placeholders behaves like the variable is absent."""
import os

from dotenv import load_dotenv

_PREFIXES = ("AUDITOR_", "AWS_", "ANTHROPIC_", "OPENAI_")


def load_env(override: bool = False) -> None:
    load_dotenv(override=override)
    for key in [k for k, v in os.environ.items() if k.startswith(_PREFIXES) and not v.strip()]:
        del os.environ[key]
