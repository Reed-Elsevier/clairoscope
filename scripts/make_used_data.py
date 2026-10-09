"""Copy only the data Clairoscope reads into ./used, all in one flat folder, for a select-all upload to S3.

    python scripts/make_used_data.py            # ./data -> ./used  (26 tables + the data dictionary)
    aws s3 sync used/ s3://<bucket>/clairoscope-data/

The app reads the flat ./used exactly like the nested ./data (set AUDITOR_DATA_DIR=used locally, or point AUDITOR_DATA_S3_URI at the
uploaded prefix). With this subset, the measure builder only considers the tables present.
REPH data: ./used is gitignored and never leaves the event environment.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, DST = ROOT / "data", ROOT / "used"
DOCS = ["data_dictionary.csv", "03_data_dictionary.md"]  # the measure builder searches these


def tables_in_code() -> set[str]:
    """Every table the auditor's SQL reads (from / join), so the list cannot drift from the code."""
    names = {p.stem for p in SRC.glob("*/*.parquet")}
    found = set()
    for f in list((ROOT / "auditor").glob("*.py")) + [ROOT / "web" / "server.py"]:
        found |= set(re.findall(r'\b(?:from|join)\s+"?([a-z_]+)"?', f.read_text(encoding="utf-8"))) & names
    return found


def main() -> int:
    if not any(SRC.glob("*/*.parquet")):
        print("No data in ./data. Run: python scripts/extract_data.py")
        return 1
    wanted = tables_in_code()
    if DST.exists():  # OneDrive / Explorer can briefly lock an empty folder; leftovers are harmless
        shutil.rmtree(DST, ignore_errors=True)
    copied, size = [], 0
    for p in sorted(SRC.glob("*/*.parquet")):
        if p.stem in wanted:
            DST.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, DST / p.name)  # flat: table names are unique across the area folders
            copied.append(p.name)
            size += p.stat().st_size
    for d in DOCS:
        shutil.copy2(SRC / "_docs" / d, DST / d)
    for c in copied:
        print("  ", c)
    print(f"{len(copied)} tables ({size / 1e6:.1f} MB) + {len(DOCS)} dictionary files -> {DST}")
    missing = wanted - {Path(c).stem for c in copied}
    if missing:
        print("Missing from ./data:", ", ".join(sorted(missing)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
