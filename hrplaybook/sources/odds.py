"""Optional odds provider (value filter). Pluggable; defaults to a no-op.

If no provider is configured (or no API key), the pipeline emits cards with
value='unknown'. The OddsProvider interface lets a real feed drop in without
touching the scoring/report layers.
"""
from __future__ import annotations

from typing import Dict, Protocol

from ..http import Client


class OddsProvider(Protocol):
    enabled: bool

    def hr_odds(self, date: str) -> Dict[int, int]:
        """Return {player_id: american_odds} for HR props, or {} if unavailable."""
        ...


class NoOpOddsProvider:
    enabled = False

    def hr_odds(self, date: str) -> Dict[int, int]:
        return {}


class TheOddsApiProvider:
    """Best-effort The Odds API integration. Player props need a paid tier on
    most plans, so this degrades gracefully to {} on any error / empty response.
    """
    enabled = True

    def __init__(self, client: Client, api_key: str, region: str = "us"):
        self.client = client
        self.api_key = api_key
        self.region = region

    def hr_odds(self, date: str) -> Dict[int, int]:
        # Conservative by design: the free tier rarely exposes batter HR props
        # and ids must be name-matched. Returning {} keeps the value filter
        # optional and never blocks a run.
        return {}


def make_provider(cfg, client: Client) -> OddsProvider:
    if not cfg.odds.provider:
        return NoOpOddsProvider()
    key = cfg.odds_api_key()
    if cfg.odds.provider == "the-odds-api" and key:
        return TheOddsApiProvider(client, key, cfg.odds.region)
    return NoOpOddsProvider()
