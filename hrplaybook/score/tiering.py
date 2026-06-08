"""§6.6 tiering + composite PLAY_SCORE, with dead-air HR suppression and the
4-5 play cap."""
from __future__ import annotations

from typing import List

from ..config import Config
from ..model.schemas import Matchup
from . import env_tier_rank
from .batter import score_batter
from .edges import edge_bonus, recency_fade


def score_matchup(m: Matchup, cfg: Config) -> Matchup:
    """Fill batter_score, edge_bonus, perfect/gate flags, tags, play_score, tier."""
    bs = score_batter(m.batter, cfg)
    m.batter_score = bs["score"]
    m.perfect_profile = bs["perfect"]
    m.gate_passed = bs["gate_passed"]
    m.gate_kind = bs["gate_kind"]

    bonus, etags = edge_bonus(m, cfg)
    m.edge_bonus = bonus
    # merge tags (batter tags already on the batter object)
    m.tags = list(dict.fromkeys(list(m.batter.tags) + etags))

    fade = recency_fade(m, cfg)
    m.play_score = round(
        m.env_score + m.pitcher_score + m.batter_score + m.edge_bonus + fade, 2
    )

    m.tier = _assign_tier(m, cfg)
    return m


def _assign_tier(m: Matchup, cfg: Config) -> int | None:
    env_rank = env_tier_rank(m.env_tier)

    # dead-air: never an HR tier (still eligible for TB cards elsewhere)
    if m.env_tier == "dead-air":
        return None

    has_edge = m.perfect_profile or m.batter.missed_hr or m.batter.hot_contact

    # Tier 1 (CORE)
    if m.gate_passed and env_rank >= 2 and m.pitcher_score >= 3 and has_edge:
        return 1

    # Tier 2 (ENV-BOOSTED): elite environment, metrics not perfect
    if m.env_tier == "elite" and (m.gate_passed or m.batter_score >= 1):
        return 2

    # Tier 3 (LOTTERY): some positive signal but not a real edge
    if m.batter_score >= 2 or m.tags or env_rank >= 2:
        return 3

    return None


def finalize_tiers(matchups: List[Matchup], cfg: Config) -> List[Matchup]:
    """Cap HR (Tier 1) plays to max_plays by PLAY_SCORE; demote the overflow."""
    tier1 = sorted(
        [m for m in matchups if m.tier == 1],
        key=lambda x: x.play_score,
        reverse=True,
    )
    for extra in tier1[cfg.max_plays:]:
        extra.tier = 2  # demote overflow CORE plays to ENV-BOOSTED
    return matchups
