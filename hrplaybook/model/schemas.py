"""Core dataclasses shared across sources, scoring, and reporting."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Park:
    team: str
    park_name: str
    lat: Optional[float]
    lon: Optional[float]
    orientation_deg: Optional[float]
    roof: str = "open"          # open | retractable | closed
    hr_factor: float = 1.0
    venue_id: Optional[int] = None


@dataclass
class Weather:
    temp_f: Optional[float] = None
    wind_mph: Optional[float] = None
    wind_dir_deg: Optional[float] = None     # meteorological "from" direction
    wind_text: Optional[str] = None          # e.g. "5 mph, Out To CF"
    condition: Optional[str] = None
    precip_pct: Optional[float] = None
    source: str = "none"                     # statsapi | open-meteo | none
    wind_out: Optional[str] = None           # out | in | cross | calm | unknown


@dataclass
class Pitcher:
    player_id: int
    name: str
    team: str = ""
    throws: Optional[str] = None             # L | R
    season: Optional[int] = None
    small_sample: bool = False
    ip: Optional[float] = None
    era: Optional[float] = None
    games: Optional[int] = None
    hr: Optional[int] = None
    hr9: Optional[float] = None
    hrfb_pct: Optional[float] = None         # derived (approx)
    barrel_pct_allowed: Optional[float] = None
    avg_ev_allowed: Optional[float] = None
    hardhit_pct_allowed: Optional[float] = None
    k_pct: Optional[float] = None
    bb_pct: Optional[float] = None
    whiff_pct: Optional[float] = None
    fb_pct: Optional[float] = None           # flyballs allowed %
    gb_pct: Optional[float] = None
    gs: Optional[int] = None                 # games started (reliever if ~0)
    woba: Optional[float] = None
    xwoba: Optional[float] = None
    arsenal: Dict[str, float] = field(default_factory=dict)  # pitch_type -> usage %
    fastball_usage: Optional[float] = None   # ff+si+fc
    pitcher_score: int = 0
    regression_flag: bool = False
    score_breakdown: Dict[str, int] = field(default_factory=dict)


@dataclass
class Batter:
    player_id: int
    name: str
    team: str = ""
    bats: Optional[str] = None               # L | R | S
    season: Optional[int] = None
    batting_order: Optional[int] = None      # 1..9 lineup slot
    lineup_state: str = "unknown"            # projected | confirmed | unknown
    pulled_at: Optional[str] = None
    pa: Optional[int] = None
    ba: Optional[float] = None
    obp: Optional[float] = None
    slg: Optional[float] = None
    iso: Optional[float] = None
    xslg: Optional[float] = None
    xiso: Optional[float] = None
    woba: Optional[float] = None
    xwoba: Optional[float] = None
    barrel_pct: Optional[float] = None       # barrel% of BBE (season)
    avg_ev: Optional[float] = None
    hardhit_pct: Optional[float] = None
    la_avg: Optional[float] = None
    fb_pct: Optional[float] = None
    pull_pct: Optional[float] = None
    barrel_vs_pm: Optional[float] = None     # barrel% on the pitcher's pitch mix
    barrel_vs_pm_bbe: int = 0
    barrel_vs_hand: Optional[float] = None   # barrel% vs the opposing SP's hand
    barrel_vs_hand_bbe: int = 0
    recent_ev_logs: List[float] = field(default_factory=list)
    l30_h: Optional[int] = None
    l30_ab: Optional[int] = None
    recent_window_used: bool = False
    missed_hr: bool = False
    hot_contact: bool = False
    recent_hr: bool = False
    tags: List[str] = field(default_factory=list)

    @property
    def l30_avg(self) -> Optional[float]:
        if self.l30_ab:
            return round(self.l30_h / self.l30_ab, 3)
        return None


@dataclass
class Game:
    game_pk: int
    date: str
    game_time_utc: Optional[str]
    venue_id: Optional[int]
    venue_name: str
    home_team: str
    away_team: str
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None
    home_pitcher_id: Optional[int] = None
    away_pitcher_id: Optional[int] = None
    home_pitcher_name: Optional[str] = None
    away_pitcher_name: Optional[str] = None
    status: str = ""
    weather: Weather = field(default_factory=Weather)
    park: Optional[Park] = None
    env_score: int = 0
    env_tier: str = "neutral"                # elite | good | neutral | dead-air
    env_breakdown: Dict[str, int] = field(default_factory=dict)


@dataclass
class Matchup:
    """One batter vs the opposing probable starter in a given game."""
    batter: Batter
    pitcher: Optional[Pitcher]
    game: Game
    side: str                                # "home" | "away" (batter's side)
    opp_team: str = ""
    env_score: int = 0
    env_tier: str = "neutral"
    pitcher_score: int = 0
    batter_score: int = 0
    edge_bonus: int = 0
    play_score: float = 0.0
    perfect_profile: bool = False
    gate_passed: bool = False
    gate_kind: str = ""                      # elite | practical
    platoon: str = "neutral"                 # fav | unfav | neutral
    opp_bullpen_hr9: Optional[float] = None
    tier: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    model_prob: Optional[float] = None       # model P(>=1 HR)
    implied_prob: Optional[float] = None      # implied P(HR) from odds
    value: str = "unknown"                   # +EV | fair | -EV | unknown (HR)
    bets: Dict[str, str] = field(default_factory=dict)
    # per-bet odds/EV when an odds provider is wired
    odds_by_bet: Dict[str, int] = field(default_factory=dict)
    prob_by_bet: Dict[str, float] = field(default_factory=dict)
    ev_by_bet: Dict[str, float] = field(default_factory=dict)
    value_by_bet: Dict[str, str] = field(default_factory=dict)


def load_parks(path: str | Path) -> Dict[str, Park]:
    """Load parks.csv into a dict keyed by team abbreviation (StatsAPI style)."""
    parks: Dict[str, Park] = {}
    p = Path(path)
    if not p.exists():
        return parks
    with p.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            def _f(k):
                v = row.get(k, "")
                return float(v) if v not in ("", None) else None
            vid = row.get("venue_id", "")
            parks[row["team"].strip().upper()] = Park(
                team=row["team"].strip().upper(),
                park_name=row.get("park_name", "").strip(),
                lat=_f("lat"),
                lon=_f("lon"),
                orientation_deg=_f("orientation_deg"),
                roof=(row.get("roof") or "open").strip().lower(),
                hr_factor=_f("hr_factor") or 1.0,
                venue_id=int(vid) if str(vid).strip().isdigit() else None,
            )
    return parks
