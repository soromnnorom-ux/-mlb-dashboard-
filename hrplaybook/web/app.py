"""FastAPI dashboard for hrplaybook.

Serves the generated slate (games, tiers, bet cards, full matchup table, and the
grading ledger) and lets you trigger run / refresh / grade from the browser.
Long pipeline jobs run in a background thread; the UI polls for completion.
"""
from __future__ import annotations

import json
import threading
import traceback
import uuid
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..cli import _make_client, _write_outputs, build_slate
from ..config import load_config
from ..grade import append_ledger, grade_picks, summarize
from ..report.picks import load_picks
from ..sources import statsapi
from ..util import resolve_date, today_iso

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="hrplaybook", docs_url="/api/docs")

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
def _df_records(path: Path) -> list:
    if not path.exists():
        return []
    # to_json -> null for NaN and native types (json.dumps chokes on nan/numpy)
    return json.loads(pd.read_csv(path).to_json(orient="records"))


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
        client = _make_client(cfg, offline=opts.get("offline", False))
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
        _set(job_id, state="done", result={
            "games": len(games), "matchups": len(matchups),
            "tier1": tier1, "warnings": warnings,
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


@app.post("/api/run")
def api_run(payload: dict):
    return {"job_id": _spawn("run", payload)}


@app.post("/api/refresh")
def api_refresh(payload: dict):
    return {"job_id": _spawn("refresh", payload)}


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
