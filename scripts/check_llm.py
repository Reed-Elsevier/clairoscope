"""Check the LLM setup end to end:  python scripts/check_llm.py

Prints the resolved provider / model / region / auth source, then makes one tiny JSON call
through the same code path the auditor uses. Exit code 0 = ready for the demo.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from auditor.llm import LLMError, configured_provider, get_llm  # noqa: E402

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["status", "verdicts"],
          "properties": {"status": {"type": "string", "enum": ["ok"]},
                         "verdicts": {"type": "array", "items": {"type": "string"}}}}


def main() -> int:
    load_dotenv()
    provider = configured_provider()
    print(f"Provider: {provider}")
    if provider == "off":
        print("No LLM configured: the app will run in deterministic mode. See .env.example.")
        return 1
    try:
        llm = get_llm()
        print(f"Resolved: {llm.name}")
        t0 = time.time()
        out = llm.json("You are a connectivity check.",
                       'Return {"status": "ok", "verdicts": ["PROVEN", "TRADE-OFF", "UNPROVEN", "CONTRADICTED"]}.',
                       SCHEMA)
        print(f"Reply: {out}  ({time.time() - t0:.1f}s)")
        print("READY")
        return 0
    except ImportError as e:
        print(f"Missing package: {e}. Run: pip install -r requirements.txt")
    except LLMError as e:
        print(f"FAILED: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
