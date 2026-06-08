"""hrplaybook CLI: run / lineups / refresh / schedule-install."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import typer

from .cache import DiskCache
from .config import Config, load_config
from .http import Client
from .model.enrich import attach_arsenal, enrich_batter
from .model.schemas import Batter, Game, Matchup, Pitcher, load_parks
from .report import cards, cheatsheet, csvout
from .score import env_tier_rank
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
def _make_client(cfg: Config, offline: bool) -> Client:
    cache = DiskCache(CACHE_DIR, cfg.cache_ttl_minutes)
    return Client(
        cache,
        user_agent=cfg.user_agent,
        rate_limit_per_sec=cfg.rate_limit_per_sec,
        offline=offline,
    )


def _opp(side: str) -> str:
    return "away" if side == "home" else "home"


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
) -> Tuple[List[Game], Dict[int, Pitcher], List[Matchup], List[str]]:
    warnings: List[str] = []
    parks = load_parks(cfg.parks_path)

    # 1. slate
    games = statsapi.parse_schedule(statsapi.fetch_schedule(client, date))
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

    # probables
    pitchers_used: Dict[int, Pitcher] = {}
    for g in games:
        for pid, nm in ((g.home_pitcher_id, g.home_pitcher_name),
                        (g.away_pitcher_id, g.away_pitcher_name)):
            if pid and pid not in pitchers_used:
                p = _build_pitcher(pid, nm, pitcher_pool, arsenals, cfg)
                if p:
                    pitchers_used[pid] = p

    # 6. projected fallback lineups (one page for the whole slate)
    projected = rotowire.parse_rotowire(rotowire.fetch_lineups_html(client, date))

    # 7. lineups -> batters -> matchups
    matchups: List[Matchup] = []
    pulled_at = now_stamp()
    win_start = window_start(date, cfg.recent_window_days)
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

                if use_statcast and b.player_id > 0:
                    text = savant.fetch_statcast_batter(client, b.player_id, win_start, date)
                    enrich_batter(b, savant.parse_statcast(text),
                                  savant.parse_pa_events(text), opp_pitcher, cfg)
                elif b.barrel_vs_pm is None:
                    b.barrel_vs_pm = b.barrel_pct  # fallback when statcast disabled

                m = Matchup(batter=b, pitcher=opp_pitcher, game=g, side=side,
                            opp_team=opp_team)
                m.env_score = g.env_score
                m.env_tier = g.env_tier
                m.pitcher_score = opp_pitcher.pitcher_score if opp_pitcher else 0
                score_matchup(m, cfg)
                map_bets(m, cfg)
                matchups.append(m)

    # 8. cap HR plays
    finalize_tiers(matchups, cfg)

    # 9. value filter (optional)
    if use_odds:
        provider = make_provider(cfg, client)
        if getattr(provider, "enabled", False):
            apply_value(matchups, provider.hr_odds(date), cfg)
        else:
            warnings.append("No odds provider configured; value=unknown.")

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
    outdir = Path("out") / date
    csvout.write_all(outdir, games, pitchers, matchups)
    cheatsheet.render(date, games, matchups, cfg, outdir, warnings)
    cards.write_cards(outdir, matchups, cfg, date)
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
            use_odds=not no_odds, limit_games=limit)
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


if __name__ == "__main__":
    app()
