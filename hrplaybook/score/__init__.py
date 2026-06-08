"""Scoring engine: environment, pitcher weakness, batter gate, edges, tiering,
value, bet-type mapping. All pure functions over model dataclasses."""

ENV_TIER_RANK = {"dead-air": 0, "neutral": 1, "good": 2, "elite": 3}


def env_tier_rank(tier: str) -> int:
    return ENV_TIER_RANK.get(tier, 1)
