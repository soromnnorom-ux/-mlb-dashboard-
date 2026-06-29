"""Raw CSV dumps -- the analyst's own-view layer."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pandas as pd

from ..model.schemas import Game, Matchup, Pitcher


def _games_rows(games: List[Game]) -> List[dict]:
    rows = []
    for g in games:
        w = g.weather
        rows.append({
            "game_pk": g.game_pk,
            "date": g.date,
            "time_utc": g.game_time_utc,
            "matchup": f"{g.away_team}@{g.home_team}",
            "venue": g.venue_name,
            "status": g.status,
            "env_score": g.env_score,
            "env_tier": g.env_tier,
            "temp_f": w.temp_f,
            "wind_mph": w.wind_mph,
            "wind_dir_deg": w.wind_dir_deg,
            "wind_out": w.wind_out,
            "condition": w.condition,
            "precip_pct": w.precip_pct,
            "park_hr_factor": g.park.hr_factor if g.park else None,
            "roof": g.park.roof if g.park else None,
            "home_sp": g.home_pitcher_name,
            "away_sp": g.away_pitcher_name,
        })
    return rows


def _weather_rows(games: List[Game]) -> List[dict]:
    rows = []
    for g in games:
        w = g.weather
        rows.append({
            "game_pk": g.game_pk,
            "matchup": f"{g.away_team}@{g.home_team}",
            "venue": g.venue_name,
            "source": w.source,
            "temp_f": w.temp_f,
            "wind_mph": w.wind_mph,
            "wind_dir_deg": w.wind_dir_deg,
            "wind_out": w.wind_out,
            "wind_text": w.wind_text,
            "condition": w.condition,
            "precip_pct": w.precip_pct,
        })
    return rows


def _pitcher_rows(pitchers: Dict[int, Pitcher]) -> List[dict]:
    rows = []
    for p in pitchers.values():
        rows.append({
            "player_id": p.player_id,
            "name": p.name,
            "throws": p.throws,
            "ip": p.ip,
            "era": p.era,
            "hr": p.hr,
            "hr9": p.hr9,
            "hrfb_pct": p.hrfb_pct,
            "barrel_pct_allowed": p.barrel_pct_allowed,
            "avg_ev_allowed": p.avg_ev_allowed,
            "hardhit_pct_allowed": p.hardhit_pct_allowed,
            "k_pct": p.k_pct,
            "whiff_pct": p.whiff_pct,
            "fastball_usage": p.fastball_usage,
            "fb_pct": p.fb_pct,
            "pitcher_score": p.pitcher_score,
            "regression_flag": p.regression_flag,
            "small_sample": p.small_sample,
            # multi-season split (Batch 8)
            "pitcher_2025_stats": json.dumps(p.s2025) if p.s2025 else "",
            "pitcher_trend_grade": p.trend.get("grade") if p.trend else None,
            "pitcher_trend_label": p.trend.get("label") if p.trend else None,
            "pitcher_trend_reasons": "|".join(p.trend.get("reasons", [])) if p.trend else "",
            "pitch_mix_change": p.pitch_mix_change.get("summary", "") if p.pitch_mix_change else "",
            "pitch_mix_change_flags": "|".join(p.pitch_mix_change.get("flags", [])) if p.pitch_mix_change else "",
            "more_attackable_2026": p.trend.get("more_attackable_2026") if p.trend else None,
        })
    return rows


def _batter_rows(matchups: List[Matchup]) -> List[dict]:
    seen = {}
    for m in matchups:
        b = m.batter
        seen[b.player_id] = {
            "player_id": b.player_id,
            "name": b.name,
            "team": b.team,
            "bats": b.bats,
            "lineup_state": b.lineup_state,
            "pulled_at": b.pulled_at,
            "batting_order": b.batting_order,
            "pa": b.pa,
            "ba": b.ba,
            "slg": b.slg,
            "xslg": b.xslg,
            "iso": b.iso,
            "xiso": b.xiso,
            "woba": b.woba,
            "xwoba": b.xwoba,
            "barrel_pct": b.barrel_pct,
            "avg_ev": b.avg_ev,
            "hardhit_pct": b.hardhit_pct,
            "la_avg": b.la_avg,
            "fb_pct": b.fb_pct,
            "pull_pct": b.pull_pct,
            "l30_h": b.l30_h,
            "l30_ab": b.l30_ab,
        }
    return list(seen.values())


def _matchup_rows(matchups: List[Matchup]) -> List[dict]:
    rows = []
    for m in matchups:
        b, p = m.batter, m.pitcher
        rows.append({
            "batter_id": b.player_id,
            "batter": b.name,
            "team": b.team,
            "bats": b.bats,
            "order": b.batting_order,
            "lineup_state": b.lineup_state,
            "pulled_at": b.pulled_at,
            "opp_team": m.opp_team,
            "opp_sp": p.name if p else None,
            "opp_sp_throws": p.throws if p else None,
            "platoon": m.platoon,
            "opp_bullpen_hr9": m.opp_bullpen_hr9,
            "env_tier": m.env_tier,
            "env_score": m.env_score,
            "pitcher_score": m.pitcher_score,
            "regression_flag": p.regression_flag if p else None,
            "barrel_pct": b.barrel_pct,
            "avg_ev": b.avg_ev,
            "hardhit_pct": b.hardhit_pct,
            "barrel_vs_pm": b.barrel_vs_pm,
            "barrel_vs_pm_bbe": b.barrel_vs_pm_bbe,
            "barrel_vs_hand": b.barrel_vs_hand,
            # power profile -- persisted for future model/backtest validation
            # (xslg/xiso are the park- and luck-stable expected stats).
            "slg": b.slg,
            "iso": b.iso,
            "xslg": b.xslg,
            "xiso": b.xiso,
            "la_avg": b.la_avg,
            "ev_logs": "|".join(str(int(x)) for x in b.recent_ev_logs),
            "l30_avg": b.l30_avg,
            # missed-HR detail (Phase 9)
            "missed_hr": b.missed_hr,
            "missed_hr_ev": b.missed_hr_ev,
            "missed_hr_dist": b.missed_hr_dist,
            "missed_hr_la": b.missed_hr_la,
            "missed_hr_pitch": b.missed_hr_pitch,
            "missed_hr_date": b.missed_hr_date,
            # recent contact cluster (Phase 10)
            "hot_contact": b.hot_contact,
            "cluster_label": b.cluster_label,
            "cluster_score": b.cluster_score,
            "ev95_w": b.ev95_w,
            "ev100_w": b.ev100_w,
            "ev105_w": b.ev105_w,
            "ev100_l5g": b.ev100_l5g,
            "ev105_l7g": b.ev105_l7g,
            "ev110_l7g": b.ev110_l7g,
            # multi-season split (Batch 8)
            "batter_2025_stats": json.dumps(b.s2025) if b.s2025 else "",
            "batter_l30_stats": json.dumps(b.win30) if b.win30 else "",
            "batter_l14_stats": json.dumps(b.win14) if b.win14 else "",
            "batter_l7_stats": json.dumps(b.win7) if b.win7 else "",
            "weighted_profile": json.dumps(b.weighted) if b.weighted else "",
            "trend_grade": b.trend.get("grade") if b.trend else None,
            "trend_label": b.trend.get("label") if b.trend else None,
            "trend_reasons": "|".join(b.trend.get("reasons", [])) if b.trend else "",
            "sample_warnings": "|".join(b.sample_warnings),
            # BvP (Batch 10) -- supporting signal only
            "bvp_grade": (b.bvp or {}).get("grade"),
            "bvp_sample_size": (b.bvp or {}).get("sample_size"),
            "bvp_confidence": (b.bvp or {}).get("confidence"),
            "bvp_edge_label": (b.bvp or {}).get("edge_label"),
            "bvp_small_sample": (b.bvp or {}).get("small_sample"),
            "bvp_grade_capped": (b.bvp or {}).get("grade_capped"),
            "bvp_pa": (b.bvp or {}).get("pa"),
            "bvp_avg": (b.bvp or {}).get("avg"),
            "bvp_slg": (b.bvp or {}).get("slg"),
            "bvp_ops": (b.bvp or {}).get("ops"),
            "bvp_iso": (b.bvp or {}).get("iso"),
            "bvp_hr": (b.bvp or {}).get("hr"),
            "bvp_tb": (b.bvp or {}).get("tb"),
            "bvp_k": (b.bvp or {}).get("k"),
            "bvp_bb": (b.bvp or {}).get("bb"),
            "bvp_avg_ev": (b.bvp or {}).get("avg_ev"),
            "bvp_max_ev": (b.bvp or {}).get("max_ev"),
            "bvp_barrels": (b.bvp or {}).get("barrels"),
            "bvp_hardhit": (b.bvp or {}).get("hardhit"),
            "bvp_reasons": "|".join((b.bvp or {}).get("reasons", [])),
            "bvp_pitch_history": json.dumps((b.bvp or {}).get("pitch_history", [])) if b.bvp else "",
            "batter_score": m.batter_score,
            "edge_bonus": m.edge_bonus,
            "play_score": m.play_score,
            "tier": m.tier,
            "tags": "|".join(dict.fromkeys(list(m.tags) + list(b.tags))),
            "bets": "|".join(f"{k}:{v}" for k, v in m.bets.items()),
            "model_hr_prob": m.prob_by_bet.get("HR"),
            "model_tb_prob": m.prob_by_bet.get("TB"),
            "hr_odds": m.odds_by_bet.get("HR"),
            "hr_ev": m.ev_by_bet.get("HR"),
            "value": m.value,
        })
    return rows


def write_all(outdir: str | Path, games: List[Game], pitchers: Dict[int, Pitcher],
              matchups: List[Matchup]) -> List[str]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    tables = {
        "games.csv": _games_rows(games),
        "weather.csv": _weather_rows(games),
        "pitchers.csv": _pitcher_rows(pitchers),
        "batters.csv": _batter_rows(matchups),
        "matchups.csv": _matchup_rows(matchups),
    }
    for fname, rows in tables.items():
        df = pd.DataFrame(rows)
        path = outdir / fname
        df.to_csv(path, index=False)
        written.append(str(path))
    return written
