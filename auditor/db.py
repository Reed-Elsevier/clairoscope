"""DuckDB over the REPH Parquet files. Every table is registered as a view named after its file."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return Path(os.environ.get("AUDITOR_DATA_DIR", ROOT / "data"))


@lru_cache(maxsize=1)
def _connection() -> duckdb.DuckDBPyConnection:
    base = data_dir()
    files = sorted(p for p in base.glob("*/*.parquet") if not p.parent.name.startswith("_"))
    if not files:
        raise FileNotFoundError(
            f"No Parquet files under {base}. Run: python scripts/extract_data.py"
        )
    con = duckdb.connect()
    for p in files:
        path = p.resolve().as_posix().replace("'", "''")
        con.execute(f'create view "{p.stem}" as select * from read_parquet(\'{path}\')')
    return con


def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run SQL on a fresh cursor (thread-safe for Streamlit) and return a DataFrame."""
    cur = _connection().cursor()
    try:
        return cur.execute(sql, params or []).df()
    finally:
        cur.close()


def tables() -> list[str]:
    return query("select view_name from duckdb_views() where not internal order by 1")["view_name"].tolist()
