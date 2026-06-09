"""hrplaybook CLI: run / lineups / refresh / schedule-install."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import typer

from .cache import DiskCache
from .config import Config, load_config
from .grade import append_ledger, grade_picks, summarize
from .http import Client
from .model.enrich import attach_arsenal, bullpen_hr9, enrich_batter
from .model.schemas import Batter, Game, Matchup, Pitcher, load_parks
from .report import cards, cheatsheet, csvout
from . import bvp, seasons
from .report.picks import load_picks, write_picks
from .score.bettypes import map_bets
from .score.environment import score_environment
from .score.pitcher import score_pitcher
from .score.tiering import finalize_tiers, score_matchup
from .score.value import apply_value
from .sources import rotowire, savant, statsapi, weather
from .sources.odds import make_provider
from .util import normalize_name, now_stamp, resolve_date, window_start

app = typer.Typer(add_completion=False, help="MLB HR Playbook cheat-sheet generator.")
CACHE_DIR = Path.home() / ".cache" / "hrplaybook"


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def _make_client(cfg: Config, offline: bool, force_refresh: bool = False) -> Client:
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
    stat_n = 0
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

                # pull the Statcast window unless disabled or a clear non-threat
                if use_statcast and b.player_id > 0 and not (
                        not full_statcast and _weak_non_threat(b)):
                    text = savant.fetch_statcast_batter(client, b.player_id, win_start, date)
                    balls = savant.parse_statcast(text)
                    enrich_batter(b, balls, savant.parse_pa_events(text), opp_pitcher, cfg)
                    b.win30 = seasons.window_metrics(balls, date, 30)
                    b.win14 = seasons.window_metrics(balls, date, 14)
                    b.win7 = seasons.window_metrics(balls, date, 7)
                    # BvP: career history vs the opposing starter (supporting only)
                    if opp_pitcher and opp_pitcher.player_id:
                        b.bvp = bvp.build(savant.parse_bvp(savant.fetch_statcast_bvp(
                            client, b.player_id, opp_pitcher.player_id, bvp_start, date)))
                        if b.bvp:
                            b.tags.extend(t for t in b.bvp.get("tags", []) if t not in b.tags)
                    stat_n += 1
                    if progress and stat_n % 20 == 0:
                        typer.echo(f"   …statcast pulled for {stat_n} batters", err=True)
                elif b.barrel_vs_pm is None:
                    b.barrel_vs_pm = b.barrel_pct  # fallback when statcast skipped

                # multi-season weighted profile + trend (display/trend only)
                _cur = {"barrel_pct": b.barrel_pct, "avg_ev": b.avg_ev,
                        "hardhit_pct": b.hardhit_pct}
                b.weighted = seasons.weighted_profile(b.s2025, _cur, b.win30,
                                                      bet="HR", pa_2026=b.pa)
                b.trend = seasons.batter_trend(b.s2025, _cur, b.win30, pa_2026=b.pa)
                b.sample_warnings = list(b.weighted.get("warnings", []))

                m = Matchup(batter=b, pitcher=opp_pitcher, game=g, side=side,
                            opp_team=opp_team)
                m.env_score = g.env_score
                m.env_tier = g.env_tier
                m.pitcher_score = opp_pitcher.pitcher_score if opp_pitcher else 0
                m.opp_bullpen_hr9 = bullpen.get(opp_team)
                score_matchup(m, cfg)
                map_bets(m, cfg)
                matchups.append(m)

    # 8. cap HR plays
    finalize_tiers(matchups, cfg)

    # 2025 baseline coverage warning (Batch 9)
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


def _write_outputs(date: str, games, pitchers, matchups, cfg, warnings) -> Path:
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


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@app.command()
def run(
    date: str = typer.Option("today", help="today | tomorrow | YYYY-MM-DD"),
    season: Optional[int] = typer.Option(None, help="override config season"),
    no_odds: bool = typer.Option(False, "--no-odds", help="skip value filter"),
    max_plays: Optional[int] = typer.Option(None, help="cap on HR plays"),
    no_statcast: bool = typer.Option(False, "--no-statcast",
                                     help="skip per-batter Statcast window (faster)"),
    full_statcast: bool = typer.Option(False, "--full-statcast",
                                       help="pull Statcast for every batter (no non-threat pre-filter)"),
    offline: bool = typer.Option(False, "--offline", help="use cache only, no network"),
    limit: Optional[int] = typer.Option(None, help="limit number of games (testing)"),
    config: Optional[str] = typer.Option(None, help="path to config.yaml"),
):
    """Full pipeline -> CSVs + cheat sheet + bet-type cards under out/<date>/."""
    cfg = load_config(config)
    if season:
        cfg.season = season
    if max_plays:
        cfg.max_plays = max_plays
    date = resolve_date(date)
    client = _make_client(cfg, offline)
    try:
        games, pitchers, matchups, warnings = build_slate(
            date, cfg, client, use_statcast=not no_statcast,
            use_odds=not no_odds, limit_games=limit, full_statcast=full_statcast)
    finally:
        client.close()
    outdir = _write_outputs(date, games, pitchers, matchups, cfg, warnings)

    tier1 = sum(1 for m in matchups if m.tier == 1)
    typer.echo(f"✅ {date}: {len(games)} games, {len(matchups)} batter-matchups, "
               f"{tier1} Tier-1 HR plays")
    for w in warnings:
        typer.echo(f"   ⚠️  {w}")
    typer.echo(f"   📂 {outdir}/  ({client.cache.summary()})")


@app.command()
def refresh(
    date: str = typer.Option("today", help="today | YYYY-MM-DD"),
    config: Optional[str] = typer.Option(None),
    no_statcast: bool = typer.Option(False, "--no-statcast"),
):
    """Re-pull lineups + weather (short TTL) and rebuild outputs. Trusts the
    latest CONFIRMED lineup over earlier projected caches."""
    cfg = load_config(config)
    date = resolve_date(date)
    client = _make_client(cfg, offline=False)
    try:
        games, pitchers, matchups, warnings = build_slate(
            date, cfg, client, use_statcast=not no_statcast)
    finally:
        client.close()
    outdir = _write_outputs(date, games, pitchers, matchups, cfg, warnings)
    confirmed = sum(1 for m in matchups if m.batter.lineup_state == "confirmed")
    typer.echo(f"🔄 refreshed {date}: {confirmed} confirmed batter slots -> {outdir}/")


@app.command()
def backfill(
    start: str = typer.Option(..., help="first date YYYY-MM-DD"),
    end: str = typer.Option("today", help="last date (inclusive)"),
    lite: bool = typer.Option(True, help="skip per-batter Statcast (much faster; "
                              "season-stat picks only)"),
    config: Optional[str] = typer.Option(None),
):
    """Backfill a date range: build picks + grade vs results into out/_ledger.csv.

    One cached client is reused across all dates (season leaderboards/rosters are
    fetched once). LITE mode skips the per-batter Statcast pull. NOTE: season
    leaderboards are full-season aggregates -> look-ahead bias; this is a
    directional backtest, not a true point-in-time forward test.
    """
    import datetime as _dt

    cfg = load_config(config)
    d0 = _dt.date.fromisoformat(resolve_date(start))
    d1 = _dt.date.fromisoformat(resolve_date(end))
    client = _make_client(cfg, offline=False)
    final_states = {"Final", "Game Over", "Completed Early"}
    built = graded_dates = decided = skipped = errors = 0
    cur = d0
    try:
        while cur <= d1:
            ds = cur.isoformat()
            cfg.season = cur.year
            try:
                games, pitchers, matchups, warnings = build_slate(
                    ds, cfg, client, use_statcast=not lite, use_odds=False,
                    progress=False, game_types={"R"})  # regular season only
                if not games:
                    skipped += 1
                    cur += _dt.timedelta(days=1)
                    continue
                _write_outputs(ds, games, pitchers, matchups, cfg, warnings)
                built += 1
                results: Dict[int, dict] = {}
                for g in games:
                    if g.status in final_states:
                        results.update(statsapi.parse_boxscore_results(
                            statsapi.fetch_boxscore(client, g.game_pk)))
                rows = grade_picks(load_picks(Path("out") / ds), results)
                append_ledger(Path("out") / "_ledger.csv", rows)
                nd = sum(1 for r in rows if r["won"] is not None)
                decided += nd
                if nd:
                    graded_dates += 1
                typer.echo(f"  {ds}: {len(games)}g, {len(matchups)} mu, {nd} graded "
                           f"[{client.cache.summary()}]", err=True)
            except Exception as e:  # noqa: BLE001
                errors += 1
                typer.echo(f"  {ds}: ERROR {e}", err=True)
            cur += _dt.timedelta(days=1)
    finally:
        client.close()
    typer.echo(f"✅ backfill {d0}..{d1}: {built} slates built, {graded_dates} dates "
               f"graded ({decided} bets), {skipped} no-game days, {errors} errors")


@app.command()
def grade(
    date: str = typer.Option("yesterday", help="date of picks to grade (YYYY-MM-DD)"),
    config: Optional[str] = typer.Option(None),
):
    """Grade a prior day's picks against actual box-score results and update the
    rolling ledger (out/_ledger.csv)."""
    cfg = load_config(config)
    date = resolve_date(date)
    outdir = Path("out") / date
    picks = load_picks(outdir)
    if not picks:
        typer.echo(f"No picks.json under {outdir}/ — run `hrplaybook run --date {date}` first.")
        raise typer.Exit(1)

    client = _make_client(cfg, offline=False)
    final_states = {"Final", "Game Over", "Completed Early"}
    results: Dict[int, dict] = {}
    n_final = 0
    try:
        games = statsapi.parse_schedule(statsapi.fetch_schedule(client, date))
        for g in games:
            if g.status not in final_states:
                continue
            n_final += 1
            box = statsapi.fetch_boxscore(client, g.game_pk)
            results.update(statsapi.parse_boxscore_results(box))
    finally:
        client.close()

    rows = grade_picks(picks, results)
    summary = summarize(rows)
    append_ledger(Path("out") / "_ledger.csv", rows)

    decided = [r for r in rows if r["won"] is not None]
    typer.echo(f"📊 {date}: graded {len(decided)} bets across {n_final} final games "
               f"({len(rows) - len(decided)} void)\n")
    typer.echo(f"{'Bet':<6} {'W-L':>7} {'Hit%':>6} {'ROI':>7}")
    for bet in ("HR", "TB", "HRR", "Hits"):
        s = summary.get(bet)
        if not s:
            continue
        wl = f"{s['w']}-{s['l']}"
        hr = f"{s['hit_rate']*100:.0f}%" if s["hit_rate"] is not None else "—"
        roi = f"{s['roi']*100:+.0f}%" if s["roi"] is not None else "—"
        typer.echo(f"{bet:<6} {wl:>7} {hr:>6} {roi:>7}")

    # all-time ledger snapshot
    ledger = Path("out") / "_ledger.csv"
    if ledger.exists():
        import csv as _csv
        all_rows = []
        for r in _csv.DictReader(ledger.open()):
            all_rows.append({
                "bet": r["bet"],
                "won": (r["won"] == "True") if r["won"] in ("True", "False") else None,
                "profit": float(r["profit"]) if r.get("profit") not in (None, "", "None") else None,
            })
        allt = summarize(all_rows)
        tot_w = sum(s["w"] for s in allt.values())
        tot_l = sum(s["l"] for s in allt.values())
        typer.echo(f"\n🧾 all-time ledger: {tot_w}-{tot_l} "
                   f"({tot_w/(tot_w+tot_l)*100:.0f}% hit)" if (tot_w + tot_l) else "\n🧾 ledger empty")


@app.command()
def lineups(
    date: str = typer.Option("today"),
    config: Optional[str] = typer.Option(None),
    offline: bool = typer.Option(False, "--offline"),
):
    """Print projected/confirmed lineups and their states (no scoring)."""
    cfg = load_config(config)
    date = resolve_date(date)
    client = _make_client(cfg, offline)
    try:
        games = statsapi.parse_schedule(statsapi.fetch_schedule(client, date))
        projected = rotowire.parse_rotowire(rotowire.fetch_lineups_html(client, date))
        for g in games:
            typer.echo(f"\n{g.away_team} @ {g.home_team}  ({g.status})")
            box = statsapi.fetch_boxscore(client, g.game_pk)
            for side in ("away", "home"):
                entries, state = _lineup_for_side(box, side, g, projected)
                team = g.away_team if side == "away" else g.home_team
                typer.echo(f"  {team} [{state}]")
                for e in entries:
                    typer.echo(f"    {e.get('order','?'):>2}. {e.get('name','?')} "
                               f"({e.get('position','')})")
    finally:
        client.close()


@app.command("schedule-install")
def schedule_install(
    hour_run: int = typer.Option(9, help="hour to run morning build (local)"),
    hour_refresh: int = typer.Option(16, help="hour to refresh near first pitch"),
):
    """Write a crontab snippet that runs the morning build + an afternoon refresh."""
    exe = "hrplaybook"
    proj = Path.cwd()
    snippet = (
        f"# hrplaybook daily automation (added {now_stamp()})\n"
        f"{hour_run} 0  * * *  cd {proj} && {exe} run --date today >> {proj}/out/cron.log 2>&1\n"
        f"{hour_refresh} 0 * * *  cd {proj} && {exe} refresh --date today >> {proj}/out/cron.log 2>&1\n"
    )
    out = proj / "out" / "hrplaybook.cron"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(snippet)
    typer.echo(f"Wrote {out}\nInstall with:  crontab -l 2>/dev/null | cat - {out} | crontab -")


@app.command("odds-refresh")
def odds_refresh(
    date: str = typer.Option("today", help="today | YYYY-MM-DD"),
    markets: str = typer.Option("", help="CSV subset e.g. HR,TB,Hits,HRR,RBI"),
    region: str = typer.Option("", help="odds region (default from config)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="validate key only, no save"),
    force: bool = typer.Option(False, "--force", help="bypass odds cache"),
):
    """Explicitly pull live odds from The Odds API into out/<date>/api_odds.json."""
    import csv as _csv

    from . import odds_api, odds_keys
    odds_keys.load_dotenv()        # pick up keys from a git-ignored .env
    date = resolve_date(date)
    cfg = load_config()
    client = _make_client(cfg, offline=False)
    if force:
        client.force_refresh = True
    name_index: Dict[str, int] = {}
    mp = Path("out") / date / "matchups.csv"
    if mp.exists():
        for r in _csv.DictReader(mp.open()):
            bid = r.get("batter_id")
            if r.get("batter") and bid and str(bid).isdigit():
                name_index[normalize_name(r["batter"])] = int(bid)
    mk = [x.strip() for x in markets.split(",") if x.strip()] or None
    try:
        res = odds_api.pull(client, cfg, date, name_index, markets=mk,
                            region=region or None, dry_run=dry_run)
    finally:
        client.close()
    if not res.get("ok"):
        typer.echo(f"❌ odds-refresh: {res.get('error')}")
        for r in res.get("key_reports", []):
            typer.echo(f"   {r['env']}: {'valid' if r['valid'] else r['error']}")
        raise typer.Exit(1)
    if res.get("dry_run"):
        typer.echo(f"✅ dry-run OK · key {res['active_key_name']} · "
                   f"markets {res['markets_requested']} · quota {res.get('quota_remaining')}")
        return
    typer.echo(f"✅ key {res['active_key_name']} · pulled {res.get('markets_pulled') or []} · "
               f"{res['records_saved']} records · books {res.get('books')} · "
               f"quota {res.get('quota_remaining')} · unmatched {res.get('unmatched', 0)}")
    if res.get("errors"):
        typer.echo("   errors: " + ", ".join(res["errors"]))


@app.command("backfill-snapshots")
def backfill_snapshots(
    network: bool = typer.Option(False, "--network",
                                 help="rebuild slates from CURRENT data (not point-in-time)"),
):
    """Enrich historical picks.json with all-market raw probs from local matchups.csv."""
    from . import backfill_snapshots as bf
    from . import calibration
    before = calibration.coverage(calibration.load_tables("out"))
    if network:
        typer.echo("⚠️  WARNING: network backfill may not be point-in-time accurate "
                   "(uses current leaderboards -> possible lookahead bias).")
    res = bf.backfill("out", network=network)
    typer.echo(f"scanned {res['scanned']} dates · enriched {res['dates_enriched']} dates · "
               f"{res['picks_enriched']} picks enriched · {res['picks_skipped']} skipped")
    if res["reasons"]:
        typer.echo("skip/warn reasons: " + ", ".join(f"{k}={v}" for k, v in res["reasons"].items()))
    # rebuild calibration and show before/after sample coverage
    after_tables = calibration.save_tables("out")
    after = calibration.coverage(after_tables)
    typer.echo("\nCalibration sample by market (before -> after):")
    for mk in calibration.COVERAGE_MARKETS:
        b = before.get(mk, {}).get("n", 0)
        a = after.get(mk, {}).get("n", 0)
        typer.echo(f"  {mk:>4}: {b} -> {a}   [{after.get(mk, {}).get('status')}]")
    typer.echo("Run `hrplaybook calibrate` is already done (tables rebuilt).")


@app.command()
def calibrate():
    """(Re)build empirical probability-calibration tables from the result ledger."""
    from . import calibration
    tables = calibration.save_tables("out")
    if not tables:
        typer.echo("No graded history yet (out/_ledger.csv empty) — nothing to calibrate.")
        raise typer.Exit(0)
    for bet, t in sorted(tables.items()):
        typer.echo(f"{bet}: baseline {t['_baseline']} over {t['_n']} bets")
        for bk, e in t["buckets"].items():
            flag = "  ⚠ low-sample" if e["n"] < calibration.MIN_SAMPLE else ""
            typer.echo(f"   {bk:>6}: n={e['n']:5d}  raw {e['avg_raw']:.2f} -> actual {e['actual']:.2f}{flag}")
    typer.echo("Wrote out/_calibration.json")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="bind host"),
    port: int = typer.Option(8000, help="bind port"),
    reload: bool = typer.Option(False, "--reload", help="auto-reload (dev)"),
):
    """Launch the local web dashboard (run/refresh/grade + slate/cards/ledger)."""
    try:
        import uvicorn
    except ImportError:
        typer.echo("Install web deps:  pip install fastapi uvicorn")
        raise typer.Exit(1)
    typer.echo(f"🌐 hrplaybook dashboard -> http://{host}:{port}")
    uvicorn.run("hrplaybook.web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
