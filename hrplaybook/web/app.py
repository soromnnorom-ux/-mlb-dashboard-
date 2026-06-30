"""FastAPI dashboard for hrplaybook.

Serves the generated slate (games, tiers, bet cards, full matchup table, and the
grading ledger) and lets you trigger run / refresh / grade from the browser.
Long pipeline jobs run in a background thread; the UI polls for completion.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import threading
import traceback
import uuid
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..pipeline import build_slate, make_client as _make_client, write_outputs as _write_outputs
from ..config import load_config
from ..grade import append_ledger, grade_picks, summarize
from ..report.picks import load_picks
from ..sources import statsapi
from ..util import resolve_date, today_iso

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="hrplaybook", docs_url="/api/docs")


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    """Optional HTTP Basic Auth for public deploys.

    No-op unless BOTH HRPB_AUTH_USER and HRPB_AUTH_PASS are set in the
    environment, so local dev and tests are unaffected. Env is read per request
    so it can be toggled without re-import.
    """
    user = os.environ.get("HRPB_AUTH_USER")
    pw = os.environ.get("HRPB_AUTH_PASS")
    if user and pw:
        ok = False
        hdr = request.headers.get("authorization", "")
        if hdr.startswith("Basic "):
            try:
                got_u, _, got_p = base64.b64decode(hdr[6:]).decode("utf-8").partition(":")
                ok = (hmac.compare_digest(got_u, user)
                      and hmac.compare_digest(got_p, pw))
            except Exception:
                ok = False
        if not ok:
            return Response(
                "Authentication required", status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="HR Playbook"'},
            )
    return await call_next(request)

# --- background job tracking ------------------------------------------------
JOBS: Dict[str, dict] = {}
_RUN_LOCK = threading.Lock()


def _out_dir() -> Path:
    return Path("out")


def _config(path: Optional[str] = None):
    return load_config(path)


# --------------------------------------------------------------------------- #
# Data readers
# --------------------------------------------------------------------------- #
_DF_CACHE: Dict[str, tuple] = {}   # path -> (mtime, records)


def _df_records(path: Path) -> list:
    """Read a CSV to list[dict], memoized by file mtime.

    The pipeline rewrites out/<date>/*.csv on run/refresh/grade, which bumps the
    mtime and auto-invalidates this cache. Avoids re-parsing the same CSV with
    pandas on every API request (each endpoint reads several CSVs, often twice).
    """
    if not path.exists():
        return []
    try:
        mt = path.stat().st_mtime
    except OSError:
        mt = 0.0
    key = str(path)
    hit = _DF_CACHE.get(key)
    if hit is not None and hit[0] == mt:
        return hit[1]
    # to_json -> null for NaN and native types (json.dumps chokes on nan/numpy)
    recs = json.loads(pd.read_csv(path).to_json(orient="records"))
    _DF_CACHE[key] = (mt, recs)
    return recs


def _meta(date: str) -> dict:
    p = _out_dir() / date / "meta.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _slate(date: str) -> dict:
    d = _out_dir() / date
    games = _df_records(d / "games.csv")
    matchups = _df_records(d / "matchups.csv")
    elite = sum(1 for g in games if g.get("env_tier") == "elite")
    good = sum(1 for g in games if g.get("env_tier") == "good")
    dead = [g["matchup"] for g in games if g.get("env_tier") == "dead-air"]
    tier1 = sum(1 for m in matchups if m.get("tier") == 1)
    return {
        "date": date,
        "exists": bool(games or matchups),
        "games": games,
        "matchups": matchups,
        "summary": {
            "games": len(games), "elite": elite, "good": good,
            "dead_air": dead, "tier1": tier1, "matchups": len(matchups),
        },
        "meta": _meta(date),
    }


def _ledger() -> dict:
    path = _out_dir() / "_ledger.csv"
    rows = _df_records(path)
    norm = []
    for r in rows:
        won = r.get("won")
        won = True if won in (True, "True") else False if won in (False, "False") else None
        profit = r.get("profit")
        try:
            profit = float(profit) if profit not in (None, "", "None") else None
        except (TypeError, ValueError):
            profit = None
        norm.append({"bet": r.get("bet"), "won": won, "profit": profit})
    summary = summarize(norm)
    return {"summary": summary, "rows": rows[-200:], "count": len(rows)}


# --------------------------------------------------------------------------- #
# Job runners (threaded)
# --------------------------------------------------------------------------- #
def _set(job_id: str, **kw):
    JOBS.setdefault(job_id, {}).update(kw)


def _run_odds(client, cfg, date: str, matchups, opts: dict) -> dict:
    """Pull live odds as part of a full Run, using the SAME path as the
    Refresh-API-Odds button (odds_api.pull -> odds_keys). Non-fatal: always
    returns a SAFE status dict (env-var NAME only, never the raw key)."""
    if opts.get("no_odds"):
        return {"status": "skipped", "message": "odds disabled for this run"}
    from .. import odds_api, odds_keys
    from ..util import normalize_name
    if not cfg.odds.provider:
        return {"status": "no-provider",
                "message": "no odds provider configured; value stays model-only"}
    odds_keys.load_dotenv()
    if not odds_keys.has_any_key():
        return {"status": "no-key",
                "message": "no ODDS_API_KEY_1 set; value stays model-only"}
    name_index = {normalize_name(m.batter.name): m.batter.player_id
                  for m in matchups if m.batter and m.batter.name}
    try:
        res = odds_api.pull(client, cfg, date, name_index)
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "message": f"odds pull error: {type(e).__name__}"}
    if res.get("ok"):
        n = res.get("records_saved", 0)
        return {"status": "ok", "active_key_name": res.get("active_key_name"),
                "records_saved": n, "books": res.get("books", []),
                "message": f"{n} odds rows saved" + (f" ({', '.join(res.get('books', [])[:4])})"
                                                     if res.get("books") else "")}
    err = res.get("error") or "unknown"
    msg = {
        "quota_exhausted": "odds API quota exhausted (HTTP 429) — try later",
        "no_valid_key": "no valid odds key — check ODDS_API_KEY_1 (HTTP 401/403)",
    }.get(err, f"odds failed: {err}")
    return {"status": "failed", "message": msg}


def _run_job(job_id: str, kind: str, date: str, opts: dict):
    if not _RUN_LOCK.acquire(blocking=False):
        _set(job_id, state="error", error="another job is already running")
        return
    try:
        _set(job_id, state="running", kind=kind, date=date)
        cfg = _config(opts.get("config"))
        if kind == "grade":
            _grade(date, cfg)
            _set(job_id, state="done", result={"graded": date})
            return
        client = _make_client(cfg, offline=opts.get("offline", False),
                              force_refresh=opts.get("force", False))
        try:
            games, pitchers, matchups, warnings = build_slate(
                date, cfg, client,
                use_statcast=not opts.get("no_statcast", False),
                use_odds=not opts.get("no_odds", False),
                full_statcast=opts.get("full_statcast", False),
                progress=False,
            )
        finally:
            client.close()
        _write_outputs(date, games, pitchers, matchups, cfg, warnings)
        tier1 = sum(1 for m in matchups if m.tier == 1)
        # full-slate Run: pull live odds too (non-fatal). Same odds path as the
        # Refresh button; failure never fails the run, just reports a status.
        odds = _run_odds(client, cfg, date, matchups, opts)
        _set(job_id, state="done", result={
            "games": len(games), "matchups": len(matchups),
            "tier1": tier1, "warnings": warnings,
            "odds": odds,
            "out_path": str(_out_dir().resolve()),
        })
    except Exception as e:  # noqa: BLE001
        _set(job_id, state="error", error=str(e), trace=traceback.format_exc())
    finally:
        _RUN_LOCK.release()


def _grade(date: str, cfg) -> None:
    client = _make_client(cfg, offline=False)
    final_states = {"Final", "Game Over", "Completed Early"}
    results: Dict[int, dict] = {}
    try:
        games = statsapi.parse_schedule(statsapi.fetch_schedule(client, date))
        for g in games:
            if g.status in final_states:
                results.update(statsapi.parse_boxscore_results(
                    statsapi.fetch_boxscore(client, g.game_pk)))
    finally:
        client.close()
    rows = grade_picks(load_picks(_out_dir() / date), results)
    append_ledger(_out_dir() / "_ledger.csv", rows)


def _spawn(kind: str, payload: dict) -> str:
    date = resolve_date(payload.get("date", "today"))
    job_id = uuid.uuid4().hex[:12]
    _set(job_id, state="queued", kind=kind, date=date)
    threading.Thread(target=_run_job, args=(job_id, kind, date, payload),
                     daemon=True).start()
    return job_id


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/dates")
def api_dates():
    out = _out_dir()
    dates = sorted(
        (p.name for p in out.glob("*") if p.is_dir() and p.name[:4].isdigit()),
        reverse=True,
    ) if out.exists() else []
    return {"dates": dates, "today": today_iso()}


@app.get("/api/slate/{date}")
def api_slate(date: str):
    try:
        date = resolve_date(date)
    except ValueError:
        raise HTTPException(400, "bad date")
    return _slate(date)


@app.get("/api/ledger")
def api_ledger():
    return _ledger()


@app.get("/api/home/{date}")
def api_home(date: str):
    from .. import featured
    try:
        date = resolve_date(date)
    except ValueError:
        raise HTTPException(400, "bad date")
    s = _slate(date)
    if not s["exists"]:
        return {"exists": False, "date": date}
    g, m = s["games"], s["matchups"]
    pitchers = _df_records(_out_dir() / date / "pitchers.csv")
    from .. import calibration, manual_odds, odds_api, performance, value_center
    wb = featured.weather_board(g)
    missed = featured.missed_hr_candidates(m)
    clusters = featured.contact_clusters(m)
    pa = featured.pitcher_attack_table(pitchers, g)
    val = value_center.market_vs_model(m, manual_odds.load(date), api=odds_api.load(date),
                                       tables=calibration.load_tables(_out_dir()))
    return {
        "exists": True, "date": date,
        "read": featured.slate_read(g, m, pitchers),
        "best5": featured.best5(m, pitchers, g),
        "top": {mk: featured.top_by_market(m, mk, 5) for mk in featured.MARKETS},
        "pitchers": featured.pitchers_to_attack(pitchers, g, 5),
        "widgets": {
            "best_hr_env": wb["top_hr"][0] if wb["top_hr"] else None,
            "best_tb_env": wb["top_tb"][0] if wb["top_tb"] else None,
            "worst_env": wb["bottom"][0] if wb["bottom"] else None,
            "top_pitcher": pa["top10"][0] if pa["top10"] else None,
            "best_missed_hr": missed[0] if missed else None,
            "best_contact": clusters[0] if clusters else None,
        },
        "value": {
            "has_odds": val["has_odds"],
            "best": val["best_value"],
            "best_overall": val["best_overall"],
            "alerts": val["alerts"][:5],
        },
        "perf_snapshot": performance.snapshot(_out_dir()),
        "baseline_coverage": featured.baseline_coverage(m, pitchers),
        "meta": s.get("meta", {}),
    }


@app.get("/api/weather/{date}")
def api_weather(date: str):
    from .. import featured
    date = resolve_date(date)
    s = _slate(date)
    if not s["exists"]:
        return {"exists": False, "date": date}
    return {"exists": True, "date": date, **featured.weather_board(s["games"])}


@app.get("/api/pitchers/{date}")
def api_pitchers(date: str):
    from .. import featured
    date = resolve_date(date)
    s = _slate(date)
    if not s["exists"]:
        return {"exists": False, "date": date}
    pitchers = _df_records(_out_dir() / date / "pitchers.csv")
    return {"exists": True, "date": date,
            **featured.pitcher_attack_table(pitchers, s["games"])}


@app.get("/api/missed-hr/{date}")
def api_missed_hr(date: str):
    from .. import featured
    date = resolve_date(date)
    s = _slate(date)
    if not s["exists"]:
        return {"exists": False, "date": date, "candidates": []}
    return {"exists": True, "date": date,
            "candidates": featured.missed_hr_candidates(s["matchups"])}


@app.get("/api/contact/{date}")
def api_contact(date: str):
    from .. import featured
    date = resolve_date(date)
    s = _slate(date)
    if not s["exists"]:
        return {"exists": False, "date": date, "clusters": []}
    return {"exists": True, "date": date,
            "clusters": featured.contact_clusters(s["matchups"])}


# --------------------------------------------------------------------------- #
# Batch 4 — Market vs Model / odds
# --------------------------------------------------------------------------- #
def _key_tester():
    from ..sources.odds import live_key_tester
    return live_key_tester()


@app.get("/api/odds-status")
def api_odds_status():
    from .. import odds_keys
    odds_keys.load_dotenv()
    if not odds_keys.has_any_key():
        return {"connected": False, "active_key_name": None, "configured": [],
                "keys": [], "reason": "no keys configured (manual odds still work)"}
    return odds_keys.status(_key_tester())


@app.post("/api/check-keys")
def api_check_keys():
    from .. import odds_keys
    odds_keys.load_dotenv()
    return {"keys": odds_keys.check_keys(_key_tester())}


@app.get("/api/manual-odds/{date}")
def api_manual_odds(date: str):
    from .. import manual_odds
    date = resolve_date(date)
    return {"date": date, "entries": manual_odds.load(date),
            "bet_types": manual_odds.BET_TYPES, "sportsbooks": manual_odds.SPORTSBOOKS}


@app.post("/api/manual-odds/{date}")
def api_manual_odds_add(date: str, payload: dict):
    from .. import manual_odds
    date = resolve_date(date)
    return {"entry": manual_odds.add(date, payload)}


@app.delete("/api/manual-odds/{date}/{entry_id}")
def api_manual_odds_delete(date: str, entry_id: int):
    from .. import manual_odds
    date = resolve_date(date)
    return {"deleted": manual_odds.delete(date, entry_id)}


@app.get("/api/performance")
def api_performance(window: str = "all"):
    from .. import performance
    return performance.report(_out_dir(), window=window)


@app.get("/api/performance/snapshot")
def api_perf_snapshot():
    from .. import performance
    return performance.snapshot(_out_dir())


@app.get("/api/performance/yesterday")
def api_perf_yesterday():
    """Single-date review of yesterday / the most recent graded slate."""
    from .. import performance
    return performance.yesterday_report(_out_dir())


@app.get("/api/value/{date}")
def api_value(date: str):
    from .. import manual_odds, value_center
    date = resolve_date(date)
    s = _slate(date)
    if not s["exists"]:
        return {"exists": False, "date": date}
    from .. import calibration, odds_api
    tables = calibration.load_tables(_out_dir())
    manual = manual_odds.load(date)
    api = odds_api.load(date)          # read saved api_odds.json (no network)
    res = value_center.market_vs_model(s["matchups"], manual, api=api, tables=tables)
    return {"exists": True, "date": date, "coverage": calibration.coverage(tables), **res}


@app.get("/api/model/{date}")
def api_model(date: str):
    from .. import featured
    try:
        date = resolve_date(date)
    except ValueError:
        raise HTTPException(400, "bad date")
    s = _slate(date)
    if not s["exists"]:
        return {"exists": False, "date": date, "players": []}
    from .. import calibration, value_center
    tables = calibration.load_tables(_out_dir())

    def _pj(v):
        try:
            return json.loads(v) if v not in (None, "", "None") else None
        except (json.JSONDecodeError, TypeError):
            return None

    def _split(v):
        return [x for x in str(v or "").split("|") if x]

    players = []
    for m in s["matchups"]:
        sc = featured.market_scores(m)
        probs = {}
        for mk in featured.MARKETS:
            raw = value_center.model_prob(m, mk)
            probs[mk] = calibration.calibrate(raw, mk, tables)
        players.append({
            "batter": m.get("batter"), "batter_id": m.get("batter_id"),
            "team": m.get("team"), "opp_team": m.get("opp_team"),
            "opp_sp": m.get("opp_sp"), "order": m.get("order"),
            "platoon": m.get("platoon"), "env_tier": m.get("env_tier"),
            "lineup_state": m.get("lineup_state"), "tags": m.get("tags"),
            "scores": sc, "probs": probs,
            "bvp": {
                "grade": m.get("bvp_grade"), "sample_size": m.get("bvp_sample_size"),
                "confidence": m.get("bvp_confidence"), "edge_label": m.get("bvp_edge_label"),
                "pa": m.get("bvp_pa"), "avg": m.get("bvp_avg"), "slg": m.get("bvp_slg"),
                "hr": m.get("bvp_hr"), "k": m.get("bvp_k"), "max_ev": m.get("bvp_max_ev"),
                "barrels": m.get("bvp_barrels"), "reasons": _split(m.get("bvp_reasons")),
                "pitch_history": _pj(m.get("bvp_pitch_history")) or [],
            },
            "multiseason": {
                "s2025": _pj(m.get("batter_2025_stats")),
                "cur": {"barrel_pct": m.get("barrel_pct"), "avg_ev": m.get("avg_ev"),
                        "hardhit_pct": m.get("hardhit_pct")},
                "l30": _pj(m.get("batter_l30_stats")),
                "weighted": _pj(m.get("weighted_profile")),
                "trend": {"grade": m.get("trend_grade"), "label": m.get("trend_label"),
                          "reasons": _split(m.get("trend_reasons"))},
                "warnings": _split(m.get("sample_warnings")),
            },
        })
    pitchers = _df_records(_out_dir() / date / "pitchers.csv")
    return {"exists": True, "date": date, "players": players,
            "coverage": calibration.coverage(tables),
            "baseline_coverage": featured.baseline_coverage(s["matchups"], pitchers)}


@app.get("/api/odds-status")
def api_odds_status():
    """SAFE key status (connected / active key NAME / quota) — never the raw key."""
    from .. import odds_keys
    from ..sources.odds import live_key_tester
    return odds_keys.status(live_key_tester())


@app.post("/api/odds-refresh/{date}")
def api_odds_refresh(date: str, payload: dict | None = None):
    """Explicit live odds pull (button-triggered). Returns a safe summary."""
    from .. import odds_api
    from ..util import normalize_name
    payload = payload or {}
    try:
        date = resolve_date(date)
    except ValueError:
        raise HTTPException(400, "bad date")
    s = _slate(date)
    name_index = {normalize_name(m["batter"]): m["batter_id"]
                  for m in s.get("matchups", []) if m.get("batter")}
    cfg = _config()
    client = _make_client(cfg, offline=False)
    try:
        markets = payload.get("markets")
        if isinstance(markets, str):
            markets = [x.strip() for x in markets.split(",") if x.strip()]
        res = odds_api.pull(client, cfg, date, name_index, markets=markets,
                            region=payload.get("region"),
                            dry_run=bool(payload.get("dry_run")))
    finally:
        client.close()
    return res


@app.get("/api/bvp/{date}")
def api_bvp(date: str):
    from .. import featured
    try:
        date = resolve_date(date)
    except ValueError:
        raise HTTPException(400, "bad date")
    s = _slate(date)
    if not s["exists"]:
        return {"exists": False, "date": date}
    return {"exists": True, "date": date, **featured.bvp_board(s["matchups"])}


@app.get("/api/calibration")
def api_calibration():
    from .. import calibration
    tables = calibration.load_tables(_out_dir())
    return {"tables": tables, "coverage": calibration.coverage(tables)}


@app.post("/api/run")
def api_run(payload: dict):
    return {"job_id": _spawn("run", payload)}


@app.post("/api/refresh")
def api_refresh(payload: dict):
    return {"job_id": _spawn("refresh", payload)}


@app.post("/api/force-refresh")
def api_force_refresh(payload: dict):
    payload = {**(payload or {}), "force": True}
    return {"job_id": _spawn("run", payload)}


@app.get("/api/validate/{date}")
def api_validate(date: str):
    from ..freshness import validate_slate
    try:
        date = resolve_date(date)
    except ValueError:
        raise HTTPException(400, "bad date")
    s = _slate(date)
    if not s["exists"]:
        return {"overall": "FAIL",
                "checks": [{"name": "Slate", "status": "FAIL",
                            "detail": "not built yet — click Run"}]}
    return validate_slate(s.get("meta", {}), s["games"], s["matchups"])


@app.get("/api/glossary")
def api_glossary():
    p = STATIC / "glossary.json"
    return json.loads(p.read_text()) if p.exists() else {}


@app.post("/api/grade")
def api_grade(payload: dict):
    return {"job_id": _spawn("grade", payload)}


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return job


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
