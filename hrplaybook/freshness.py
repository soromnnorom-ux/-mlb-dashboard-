"""Phase 2 — data freshness + slate validation.

Pure functions so they unit-test offline:
- build_meta(): summarize per-section data provenance at build time -> meta.json
- validate_slate(): turn that meta (+ slate records) into PASS / WARNING / FAIL

Stale thresholds (minutes) are emitted in the meta so the dashboard can colour
sections GREEN/YELLOW/RED by comparing built_at age to the threshold at view time.
"""
from __future__ import annotations

import datetime as _dt
from collections import Counter
from typing import Dict, List, Optional

# minutes after which a section is considered stale (Phase 2 rules)
STALE_MINUTES = {
    "schedule": 60,
    "lineups": 15,      # on game day
    "weather": 60,
    "odds": 15,
    "statcast": 720,
}


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def build_meta(date: str, games, matchups, warnings: List[str], cfg) -> dict:
    """Summarize provenance/freshness of each data section for a built slate."""
    built = _now()
    n_games = len(games)

    # lineups
    states = Counter(m.batter.lineup_state for m in matchups)
    if states.get("confirmed") and not states.get("projected") and not states.get("none"):
        lineup_state = "green"
    elif states.get("confirmed") or states.get("projected"):
        lineup_state = "yellow"
    else:
        lineup_state = "red"

    # weather
    wsrc = Counter((g.weather.source or "none") for g in games)
    have_temp = sum(1 for g in games if g.weather and g.weather.temp_f is not None)
    weather_state = "green" if (n_games and have_temp == n_games) else (
        "yellow" if have_temp else "red")

    # pitchers
    have_sp = sum(1 for g in games
                  for pid in (g.home_pitcher_id, g.away_pitcher_id) if pid)
    miss_sp = sum(1 for g in games
                  for pid in (g.home_pitcher_id, g.away_pitcher_id) if not pid)
    sp_state = "green" if miss_sp == 0 else ("yellow" if have_sp else "red")

    # statcast
    pulled = sum(1 for m in matchups if m.batter.recent_window_used)
    statcast_state = "green" if pulled else "yellow"

    # game status
    postponed = [
        f"{g.away_team}@{g.home_team}" for g in games
        if any(k in (g.status or "").lower() for k in ("postpon", "suspend", "cancel"))
    ]

    odds_present = any(getattr(m, "odds_by_bet", None) for m in matchups)

    return {
        "date": date,
        "built_at": built,
        "season": getattr(cfg, "season", None),
        "stale_minutes": STALE_MINUTES,
        "sections": {
            "schedule": {"source": "statsapi", "count": n_games,
                         "state": "green" if n_games else "red", "built_at": built},
            "lineups": {"source": "statsapi/rotowire", "state": lineup_state,
                        "confirmed": states.get("confirmed", 0),
                        "projected": states.get("projected", 0),
                        "none": states.get("none", 0), "built_at": built},
            "weather": {"sources": dict(wsrc), "have_temp": have_temp,
                        "state": weather_state, "built_at": built},
            "pitchers": {"found": have_sp, "missing": miss_sp,
                         "state": sp_state, "built_at": built},
            "statcast": {"pulled": pulled, "state": statcast_state, "built_at": built},
            "odds": {"state": "green" if odds_present else "gray",
                     "present": odds_present, "built_at": built},
        },
        "postponed": postponed,
        "warnings": warnings,
    }


def _age_minutes(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return (_dt.datetime.now() - _dt.datetime.fromisoformat(iso)).total_seconds() / 60.0
    except ValueError:
        return None


def validate_slate(meta: dict, games: List[dict], matchups: List[dict]) -> dict:
    """Return {overall: PASS|WARNING|FAIL, checks: [...]} for a built slate."""
    checks: List[dict] = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    sec = (meta or {}).get("sections", {})
    n_games = len(games)

    # schedule
    add("Schedule", "PASS" if n_games else "FAIL",
        f"{n_games} games" if n_games else "no games for this date")

    # pitchers
    miss = sum(1 for g in games if not g.get("home_sp") or not g.get("away_sp"))
    add("Probable pitchers",
        "PASS" if miss == 0 else ("WARNING" if miss < n_games else "FAIL"),
        "all probables set" if miss == 0 else f"{miss} game(s) missing a probable")

    # lineups
    ls = sec.get("lineups", {})
    if ls.get("confirmed") and not ls.get("projected") and not ls.get("none"):
        add("Lineups", "PASS", "all confirmed")
    elif ls.get("confirmed") or ls.get("projected"):
        add("Lineups", "WARNING",
            f"{ls.get('confirmed',0)} confirmed, {ls.get('projected',0)} projected")
    else:
        add("Lineups", "FAIL", "no lineups available")

    # weather
    ws = sec.get("weather", {})
    add("Weather", {"green": "PASS", "yellow": "WARNING"}.get(ws.get("state"), "FAIL"),
        f"{ws.get('have_temp',0)}/{n_games} games have weather")

    # statcast
    st = sec.get("statcast", {})
    add("Statcast", "PASS" if st.get("pulled") else "WARNING",
        f"{st.get('pulled',0)} batters pulled" if st.get("pulled")
        else "not pulled (season metrics only)")

    # game status
    postponed = (meta or {}).get("postponed", [])
    add("Game status", "FAIL" if postponed else "PASS",
        ("postponed/suspended: " + ", ".join(postponed)) if postponed else "all schedulable")

    # odds
    add("Odds", "PASS" if sec.get("odds", {}).get("present") else "WARNING",
        "odds present" if sec.get("odds", {}).get("present")
        else "no odds (value=unknown; use manual entry)")

    # freshness
    age = _age_minutes((meta or {}).get("built_at"))
    if age is None:
        add("Freshness", "WARNING", "unknown build time")
    else:
        limit = (meta or {}).get("stale_minutes", STALE_MINUTES).get("lineups", 15)
        add("Freshness",
            "PASS" if age <= 60 else ("WARNING" if age <= 6 * 60 else "FAIL"),
            f"built {age:.0f} min ago" + (" (rebuild for live lineups)" if age > limit else ""))

    order = {"FAIL": 2, "WARNING": 1, "PASS": 0}
    overall = max((c["status"] for c in checks), key=lambda s: order[s])
    return {"overall": overall, "checks": checks, "built_at": (meta or {}).get("built_at")}
