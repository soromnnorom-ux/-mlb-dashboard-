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
from .pipeline import (  # orchestration moved to the application layer
    CACHE_DIR, build_slate, make_client as _make_client,
    write_outputs as _write_outputs,
)

app = typer.Typer(add_completion=False, help="MLB HR Playbook cheat-sheet generator.")




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


def build_cron(proj: Path, hour_morning: int, hour_refresh: int, exe: str) -> str:
    """Local crontab snippet for the daily game-day routine.

    Cron fields are `minute hour dom mon dow` -> `0 H * * *` = H:00 local.
    Morning: grade yesterday (grows the ledger/calibration) then build today.
    Pre-first-pitch: pull confirmed lineups + live odds for today's slate.
    """
    log = f"{proj}/out/cron.log"
    return (
        f"# hrplaybook game-day automation (added {now_stamp()})\n"
        f"0 {hour_morning} * * *  cd {proj} && {exe} grade --date yesterday >> {log} 2>&1 && "
        f"{exe} run --date today --full-statcast >> {log} 2>&1\n"
        f"0 {hour_refresh} * * *  cd {proj} && {exe} refresh --date today >> {log} 2>&1 && "
        f"{exe} odds-refresh --date today >> {log} 2>&1\n"
    )


@app.command("schedule-install")
def schedule_install(
    hour_morning: int = typer.Option(9, help="local hour for grade-yesterday + build-today"),
    hour_refresh: int = typer.Option(16, help="local hour for lineups + odds refresh (pre-first-pitch)"),
):
    """Write a crontab snippet for the daily game-day routine (local cron)."""
    proj = Path.cwd()
    exe = str(proj / ".venv" / "bin" / "hrplaybook")
    if not Path(exe).exists():
        exe = "hrplaybook"
    snippet = build_cron(proj, hour_morning, hour_refresh, exe)
    out = proj / "out" / "hrplaybook.cron"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(snippet)
    typer.echo(snippet)
    typer.echo(f"Wrote {out}")
    typer.echo(f"Install:  crontab -l 2>/dev/null | cat - {out} | crontab -")
    typer.echo("(Local cron — runs on THIS machine so it can read .env + the out/ cache. "
               "A remote /schedule agent cannot drive a local install.)")


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
