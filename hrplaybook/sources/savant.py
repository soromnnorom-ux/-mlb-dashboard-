"""Baseball Savant: batter/pitcher custom leaderboards, pitch arsenals,
statcast_search batted-ball logs.

NOTE (verified against live responses 2025): the custom-leaderboard column ids
that actually populate are barrel_batted_rate / exit_velocity_avg /
hard_hit_percent / isolated_power / slg_percent (NOT the older
barrels_per_pa_percent / avg_hit_speed / ev95percent / iso / slg, which return
empty columns). If a selection 404s or comes back empty we keep going with
whatever resolved.
"""
from __future__ import annotations

import csv
import io
from typing import Dict, List, Optional

from ..http import Client
from ..model.schemas import Batter, Pitcher
from ..util import flip_name, parse_ip, to_float, to_int

BASE = "https://baseballsavant.mlb.com"

BATTER_SELECTIONS = (
    "player_id,player_name,pa,batting_avg,on_base_percent,slg_percent,"
    "isolated_power,xslg,xiso,woba,xwoba,barrel_batted_rate,exit_velocity_avg,"
    "hard_hit_percent,launch_angle_avg,flyballs_percent,pull_percent"
)
PITCHER_SELECTIONS = (
    "player_id,player_name,pa,p_formatted_ip,p_era,p_game,p_gs,home_run,"
    "barrel_batted_rate,exit_velocity_avg,hard_hit_percent,k_percent,bb_percent,"
    "whiff_percent,woba,xwoba,flyballs_percent,groundballs_percent"
)

PITCH_CODES = {
    "n_ff": "FF", "n_si": "SI", "n_fc": "FC", "n_sl": "SL", "n_ch": "CH",
    "n_cu": "CU", "n_fs": "FS", "n_kn": "KN", "n_st": "ST", "n_sv": "SV",
}
FASTBALL_TYPES = {"FF", "SI", "FC"}


def _read_csv(text: Optional[str]) -> List[dict]:
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    return list(reader)


def fetch_batter_leaderboard(client: Client, season: int, min_q: str = "q") -> Optional[str]:
    url = f"{BASE}/leaderboard/custom"
    params = {
        "year": season, "type": "batter", "filter": "", "min": min_q,
        "selections": BATTER_SELECTIONS, "csv": "true",
    }
    return client.get_text("savant", url, params)


def parse_batter_leaderboard(text: Optional[str], season: int) -> Dict[int, Batter]:
    out: Dict[int, Batter] = {}
    for row in _read_csv(text):
        pid = to_int(row.get("player_id"))
        if pid is None:
            continue
        out[pid] = Batter(
            player_id=pid,
            name=flip_name(row.get("player_name") or row.get("last_name, first_name", "")),
            season=season,
            pa=to_int(row.get("pa")),
            ba=to_float(row.get("batting_avg")),
            obp=to_float(row.get("on_base_percent")),
            slg=to_float(row.get("slg_percent")),
            iso=to_float(row.get("isolated_power")),
            xslg=to_float(row.get("xslg")),
            xiso=to_float(row.get("xiso")),
            woba=to_float(row.get("woba")),
            xwoba=to_float(row.get("xwoba")),
            barrel_pct=to_float(row.get("barrel_batted_rate")),
            avg_ev=to_float(row.get("exit_velocity_avg")),
            hardhit_pct=to_float(row.get("hard_hit_percent")),
            la_avg=to_float(row.get("launch_angle_avg")),
            fb_pct=to_float(row.get("flyballs_percent")),
            pull_pct=to_float(row.get("pull_percent")),
        )
    return out


def fetch_pitcher_leaderboard(client: Client, season: int, min_q: str = "q") -> Optional[str]:
    url = f"{BASE}/leaderboard/custom"
    params = {
        "year": season, "type": "pitcher", "filter": "", "min": min_q,
        "selections": PITCHER_SELECTIONS, "csv": "true",
    }
    return client.get_text("savant", url, params)


def parse_pitcher_leaderboard(text: Optional[str], season: int) -> Dict[int, Pitcher]:
    out: Dict[int, Pitcher] = {}
    for row in _read_csv(text):
        pid = to_int(row.get("player_id"))
        if pid is None:
            continue
        ip = parse_ip(row.get("p_formatted_ip"))
        hr = to_int(row.get("home_run"))
        pa = to_int(row.get("pa"))
        k_pct = to_float(row.get("k_percent"))
        bb_pct = to_float(row.get("bb_percent"))
        fb_pct = to_float(row.get("flyballs_percent"))
        hr9 = round(hr / ip * 9, 2) if (hr is not None and ip) else None
        # HR/FB% derived: HR / flyball count; flyballs ~= BBE * fb%. BBE approx
        # = PA*(1 - (K%+BB%)/100) (HBP ignored). Flagged as derived.
        hrfb = None
        if hr is not None and pa and fb_pct and k_pct is not None and bb_pct is not None:
            bbe = pa * max(0.0, 1 - (k_pct + bb_pct) / 100.0)
            fb_count = bbe * fb_pct / 100.0
            if fb_count >= 1:
                hrfb = round(hr / fb_count * 100.0, 1)
        out[pid] = Pitcher(
            player_id=pid,
            name=flip_name(row.get("player_name") or row.get("last_name, first_name", "")),
            season=season,
            ip=ip,
            era=to_float(row.get("p_era")),
            games=to_int(row.get("p_game")),
            hr=hr,
            hr9=hr9,
            hrfb_pct=hrfb,
            barrel_pct_allowed=to_float(row.get("barrel_batted_rate")),
            avg_ev_allowed=to_float(row.get("exit_velocity_avg")),
            hardhit_pct_allowed=to_float(row.get("hard_hit_percent")),
            k_pct=k_pct,
            bb_pct=bb_pct,
            whiff_pct=to_float(row.get("whiff_percent")),
            fb_pct=fb_pct,
            gb_pct=to_float(row.get("groundballs_percent")),
            gs=to_int(row.get("p_gs")) or 0,
            woba=to_float(row.get("woba")),
            xwoba=to_float(row.get("xwoba")),
        )
    return out


def fetch_arsenals(client: Client, season: int) -> Optional[str]:
    url = f"{BASE}/leaderboard/pitch-arsenals"
    params = {"year": season, "type": "n_", "hand": "", "csv": "true"}
    return client.get_text("savant", url, params)


def parse_arsenals(text: Optional[str]) -> Dict[int, Dict[str, float]]:
    out: Dict[int, Dict[str, float]] = {}
    for row in _read_csv(text):
        pid = to_int(row.get("pitcher") or row.get("player_id"))
        if pid is None:
            continue
        mix: Dict[str, float] = {}
        for code, name in PITCH_CODES.items():
            v = to_float(row.get(code))
            if v and v > 0:
                mix[name] = v
        out[pid] = mix
    return out


def fetch_statcast_batter(client: Client, batter_id: int, start: str, end: str) -> Optional[str]:
    url = f"{BASE}/statcast_search/csv"
    params = {
        "all": "true",
        "type": "details",
        "player_type": "batter",
        "batters_lookup[]": [batter_id],
        "game_date_gt": start,
        "game_date_lt": end,
        "min_results": 0,
    }
    return client.get_text("savant", url, params)


def parse_statcast(text: Optional[str]) -> List[dict]:
    """One dict per *batted ball* (rows with a launch_speed)."""
    out: List[dict] = []
    for row in _read_csv(text):
        ev = to_float(row.get("launch_speed"))
        if ev is None:
            continue
        out.append({
            "game_date": row.get("game_date"),
            "pitch_type": (row.get("pitch_type") or "").strip().upper(),
            "events": (row.get("events") or "").strip(),
            "launch_speed": ev,
            "launch_angle": to_float(row.get("launch_angle")),
            "hit_distance_sc": to_float(row.get("hit_distance_sc")),
            "bb_type": (row.get("bb_type") or "").strip(),
            "stand": (row.get("stand") or "").strip().upper(),       # batter hand
            "p_throws": (row.get("p_throws") or "").strip().upper(),  # pitcher hand
        })
    return out


def parse_pa_events(text: Optional[str]) -> List[dict]:
    """One dict per plate-appearance outcome (rows with a non-empty `events`).

    Includes strikeouts/walks, unlike parse_statcast -- needed to compute a
    correct batting average over a recent window.
    """
    out: List[dict] = []
    for row in _read_csv(text):
        ev = (row.get("events") or "").strip()
        if not ev:
            continue
        out.append({"game_date": row.get("game_date"), "events": ev})
    return out


def fetch_statcast_bvp(client: Client, batter_id: int, pitcher_id: int,
                       start: str, end: str) -> Optional[str]:
    """All pitches a batter has seen from a specific pitcher (career window)."""
    url = f"{BASE}/statcast_search/csv"
    params = {
        "all": "true", "type": "details", "player_type": "batter",
        "batters_lookup[]": [batter_id], "pitchers_lookup[]": [pitcher_id],
        "game_date_gt": start, "game_date_lt": end, "min_results": 0,
    }
    return client.get_text("savant", url, params)


def parse_bvp(text: Optional[str]) -> List[dict]:
    """One dict per pitch (includes non-contact pitches), newest first."""
    out: List[dict] = []
    for row in _read_csv(text):
        out.append({
            "game_date": row.get("game_date"),
            "inning": to_int(row.get("inning")),
            "balls": to_int(row.get("balls")),
            "strikes": to_int(row.get("strikes")),
            "pitch_type": (row.get("pitch_type") or "").strip().upper(),
            "release_speed": to_float(row.get("release_speed")),
            "description": (row.get("description") or "").strip(),
            "events": (row.get("events") or "").strip(),
            "launch_speed": to_float(row.get("launch_speed")),
            "launch_angle": to_float(row.get("launch_angle")),
            "hit_distance_sc": to_float(row.get("hit_distance_sc")),
            "bb_type": (row.get("bb_type") or "").strip(),
            "xwoba": to_float(row.get("estimated_woba_using_speedangle")),
        })
    return out
