"""§6.7 value filter (optional). Compares a heuristic model HR probability to
the implied probability from odds. No odds -> value='unknown'."""
from __future__ import annotations

from typing import Dict, List

from ..config import Config
from ..model.schemas import Matchup


def implied_prob(american_odds: int) -> float:
    if american_odds < 0:
        return (-american_odds) / ((-american_odds) + 100.0)
    return 100.0 / (american_odds + 100.0)


def estimate_hr_prob(m: Matchup) -> float:
    """Rough model HR probability from the composite play score.

    Deliberately simple/transparent: a ~4% floor scaled by play score, capped at
    30%. This is a heuristic, not a trained model -- it exists so the value
    filter has something to compare against when odds are available.
    """
    p = 0.04 + 0.012 * max(0.0, m.play_score)
    return round(min(0.30, max(0.03, p)), 3)


def apply_value(matchups: List[Matchup], odds_map: Dict[int, int], cfg: Config) -> None:
    if not odds_map:
        return  # leave value='unknown'
    for m in matchups:
        odds = odds_map.get(m.batter.player_id)
        if odds is None:
            continue
        m.implied_prob = round(implied_prob(odds), 3)
        m.model_prob = estimate_hr_prob(m)
        if m.model_prob > m.implied_prob * 1.05:
            m.value = "+EV"
        elif m.model_prob < m.implied_prob * 0.95:
            m.value = "-EV"
        else:
            m.value = "fair"
