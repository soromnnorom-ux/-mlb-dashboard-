"""SQLite-compatible DDL for the minimal store (Postgres variant in db/schema.postgres.sql).

Typed columns replace the stringly-typed CSV contract; the long tail of rich
fields lives in `extra_json` (the path to a Postgres jsonb column later).
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS slates (
    date        TEXT PRIMARY KEY,
    built_at    TEXT,
    season      INTEGER,
    n_matchups  INTEGER DEFAULT 0,
    meta_json   TEXT
);

CREATE TABLE IF NOT EXISTS matchups (
    date            TEXT NOT NULL,
    batter_id       INTEGER NOT NULL,
    batter          TEXT,
    team            TEXT,
    opp_team        TEXT,
    opp_sp          TEXT,
    lineup_state    TEXT,
    "order"         INTEGER,
    env_tier        TEXT,
    env_score       REAL,
    pitcher_score   REAL,
    barrel_vs_pm    REAL,
    play_score      REAL,
    tier            INTEGER,
    model_hr_prob   REAL,
    model_tb_prob   REAL,
    value           TEXT,
    bets            TEXT,
    tags            TEXT,
    extra_json      TEXT,
    PRIMARY KEY (date, batter_id)
);
CREATE INDEX IF NOT EXISTS ix_matchups_date_play ON matchups(date, play_score DESC);

CREATE TABLE IF NOT EXISTS ledger (
    date        TEXT NOT NULL,
    batter_id   INTEGER NOT NULL,
    bet         TEXT NOT NULL,
    line        TEXT,
    need        INTEGER,
    got         INTEGER,
    won         INTEGER,          -- 0/1/NULL(void)
    odds        INTEGER,
    profit      REAL,
    PRIMARY KEY (date, batter_id, bet)
);
CREATE INDEX IF NOT EXISTS ix_ledger_bet ON ledger(bet);

CREATE TABLE IF NOT EXISTS odds (
    date        TEXT NOT NULL,
    player_norm TEXT NOT NULL,
    bet_type    TEXT NOT NULL,
    line        TEXT,
    sportsbook  TEXT,
    american    INTEGER,
    source      TEXT,             -- manual | api
    pulled_at   TEXT,
    PRIMARY KEY (date, player_norm, bet_type, sportsbook, source)
);
"""
