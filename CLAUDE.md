# CLAUDE.md — HR Playbook Project Instructions

## Project Identity

This project is called **HR Playbook**.

It is an MLB betting research platform for:

* Home Runs
* 2+ Total Bases
* Hits + Runs + RBI
* Hits
* Pitcher attack spots
* Weather/environment edges
* Pitch mix edges
* Market vs model value
* Model performance tracking

This is NOT a generic baseball stats app.

The goal is to help answer:

1. Who are the best plays today?
2. Why are they good plays?
3. Is the data fresh?
4. Is the bet mispriced?
5. Which signals actually win over time?

---

## Do Not Rebuild From Scratch

Never rebuild the app from scratch unless explicitly told.

Always inspect the current codebase first.

Preserve:

* CLI commands
* FastAPI dashboard
* existing pipeline
* output files
* tests
* cache behavior
* grading/backtesting system
* manual odds system
* calibration system

Do targeted upgrades only.

---

## Core Commands That Must Keep Working

These commands must never break:

```bash
hrplaybook run --date today
hrplaybook refresh --date today
hrplaybook grade --date yesterday
hrplaybook lineups --date today
hrplaybook serve
hrplaybook calibrate
hrplaybook backfill-snapshots
```

Before committing, run tests.

---

## Data Source Priority

### 1. Baseball Savant

Use for:

* Statcast
* Exit Velocity
* Launch Angle
* Barrel %
* Hard Hit %
* xSLG
* xwOBA
* Pitch Mix
* Pitch Type Performance
* Pitcher Arsenal
* Zone Data
* Missed HR Tracker
* BvP Pitch History
* Expected Statistics
* EV Logs
* Distance
* Sweet Spot %

Baseball Savant is the source of truth for player quality.

### 2. Rotowire Daily Lineups

Use for:

* Projected lineups
* Confirmed lineups
* Batting order
* Starting pitchers
* Injury/news notes
* Lineup status

Rotowire is the main lineup source.

### 3. MLB Stats API

Use for:

* Schedule
* Teams
* Rosters
* Live scores
* Game feed
* Box scores
* Historical results
* Final grading

### 4. Weather Source / Open-Meteo

Use for:

* Temperature
* Wind speed
* Wind direction
* Humidity if available
* Rain probability
* Forecast data

---

## Freshness Rules

Always show freshness status.

Important freshness checks:

* Schedule older than 60 minutes = stale
* Lineups older than 15 minutes on game day = stale
* Weather older than 60 minutes = stale
* API odds older than 15 minutes = stale
* Manual odds older than 60 minutes = warning
* Manual odds older than 3 hours = stale
* Pitcher changed = rebuild required

Never hide stale data.

Never generate fake data if a source fails.

---

## API Key Safety

Never hardcode API keys.

Never print API keys.

Never log API keys.

Never expose API keys in dashboard JSON or HTML.

Never commit `.env`.

Read keys only from:

```bash
ODDS_API_KEY_1
ODDS_API_KEY_2
ODDS_API_KEY_3
ODDS_API_KEY
```

Dashboard may show only:

* connected yes/no
* active key variable name
* quota if available

Never show the raw key value.

`.gitignore` must include:

```gitignore
.env
*.env
```

---

## Betting Model Philosophy

Do not force plays.

If the slate is weak, say it is weak.

Home runs are volatile. Total Bases is usually the stronger main edge.

The app should separate:

* Best raw play
* Best value play

Those are not always the same.

Example:

A player can have a great HR score but bad odds.

A different player can have a lower raw score but better value.

---

## Main Betting Inputs

The model should care about:

1. Environment
2. Pitcher weakness
3. Pitch mix edge
4. Batter contact quality
5. Recent EV/contact cluster
6. Missed HR signals
7. Bullpen exposure
8. Lineup spot
9. Platoon/handedness
10. Market odds/value
11. Historical model performance

---

## Contact Quality Rules

Useful power indicators:

* EV 95+ = strong contact
* EV 100+ = serious power signal
* EV 105+ = elite contact
* EV 110+ = rare/nuclear contact

Barrel % vs pitch mix:

* 10-15% = good
* 15-20% = strong
* 20%+ = elite

Do not overuse elite tags.

`NUCLEAR_CONTACT` should be rare, roughly top 5-12% of the slate.

---

## Calibration Rules

Raw model scores are for ranking.

Calibrated probabilities are for value.

Do not use raw probability for Market vs Model if calibrated probability exists.

Display both:

* Raw model probability
* Calibrated probability
* Implied sportsbook probability
* Edge %

Value grade must use calibrated probability.

If calibration is unavailable, show:

`RAW_FALLBACK`

Do not hide this warning.

---

## Calibration Coverage

Every market should show calibration status:

* CALIBRATED
* PARTIAL
* RAW_FALLBACK
* NO_DATA

Current truth:

* TB is the strongest calibrated lane
* HRR and Hits are partial but improving
* HR may be raw fallback until enough graded HR bets exist

Do not pretend HR value is as reliable as TB value if calibration data is missing.

---

## Multi-Season Rules

Use:

* 2025 baseline
* 2026 current season
* Last 30 days
* Last 14 days
* Last 7 days

Do not mix seasons blindly.

2025 = baseline skill

2026 = current season form

Recent windows = current contact trend

Use small-sample warnings.

Do not overreact to tiny 2026 samples.

---

## UI Priorities

The homepage should answer the betting questions fast.

Default landing experience should include:

1. Today’s Best 5
2. Slate Read
3. Top Value Bets
4. Top HR Plays
5. Top TB Plays
6. Top HRR Plays
7. Top Hit Plays
8. Best Environments
9. Pitchers To Attack
10. Missed HR Candidates
11. Full Matchup Table

Do not make the user scan 100+ rows first.

Raw tables should remain, but should not be the first decision screen.

---

## Required Dashboard Tabs

Keep or support these tabs:

* Home
* Slate
* Cheat Sheet
* Bet Cards
* Matchups
* My Model
* Weather
* Pitchers
* Missed HR
* Contact
* Value
* Performance
* Glossary

Add new tabs only if they clearly improve workflow.

---

## Explain Every Recommendation

Never recommend a play without reasons.

Each play should show:

* Score
* Grade
* Confidence
* Top reasons
* Red flags
* Lineup status
* Last updated
* Calibration status
* Value status if odds exist

Every score should be traceable.

Use “Why This Play?” sections.

---

## BvP Rule

Batter vs Pitcher is a supporting signal only.

Do not overrate BvP.

Raw BvP can be misleading because samples are small.

BvP cannot override:

* bad environment
* bad pitch mix
* bad recent contact
* strong pitcher
* bad lineup spot

If BvP is added, always show sample size and confidence.

---

## Model Performance Rules

Do not hide losses.

If ROI is negative, show it clearly.

If sample is small, show a warning.

The goal is to improve the model, not make it look good.

Track performance by:

* bet type
* grade
* signal
* tag
* pitch mix bucket
* contact cluster
* weather grade
* pitcher attack grade
* value alerts

---

## Testing Rules

Add tests for every new engine.

Prefer pure functions and offline tests.

Do not require live network for tests unless explicitly separated.

Before finishing a batch:

1. Run tests
2. Rebuild today’s slate if needed
3. Smoke test endpoints
4. Visually verify dashboard
5. Commit changes
6. Report changed files, new files, endpoints, limitations

---

## Development Style

Be honest.

Do not fake missing data.

Do not overstate confidence.

Do not make pretty UI while leaving bad logic.

Do not add new features before the current batch is stable.

Do not silently change scoring behavior.

If a feature is incomplete, label it clearly.

---

## Current Build Order

Recommended order:

1. Freshness + glossary
2. Decision screens
3. Weather / Pitchers / Missed HR / Contact
4. Market vs Model + manual odds
5. Model Performance
6. Calibration
7. Calibration coverage + snapshot backfill
8. Multi-season split
9. Multi-season coverage cleanup
10. Advanced BvP
11. Live API odds auto-pull
12. Further model tuning

Do not jump ahead without permission.

