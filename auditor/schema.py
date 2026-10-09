"""Data-dictionary retrieval: the retrieval-augmented part of the measure builder.

The dataset ships a data dictionary (116 tables, 1,042 columns, each with a business meaning). For a project
that no catalog measure fits, we rank tables by how well their descriptions match the project (BM25 over table
and column text) and hand Claude only the best few, so it designs a measure from real columns instead of
guessing. Retrieval is lexical and local: no embeddings, no training, nothing leaves the environment.
"""
from __future__ import annotations

import csv
import math
import re
from collections import Counter
from functools import lru_cache

from .db import docs_dir, tables

# Tables that hold what teams *report* about AI, not the operational record of the work itself.
SELF_REPORTED_DOMAINS = {"J_ai_portfolio"}
_STOP = set("""a an the and or of for to in on at by with from per is are was be as it its this that these those
not no yes flag identifier foreign key free text date time ts id ids""".split())
# Words that describe the AI rather than the work; they would pull in tables about AI tooling.
_AI_WORDS = set("ai autonomous agent bot copilot assistant automation insights dashboard predictive genai llm".split())


def _tok(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1]


@lru_cache(maxsize=1)
def dictionary() -> dict[str, dict]:
    """table -> {table, domain, description, columns: [{name, type, key, allowed, meaning}]}"""
    docs = docs_dir()
    tables: dict[str, dict] = {}
    with open(docs / "data_dictionary.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = tables.setdefault(r["table"], {"table": r["table"], "domain": r["domain"], "description": "",
                                               "columns": []})
            t["columns"].append({"name": r["column"], "type": r["data_type"], "key": r["key"],
                                 "allowed": r["allowed_values_or_range"], "meaning": r["business_meaning"]})
    md = docs / "03_data_dictionary.md"
    if md.exists():
        for m in re.finditer(r"### `(\w+)`[^#]*?\*\*Description:\*\* ([^\n]+)", md.read_text(encoding="utf-8")):
            if m.group(1) in tables:
                tables[m.group(1)]["description"] = m.group(2).strip()
    return tables


def _doc_text(t: dict) -> str:
    cols = " ".join(f"{c['name'].replace('_', ' ')} {c['meaning']} {c['allowed'][:200]}" for c in t["columns"])
    # table name and description count double: they say what the table is about
    return f"{t['table'].replace('_', ' ')} {t['table'].replace('_', ' ')} {t['description']} {t['description']} {cols}"


@lru_cache(maxsize=1)
def available() -> frozenset[str]:
    """Tables whose data is actually loaded (a deployment may upload only a subset, see make_used_data.py)."""
    return frozenset(tables())


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, Counter], dict[str, float], float]:
    tf = {name: Counter(_tok(_doc_text(t))) for name, t in dictionary().items()
          if t["domain"] not in SELF_REPORTED_DOMAINS and name in available()}
    n = len(tf)
    df = Counter(w for c in tf.values() for w in c)
    idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}
    avg = sum(sum(c.values()) for c in tf.values()) / max(n, 1)
    return tf, idf, avg


def search(text: str, k: int = 6) -> list[dict]:
    """Top-k operational tables for a free-text description of the work (BM25, k1=1.2, b=0.75)."""
    tf, idf, avg = _index()
    q = set(_tok(text)) - _AI_WORDS
    scores = []
    for name, c in tf.items():
        length = sum(c.values())
        s = sum(idf.get(w, 0) * c[w] * 2.2 / (c[w] + 1.2 * (0.25 + 0.75 * length / avg)) for w in q if c[w])
        if s > 0:
            scores.append((s, name))
    scores.sort(reverse=True)
    return [dict(dictionary()[name], score=round(s, 2)) for s, name in scores[:k]]


def brief(table: dict) -> dict:
    """Compact table card for the prompt."""
    return {"table": table["table"], "description": table["description"],
            "columns": [{"name": c["name"], "type": c["type"], **({"key": c["key"]} if c["key"] else {}),
                         "values": c["allowed"][:160], "meaning": c["meaning"]} for c in table["columns"]]}


def columns(table: str) -> set[str]:
    return {c["name"] for c in dictionary()[table]["columns"]}


def primary_key(table: str) -> str | None:
    return next((c["name"] for c in dictionary()[table]["columns"] if c["key"] == "PK"), None)
