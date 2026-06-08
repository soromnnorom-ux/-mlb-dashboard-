"""Grade prior picks against actual box-score results and keep a rolling ledger.

Closes the loop: did the sheet actually win? Per bet-type and per tier it reports
record + hit-rate, and (where odds were recorded) ROI at the priced number.
"""
from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def parse_line(line: str) -> Optional[Tuple[str, int]]:
    """'2+ TB' -> ('tb', 2); '1.5 HRR' -> ('hrr', 2); 'HR' -> ('hr', 1)."""
    s = (line or "").strip()
    if not s:
        return None
    if "TB" in s:
        stat = "tb"
    elif "HRR" in s:
        stat = "hrr"
    elif "Hit" in s:
        stat = "h"
    elif "HR" in s:
        stat = "hr"
    else:
        return None
    nums = re.findall(r"[\d.]+", s)
    if not nums:
        return (stat, 1)  # plain "HR"
    v = float(nums[0])
    thr = int(v) + 1 if v != int(v) else int(v)  # 1.5 -> 2, 2 (from "2+") -> 2
    return (stat, max(1, thr))


def actual_stat(stat: str, actual: dict) -> int:
    if stat == "hrr":
        return actual.get("h", 0) + actual.get("r", 0) + actual.get("rbi", 0)
    return actual.get(stat, 0)


def _profit_per_unit(odds: int) -> float:
    return odds / 100.0 if odds > 0 else 100.0 / (-odds)


def grade_picks(picks: List[dict], results: Dict[int, dict],
                graded_ids: Optional[set] = None) -> List[dict]:
    """Return one row per (pick, bet). `won` is None when the player has no
    result (DNP / game not final) -> treated as void."""
    rows: List[dict] = []
    for p in picks:
        pid = p["batter_id"]
        actual = results.get(pid)
        playable = actual is not None and (graded_ids is None or pid in graded_ids)
        for bet, line in (p.get("bets") or {}).items():
            parsed = parse_line(line)
            if parsed is None:
                continue
            stat, thr = parsed
            if not playable:
                won = None
            else:
                won = actual_stat(stat, actual) >= thr
            odds = (p.get("odds") or {}).get(bet)
            profit = None
            if won is not None and odds is not None:
                profit = _profit_per_unit(int(odds)) if won else -1.0
            rows.append({
                "date": p["date"],
                "batter_id": pid,
                "batter": p["batter"],
                "team": p["team"],
                "bet": bet,
                "line": line,
                "tier": p.get("tier"),
                "stat": stat,
                "need": thr,
                "got": actual_stat(stat, actual) if actual else None,
                "won": won,
                "odds": odds,
                "profit": profit,
            })
    return rows


def summarize(rows: List[dict]) -> Dict[str, dict]:
    """Aggregate by bet type: record, hit-rate, ROI (where odds present)."""
    agg: Dict[str, dict] = defaultdict(lambda: {"w": 0, "l": 0, "void": 0,
                                                 "staked": 0.0, "profit": 0.0})
    for r in rows:
        a = agg[r["bet"]]
        if r["won"] is None:
            a["void"] += 1
            continue
        a["w" if r["won"] else "l"] += 1
        if r["profit"] is not None:
            a["staked"] += 1.0
            a["profit"] += r["profit"]
    out = {}
    for bet, a in agg.items():
        decided = a["w"] + a["l"]
        out[bet] = {
            **a,
            "decided": decided,
            "hit_rate": round(a["w"] / decided, 3) if decided else None,
            "roi": round(a["profit"] / a["staked"], 3) if a["staked"] else None,
        }
    return out


def append_ledger(ledger_path: str | Path, rows: List[dict]) -> None:
    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    decided = [r for r in rows if r["won"] is not None]
    if not decided:
        return
    fields = ["date", "batter_id", "batter", "team", "bet", "line", "tier",
              "stat", "need", "got", "won", "odds", "profit"]
    exists = ledger_path.exists()
    # de-dup: don't double-write the same (date, batter_id, bet)
    seen = set()
    if exists:
        with ledger_path.open(newline="") as f:
            for r in csv.DictReader(f):
                seen.add((r["date"], r["batter_id"], r["bet"]))
    with ledger_path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        for r in decided:
            key = (r["date"], str(r["batter_id"]), r["bet"])
            if key in seen:
                continue
            seen.add(key)
            w.writerow({k: r.get(k) for k in fields})
