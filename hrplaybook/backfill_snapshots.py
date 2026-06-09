"""Backfill richer snapshots into historical out/<date>/picks.json (Batch 7).

Old picks.json stored raw probs for HR/TB only, so HR/HRR/Hits couldn't be
calibrated. But each date's matchups.csv IS a point-in-time slate snapshot with
the rich fields (env, pitcher_score, barrel_vs_pm, model_hr/tb_prob, tags ...),
so we can reconstruct all-market raw probabilities and context LOCALLY with no
network and no lookahead bias.

Rules: never invent missing fields, always back up picks.json before replacing,
keep the original bets/odds/value, only ADD what can be reconstructed.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import value_center

# context fields copied from matchups.csv when present (never invented)
_CTX_FLOAT = ["env_score", "pitcher_score", "barrel_vs_pm", "barrel_pct",
              "hardhit_pct", "avg_ev", "l30_avg", "opp_bullpen_hr9"]
_CTX_OTHER = ["env_tier", "cluster_label", "lineup_state", "order", "missed_hr"]


def _read_matchups(date_dir: Path) -> Dict[str, dict]:
    p = date_dir / "matchups.csv"
    if not p.exists():
        return {}
    out = {}
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            bid = row.get("batter_id")
            if bid:
                out[str(bid)] = row
    return out


def _f(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def reconstruct_pick(pick: dict, mrow: Optional[dict]) -> Tuple[dict, List[str]]:
    """Return (enriched_pick, warnings). Original pick preserved; only adds."""
    warnings: List[str] = []
    enriched = dict(pick)
    if not mrow:
        warnings.append("no_matchup_row")
        enriched["backfill_warning"] = "no_matchup_row"
        return enriched, warnings

    # all-market raw probabilities (HR/TB straight from the snapshot; HRR/Hits
    # via the same score-derived formula used live -> point-in-time consistent)
    mp = dict(enriched.get("model_prob") or {})
    if "HR" not in mp and _f(mrow.get("model_hr_prob")) is not None:
        mp["HR"] = _f(mrow.get("model_hr_prob"))
    if "TB" not in mp and _f(mrow.get("model_tb_prob")) is not None:
        mp["TB"] = _f(mrow.get("model_tb_prob"))
    for mk in ("HRR", "Hits"):
        if mk not in mp:
            rp = value_center.model_prob(mrow, mk)
            if rp is not None:
                mp[mk] = rp
    enriched["model_prob"] = mp

    # context fields (only when present in matchups.csv)
    missing = []
    for k in _CTX_FLOAT:
        if k not in enriched or enriched.get(k) is None:
            v = _f(mrow.get(k))
            if v is not None:
                enriched[k] = v
            else:
                missing.append(k)
    for k in _CTX_OTHER:
        if k not in enriched or enriched.get(k) in (None, ""):
            v = mrow.get(k)
            if v not in (None, ""):
                enriched[k] = (str(v).lower() == "true") if k == "missed_hr" else v
            else:
                missing.append(k)
    if "tags" not in enriched or not enriched.get("tags"):
        tg = mrow.get("tags")
        if tg:
            enriched["tags"] = [t for t in tg.split("|") if t]
    if missing:
        enriched["backfill_warning"] = "missing:" + ",".join(sorted(set(missing)))
        warnings.append("partial_context")
    enriched["backfilled"] = True
    return enriched, warnings


def backfill_date(date_dir: Path) -> dict:
    pj = date_dir / "picks.json"
    if not pj.exists():
        return {"date": date_dir.name, "enriched": 0, "skipped": 0,
                "reasons": {"no_picks_json": 1}, "changed": False}
    try:
        picks = json.loads(pj.read_text())
    except (json.JSONDecodeError, OSError):
        return {"date": date_dir.name, "enriched": 0, "skipped": 0,
                "reasons": {"bad_picks_json": 1}, "changed": False}
    mrows = _read_matchups(date_dir)
    enriched_list, n_enriched, n_skipped = [], 0, 0
    reasons: Dict[str, int] = {}
    for pk in picks:
        mrow = mrows.get(str(pk.get("batter_id")))
        new, warns = reconstruct_pick(pk, mrow)
        # "enriched" = gained at least HRR or Hits raw prob it didn't have before
        before = set((pk.get("model_prob") or {}).keys())
        after = set((new.get("model_prob") or {}).keys())
        if after - before:
            n_enriched += 1
        else:
            n_skipped += 1
        for w in warns:
            reasons[w] = reasons.get(w, 0) + 1
        enriched_list.append(new)
    changed = n_enriched > 0
    if changed:
        bak = date_dir / "picks.json.bak"
        if not bak.exists():                       # preserve the TRUE original
            shutil.copy2(pj, bak)
        pj.write_text(json.dumps(enriched_list, indent=2))
    return {"date": date_dir.name, "enriched": n_enriched, "skipped": n_skipped,
            "reasons": reasons, "changed": changed}


def backfill(out_root: str | Path = "out", network: bool = False,
             dates: Optional[List[str]] = None) -> dict:
    out_root = Path(out_root)
    if not out_root.exists():
        return {"scanned": 0, "dates_enriched": 0, "picks_enriched": 0,
                "picks_skipped": 0, "reasons": {}, "per_date": []}
    dirs = sorted(d for d in out_root.glob("*")
                  if d.is_dir() and d.name[:4].isdigit()
                  and (dates is None or d.name in dates))
    scanned = dates_enriched = picks_enriched = picks_skipped = 0
    reasons: Dict[str, int] = {}
    per_date = []
    for d in dirs:
        if network and not (d / "matchups.csv").exists():
            r = _network_rebuild(d, out_root)   # explicit opt-in only
        else:
            r = backfill_date(d)
        scanned += 1
        dates_enriched += 1 if r["changed"] else 0
        picks_enriched += r["enriched"]
        picks_skipped += r["skipped"]
        for k, v in r["reasons"].items():
            reasons[k] = reasons.get(k, 0) + v
        per_date.append(r)
    return {"scanned": scanned, "dates_enriched": dates_enriched,
            "picks_enriched": picks_enriched, "picks_skipped": picks_skipped,
            "reasons": reasons, "per_date": per_date}


def _network_rebuild(date_dir: Path, out_root: Path) -> dict:
    """Rebuild a slate from current data to synthesize a snapshot. EXPLICIT
    opt-in only; uses current leaderboards -> NOT point-in-time accurate."""
    try:
        from .cli import _make_client, _write_outputs, build_slate
        from .config import load_config
        cfg = load_config()
        client = _make_client(cfg, offline=False)
        try:
            games, pitchers, matchups, _ = build_slate(date_dir.name, cfg, client,
                                                        progress=False)
        finally:
            client.close()
        _write_outputs(date_dir.name, games, pitchers, matchups, cfg, [])
        return backfill_date(date_dir)
    except Exception as e:  # noqa: BLE001
        return {"date": date_dir.name, "enriched": 0, "skipped": 0,
                "reasons": {f"network_error:{type(e).__name__}": 1}, "changed": False}
