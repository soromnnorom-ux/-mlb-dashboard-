-- HR Playbook — production relational schema (Postgres).
-- The minimal store (hrplaybook/store/) runs the SQLite-compatible subset today;
-- this is the production target: jsonb for the rich tail, date-partitioned hot
-- tables, and auth tables for multi-user serving.

-- ---- reference data (slowly changing) ----
CREATE TABLE players (
    player_id   BIGINT PRIMARY KEY,          -- stable MLBAM id
    full_name   TEXT NOT NULL,
    bats        CHAR(1), throws CHAR(1),
    primary_pos TEXT,
    updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE teams (
    abbr TEXT PRIMARY KEY, team_id BIGINT UNIQUE, name TEXT, park_factor REAL
);

-- ---- per-slate facts ----
CREATE TABLE slates (
    date        DATE PRIMARY KEY,
    season      INT,
    built_at    TIMESTAMPTZ,
    slate_grade TEXT,
    lean        TEXT,
    meta        JSONB                          -- freshness/provenance (was meta.json)
);

CREATE TABLE games (
    game_pk     BIGINT PRIMARY KEY,
    date        DATE NOT NULL REFERENCES slates(date),
    home_team   TEXT REFERENCES teams(abbr),
    away_team   TEXT REFERENCES teams(abbr),
    venue       TEXT,
    game_time   TIMESTAMPTZ,
    status      TEXT,
    env_tier    TEXT, env_score INT,
    weather     JSONB
);
CREATE INDEX ix_games_date ON games(date);

CREATE TABLE matchups (
    date            DATE NOT NULL,
    batter_id       BIGINT NOT NULL,
    game_pk         BIGINT REFERENCES games(game_pk),
    team            TEXT, opp_team TEXT, opp_sp TEXT,
    lineup_state    TEXT, lineup_spot INT,
    env_tier        TEXT, env_score INT, pitcher_score INT,
    barrel_vs_pm    REAL,
    play_score      REAL, tier INT,
    hr_score INT, tb_score INT, hrr_score INT, hits_score INT,
    model_hr_prob REAL, model_tb_prob REAL,
    cal_hr_prob REAL, cal_tb_prob REAL,
    value           TEXT,
    bets            TEXT[],                    -- e.g. {'HR:HR','TB:2+ TB'}
    tags            TEXT[],
    multiseason     JSONB,                     -- 2025/2026/L30/weighted/trend
    bvp             JSONB,                     -- batter-vs-pitcher record
    PRIMARY KEY (date, batter_id)
) PARTITION BY RANGE (date);                   -- monthly partitions in prod
CREATE INDEX ix_matchups_date_play ON matchups(date, play_score DESC);

-- ---- picks snapshot (point-in-time, for grading + calibration) ----
CREATE TABLE picks (
    date DATE, batter_id BIGINT, bet TEXT, line TEXT,
    model_prob REAL, snapshot JSONB,           -- full rich context at gen time
    PRIMARY KEY (date, batter_id, bet)
);

-- ---- odds (line-aware; manual + api) ----
CREATE TABLE odds (
    id          BIGSERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    player_norm TEXT NOT NULL,
    bet_type    TEXT NOT NULL,
    line        TEXT,                          -- canonical threshold matters for value
    line_thr    INT,
    sportsbook  TEXT,
    american    INT,
    source      TEXT CHECK (source IN ('manual','api')),
    event_id    TEXT,
    pulled_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ix_odds_lookup ON odds(date, player_norm, bet_type, line_thr);

-- ---- realized results / ledger ----
CREATE TABLE ledger (
    date DATE, batter_id BIGINT, bet TEXT, line TEXT,
    need INT, got INT, won BOOLEAN, odds INT, profit REAL,
    graded_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (date, batter_id, bet)
);
CREATE INDEX ix_ledger_bet ON ledger(bet);

-- ---- calibration tables (versioned) ----
CREATE TABLE calibration (
    bet TEXT, bucket TEXT, n INT, avg_raw REAL, actual REAL,
    built_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (bet, bucket)
);

-- ---- multi-user / auth ----
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY, email TEXT UNIQUE, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE api_keys (
    id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id),
    key_hash TEXT NOT NULL,                    -- store a hash, never the raw key
    label TEXT, created_at TIMESTAMPTZ DEFAULT now(), revoked_at TIMESTAMPTZ
);
-- Retention: keep matchups/odds 2 seasons hot, archive older partitions to object storage.
