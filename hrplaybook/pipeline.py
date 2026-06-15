"""Application layer: the slate-building use case.

Clean-architecture seam between the interface layer (CLI, FastAPI web) and the
domain/infrastructure layers (sources/*, model/*, score/*, engines, report/*).
CLI and web both depend on THIS — neither imports the other. Framework-free
(no typer/fastapi here); progress goes to stderr.

Behavior is identical to the previous cli.build_slate / _write_outputs.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import bvp, seasons
from .cache import DiskCache
from .config import Config
from .http import Client
from .model.enrich import attach_arsenal, bullpen_hr9, enrich_batter
from .model.schemas import Batter, Game, Matchup, Pitcher, load_parks
from .report import cards, cheatsheet, csvout
from .report.picks import write_picks
from .score.bettypes import map_bets
from .score.environment import score_environment
from .score.pitcher import score_pitcher
from .score.tiering import finalize_tiers, score_matchup
from .score.value import apply_value
from .sources import rotowire, savant, statsapi, weather
from .sources.odds import make_provider
from .util import normalize_name, now_stamp, window_start

CACHE_DIR = Path.home() / ".cache" / "hrplaybook"


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- #
# Infrastructure wiring
# --------------------------------------------------------------------------- #
def make_client(cfg: Config, offline: bool, force_refresh: bool = False) -> Client:
    cache = DiskCache(CACHE_DIR, cfg.cache_ttl_minutes)
    return Client(
        cache,
        user_agent=cfg.user_agent,
        rate_limit_per_sec=cfg.rate_limit_per_sec,
        offline=offline,
        force_refresh=force_refresh,
    )


def _opp(side: str) -> str:
    return "away" if side == "home" else "home"


def _weak_non_threat(b: Batter) -> bool:
    """Clear non-HR-threat by season profile -> skip the per-batter Statcast pull
    (saves requests). barrel_vs_pm falls back to season barrel%; TB/Hits cards
    still work off season contact metrics."""
    return (b.barrel_pct is not None and b.barrel_pct < 2.0
            and (b.slg or 0) < 0.330)


def _lineup_for_side(box: Optional[dict], side: str, game: Game,
                     projected: Dict[str, List[dict]]) -> Tuple[List[dict], str]:
    """Return (lineup_entries, state). Prefer confirmed StatsAPI boxscore."""
    confirmed = statsapi.parse_lineup(box, side)
    if confirmed:
        return confirmed, "confirmed"
    team = game.home_team if side == "home" else game.away_team
    proj = projected.get(team, [])
    return proj, ("projected" if proj else "none")


def _resolve_batter(entry: dict, pool: Dict[int, Batter],
                    name_index: Dict[str, int]) -> Batter:
    pid = entry.get("player_id")
    if pid and pid in pool:
        import copy
        return copy.deepcopy(pool[pid])
    # projected entries arrive without ids -> match by normalized name
    key = normalize_name(entry.get("name", ""))
    mid = name_index.get(key)
    if mid is not None and mid in pool:
        import copy
        b = copy.deepcopy(pool[mid])
        return b
    # unknown player: minimal record, flagged (no metrics)
    b = Batter(player_id=pid or -abs(hash(key)) % (10 ** 8), name=entry.get("name", "?"))
    b.tags.append("NO_METRICS")
    return b


def _build_pitcher(pid: Optional[int], name: Optional[str], pool: Dict[int, Pitcher],
                   arsenals: Dict[int, Dict[str, float]], cfg: Config) -> Optional[Pitcher]:
    if pid is None:
        return None
    p = pool.get(pid)
    if p is None:
        # below the qualified (min=q) leaderboard threshold -> stub, scores 0
        p = Pitcher(player_id=pid, name=name or "SP", small_sample=True)
    attach_arsenal(p, arsenals.get(pid))
    score_pitcher(p, cfg)
    return p


def build_slate(
    date: str,
    cfg: Config,
    client: Client,
    use_statcast: bool = True,
    use_odds: bool = True,
    limit_games: Optional[int] = None,
    full_statcast: bool = False,
    progress: bool = True,
    game_types: Optional[set] = None,
) -> Tuple[List[Game], Dict[int, Pitcher], List[Matchup], List[str]]:
    warnings: List[str] = []
    parks = load_parks(cfg.parks_path)

    # 1. slate
    games = statsapi.parse_schedule(statsapi.fetch_schedule(client, date), game_types)
    if limit_games:
        games = games[:limit_games]
    if not games:
        warnings.append("No games found for this date.")
    for g in games:
        g.park = parks.get(g.home_team)
        if g.park is None:
            warnings.append(f"No park metadata for {g.home_team}; using neutral env.")

    # 2-4. weather + environment
    for g in games:
        g.weather = weather.get_weather(client, g, g.park)
        score_environment(g, cfg)

    # 5. season pools (one fetch each, heavily cached)
    batter_pool = savant.parse_batter_leaderboard(
        savant.fetch_batter_leaderboard(client, cfg.season, cfg.savant_batter_min), cfg.season)
    pitcher_pool = savant.parse_pitcher_leaderboard(
        savant.fetch_pitcher_leaderboard(client, cfg.season, cfg.savant_pitcher_min), cfg.season)
    arsenals = savant.parse_arsenals(savant.fetch_arsenals(client, cfg.season))
    if not batter_pool:
        warnings.append("Batter leaderboard empty (network?); metrics will be sparse.")
    name_index = {normalize_name(b.name): pid for pid, b in batter_pool.items()}

    # 5b. multi-season baseline pools (2025) — ids are stable across seasons.
    # Use a LOWER baseline min so non-qualified bats still get a baseline.
    baseline_year = cfg.season - 1
    bmin25 = getattr(cfg, "savant_batter_min_2025", None) or cfg.savant_batter_min
    pmin25 = getattr(cfg, "savant_pitcher_min_2025", None) or cfg.savant_pitcher_min
    batter_pool_2025 = savant.parse_batter_leaderboard(
        savant.fetch_batter_leaderboard(client, baseline_year, bmin25), baseline_year)
    pitcher_pool_2025 = savant.parse_pitcher_leaderboard(
        savant.fetch_pitcher_leaderboard(client, baseline_year, pmin25), baseline_year)
    arsenals_2025 = savant.parse_arsenals(savant.fetch_arsenals(client, baseline_year))

    # probables
    pitchers_used: Dict[int, Pitcher] = {}
    for g in games:
        for pid, nm in ((g.home_pitcher_id, g.home_pitcher_name),
                        (g.away_pitcher_id, g.away_pitcher_name)):
            if pid and pid not in pitchers_used:
                p = _build_pitcher(pid, nm, pitcher_pool, arsenals, cfg)
                if p:
                    pitchers_used[pid] = p

    # handedness for the probable starters (one batched call) -> platoon edge
    people = statsapi.parse_people(statsapi.fetch_people(client, list(pitchers_used)))
    for pid, info in people.items():
        if pid in pitchers_used and info.get("throws"):
            pitchers_used[pid].throws = info["throws"]

    # multi-season split for pitchers: 2025 baseline + pitch-mix change + trend
    for pid, p in pitchers_used.items():
        p.s2025 = seasons.pitcher_baseline(pitcher_pool_2025.get(pid))
        p.arsenal_2025 = arsenals_2025.get(pid, {})
        p.pitch_mix_change = seasons.pitch_mix_change(p.arsenal_2025, p.arsenal)
        p.trend = seasons.pitcher_trend(
            p.s2025, {"hr9": p.hr9, "barrel_pct_allowed": p.barrel_pct_allowed,
                      "k_pct": p.k_pct}, p.pitch_mix_change, ip_2026=p.ip)
        if p.pitch_mix_change.get("changed"):
            p.sample_warnings.append("PITCH_MIX_CHANGE")

    # 6. projected fallback lineups (one page for the whole slate)
    projected = rotowire.parse_rotowire(rotowire.fetch_lineups_html(client, date))

    # 6b. bullpen exposure: opponent reliever HR/9 per team (LATE_HR edge)
    team_ids: Dict[str, int] = {}
    for g in games:
        if g.home_team_id:
            team_ids[g.home_team] = g.home_team_id
        if g.away_team_id:
            team_ids[g.away_team] = g.away_team_id
    bullpen: Dict[str, Optional[float]] = {}
    for abbr, tid in team_ids.items():
        roster = statsapi.parse_roster_pitchers(statsapi.fetch_roster(client, tid))
        bullpen[abbr] = bullpen_hr9(roster, pitcher_pool, cfg)

    # 7. lineups -> batters -> matchups
    matchups: List[Matchup] = []
    pulled_at = now_stamp()
    win_start = window_start(date, cfg.recent_window_days)
    bvp_start = f"{cfg.season - 3}-01-01"   # ~3-season BvP career window

    # --- Phase A: resolve every batter (no network) + collect who needs a pull.
    # Same eligibility predicate as before -> identical set of enriched batters.
    records: List[dict] = []
    elig_by_game: Dict[int, set] = {}                  # game_pk -> {batter_id}
    elig_by_side: Dict[tuple, set] = {}                # (game_pk, opp_pid) -> {batter_id}
    for g in games:
        box = statsapi.fetch_boxscore(client, g.game_pk)
        for side in ("home", "away"):
            entries, state = _lineup_for_side(box, side, g, projected)
            opp_pid = g.away_pitcher_id if side == "home" else g.home_pitcher_id
            opp_pitcher = pitchers_used.get(opp_pid) if opp_pid else None
            opp_team = g.away_team if side == "home" else g.home_team
            bat_team = g.home_team if side == "home" else g.away_team
            for entry in entries:
                b = _resolve_batter(entry, batter_pool, name_index)
                b.team = bat_team
                b.batting_order = entry.get("order")
                b.lineup_state = state
                b.pulled_at = pulled_at
                if entry.get("bats") and not b.bats:
                    b.bats = entry["bats"]
                b.s2025 = seasons.batter_baseline(batter_pool_2025.get(b.player_id))
                eligible = (use_statcast and b.player_id > 0
                            and not (not full_statcast and _weak_non_threat(b)))
                records.append({"b": b, "game": g, "side": side, "state": state,
                                "opp_pitcher": opp_pitcher, "opp_team": opp_team,
                                "eligible": eligible})
                if eligible:
                    elig_by_game.setdefault(g.game_pk, set()).add(b.player_id)
                    if opp_pitcher and opp_pitcher.player_id:
                        elig_by_side.setdefault((g.game_pk, opp_pitcher.player_id),
                                                set()).add(b.player_id)

    # --- Phase B: ONE statcast call per game + ONE BvP call per side, regrouped
    # per batter (replaces ~540 serial calls with ~45; same rows per batter).
    statcast_groups: Dict[int, dict] = {}
    for ids in elig_by_game.values():
        text = savant.fetch_statcast_batters(client, sorted(ids), win_start, date)
        balls_by = savant.group_statcast_by_batter(savant.parse_statcast(text))
        pa_by = savant.group_pa_events_by_batter(savant.parse_pa_events(text))
        for bid in ids:
            statcast_groups[bid] = {"balls": balls_by.get(bid, []),
                                    "pa": pa_by.get(bid, [])}
    bvp_groups: Dict[tuple, list] = {}
    for (game_pk, pid), ids in elig_by_side.items():
        text = savant.fetch_statcast_bvp_multi(client, sorted(ids), pid, bvp_start, date)
        by = savant.group_bvp_by_batter(savant.parse_bvp(text))
        for bid in ids:
            bvp_groups[(bid, pid)] = by.get(bid, [])

    # --- Phase C: enrich + score (pure CPU; identical to the old per-batter path)
    stat_n = 0
    for r in records:
        b, opp_pitcher = r["b"], r["opp_pitcher"]
        if r["eligible"]:
            sg = statcast_groups.get(b.player_id) or {"balls": [], "pa": []}
            balls = sg["balls"]
            enrich_batter(b, balls, sg["pa"], opp_pitcher, cfg)
            b.win30 = seasons.window_metrics(balls, date, 30)
            b.win14 = seasons.window_metrics(balls, date, 14)
            b.win7 = seasons.window_metrics(balls, date, 7)
            if opp_pitcher and opp_pitcher.player_id:
                b.bvp = bvp.build(bvp_groups.get((b.player_id, opp_pitcher.player_id), []))
                if b.bvp:
                    b.tags.extend(t for t in b.bvp.get("tags", []) if t not in b.tags)
            stat_n += 1
            if progress and stat_n % 20 == 0:
                _progress(f"   …statcast enriched for {stat_n} batters")
        elif b.barrel_vs_pm is None:
            b.barrel_vs_pm = b.barrel_pct  # fallback when statcast skipped

        _cur = {"barrel_pct": b.barrel_pct, "avg_ev": b.avg_ev,
                "hardhit_pct": b.hardhit_pct}
        b.weighted = seasons.weighted_profile(b.s2025, _cur, b.win30,
                                              bet="HR", pa_2026=b.pa)
        b.trend = seasons.batter_trend(b.s2025, _cur, b.win30, pa_2026=b.pa)
        b.sample_warnings = list(b.weighted.get("warnings", []))

        m = Matchup(batter=b, pitcher=opp_pitcher, game=r["game"], side=r["side"],
                    opp_team=r["opp_team"])
        m.env_score = r["game"].env_score
        m.env_tier = r["game"].env_tier
        m.pitcher_score = opp_pitcher.pitcher_score if opp_pitcher else 0
        m.opp_bullpen_hr9 = bullpen.get(r["opp_team"])
        score_matchup(m, cfg)
        map_bets(m, cfg)
        matchups.append(m)

    # 8. cap HR plays
    finalize_tiers(matchups, cfg)

    # 2025 baseline coverage warning
    bat_ids = {m.batter.player_id for m in matchups}
    bat_with = {m.batter.player_id for m in matchups if m.batter.s2025}
    if bat_ids and len(bat_with) / len(bat_ids) < 0.70:
        warnings.append(
            f"LOW_2025_BASELINE_COVERAGE: {len(bat_with)}/{len(bat_ids)} batters "
            f"have a 2025 baseline.")

    # 9. value filter (model probs always; +EV verdict only when odds exist)
    odds_maps: Dict[str, Dict[int, int]] = {"HR": {}, "TB": {}}
    # NEVER auto-pull paid odds during a normal run unless auto_pull is enabled;
    # the explicit path is `hrplaybook odds-refresh` / the Refresh-API-Odds button.
    if use_odds and getattr(cfg.odds, "auto_pull", False):
        provider = make_provider(cfg, client, name_index)
        if getattr(provider, "enabled", False):
            odds_maps = provider.odds(date)
            if not any(odds_maps.values()):
                warnings.append("Odds provider returned no props; value=unknown.")
        else:
            warnings.append("No odds provider configured; value=unknown (model probs shown).")
    else:
        warnings.append("Live odds not auto-pulled (use odds-refresh / Refresh API Odds).")
    apply_value(matchups, odds_maps, cfg)

    # staleness / network warnings
    if client.offline:
        warnings.append("OFFLINE mode: served from cache; data may be stale.")
    if client.network_errors:
        warnings.append(
            f"{client.network_errors} network errors; some data served stale/empty.")
    if client.cache.stale_served:
        warnings.append(f"{client.cache.stale_served} stale cache entries served.")

    return games, pitchers_used, matchups, warnings


def write_outputs(date: str, games, pitchers, matchups, cfg, warnings) -> Path:
    import json as _json

    from .freshness import build_meta
    outdir = Path("out") / date
    csvout.write_all(outdir, games, pitchers, matchups)
    cheatsheet.render(date, games, matchups, cfg, outdir, warnings)
    cards.write_cards(outdir, matchups, cfg, date)
    write_picks(outdir, matchups, date)
    (outdir / "meta.json").write_text(
        _json.dumps(build_meta(date, games, matchups, warnings, cfg), indent=2))
    return outdir
