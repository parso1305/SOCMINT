"""Typer CLI: `socmint <command>`."""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import typer

from socmint.core.settings import PROJECT_ROOT, get_settings, reset_settings

app = typer.Typer(add_completion=False, help="SOCMINT-India v0 prototype CLI.", no_args_is_help=True)


def _utf8_stdout() -> None:
    """Windows consoles default to cp1252; force UTF-8 so Hindi/Marathi output prints."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


_utf8_stdout()


def ensure_env_file() -> Path:
    """Create .env from .env.example with generated PRIVACY_SALT and SOCMINT_API_KEY if missing."""
    env = PROJECT_ROOT / ".env"
    if env.exists():
        return env
    example = PROJECT_ROOT / ".env.example"
    lines = example.read_text(encoding="utf-8").splitlines() if example.exists() else []
    out = []
    for line in lines:
        if line.startswith("PRIVACY_SALT="):
            line = f"PRIVACY_SALT={secrets.token_hex(32)}"
        elif line.startswith("SOCMINT_API_KEY="):
            line = f"SOCMINT_API_KEY={secrets.token_urlsafe(24)}"
        out.append(line)
    env.write_text("\n".join(out) + "\n", encoding="utf-8")
    reset_settings()
    return env


@app.command("init-db")
def init_db_cmd() -> None:
    """Create the database tables (and .env with generated secrets if missing)."""
    from socmint.core.db import init_db
    from socmint.ingest.registry import sync_brands, sync_sources

    env = ensure_env_file()
    init_db()
    sync_sources()
    sync_brands()
    typer.echo(f"Database ready: {get_settings().db_url}")
    typer.echo(f"Settings file: {env}")


@app.command()
def ingest(source: str = typer.Option("all", "--source", "-s", help="Source name from sources.yaml, or 'all'.")) -> None:
    """Run ingestion sources (failing sources are logged and skipped)."""
    from socmint.core.db import init_db
    from socmint.ingest.runner import run_sources

    init_db()
    results = run_sources(source)
    for r in results:
        new = r.get("new") or {}
        new_txt = ", ".join(f"{k}={v}" for k, v in new.items() if v) or "-"
        typer.echo(f"{r['source']:<22} {r['status']:<18} fetched={r.get('fetched', 0):<5} new: {new_txt}"
                   + (f"  ({r['detail'][:110]})" if r.get("detail") else ""))
    ok = sum(r["status"] == "ok" for r in results)
    typer.echo(f"\n{ok}/{len(results)} sources ok")


@app.command()
def version() -> None:
    """Print the version."""
    from socmint import __version__

    typer.echo(__version__)


@app.command()
def purge() -> None:
    """Apply retention: delete raw media older than RETENTION_DAYS and clear their paths."""
    from socmint.tipline.pipeline import apply_retention

    stats = apply_retention()
    _print_json(stats)


def _print_json(obj: object) -> None:
    typer.echo(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    app()
