"""Env-gated HTTP Basic Auth on the dashboard (for public deploys).

Off by default so local/dev/tests are unaffected; enforced only when both
HRPB_AUTH_USER and HRPB_AUTH_PASS are set.
"""
import base64

from fastapi.testclient import TestClient

from hrplaybook.web.app import app


def _basic(u, p):
    tok = base64.b64encode(f"{u}:{p}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


def test_no_auth_when_env_unset(monkeypatch):
    monkeypatch.delenv("HRPB_AUTH_USER", raising=False)
    monkeypatch.delenv("HRPB_AUTH_PASS", raising=False)
    assert TestClient(app).get("/api/dates").status_code == 200


def test_auth_enforced_when_env_set(monkeypatch):
    monkeypatch.setenv("HRPB_AUTH_USER", "scout")
    monkeypatch.setenv("HRPB_AUTH_PASS", "s3cret")
    c = TestClient(app)
    assert c.get("/api/dates").status_code == 401                       # no creds
    assert "WWW-Authenticate" in c.get("/api/dates").headers
    assert c.get("/api/dates", headers=_basic("scout", "wrong")).status_code == 401
    assert c.get("/api/dates", headers=_basic("scout", "s3cret")).status_code == 200
