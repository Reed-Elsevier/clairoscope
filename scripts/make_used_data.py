"""Pack only the data Clairoscope reads into 3 files in ./used, for upload portals that limit the file count.

    python scripts/make_used_data.py
    -> used/clairoscope.duckdb       the 26 tables the code reads (one DuckDB database file)
       used/data_dictionary.csv      the data dictionary the measure builder searches
       used/03_data_dictionary.md    table descriptions for the same search

Upload those 3 files to the deployment's data location (or an S3 prefix set as AUDITOR_DATA_S3_URI). The app
reads the .duckdb file directly, read-only; no unpacking. With this subset the measure builder only considers
the tables present. REPH data: ./used is gitignored and never leaves the event environment.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SRC, DST = ROOT / "data", ROOT / "used"
DB_NAME = "clairoscope.duckdb"
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
    DST.mkdir(parents=True, exist_ok=True)
    for old in DST.iterdir():  # file by file: OneDrive / Explorer can lock a folder being deleted
        shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink()
    con = duckdb.connect(str(DST / DB_NAME))
    packed = []
    for p in sorted(SRC.glob("*/*.parquet")):
        if p.stem in wanted:
            con.execute(f'create table "{p.stem}" as select * from read_parquet(?)', [p.as_posix()])
            packed.append(p.stem)
    con.execute("checkpoint")
    con.close()
    for d in DOCS:
        shutil.copy2(SRC / "_docs" / d, DST / d)
    files = sorted(f for f in DST.iterdir() if f.is_file())
    for f in files:
        print(f"   {f.name:28} {f.stat().st_size / 1e6:7.1f} MB")
    print(f"{len(packed)} tables packed into {DB_NAME}; {len(files)} files to upload from {DST}")
    missing = wanted - set(packed)
    if missing:
        print("Missing from ./data:", ", ".join(sorted(missing)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
