"""Extract the REPH data package (Parquet + _docs) from center_data.zip into ./data.

Usage:  python scripts/extract_data.py [path/to/center_data.zip] [target_dir]
CSV copies and raw/ are skipped: the auditor reads the curated Parquet files only.
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    zip_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "center_data.zip"
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data"
    if not zip_path.exists():
        sys.exit(f"Data package not found: {zip_path}")
    target_resolved = target.resolve()
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or not (name.endswith(".parquet") or name.startswith("_docs/")):
                continue
            dest = (target / name).resolve()
            if target_resolved not in dest.parents:  # refuse path traversal
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
            count += 1
    print(f"Extracted {count} files to {target}")


if __name__ == "__main__":
    main()
