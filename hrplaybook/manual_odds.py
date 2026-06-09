"""Local manual-odds storage: out/<date>/manual_odds.json.

Lives alongside the slate outputs but is NEVER overwritten by the pipeline
(`run`/`refresh` only write games/pitchers/batters/matchups/cheatsheet/cards/
picks). Each entry is one player+bet-type+book price the user pasted in.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .util import normalize_name, now_stamp

BET_TYPES = ["HR", "TB", "HRR", "Hits", "RBI"]
SPORTSBOOKS = ["DraftKings", "FanDuel", "BetMGM", "Caesars", "ESPN Bet",
               "Fanatics", "Hard Rock", "Other"]


def _path(date: str, out_root: str | Path = "out") -> Path:
    return Path(out_root) / date / "manual_odds.json"


def load(date: str, out_root: str | Path = "out") -> List[dict]:
    p = _path(date, out_root)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save(date: str, entries: List[dict], out_root: str | Path = "out") -> None:
    p = _path(date, out_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, indent=2))


def _next_id(entries: List[dict]) -> int:
    return (max((e.get("id", 0) for e in entries), default=0) or 0) + 1


def add(date: str, entry: dict, out_root: str | Path = "out") -> dict:
    """Add or replace (same player+bet_type+sportsbook) a manual odds entry."""
    entries = load(date, out_root)
    e = {
        "id": _next_id(entries),
        "player": (entry.get("player") or "").strip(),
        "player_norm": normalize_name(entry.get("player") or ""),
        "batter_id": entry.get("batter_id"),
        "team": (entry.get("team") or "").strip().upper(),
        "bet_type": entry.get("bet_type") or "HR",
        "sportsbook": entry.get("sportsbook") or "Other",
        "odds": int(entry["odds"]) if str(entry.get("odds", "")).lstrip("+-").isdigit() else None,
        "line": (str(entry.get("line")) if entry.get("line") not in (None, "") else None),
        "timestamp": now_stamp(),
        "source": "manual",
    }
    # replace an existing same player+bet+book row instead of duplicating
    entries = [x for x in entries if not (
        x.get("player_norm") == e["player_norm"]
        and x.get("bet_type") == e["bet_type"]
        and x.get("sportsbook") == e["sportsbook"])]
    entries.append(e)
    save(date, entries, out_root)
    return e


def delete(date: str, entry_id: int, out_root: str | Path = "out") -> bool:
    entries = load(date, out_root)
    new = [e for e in entries if e.get("id") != entry_id]
    if len(new) == len(entries):
        return False
    save(date, new, out_root)
    return True
