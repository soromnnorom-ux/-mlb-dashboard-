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


def bullpen_hr9(roster_ids: List[int], pool: Dict[int, Pitcher], cfg: Config) -> Optional[float]:
    """Aggregate reliever HR/9 for a team from its active-roster pitchers.

    Relievers = roster pitchers in the leaderboard pool with games-started at or
    below the configured ceiling. Returns innings-weighted HR/9, or None when no
    reliever data resolved (so the LATE_HR edge simply doesn't fire).
    """
    tot_hr, tot_ip = 0.0, 0.0
    for pid in roster_ids:
        p = pool.get(pid)
        if p is None or p.ip is None or p.hr is None:
            continue
        if (p.gs or 0) > cfg.bullpen.reliever_max_gs:
            continue
        tot_hr += p.hr
        tot_ip += p.ip
    if tot_ip <= 0:
        return None
    return round(tot_hr / tot_ip * 9.0, 2)


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

    # fill batter handedness from the most common Statcast `stand` if unknown
    if not batter.bats:
        stands = [b.get("stand") for b in batted_balls if b.get("stand") in ("L", "R")]
        if stands:
            batter.bats = max(set(stands), key=stands.count)

    p_hand = pitcher.throws if pitcher else None

    def _barrel_pct(rows: List[dict]):
        if not rows:
            return None, 0
        bar = sum(1 for b in rows if is_barrel(b["launch_speed"], b["launch_angle"]))
        return round(bar / len(rows) * 100.0, 1), len(rows)

    # --- barrel% vs the opposing pitcher's hand (platoon-aware signal) ------
    if p_hand in ("L", "R"):
        vs_hand = [b for b in batted_balls if b.get("p_throws") == p_hand]
        bvh, n = _barrel_pct(vs_hand)
        if n >= 8:
            batter.barrel_vs_hand, batter.barrel_vs_hand_bbe = bvh, n

    # --- barrel% vs the pitcher's pitch mix --------------------------------
    # Prefer pitch-mix AND same-hand when that keeps a usable sample; otherwise
    # mix-only; otherwise the season barrel%.
    mix = pitcher.arsenal if pitcher else {}
    prim = primary_pitches(mix)
    mix_rows = [b for b in batted_balls if (not prim or b["pitch_type"] in prim)]
    hand_mix_rows = [b for b in mix_rows
                     if p_hand in ("L", "R") and b.get("p_throws") == p_hand]
    if len(hand_mix_rows) >= 5:
        batter.barrel_vs_pm, batter.barrel_vs_pm_bbe = _barrel_pct(hand_mix_rows)
    elif len(mix_rows) >= 5:
        batter.barrel_vs_pm, batter.barrel_vs_pm_bbe = _barrel_pct(mix_rows)
    else:
        batter.barrel_vs_pm = batter.barrel_pct
        batter.barrel_vs_pm_bbe = len(mix_rows)
        if batter.recent_window_used:
            batter.tags.append("SMALL_PM_SAMPLE")

    # --- recent EV logs (most recent batted balls) -------------------------
    batter.recent_ev_logs = [round(b["launch_speed"]) for b in batted_balls[:8]]

    # --- L30 batting average from PA outcomes ------------------------------
    h = sum(1 for e in pa_events if e["events"] in HIT_EVENTS)
    ab = sum(1 for e in pa_events if e["events"] in HIT_EVENTS or e["events"] in AB_OUT_EVENTS)
    if ab:
        batter.l30_h, batter.l30_ab = h, ab

    # --- missed-HR tracker (EV>=100, dist>=370, LA 20-38, not a HR) ---------
    mh = cfg.missed_hr
    missed = [
        b for b in batted_balls
        if b["launch_speed"] >= mh.ev
        and (b["hit_distance_sc"] or 0) >= mh.dist
        and (b["launch_angle"] is not None and 20 <= b["launch_angle"] <= 38)
        and b["events"] != "home_run"
    ]
    if missed:
        batter.missed_hr = True
        batter.tags.append("MISSED_HR")
        best = max(missed, key=lambda b: b["launch_speed"])
        batter.missed_hr_ev = round(best["launch_speed"], 1)
        batter.missed_hr_dist = round(best["hit_distance_sc"]) if best["hit_distance_sc"] else None
        batter.missed_hr_la = round(best["launch_angle"], 1) if best["launch_angle"] is not None else None
        batter.missed_hr_pitch = best.get("pitch_type") or None
        batter.missed_hr_date = best.get("game_date")

    # --- recent contact cluster (Phase 10) --------------------------------
    hc = cfg.hot_contact
    batter.ev95_w = sum(1 for b in batted_balls if b["launch_speed"] >= 95)
    batter.ev100_w = sum(1 for b in batted_balls if b["launch_speed"] >= 100)
    batter.ev105_w = sum(1 for b in batted_balls if b["launch_speed"] >= 105)
    last5 = _recent_dates(batted_balls, 5)
    batter.ev100_l5g = sum(1 for b in batted_balls
                           if b["game_date"] in last5 and b["launch_speed"] >= 100)
    ev95_l5 = sum(1 for b in batted_balls
                  if b["game_date"] in last5 and b["launch_speed"] >= 95)
    ev105_l5 = sum(1 for b in batted_balls
                   if b["game_date"] in last5 and b["launch_speed"] >= 105)
    recent = _recent_dates(batted_balls, hc.games)
    hard = sum(1 for b in batted_balls
               if b["game_date"] in recent and b["launch_speed"] >= hc.ev)
    if hard >= hc.count:
        batter.hot_contact = True
    # cluster label -- based on RECENT form (last 5 games), not 30-day totals
    if batter.ev100_l5g >= 6 or ev105_l5 >= 3:
        label = "NUCLEAR"
    elif hard >= hc.count or batter.ev100_l5g >= 3:
        label = "HOT"
    elif batter.recent_window_used and len(last5) >= 3 and ev95_l5 == 0:
        label = "COLD"
    else:
        label = "NORMAL"
    batter.cluster_label = label
    batter.cluster_score = int(min(100, batter.ev95_w * 4 + batter.ev100_w * 6
                                   + batter.ev105_w * 10))
    if label == "NUCLEAR":
        batter.tags.append("NUCLEAR_CONTACT")
    if label == "HOT" and "HOT_CONTACT" not in batter.tags:
        batter.tags.append("HOT_CONTACT")
    elif label == "HOT":
        pass
    if batter.ev100_w >= 2:
        batter.tags.append("MULTIPLE_100_EV")
    if label == "COLD":
        batter.tags.append("COLD_CONTACT")

    # --- recency fade: homered in last 1-2 games ---------------------------
    last2 = _recent_dates(batted_balls, 2)
    if any(b["events"] == "home_run" and b["game_date"] in last2 for b in batted_balls):
        batter.recent_hr = True
        batter.tags.append("RECENT_HR")

    return batter
