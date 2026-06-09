"""Live odds auto-pull from The Odds API (Batch 11) — EXPLICIT trigger only.

Never called on page load or inside a normal `run` (auto_pull defaults False).
Produces per-book records in the same shape value_center merges with manual odds,
saved to out/<date>/api_odds.json (manual_odds.json is never touched). Raw API
keys are obtained via odds_keys and never logged or returned.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from . import odds_keys
from .http import Client
from .sources.odds import ODDS_BASE, live_key_tester
from .util import normalize_name, now_stamp

# model bet_type -> (odds-api market key, canonical point, human line)
MARKETS: Dict[str, tuple] = {
    "HR": ("batter_home_runs", 0.5, "1+ HR"),
    "TB": ("batter_total_bases", 1.5, "2+ TB"),
    "Hits": ("batter_hits", 0.5, "1+ Hits"),
    "HRR": ("batter_hits_runs_rbis", 1.5, "2+ HRR"),
    "RBI": ("batter_rbis", 0.5, "1+ RBI"),
    "Runs": ("batter_runs_scored", 0.5, "1+ Runs"),
}
_KEY_TO_BET = {v[0]: (bet, v[1], v[2]) for bet, v in MARKETS.items()}
BOOK_NAMES = {
    "draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM",
    "williamhill_us": "Caesars", "espnbet": "ESPN Bet", "fanatics": "Fanatics",
    "hardrockbet": "Hard Rock",
}


def _path(date: str, out_root: str | Path = "out") -> Path:
    return Path(out_root) / date / "api_odds.json"


def load(date: str, out_root: str | Path = "out") -> List[dict]:
    p = _path(date, out_root)
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def parse_event_rows(eo: dict, want_keys: set, name_index: Dict[str, int],
                     game: str = "") -> List[dict]:
    """One record per (player, market, book) for the canonical line of each market."""
    out: List[dict] = []
    if not eo:
        return out
    eid = eo.get("id")
    stamp = now_stamp()
    for bm in eo.get("bookmakers", []) or []:
        book = BOOK_NAMES.get(bm.get("key"), bm.get("title") or bm.get("key") or "Other")
        for mkt in bm.get("markets", []) or []:
            mk = mkt.get("key")
            if mk not in want_keys or mk not in _KEY_TO_BET:
                continue
            bet_type, point, line = _KEY_TO_BET[mk]
            for o in mkt.get("outcomes", []) or []:
                if str(o.get("name", "")).lower() not in ("over", "yes"):
                    continue
                pt = o.get("point")
                if pt is not None and abs(float(pt) - point) > 0.01:
                    continue   # only the canonical line (don't merge 1.5 vs 2.5)
                price = o.get("price")
                desc = o.get("description", "")
                if price is None or not desc:
                    continue
                out.append({
                    "player": desc, "player_norm": normalize_name(desc),
                    "batter_id": name_index.get(normalize_name(desc)),
                    "bet_type": bet_type, "line": line, "market": mk,
                    "sportsbook": book, "odds": int(price),
                    "event_id": eid, "game": game,
                    "timestamp": stamp, "source": "api",
                })
    return out


def pull(client: Client, cfg, date: str, name_index: Dict[str, int],
         markets: Optional[List[str]] = None, region: Optional[str] = None,
         dry_run: bool = False, out_root: str | Path = "out") -> dict:
    """Explicit live pull. Returns a SAFE summary (no raw key)."""
    region = region or cfg.odds.region
    wanted = [m for m in (markets or list(MARKETS)) if m in MARKETS]
    want_keys = {MARKETS[m][0] for m in wanted}

    tester = live_key_tester()
    act = odds_keys.active_key(tester)
    if not act:
        reports = odds_keys.check_keys(tester)
        err = "no_key" if not reports else (
            "quota_exhausted" if any(r["error"] == "http_429" for r in reports)
            else "invalid_key")
        return {"ok": False, "error": err, "active_key_name": None,
                "markets_requested": wanted, "key_reports": reports,
                "records_saved": 0, "books": [], "quota_remaining": None}
    key_name, key = act
    _valid, quota, _e = tester(key)

    if dry_run:
        return {"ok": True, "dry_run": True, "active_key_name": key_name,
                "markets_requested": wanted, "quota_remaining": quota,
                "records_saved": 0, "books": [], "note": "validated key; no odds saved"}

    client.force_refresh = True   # bypass cache for an explicit refresh
    records: List[dict] = []
    errors: List[str] = []
    try:
        events = client.get_json("odds", f"{ODDS_BASE}/events",
                                 {"apiKey": key, "dateFormat": "iso"})
        events = [e for e in (events or []) if str(e.get("commence_time", "")).startswith(date)] \
            if isinstance(events, list) else []
        for ev in events:
            eid = ev.get("id")
            if not eid:
                continue
            game = f"{ev.get('away_team', '')} @ {ev.get('home_team', '')}".strip(" @")
            eo = client.get_json(
                "odds", f"{ODDS_BASE}/events/{eid}/odds",
                {"apiKey": key, "regions": region,
                 "markets": ",".join(sorted(want_keys)), "oddsFormat": "american"})
            records.extend(parse_event_rows(eo, want_keys, name_index, game))
    except Exception as e:  # noqa: BLE001
        errors.append(type(e).__name__)
    finally:
        client.force_refresh = False

    if records or not errors:
        p = _path(date, out_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(records, indent=2))
    _v, quota_after, _ = tester(key)
    books = sorted({r["sportsbook"] for r in records})
    markets_pulled = sorted({r["bet_type"] for r in records})
    return {"ok": True, "active_key_name": key_name, "region": region,
            "markets_requested": wanted, "markets_pulled": markets_pulled,
            "records_saved": len(records), "books": books,
            "quota_remaining": quota_after if quota_after is not None else quota,
            "errors": errors, "pulled_at": now_stamp(),
            "unmatched": sum(1 for r in records if r["batter_id"] is None)}
