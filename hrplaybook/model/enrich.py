"""Join batters to the opposing pitcher's pitch mix and fold in recent-window
Statcast signals (barrel% vs pitch-mix, EV logs, L30 AVG, missed-HR / hot-contact)."""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from ..config import Config
from ..model.schemas import Batter, Pitcher
from ..sources.savant import FASTBALL_TYPES
from ..util import is_barrel

HIT_EVENTS = {"single", "double", "triple", "home_run"}
AB_OUT_EVENTS = {
    "field_out", "strikeout", "strikeout_double_play", "grounded_into_double_play",
    "double_play", "force_out", "fielders_choice", "fielders_choice_out",
    "field_error", "triple_play", "other_out",
}


def attach_arsenal(pitcher: Pitcher, mix: Optional[Dict[str, float]]) -> Pitcher:
    if not mix:
        return pitcher
    pitcher.arsenal = dict(mix)
    pitcher.fastball_usage = round(
        sum(u for pt, u in mix.items() if pt in FASTBALL_TYPES), 1
    )
    return pitcher


def primary_pitches(mix: Optional[Dict[str, float]], min_usage: float = 10.0) -> Set[str]:
    if not mix:
        return set()
    prim = {pt for pt, u in mix.items() if u >= min_usage}
    if prim:
        return prim
    # fall back to top-3 by usage
    return {pt for pt, _ in sorted(mix.items(), key=lambda kv: -kv[1])[:3]}


def _recent_dates(rows: List[dict], n: int) -> Set[str]:
    dates = sorted({r["game_date"] for r in rows if r.get("game_date")}, reverse=True)
    return set(dates[:n])


def enrich_batter(
    batter: Batter,
    batted_balls: List[dict],
    pa_events: List[dict],
    pitcher: Optional[Pitcher],
    cfg: Config,
) -> Batter:
    """Mutate `batter` in place with matchup- and recent-window-derived fields."""
    batter.recent_window_used = bool(batted_balls or pa_events)

    # --- barrel% vs the pitcher's pitch mix --------------------------------
    mix = pitcher.arsenal if pitcher else {}
    prim = primary_pitches(mix)
    relevant = [b for b in batted_balls if (not prim or b["pitch_type"] in prim)]
    if len(relevant) >= 5:
        barrels = sum(1 for b in relevant if is_barrel(b["launch_speed"], b["launch_angle"]))
        batter.barrel_vs_pm = round(barrels / len(relevant) * 100.0, 1)
        batter.barrel_vs_pm_bbe = len(relevant)
    else:
        # too few batted balls vs this mix -> fall back to season barrel%
        batter.barrel_vs_pm = batter.barrel_pct
        batter.barrel_vs_pm_bbe = len(relevant)
        if batter.recent_window_used:
            batter.tags.append("SMALL_PM_SAMPLE")

    # --- recent EV logs (most recent batted balls) -------------------------
    batter.recent_ev_logs = [round(b["launch_speed"]) for b in batted_balls[:8]]

    # --- L30 batting average from PA outcomes ------------------------------
    h = sum(1 for e in pa_events if e["events"] in HIT_EVENTS)
    ab = sum(1 for e in pa_events if e["events"] in HIT_EVENTS or e["events"] in AB_OUT_EVENTS)
    if ab:
        batter.l30_h, batter.l30_ab = h, ab

    # --- missed-HR tracker -------------------------------------------------
    mh = cfg.missed_hr
    if any(
        b["launch_speed"] >= mh.ev
        and (b["hit_distance_sc"] or 0) >= mh.dist
        and b["events"] != "home_run"
        for b in batted_balls
    ):
        batter.missed_hr = True
        batter.tags.append("MISSED_HR")

    # --- hot-contact cluster ----------------------------------------------
    hc = cfg.hot_contact
    recent = _recent_dates(batted_balls, hc.games)
    hard = sum(
        1 for b in batted_balls
        if b["game_date"] in recent and b["launch_speed"] >= hc.ev
    )
    if hard >= hc.count:
        batter.hot_contact = True
        batter.tags.append("HOT_CONTACT")

    # --- recency fade: homered in last 1-2 games ---------------------------
    last2 = _recent_dates(batted_balls, 2)
    if any(b["events"] == "home_run" and b["game_date"] in last2 for b in batted_balls):
        batter.recent_hr = True
        batter.tags.append("RECENT_HR")

    return batter
