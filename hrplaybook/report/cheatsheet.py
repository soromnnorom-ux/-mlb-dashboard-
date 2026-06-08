"""Render the tiered cheat sheet to markdown + HTML via jinja2."""
from __future__ import annotations

from pathlib import Path
from typing import List

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Config
from ..model.schemas import Game, Matchup
from ..util import now_stamp
from .render import matchup_view, slate_banner

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _tiers(matchups: List[Matchup], fmt: str) -> dict:
    out = {1: [], 2: [], 3: []}
    ranked = sorted(matchups, key=lambda m: m.play_score, reverse=True)
    for m in ranked:
        if m.tier in (1, 2, 3):
            out[m.tier].append(matchup_view(m, fmt))
    return out


def build_context(date: str, games: List[Game], matchups: List[Matchup],
                  cfg: Config, fmt: str, warnings: List[str]) -> dict:
    banner = slate_banner(games)
    dead = [f"{g.away_team}@{g.home_team}" for g in games if g.env_tier == "dead-air"]
    return {
        "date": date,
        "generated_at": now_stamp(),
        "gate": cfg.gate,
        "max_plays": cfg.max_plays,
        "offline": any("OFFLINE" in w for w in warnings),
        "warnings": warnings,
        "banner": banner,
        "dead_games": dead,
        "tiers": _tiers(matchups, fmt),
    }


def render(date: str, games: List[Game], matchups: List[Matchup], cfg: Config,
           outdir: str | Path, warnings: List[str] | None = None) -> List[str]:
    warnings = warnings or []
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    env = _env()
    written = []

    md = env.get_template("cheatsheet.md.j2").render(
        **build_context(date, games, matchups, cfg, "md", warnings))
    md_path = outdir / "cheatsheet.md"
    md_path.write_text(md)
    written.append(str(md_path))

    html = env.get_template("cheatsheet.html.j2").render(
        **build_context(date, games, matchups, cfg, "html", warnings))
    html_path = outdir / "cheatsheet.html"
    html_path.write_text(html)
    written.append(str(html_path))
    return written
