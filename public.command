#!/bin/bash
# HR Playbook — start the dashboard + a public URL, owned by YOUR terminal.
# Double-click this file in Finder, or run:  ~/hrplaybook/public.command
# Keep this window open; closing it (or Ctrl-C) stops the server + tunnel.
set -e
cd "$(dirname "$0")"

# 1) venv
if [ ! -d .venv ]; then python3 -m venv .venv; fi
source .venv/bin/activate
python -c "import hrplaybook" 2>/dev/null || pip install -q -e . >/dev/null 2>&1

# 2) load odds key from .env if present (kept out of git)
[ -f .env ] && echo "odds .env: present" || echo "odds .env: none (manual odds still work)"

# 3) start the API server on :8000 (reuse if already up)
if ! curl -s -o /dev/null -m1 http://127.0.0.1:8000/api/dates 2>/dev/null; then
  echo "starting server on http://127.0.0.1:8000 ..."
  hrplaybook serve --host 127.0.0.1 --port 8000 >/tmp/hrpb_web.log 2>&1 &
  SERVER_PID=$!
  until curl -s -o /dev/null -m1 http://127.0.0.1:8000/api/dates 2>/dev/null; do sleep 0.5; done
fi
echo "✅ local:  http://127.0.0.1:8000"

# 4) start the public tunnel (no account needed; URL changes each run)
CF=./bin/cloudflared
if [ ! -x "$CF" ]; then
  echo "cloudflared not found at $CF — local URL still works."
  echo "Press Ctrl-C to stop."; wait; exit 0
fi
echo "opening public tunnel (URL prints below in ~5s) ..."
"$CF" tunnel --url http://localhost:8000 --no-autoupdate 2>/tmp/cf_tunnel.log &
TUNNEL_PID=$!
for i in $(seq 1 30); do
  URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/cf_tunnel.log | head -1)
  [ -n "$URL" ] && break; sleep 1
done
echo ""
echo "============================================================"
echo "  🌐 PUBLIC URL:  ${URL:-see /tmp/cf_tunnel.log}"
echo "  (works on your phone / shareable while this window is open)"
echo "============================================================"
echo ""
echo "Leave this window open. Ctrl-C to stop everything."

cleanup(){ echo; echo "stopping..."; kill "$TUNNEL_PID" 2>/dev/null; pkill -f "hrplaybook serve" 2>/dev/null; exit 0; }
trap cleanup INT TERM
wait "$TUNNEL_PID"
