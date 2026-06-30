# HR Playbook — production image (FastAPI dashboard + build pipeline)
#
# Runs FROM SOURCE at /app (NOT `pip install .`) on purpose: config.py derives
# REPO_ROOT from __file__, and the app reads ./out, ./config.yaml, ./parks.csv
# relative to the working dir. Installing as a package would break those paths.
#
# The per-date data dir is ./out  ->  mount a persistent volume at /app/out.
FROM python:3.12-slim

# system deps kept minimal; pandas/httpx ship manylinux wheels (no compiler needed)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1) deps first for layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 2) app source (out/, .venv, .git, snapshots excluded via .dockerignore)
COPY . .

# 3) data dir for the persistent volume mount point
RUN mkdir -p /app/out

# Railway/Render/Fly inject $PORT; default 8000 for local/VPS runs.
EXPOSE 8000
CMD ["sh", "-c", "python -m uvicorn hrplaybook.web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
