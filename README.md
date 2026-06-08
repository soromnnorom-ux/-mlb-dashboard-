# hrplaybook ⚾

Automated MLB **home-run / total-bases / HRR / Hits** betting cheat-sheet generator.
Given a date, it pulls the slate, probable pitchers, lineups, batter & pitcher
Statcast metrics, pitch arsenals, park factors and weather, scores everything
against the HR Playbook rules, and writes a tiered cheat sheet (MD + HTML),
bet-type cards, and raw CSVs to `out/<date>/`.

No paid APIs. Public/free endpoints only (MLB Stats API, Baseball Savant,
Open-Meteo, Rotowire fallback).

## Install

```bash
cd ~/hrplaybook
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .            # or: pip install -r requirements.txt
```

## Usage

```bash
hrplaybook run --date today                 # full slate, all outputs
hrplaybook run --date 2025-07-19 --season 2025
hrplaybook run --date today --no-statcast   # skip per-batter pulls (much faster)
hrplaybook run --date today --offline       # cache only, no network
hrplaybook run --date today --no-odds --max-plays 4

hrplaybook run --date today --full-statcast # pull Statcast for every batter (no pre-filter)

hrplaybook lineups --date today             # print projected/confirmed lineups
hrplaybook refresh --date today             # re-pull lineups+weather, re-tier
hrplaybook grade --date yesterday           # grade past picks vs box scores -> ledger
hrplaybook schedule-install                 # write a daily crontab snippet
```

Outputs (under `out/<date>/`):
- `cheatsheet.md` / `cheatsheet.html` — Tier 1 / 2 / 3, with a SLATE READ banner
- `cards_TB.md`, `cards_HR.md`, `cards_HRR.md`, `cards_Hits.md`
- `games.csv`, `pitchers.csv`, `batters.csv`, `weather.csv`, `matchups.csv`
- `picks.json` — machine-readable picks consumed by `grade`
- `out/_ledger.csv` — rolling graded results across all dates (W/L, hit-rate, ROI)

## Grading / backtest loop

`hrplaybook grade --date <past>` pulls actual HR / TB / hits / runs / RBI from the
StatsAPI box scores for that date, scores each saved bet (HR, TB 2+, HRR, Hits),
prints per-bet-type record + hit-rate + ROI, and appends to `out/_ledger.csv`.
Run it the next morning to close the loop and tune thresholds with evidence.
ROI populates only for bets that had recorded odds.

## How it scores (all thresholds in `config.yaml`)

1. **Environment first** (§6.1) — park HR factor + roof + temp + wind-vs-orientation.
   Dead-air games never produce a Tier-1/HR play (still eligible for TB).
2. **Pitcher weakness** (§6.2) — HR/9, HR/FB%, barrel/EV allowed, low K/whiff,
   fastball-heavy, plus a regression flag (hard contact, HRs not landed yet).
3. **Batter gate** (§6.3) — `elite` (EV≥102 & barrel≥25% & hard-hit≥50%) or
   `practical` (barrel% vs the pitcher's pitch-mix ≥ 10%). `gate:` in config picks
   which drives Tier 1.
4. **Perfect profile** (§6.4) + **edges** (§6.5): missed-HR, hot-contact, recency
   fade, **LATE_HR** (opponent bullpen reliever HR/9), and the **platoon** edge
   (batter hand vs starter hand; `PLATOON+`/`PLATOON-` tags).
5. **Tiering** (§6.6) + HR cap to `max_plays`.
6. **Value** (§6.7) and **bet-type mapping** (§6.8).

### Platoon & model probabilities
- `barrel_vs_pm` is computed pitch-mix **and** same-hand aware when the Statcast
  window has the sample for it; `barrel_vs_hand` is the batter's barrel% vs the
  starter's throwing hand. Both come free from the per-batter Statcast pull
  (`stand` / `p_throws`).
- `model_hr_prob` / `model_tb_prob` are transparent empirical estimates: a league
  base rate scaled by batter contact quality, pitcher HR-proneness, park factor
  and the platoon edge, over an expected-PA count (all tunable under
  `value_model:` in config). They are compared to odds-implied probabilities to
  tag each bet `+EV` / `fair` / `-EV` and rank the HR card by EV when priced.

### Odds (optional, for real +EV)
Set in `config.yaml`:
```yaml
odds:
  provider: "the-odds-api"
  api_key_env: "ODDS_API_KEY"   # export ODDS_API_KEY=...
  region: "us"
```
Pulls `batter_home_runs` (Over 0.5) and `batter_total_bases` (Over 1.5) player
props per event, name-matched to the Savant pool, best price kept. Degrades
gracefully to `value=unknown` (model probs still shown) when absent.

## Data-source notes (verified against live responses)

- Savant's *custom leaderboard* serves contact metrics under
  `barrel_batted_rate`, `exit_velocity_avg`, `hard_hit_percent`,
  `isolated_power`, `slg_percent` — **not** the older `barrels_per_pa_percent` /
  `avg_hit_speed` / `ev95percent` / `iso` / `slg`, which return empty columns.
- HR/9 is computed from `home_run` + `p_formatted_ip`; HR/FB% is **derived**
  (HR ÷ estimated fly-ball count) because `hr_flyballs_percent` is empty.
- Fly-ball/contact trackers (missed-HR, hot-contact, EV logs, L30 AVG, barrel%
  vs pitch-mix) come from `statcast_search/csv` per batter over a rolling window.
- Confirmed lineups come from the StatsAPI boxscore `battingOrder`; before
  confirmation we fall back to Rotowire projected lineups (best-effort scrape,
  name-matched to the Savant pool). Every batter row carries `lineup_state`
  (`confirmed`/`projected`/`unknown`) and `pulled_at`. `refresh` trusts the
  latest CONFIRMED lineup over an earlier projected cache.

### Known limitations
- Starters below Savant's qualified threshold lack rate stats and score 0
  (flagged `small_sample`).
- Bullpen HR/9 is aggregated from active-roster relievers (StatsAPI roster ×
  Savant pool); relievers below the leaderboard min are skipped.
- `model_prob` is a transparent empirical estimate, not a trained model — treat
  it as a prior and recalibrate against `out/_ledger.csv` over time.
- HR/FB% is derived (Savant doesn't serve it); park orientations / HR factors in
  `parks.csv` are reasonable approximations — tune them to taste.

## ⚠️ This directory is in iCloud
`~` here syncs Desktop/Documents via iCloud, which can evict/relocate files in a
live git project. Consider moving the project to a non-synced path (e.g.
`~/Developer/hrplaybook`) for long-term use. A working copy is also kept at
`/tmp/hrplaybook_backup` during the build.

## Tests

```bash
pytest            # offline; runs against recorded fixtures in tests/fixtures/
```

Caching: responses are cached under `~/.cache/hrplaybook` with per-source TTLs
(`config.yaml`). Re-runs are fast; offline runs render from cache with staleness
warnings. Be a good citizen — default rate limit is ~1 req/sec with a custom UA.
