"""Safe odds-API key manager.

Keys are read ONLY from environment variables (or a local .env that is
git-ignored). Raw key values never leave this module: every public function
returns env-var *names*, validity, quota, and error type — never the secret.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# Priority order: numbered keys first, then the legacy fallback.
KEY_ENV_VARS = ["ODDS_API_KEY_1", "ODDS_API_KEY_2", "ODDS_API_KEY_3", "ODDS_API_KEY"]


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: sets os.environ for KEY_* lines if not already set.

    Never prints values. Safe no-op if the file is missing.
    """
    p = Path(path)
    if not p.exists():
        return
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


def available_keys() -> List[Tuple[str, str]]:
    """[(env_var_name, raw_key)] for every non-empty configured key.

    INTERNAL USE ONLY — never serialize the second element.
    """
    out: List[Tuple[str, str]] = []
    for name in KEY_ENV_VARS:
        v = os.environ.get(name)
        if v and v.strip():
            out.append((name, v.strip()))
    return out


def has_any_key() -> bool:
    return bool(available_keys())


# A KeyTester takes a raw key and returns (valid, quota_remaining, error_type).
KeyTester = Callable[[str], Tuple[bool, Optional[int], Optional[str]]]


def check_keys(tester: KeyTester) -> List[dict]:
    """Test every configured key. Returns SAFE reports (no raw key)."""
    reports = []
    for name, key in available_keys():
        try:
            valid, quota, err = tester(key)
        except Exception as e:  # noqa: BLE001
            valid, quota, err = False, None, type(e).__name__
        reports.append({"env": name, "valid": bool(valid),
                        "quota_remaining": quota, "error": None if valid else err})
    return reports


def active_key(tester: KeyTester) -> Optional[Tuple[str, str]]:
    """First valid (env_name, raw_key), trying each in priority order.

    INTERNAL — the raw key is for making provider calls only.
    """
    for name, key in available_keys():
        try:
            valid, _, _ = tester(key)
        except Exception:  # noqa: BLE001
            valid = False
        if valid:
            return name, key
    return None


def status(tester: Optional[KeyTester] = None) -> dict:
    """Dashboard-safe status. NEVER includes any raw key value."""
    keys = available_keys()
    if not keys:
        return {"connected": False, "active_key_name": None,
                "configured": [], "keys": [], "reason": "no keys in environment"}
    if tester is None:
        # can't validate without a tester; report presence only
        return {"connected": False, "active_key_name": None,
                "configured": [n for n, _ in keys], "keys": [],
                "reason": "not validated"}
    reports = check_keys(tester)
    act = next((r["env"] for r in reports if r["valid"]), None)
    return {"connected": act is not None, "active_key_name": act,
            "configured": [n for n, _ in keys], "keys": reports}
