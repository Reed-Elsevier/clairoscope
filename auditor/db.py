"""DuckDB over the REPH Parquet files. Every table is registered as a view named after its file."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return Path(os.environ.get("AUDITOR_DATA_DIR", "").strip() or ROOT / "data")


def parquet_files(base: Path | None = None) -> list[Path]:
    """Tables as <area>/<table>.parquet (the package layout) or flat <table>.parquet (e.g. a one-level upload)."""
    base = base or data_dir()
    nested = [p for p in base.glob("*/*.parquet") if not p.parent.name.startswith("_")]
    return sorted(nested + list(base.glob("*.parquet")))


def database_files(base: Path | None = None) -> list[Path]:
    """DuckDB files holding the tables (one file instead of many, for upload portals that limit file counts)."""
    return sorted((base or data_dir()).glob("*.duckdb"))


def docs_dir() -> Path:
    """Where the data dictionary lives: <data>/_docs, or the data folder itself in a flat upload."""
    return data_dir() / "_docs" if (data_dir() / "_docs").is_dir() else data_dir()


@lru_cache(maxsize=1)
def _connection() -> duckdb.DuckDBPyConnection:
    base = data_dir()
    files, dbs = parquet_files(base), database_files(base)
    if not files and not dbs:
        raise FileNotFoundError(
            f"No Parquet or .duckdb data under {base}. Run: python scripts/extract_data.py"
        )
    con = duckdb.connect()
    seen: set[str] = set()
    for i, d in enumerate(dbs):  # tables inside a .duckdb file, read-only
        alias = f"src{i}_{re.sub(r'[^A-Za-z0-9_]', '_', d.stem)}"
        con.execute(f"attach '{d.resolve().as_posix().replace(chr(39), chr(39) * 2)}' as {alias} (read_only)")
        names = con.execute("select table_name from duckdb_tables() where database_name = ? order by 1", [alias]).fetchall()
        for (t,) in names:
            if t not in seen:
                seen.add(t)
                con.execute(f'create view "{t}" as select * from {alias}.main."{t}"')
    for p in files:
        if p.stem in seen:  # the same table in two places (e.g. nested and flat copies): load it once
            continue
        seen.add(p.stem)
        path = p.resolve().as_posix().replace("'", "''")
        con.execute(f'create view "{p.stem}" as select * from read_parquet(\'{path}\')')
    return con


def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run SQL on a fresh cursor (thread-safe for the web server) and return a DataFrame."""
    cur = _connection().cursor()
    try:
        return cur.execute(sql, params or []).df()
    finally:
        cur.close()


def tables() -> list[str]:
    return query("select view_name from duckdb_views() where not internal order by 1")["view_name"].tolist()
