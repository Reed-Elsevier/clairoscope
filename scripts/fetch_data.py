"""Make sure the Parquet data is present before the server starts (container entrypoint).

  * data already in the image or volume  -> nothing to do
  * AUDITOR_DATA_S3_URI=s3://bucket/prefix -> download the Parquet files and the data dictionary into the data dir.
    The prefix may hold the package folders (<area>/<table>.parquet, _docs/) or one flat level
    (<table>.parquet, data_dictionary.csv, 03_data_dictionary.md), e.g. from scripts/make_used_data.py.

Uses the container's IAM role (needs s3:ListBucket and s3:GetObject on that prefix).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICTIONARY = {"data_dictionary.csv", "03_data_dictionary.md"}  # flat-upload location of the data dictionary


def main() -> int:
    target = Path(os.environ.get("AUDITOR_DATA_DIR", "").strip() or ROOT / "data")
    if any(target.glob("*/*.parquet")) or any(target.glob("*.parquet")):
        print(f"[fetch_data] data present in {target}")
        return 0
    uri = os.environ.get("AUDITOR_DATA_S3_URI", "").strip()
    if not uri:
        print(f"[fetch_data] no data in {target} and AUDITOR_DATA_S3_URI not set", file=sys.stderr)
        return 1
    import boto3

    bucket, _, prefix = uri.removeprefix("s3://").partition("/")
    prefix = prefix.rstrip("/") + "/" if prefix else ""
    s3 = boto3.client("s3")
    count = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(prefix):]
            if not (rel.endswith(".parquet") or rel.startswith("_docs/") or rel in DICTIONARY) or ".." in rel:
                continue
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(dest))
            count += 1
    print(f"[fetch_data] downloaded {count} files from {uri} to {target}")
    return 0 if count else 1


if __name__ == "__main__":
    sys.exit(main())
