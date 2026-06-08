"""MLB Stats API: schedule + probable pitchers, boxscore lineups, people.

Network functions (fetch_*) call the shared Client; parse_* functions are pure
and operate on already-fetched JSON so they unit-test against fixtures offline.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..http import Client
from ..model.schemas import Game, Weather
from ..util import to_float

BASE = "https://statsapi.mlb.com/api/v1"


def fetch_schedule(client: Client, date: str) -> Optional[dict]:
    url = f"{BASE}/schedule"
    params = {
        "sportId": 1,
        "date": date,
        "hydrate": "probablePitcher,venue,weather,team",
    }
    return client.get_json("schedule", url, params)


_WIND_RE = re.compile(r"([\d.]+)\s*mph", re.I)


def parse_statsapi_weather(block: Optional[dict]) -> Weather:
    w = Weather(source="none")
    if not block:
        return w
    w.source = "statsapi"
    w.condition = block.get("condition")
    w.temp_f = to_float(block.get("temp"))
    wind = block.get("wind") or ""
    w.wind_text = wind or None
    m = _WIND_RE.search(wind)
    if m:
        w.wind_mph = to_float(m.group(1))
    wl = wind.lower()
    if "calm" in wl:
        w.wind_out = "calm"
        if w.wind_mph is None:
            w.wind_mph = 0.0
    elif "out to" in wl:
        w.wind_out = "out"
    elif "in from" in wl:
        w.wind_out = "in"
    elif "to r" in wl or "to l" in wl or "varies" in wl:
        w.wind_out = "cross"
    return w


def parse_schedule(data: Optional[dict]) -> List[Game]:
    games: List[Game] = []
    if not data:
        return games
    for d in data.get("dates", []):
        date = d.get("date")
        for g in d.get("games", []):
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            venue = g.get("venue", {}) or {}
            hp = home.get("probablePitcher") or {}
            ap = away.get("probablePitcher") or {}
            games.append(Game(
                game_pk=g["gamePk"],
                date=date,
                game_time_utc=g.get("gameDate"),
                venue_id=venue.get("id"),
                venue_name=venue.get("name", ""),
                home_team=home["team"].get("abbreviation", ""),
                away_team=away["team"].get("abbreviation", ""),
                home_team_id=home["team"].get("id"),
                away_team_id=away["team"].get("id"),
                home_pitcher_id=hp.get("id"),
                away_pitcher_id=ap.get("id"),
                home_pitcher_name=hp.get("fullName"),
                away_pitcher_name=ap.get("fullName"),
                status=g.get("status", {}).get("detailedState", ""),
                weather=parse_statsapi_weather(g.get("weather")),
            ))
    return games


def fetch_boxscore(client: Client, game_pk: int) -> Optional[dict]:
    url = f"{BASE}/game/{game_pk}/boxscore"
    return client.get_json("lineups", url, None)


def parse_lineup(box: Optional[dict], side: str) -> List[dict]:
    """Ordered confirmed lineup for 'home'/'away'.

    Each entry: {player_id, name, order(1..9), position, team}. Empty list when
    the lineup is not yet confirmed (battingOrder absent/empty).
    """
    out: List[dict] = []
    if not box:
        return out
    team = box.get("teams", {}).get(side, {})
    order_ids = team.get("battingOrder") or []
    players = team.get("players", {}) or {}
    abbrev = team.get("team", {}).get("abbreviation", "")
    for slot, pid in enumerate(order_ids, start=1):
        pdata = players.get(f"ID{pid}", {})
        out.append({
            "player_id": pid,
            "name": pdata.get("person", {}).get("fullName", ""),
            "order": slot,
            "position": pdata.get("position", {}).get("abbreviation", ""),
            "team": abbrev,
        })
    return out


def fetch_roster(client: Client, team_id: int) -> Optional[dict]:
    url = f"{BASE}/teams/{team_id}/roster"
    return client.get_json("roster", url, {"rosterType": "active"})


def parse_roster_pitchers(data: Optional[dict]) -> List[int]:
    """Return active-roster pitcher person ids for a team."""
    out: List[int] = []
    if not data:
        return out
    for r in data.get("roster", []):
        pos = (r.get("position") or {}).get("abbreviation", "")
        code = (r.get("position") or {}).get("code", "")
        if pos == "P" or code == "1":
            pid = (r.get("person") or {}).get("id")
            if pid:
                out.append(pid)
    return out


def parse_boxscore_results(box: Optional[dict]) -> Dict[int, dict]:
    """Per-batter actual batting line for grading. {player_id: {hr,tb,h,rbi,r,ab}}."""
    out: Dict[int, dict] = {}
    if not box:
        return out
    for side in ("home", "away"):
        players = box.get("teams", {}).get(side, {}).get("players", {}) or {}
        for pdata in players.values():
            bat = (pdata.get("stats") or {}).get("batting") or {}
            if not bat:
                continue
            pid = (pdata.get("person") or {}).get("id")
            if pid is None:
                continue
            out[pid] = {
                "hr": bat.get("homeRuns", 0) or 0,
                "tb": bat.get("totalBases", 0) or 0,
                "h": bat.get("hits", 0) or 0,
                "rbi": bat.get("rbi", 0) or 0,
                "r": bat.get("runs", 0) or 0,
                "ab": bat.get("atBats", 0) or 0,
            }
    return out


def fetch_people(client: Client, person_ids: List[int]) -> Optional[dict]:
    if not person_ids:
        return {"people": []}
    url = f"{BASE}/people"
    params = {"personIds": ",".join(str(i) for i in person_ids)}
    return client.get_json("people", url, params)


def parse_people(data: Optional[dict]) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    if not data:
        return out
    for p in data.get("people", []):
        out[p["id"]] = {
            "name": p.get("fullName", ""),
            "bats": (p.get("batSide") or {}).get("code"),
            "throws": (p.get("pitchHand") or {}).get("code"),
            "position": (p.get("primaryPosition") or {}).get("abbreviation"),
        }
    return out
