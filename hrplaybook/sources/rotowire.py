"""Rotowire projected lineups (pre-confirmation fallback). Best-effort scrape.

Verified against the daily-lineups HTML (2025): each game is a `lineup is-mlb`
block holding two `lineup__abbr` (away, home) and `is-visit`/`is-home` player
lists; each player li carries `lineup__pos`, an anchor name, and `lineup__bats`.
If the markup shifts and nothing parses, we return {} and the pipeline falls
back to confirmed-only lineups.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..http import Client

URL = "https://www.rotowire.com/baseball/daily-lineups.php"

# Rotowire -> StatsAPI abbreviation aliases
ALIASES = {
    "CHW": "CWS", "WAS": "WSH", "ARI": "AZ", "SDP": "SD", "SFG": "SF",
    "TBR": "TB", "KCR": "KC", "OAK": "ATH",
}

_PLAYER_RE = re.compile(
    r'<li class="lineup__player">\s*'
    r'<div class="lineup__pos">([A-Z0-9]+)</div>\s*'
    r'<a[^>]*>([^<]+)</a>\s*'
    r'<span class="lineup__bats">([LRS])',
    re.S,
)


def fetch_lineups_html(client: Client, date: str) -> Optional[str]:
    return client.get_text("lineups", URL, {"date": date})


def _norm_abbr(a: str) -> str:
    a = a.strip().upper()
    return ALIASES.get(a, a)


def parse_rotowire(html: Optional[str]) -> Dict[str, List[dict]]:
    """team_abbr -> ordered [{name, order, position, bats}] (projected)."""
    out: Dict[str, List[dict]] = {}
    if not html:
        return out
    chunks = re.split(r'class="lineup is-mlb', html)
    for chunk in chunks[1:]:
        abbrs = re.findall(r'lineup__abbr">([A-Z]{2,3})', chunk)
        if len(abbrs) < 2:
            continue
        away, home = _norm_abbr(abbrs[0]), _norm_abbr(abbrs[1])
        i_visit = chunk.find("is-visit")
        i_home = chunk.find("is-home")
        if i_visit == -1 or i_home == -1:
            continue
        regions = ((away, chunk[i_visit:i_home]), (home, chunk[i_home:]))
        for team, region in regions:
            players = []
            for order, m in enumerate(_PLAYER_RE.finditer(region), start=1):
                players.append({
                    "name": m.group(2).strip(),
                    "order": order,
                    "position": m.group(1),
                    "bats": m.group(3),
                })
                if order >= 9:
                    break
            if players:
                out[team] = players
    return out
