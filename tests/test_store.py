"""Minimal store slice: ingest a built slate from CSV into the relational store
and query it back (typed), proving the CSV->DB migration path works offline."""
import csv

from hrplaybook import store
from hrplaybook.store.models import SlateOut
from hrplaybook.store.repo import ingest_ledger


def _write_matchups(d):
    d.mkdir(parents=True, exist_ok=True)
    cols = ["batter_id", "batter", "team", "opp_team", "opp_sp", "order",
            "env_tier", "env_score", "pitcher_score", "barrel_vs_pm",
            "play_score", "tier", "model_hr_prob", "model_tb_prob",
            "value", "bets", "tags", "cluster_label"]
    rows = [
        {"batter_id": 1, "batter": "Slugger", "team": "NYY", "opp_team": "BOS",
         "opp_sp": "Weak Arm", "order": 2, "env_tier": "good", "env_score": 4,
         "pitcher_score": 6, "barrel_vs_pm": 17, "play_score": 14, "tier": 1,
         "model_hr_prob": 0.2, "model_tb_prob": 0.6, "value": "unknown",
         "bets": "HR:HR|TB:2+ TB", "tags": "HOT_CONTACT|MISSED_HR",
         "cluster_label": "NUCLEAR"},
        {"batter_id": 2, "batter": "Slappy", "team": "SF", "opp_team": "SD",
         "opp_sp": "Ace", "order": 8, "env_tier": "neutral", "env_score": 0,
         "pitcher_score": 1, "barrel_vs_pm": 4, "play_score": 3, "tier": "",
         "model_hr_prob": 0.05, "model_tb_prob": 0.35, "value": "unknown",
         "bets": "TB:1.5 TB", "tags": "", "cluster_label": "NORMAL"},
    ]
    with (d / "matchups.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def test_ingest_and_query_roundtrip(tmp_path):
    _write_matchups(tmp_path / "2026-06-09")
    conn = store.connect()
    store.init_db(conn)
    n = store.ingest_slate(conn, tmp_path, "2026-06-09")
    assert n == 2
    rows = store.get_slate(conn, "2026-06-09")
    # typed + ordered by play_score desc
    assert [r["batter"] for r in rows] == ["Slugger", "Slappy"]
    assert isinstance(rows[0]["env_score"], (int, float)) and rows[0]["env_score"] == 4.0
    assert rows[0]["tier"] == 1 and rows[1]["tier"] is None   # "" -> NULL
    assert store.list_dates(conn) == ["2026-06-09"]
    # typed API model validates the contract
    out = SlateOut(date="2026-06-09", exists=True, n_matchups=n,
                   matchups=[r for r in rows])
    assert out.matchups[0].model_hr_prob == 0.2


def test_ingest_is_idempotent(tmp_path):
    _write_matchups(tmp_path / "2026-06-09")
    conn = store.connect(); store.init_db(conn)
    store.ingest_slate(conn, tmp_path, "2026-06-09")
    store.ingest_slate(conn, tmp_path, "2026-06-09")          # re-run = overwrite
    assert len(store.get_slate(conn, "2026-06-09")) == 2      # not duplicated


def test_ledger_ingest_typed(tmp_path):
    led = tmp_path / "_ledger.csv"
    with led.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "batter_id", "bet", "line",
                                          "need", "got", "won", "odds", "profit"])
        w.writeheader()
        w.writerow({"date": "2026-06-08", "batter_id": 1, "bet": "TB", "line": "2+ TB",
                    "need": 2, "got": 3, "won": "True", "odds": "", "profit": ""})
    conn = store.connect(); store.init_db(conn)
    assert ingest_ledger(conn, led) == 1
    r = conn.execute("SELECT won, need FROM ledger").fetchone()
    assert r["won"] == 1 and r["need"] == 2
