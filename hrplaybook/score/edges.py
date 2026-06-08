"""§6.5 advanced edges -> bonus points + tags.

Implemented: missed-HR bounce-back, hot-contact cluster, pitcher regression
spot, recency fade, LATE_HR bullpen exposure, and the platoon (handedness) edge.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..config import Config
from ..model.schemas import Matchup


def determine_platoon(bats: Optional[str], throws: Optional[str]) -> str:
    """fav | unfav | neutral from batter hand vs pitcher hand.

    Switch hitters always take the platoon side -> fav. Opposite hands -> fav.
    Same hand (L/L, R/R) -> unfav. Missing data -> neutral.
    """
    if not bats or not throws:
        return "neutral"
    if bats == "S":
        return "fav"
    if bats != throws:        # L vs R or R vs L
        return "fav"
    return "unfav"            # same hand


def edge_bonus(m: Matchup, cfg: Config) -> Tuple[int, List[str]]:
    bonus = 0
    tags: List[str] = []
    b = m.batter
    if b.missed_hr:
        bonus += 1
    if b.hot_contact:
        bonus += 1
    if m.pitcher and m.pitcher.regression_flag:
        bonus += 1
        tags.append("REGRESSION_SPOT")
    # LATE_HR: opponent bullpen is homer-prone -> late-game HR chance
    if m.opp_bullpen_hr9 is not None and m.opp_bullpen_hr9 >= cfg.bullpen.hr9_high:
        bonus += 1
        tags.append("LATE_HR")
    return bonus, tags


def platoon_adjust(m: Matchup, cfg: Config) -> float:
    """Set m.platoon, and return the play_score nudge for the handedness edge."""
    throws = m.pitcher.throws if m.pitcher else None
    m.platoon = determine_platoon(m.batter.bats, throws)
    if m.platoon == "fav":
        return cfg.platoon.fav_bonus
    if m.platoon == "unfav":
        return cfg.platoon.unfav_penalty
    return 0.0


def recency_fade(m: Matchup, cfg: Config) -> float:
    """Negative weight if the batter just homered (fade who already went yard)."""
    return cfg.recency_fade_weight if m.batter.recent_hr else 0.0
