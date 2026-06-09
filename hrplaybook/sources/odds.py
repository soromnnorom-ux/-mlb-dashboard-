"""Optional odds provider (value filter). Pluggable; defaults to a no-op.

If no provider is configured (or no API key), the pipeline emits cards with
value='unknown'. The OddsProvider interface lets a real feed drop in without
touching the scoring/report layers.

The Odds API player-prop markets used:
  batter_home_runs   -> Over 0.5  == 1+ HR
  batter_total_bases -> Over 1.5  == 2+ TB
Player props live on the per-event odds endpoint and may require a paid plan;
everything degrades gracefully to {} on any error so a run never blocks.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from ..http import Client
from ..util import normalize_name

ODDS_BASE = "https://api.the-odds-api.com/v4/sports/baseball_mlb"


class OddsProvider(Protocol):
    enabled: bool

    def odds(self, date: str) -> Dict[str, Dict[int, int]]:
        """Return {"HR": {player_id: american}, "TB": {player_id: american}}."""
        ...


class NoOpOddsProvider:
    enabled = False

    def odds(self, date: str) -> Dict[str, Dict[int, int]]:
        return {"HR": {}, "TB": {}}


def parse_event_odds(
    event_odds: dict,
    market_key: str,
    point: float,
    name_index: Dict[str, int],
    books: Optional[set] = None,
) -> Dict[int, int]:
    """Best (highest-payout) Over `point` american odds per matched player id."""
    out: Dict[int, int] = {}
    if not event_odds:
        return out
    for bm in event_odds.get("bookmakers", []) or []:
        if books and bm.get("key") not in books:
            continue
        for mkt in bm.get("markets", []) or []:
            if mkt.get("key") != market_key:
                continue
            for o in mkt.get("outcomes", []) or []:
                if str(o.get("name", "")).lower() not in ("over", "yes"):
                    continue
                pt = o.get("point")
                if pt is not None and abs(float(pt) - point) > 0.01:
                    continue
                pid = name_index.get(normalize_name(o.get("description", "")))
                price = o.get("price")
                if pid is None or price is None:
                    continue
                price = int(price)
                if pid not in out or price > out[pid]:  # max() = best price
                    out[pid] = price
    return out


class TheOddsApiProvider:
    enabled = True

    def __init__(self, client: Client, api_key: str, name_index: Dict[str, int],
                 region: str = "us", books: str = ""):
        self.client = client
        self.api_key = api_key
        self.name_index = name_index
        self.region = region
        self.books = {b.strip() for b in books.split(",") if b.strip()} or None

    def _events(self, date: str) -> List[dict]:
        data = self.client.get_json(
            "odds", f"{ODDS_BASE}/events",
            {"apiKey": self.api_key, "dateFormat": "iso"})
        if not isinstance(data, list):
            return []
        return [e for e in data if str(e.get("commence_time", "")).startswith(date)]

    def odds(self, date: str) -> Dict[str, Dict[int, int]]:
        hr: Dict[int, int] = {}
        tb: Dict[int, int] = {}
        try:
            for ev in self._events(date):
                eid = ev.get("id")
                if not eid:
                    continue
                eo = self.client.get_json(
                    "odds", f"{ODDS_BASE}/events/{eid}/odds",
                    {"apiKey": self.api_key, "regions": self.region,
                     "markets": "batter_home_runs,batter_total_bases",
                     "oddsFormat": "american"})
                if not eo:
                    continue
                hr.update(parse_event_odds(eo, "batter_home_runs", 0.5,
                                           self.name_index, self.books))
                tb.update(parse_event_odds(eo, "batter_total_bases", 1.5,
                                           self.name_index, self.books))
        except Exception:
            pass
        return {"HR": hr, "TB": tb}


def live_key_tester(timeout: float = 8.0):
    """Return a KeyTester(key)->(valid, quota_remaining, error) for The-Odds-API.

    Hits the lightweight /sports endpoint and reads the x-requests-remaining
    header. The key is only ever sent to the provider; it is never logged.
    """
    import httpx

    def _test(key: str):
        try:
            r = httpx.get("https://api.the-odds-api.com/v4/sports",
                          params={"apiKey": key}, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return False, None, type(e).__name__
        if r.status_code == 200:
            q = r.headers.get("x-requests-remaining")
            return True, (int(q) if q and q.isdigit() else None), None
        if r.status_code in (401, 403):
            return False, None, "unauthorized"
        if r.status_code == 422:
            return False, None, "invalid_key"
        return False, None, f"http_{r.status_code}"

    return _test


def make_provider(cfg, client: Client, name_index: Dict[str, int]) -> OddsProvider:
    if not cfg.odds.provider:
        return NoOpOddsProvider()
    key = cfg.odds_api_key()
    if cfg.odds.provider == "the-odds-api" and key:
        return TheOddsApiProvider(client, key, name_index, cfg.odds.region, cfg.odds.books)
    return NoOpOddsProvider()
