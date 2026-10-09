"""Run audits from the terminal (useful for testing and as a demo fallback).

  python scripts/audit_cli.py UC0002            # one use case
  python scripts/audit_cli.py --all-flagship    # UC0001-UC0008
  python scripts/audit_cli.py --claim "The legal classifier cut handling time 43%"
  add --no-llm to force deterministic mode
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from auditor.agent import run_audit  # noqa: E402
from auditor.llm import get_llm  # noqa: E402


def show(r: dict, verbose: bool) -> None:
    v = r["verdict"]
    uc = r["use_case"]
    print(f"\n=== {uc['use_case_id']} {uc['name']} [{r['mode']}]")
    print(f"VERDICT: {v['verdict']}  (rubric: {r['rubric']['verdict']})  {v['headline']}")
    for reason in r["rubric"]["reasons"]:
        print(f"  - {reason}")
    for t in r["traps"]:
        print(f"  ! [{t['severity']}] {t['code']}: {t['detail']}")
    if verbose:
        for e in r["evidence"]:
            print(f"  {e['id']} {e['summary']}")
        for s in r["trace"]:
            print(f"  > {s['t']:>5}s {s['step']}: {s['detail']}")
    print(f"  grounding: {r['grounding']}")


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("use_case", nargs="?")
    ap.add_argument("--all-flagship", action="store_true")
    ap.add_argument("--claim")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    llm = None if a.no_llm else get_llm()
    if a.claim:
        show(run_audit(claim_text=a.claim, llm=llm), a.verbose)
    ids = [f"UC000{i}" for i in range(1, 9)] if a.all_flagship else ([a.use_case] if a.use_case else [])
    for uc in ids:
        show(run_audit(uc, llm=llm), a.verbose)


if __name__ == "__main__":
    main()
