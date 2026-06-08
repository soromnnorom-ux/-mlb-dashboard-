"""§6.1 Environment score -- set the slate first; suppress HRs in dead air."""
from __future__ import annotations

from ..config import Config
from ..model.schemas import Game, Park


def _temp_pts(temp_f, env) -> int:
    if temp_f is None:
        return 0
    if temp_f >= env.temp_elite:
        return 2
    if temp_f >= env.temp_boost:
        return 1
    if temp_f < env.temp_cold:
        return -1
    return 0


def _wind_pts(wind_out, wind_mph, env) -> int:
    if wind_out == "out":
        if wind_mph is not None and wind_mph >= env.wind_out_strong:
            return 2
        return 1
    if wind_out == "in":
        return -1
    # calm / cross / unknown
    return 0


def _park_pts(team: str, park: Park | None, cfg: Config) -> int:
    if team in cfg.elite_parks:
        return 2
    if team in cfg.solid_parks:
        return 1
    if team in cfg.pitcher_parks:
        return -1
    # fall back to hr_factor buckets when park isn't explicitly listed
    f = park.hr_factor if park else 1.0
    if f >= 1.10:
        return 2
    if f >= 1.03:
        return 1
    if f <= 0.93:
        return -1
    return 0


def tier_for(score: int) -> str:
    if score >= 4:
        return "elite"
    if score >= 2:
        return "good"
    if score >= 0:
        return "neutral"
    return "dead-air"


def score_environment(game: Game, cfg: Config) -> Game:
    park = game.park
    w = game.weather
    closed = bool(park and park.roof == "closed")

    temp_pts = 0 if closed else _temp_pts(w.temp_f, cfg.thresholds.env)
    wind_pts = 0 if closed else _wind_pts(w.wind_out, w.wind_mph, cfg.thresholds.env)
    park_pts = _park_pts(game.home_team, park, cfg)

    score = temp_pts + wind_pts + park_pts
    game.env_score = score
    game.env_tier = tier_for(score)
    game.env_breakdown = {
        "temp_pts": temp_pts,
        "wind_pts": wind_pts,
        "park_pts": park_pts,
        "roof_closed": int(closed),
    }
    return game
