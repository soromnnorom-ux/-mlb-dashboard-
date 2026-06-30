# Deploying HR Playbook (build-capable)

The FastAPI server **is** the whole app: it serves the styled dashboard at `/`
and the data API at `/api/*`, and runs the Run / Refresh / Grade pipeline as
in-process background threads. So deploying this one service gives you the full
live site at your domain — you do **not** need the standalone `hrplaybook-live.html`.

## What the host must provide

Because builds run in background threads and write to `./out`, the host must be:

- **Always-on** (no scale-to-zero / sleep — that would kill an in-progress build
  and drop the job).
- **Persistent disk** mounted at **`/app/out`** (per-date data, `_ledger.csv`,
  calibration tables live here).
- **Outbound network** (Baseball Savant, Rotowire, MLB API, Open-Meteo, Odds API).
- **Secrets**: `ODDS_API_KEY_1` (and optionally `_2`, `_3`). Without them the app
  still runs; only live odds pulls are disabled.

> Resource sizing: ~1 vCPU / 1 GB RAM is enough; a full slate build is network-
> bound (a minute or two), not CPU-bound.

---

## Option A — Railway (easiest always-on)

1. Push this repo to GitHub.
2. Railway → **New Project → Deploy from GitHub repo** → pick this repo.
   It auto-detects the `Dockerfile` (via `railway.json`).
3. **Variables** → add:
   - `ODDS_API_KEY_1` = your key (and `_2`/`_3` if you have them).
   - `HRPB_AUTH_USER` and `HRPB_AUTH_PASS` = **a username + password to lock the
     site** (strongly recommended for a public URL — see "Password protection").
4. **Volumes** → New Volume → mount path **`/app/out`**. (Critical — without this
   your builds vanish on every redeploy.)
5. **Settings → Networking → Generate Domain.** Open it → the browser prompts for
   the user/pass you set, then the dashboard loads.
6. Click **Run** in the header to build today's slate on the server.

### Password protection (HTTP Basic Auth)
The dashboard is otherwise **unauthenticated** and its Run/Refresh/Grade buttons
trigger jobs and use your odds-API quota. Set **both** of these env vars and the
app requires a username+password on every request:

```
HRPB_AUTH_USER=scout
HRPB_AUTH_PASS=<a long random password>
```

If either is unset the middleware is a no-op (local dev stays open). Auth is a
single Basic-Auth gate — fine over Railway's HTTPS; don't run it over plain HTTP.

Railway keeps the service always-on (usage-based, ~$5/mo for an idle small app).

---

## Option B — Any VPS (Docker Compose)

```bash
# on the server, in the repo dir
printf 'ODDS_API_KEY_1=YOURKEY\n' > .env        # not committed
docker compose up -d --build
# app on http://<server-ip>:8000
```

Put a reverse proxy (Caddy/Nginx) in front for HTTPS, e.g. Caddy one-liner:

```
your.domain.com {
    reverse_proxy 127.0.0.1:8000
}
```

The named volume `hrplaybook_out` persists `/app/out` across restarts and rebuilds.

---

## Seeding your existing history (optional but recommended)

A fresh deploy starts with an **empty `out/`** (Performance/Calibration will be
sparse until you build slates). To carry over your local 95 MB of history:

- **VPS / Compose:** copy local `out/` into the named volume:
  ```bash
  # from your machine, after the container is up on the server
  rsync -az ./out/ <user>@<server>:/tmp/out_seed/
  # on the server:
  docker run --rm -v hrplaybook_out:/dst -v /tmp/out_seed:/src alpine \
    sh -c 'cp -a /src/. /dst/'
  docker compose restart
  ```
- **Railway:** simplest is to (1) temporarily comment out the `out/` line in
  `.dockerignore`, (2) redeploy once so the image seeds the volume on first boot,
  (3) restore `.dockerignore` and redeploy. (Adds ~95 MB to that one build.)
- **Or** just start fresh and let the model rebuild history as you run slates.

---

## Notes

- **CORS:** not needed — the backend serves the dashboard itself, same origin.
  Only relevant if you host `hrplaybook-live.html` on a *separate* static host
  pointed at this API (then set `HRPB_API_BASE` in that file + add CORS here).
- **Image build runs from source at `/app`** (not `pip install .`) on purpose, so
  `config.yaml`, `parks.csv`, and `./out` resolve correctly. Don't "optimize" that
  into a package install.
- **Keys are never baked into the image** — they come from host env/secrets only.
