"""§6.5 advanced edges -> bonus points + tags.

Implemented: missed-HR bounce-back, hot-contact cluster, pitcher regression
spot, recency fade. Not wired: LATE_HR (bullpen exposure) -- needs a reliever
HR/9 feed that isn't part of the free core sources; left as a documented TODO
so the score stays honest about what it measures.
"""
from __future__ import annotations

from typing import List, Tuple

from ..config import Config
from ..model.schemas import Matchup


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
    return bonus, tags


def recency_fade(m: Matchup, cfg: Config) -> float:
    """Negative weight if the batter just homered (fade who already went yard)."""
    return cfg.recency_fade_weight if m.batter.recent_hr else 0.0
