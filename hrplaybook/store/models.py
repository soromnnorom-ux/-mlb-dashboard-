"""Typed API response models (the contract the dashboard can rely on).

Replaces the implicit "frontend assumes the CSV->JSON shape" coupling with
explicit, validated schemas. FastAPI can declare these as response_model.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class MatchupOut(BaseModel):
    date: str
    batter_id: int
    batter: Optional[str] = None
    team: Optional[str] = None
    opp_team: Optional[str] = None
    opp_sp: Optional[str] = None
    lineup_state: Optional[str] = None
    order: Optional[int] = None
    env_tier: Optional[str] = None
    env_score: Optional[float] = None
    pitcher_score: Optional[float] = None
    barrel_vs_pm: Optional[float] = None
    play_score: Optional[float] = None
    tier: Optional[int] = None
    model_hr_prob: Optional[float] = None
    model_tb_prob: Optional[float] = None
    value: Optional[str] = None
    bets: Optional[str] = None
    tags: Optional[str] = None


class SlateOut(BaseModel):
    date: str
    exists: bool
    n_matchups: int
    matchups: List[MatchupOut] = []
