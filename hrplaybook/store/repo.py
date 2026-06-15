"""Repository: ingest a built out/<date> slate into the relational store and query it.

Stdlib-only (sqlite3 + csv + json). The typed columns are extracted from
matchups.csv; everything else is preserved verbatim in `extra_json` so nothing
is lost in the CSV->DB migration. Idempotent (INSERT OR REPLACE) so re-running a
slate or re-grading simply overwrites — the same property the file store had.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from .schema import SCHEMA_SQL

# typed columns pulled out of the wide matchups.csv (rest -> extra_json)
_TYPED = ["batter", "team", "opp_team", "opp_sp", "lineup_state", "order",
          "env_tier", "env_score", "pitcher_score", "barrel_vs_pm", "play_score",
          "tier", "model_hr_prob", "model_tb_prob", "value", "bets", "tags"]
_FLOAT = {"env_score", "pitcher_score", "barrel_vs_pm", "play_score",
          "model_hr_prob", "model_tb_prob"}
_INT = {"order", "tier"}


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _num(v, cast):
    if v in (None, "", "None"):
        return None
    try:
        return cast(float(v)) if cast is int else cast(v)
    except (TypeError, ValueError):
        return None


def _read_csv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def ingest_slate(conn: sqlite3.Connection, out_root: str | Path, date: str) -> int:
    """Load out/<date>/matchups.csv (+ meta.json) into the store. Returns row count."""
    d = Path(out_root) / date
    rows = _read_csv(d / "matchups.csv")
    meta = {}
    mpath = d / "meta.json"
    if mpath.exists():
        try:
            meta = json.loads(mpath.read_text())
        except (json.JSONDecodeError, OSError):
            meta = {}
    cols = ["date", "batter_id"] + _TYPED + ["extra_json"]
    ph = ",".join("?" * len(cols))
    quoted = ",".join(f'"{c}"' for c in cols)
    sql = f"INSERT OR REPLACE INTO matchups ({quoted}) VALUES ({ph})"
    n = 0
    for r in rows:
        bid = _num(r.get("batter_id"), int)
        if bid is None:
            continue
        vals = [date, bid]
        for c in _TYPED:
            v = r.get(c)
            vals.append(_num(v, int) if c in _INT else
                        _num(v, float) if c in _FLOAT else (v if v not in ("",) else None))
        extra = {k: v for k, v in r.items() if k not in _TYPED and k not in ("batter_id",)}
        vals.append(json.dumps(extra))
        conn.execute(sql, vals)
        n += 1
    conn.execute(
        "INSERT OR REPLACE INTO slates (date, built_at, season, n_matchups, meta_json) "
        "VALUES (?,?,?,?,?)",
        (date, meta.get("built_at"), meta.get("season"), n, json.dumps(meta)))
    conn.commit()
    return n


def get_slate(conn: sqlite3.Connection, date: str) -> List[dict]:
    cur = conn.execute(
        "SELECT * FROM matchups WHERE date=? ORDER BY play_score DESC", (date,))
    out = []
    for row in cur.fetchall():
        d = dict(row)
        d.pop("extra_json", None)
        out.append(d)
    return out


def list_dates(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute("SELECT date FROM slates ORDER BY date DESC")
    return [r["date"] for r in cur.fetchall()]


def ingest_ledger(conn: sqlite3.Connection, ledger_csv: str | Path) -> int:
    """Load the global _ledger.csv into the ledger table (typed)."""
    rows = _read_csv(Path(ledger_csv))
    sql = ("INSERT OR REPLACE INTO ledger (date,batter_id,bet,line,need,got,won,odds,profit) "
           "VALUES (?,?,?,?,?,?,?,?,?)")
    n = 0
    for r in rows:
        bid = _num(r.get("batter_id"), int)
        if bid is None:
            continue
        won = r.get("won")
        won = 1 if str(won).lower() == "true" else 0 if str(won).lower() == "false" else None
        conn.execute(sql, (r.get("date"), bid, r.get("bet"), r.get("line"),
                           _num(r.get("need"), int), _num(r.get("got"), int), won,
                           _num(r.get("odds"), int), _num(r.get("profit"), float)))
        n += 1
    conn.commit()
    return n
