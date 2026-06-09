"""Typed configuration loaded from config.yaml (with sane defaults)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_PARKS_PATH = REPO_ROOT / "parks.csv"


class EliteGate(BaseModel):
    ev: float = 102
    barrel: float = 25
    hard_hit: float = 50


class PracticalGate(BaseModel):
    barrel_vs_pm: float = 10


class PerfectProfile(BaseModel):
    ev_min: float = 102
    ev_max: float = 110
    la_min: float = 25
    la_max: float = 33
    dist_min: float = 380
    dist_max: float = 430


class PitcherThresholds(BaseModel):
    hr9_high: float = 1.5
    hr9_mid: float = 1.2
    hrfb_high: float = 15
    hrfb_mid: float = 12
    k_low: float = 20
    whiff_low: float = 23
    # contact-quality-allowed knobs (extra; tunable, defaulted so older yaml works)
    barrel_allowed_high: float = 8.5
    ev_allowed_high: float = 89.5
    fastball_heavy: float = 55
    fb_allowed_high: float = 25
    regression_hr9_low: float = 1.0
    small_sample_ip: float = 30


class EnvThresholds(BaseModel):
    temp_elite: float = 85
    temp_boost: float = 75
    temp_cold: float = 60
    wind_out_strong: float = 10


class Thresholds(BaseModel):
    elite: EliteGate = Field(default_factory=EliteGate)
    practical: PracticalGate = Field(default_factory=PracticalGate)
    perfect_profile: PerfectProfile = Field(default_factory=PerfectProfile)
    pitcher: PitcherThresholds = Field(default_factory=PitcherThresholds)
    env: EnvThresholds = Field(default_factory=EnvThresholds)


class HotContact(BaseModel):
    ev: float = 95
    count: int = 3
    games: int = 5


class MissedHR(BaseModel):
    ev: float = 100
    dist: float = 380


class OddsConfig(BaseModel):
    provider: str = ""
    api_key_env: str = "ODDS_API_KEY"
    api_key_envs: List[str] = Field(
        default_factory=lambda: ["ODDS_API_KEY_1", "ODDS_API_KEY_2", "ODDS_API_KEY_3"])
    region: str = "us"
    books: str = ""                  # optional CSV of bookmaker keys to keep (best price if blank)
    auto_pull: bool = False          # NEVER auto-call the paid API unless True
    api_refresh_ttl_minutes: int = 15


class ValueModel(BaseModel):
    """Transparent empirical priors for the model probabilities (tunable)."""
    league_hr_pa: float = 0.0345     # league HR per plate appearance
    league_barrel: float = 8.0       # league barrel% of BBE baseline
    league_hr9: float = 1.25         # league starter HR/9 baseline
    pa_top: float = 4.6              # expected PA for the leadoff slot
    pa_decay: float = 0.15           # PA lost per lineup slot down
    pa_floor: float = 3.4
    platoon_fav: float = 1.10        # HR-rate multiplier with the platoon edge
    platoon_unfav: float = 0.90
    edge_threshold: float = 0.05     # model must beat implied by this fraction for +EV


class Platoon(BaseModel):
    fav_bonus: float = 1.0           # play_score nudge with the platoon edge
    unfav_penalty: float = -1.0


class Bullpen(BaseModel):
    hr9_high: float = 1.3            # opponent bullpen HR/9 that triggers LATE_HR
    reliever_max_gs: int = 2         # games-started ceiling to count as a reliever


class Config(BaseModel):
    season: int = 2026
    max_plays: int = 5
    gate: str = "practical"  # "practical" | "elite"

    thresholds: Thresholds = Field(default_factory=Thresholds)
    recent_window_days: int = 30
    hot_contact: HotContact = Field(default_factory=HotContact)
    missed_hr: MissedHR = Field(default_factory=MissedHR)
    recency_fade_weight: float = -1
    platoon: Platoon = Field(default_factory=Platoon)
    bullpen: Bullpen = Field(default_factory=Bullpen)
    value_model: ValueModel = Field(default_factory=ValueModel)

    elite_parks: List[str] = Field(default_factory=lambda: ["CIN", "BAL", "TOR", "COL"])
    solid_parks: List[str] = Field(default_factory=lambda: ["LAD", "NYM", "CHC"])
    pitcher_parks: List[str] = Field(default_factory=lambda: ["SEA", "SF", "MIA"])

    # Savant leaderboard min-PA thresholds. "q" = qualified (~145 batters / 52
    # pitchers only); numeric strings widen coverage to bench/platoon/call-ups.
    savant_batter_min: str = "25"
    savant_pitcher_min: str = "10"
    # 2025 baseline pull uses a LOWER min so platoon bats / call-ups / injury
    # returns still get a baseline (props often involve non-qualified hitters).
    savant_batter_min_2025: str = "25"
    savant_pitcher_min_2025: str = "10"

    rate_limit_per_sec: float = 1
    cache_ttl_minutes: Dict[str, int] = Field(
        default_factory=lambda: {
            "schedule": 60, "lineups": 5, "savant": 720, "weather": 60,
            "people": 1440, "roster": 1440, "odds": 10, "results": 1440,
        }
    )
    user_agent: str = "hrplaybook/1.0 (personal use)"
    odds: OddsConfig = Field(default_factory=OddsConfig)

    parks_path: str = str(DEFAULT_PARKS_PATH)

    def odds_api_key(self) -> str | None:
        if not self.odds.provider:
            return None
        return os.environ.get(self.odds.api_key_env)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    data: dict = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    cfg = Config(**data)
    if "parks_path" not in data:
        cfg.parks_path = str(DEFAULT_PARKS_PATH)
    return cfg
