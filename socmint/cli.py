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
def analyze(module: str = typer.Option("all", "--module", "-m", help="Module name or 'all'.")) -> None:
    """Run analysis modules (embeddings, media, impersonation, narratives, coordination, market, hate)."""
    from socmint.analyze.runner import run_modules
    from socmint.core.db import init_db

    init_db()
    for r in run_modules(module):
        detail = json.dumps(r.get("result") or r.get("error"), ensure_ascii=False, default=str)
        typer.echo(f"{r['module']:<14} {r['status']:<6} {detail[:300]}")


@app.command()
def check(
    text: str = typer.Argument(None, help="Message text to check."),
    file: Path = typer.Option(None, "--file", "-f", help="Image / voice note / video / text file."),
    lang: str = typer.Option(None, "--lang", help="Reply language: en | hi | mr (default: detected)."),
    as_json: bool = typer.Option(False, "--json", help="Print the full result as JSON."),
    no_store: bool = typer.Option(False, "--no-store", help="Do not store the submission."),
) -> None:
    """Run the tipline pipeline locally on text and/or a file."""
    from socmint.core.db import init_db
    from socmint.tipline.pipeline import check as run_check

    if not text and not file:
        raise typer.BadParameter("give message text or --file")
    if file and file.suffix.lower() == ".txt" and not text:
        text, file = file.read_text(encoding="utf-8"), None
    init_db()
    res = run_check(text=text, file_path=file, reply_lang=lang, channel="cli", store=not no_store)
    if as_json:
        _print_json(res.model_dump(mode="json"))
        return
    typer.echo(res.reply_text)
    typer.echo(f"\n--- evidence (verdict={res.verdict.value}, score={res.score:.0f}, lang={res.lang}, "
               f"{res.timings_ms.get('total_ms', 0):.0f} ms) ---")
    for r in res.reasons:
        typer.echo(f"  [{r.weight:>4.0f}] {r.code}: {json.dumps(r.evidence, ensure_ascii=False, default=str)[:160]}")
    for i in res.indicators:
        typer.echo(f"  indicator (suspected): {i.type} {i.value}" + (f" brand={i.linked_brand}" if i.linked_brand else ""))
    if res.degraded:
        typer.echo(f"  degraded: {', '.join(res.degraded)}")


@app.command()
def bot() -> None:
    """Start the Telegram tipline bot (needs TELEGRAM_BOT_TOKEN)."""
    from socmint.core.db import init_db
    from socmint.tipline.telegram_bot import run_bot

    init_db()
    run_bot()


@app.command()
def api(host: str = typer.Option("127.0.0.1", help="Bind address (keep local)."), port: int = 8000) -> None:
    """Start the FastAPI service."""
    import uvicorn

    uvicorn.run("socmint.api.app:app", host=host, port=port, log_level="info")


@app.command("telegram-login")
def telegram_login() -> None:
    """One-time interactive Telethon login for reading public channels (needs TELEGRAM_API_ID/HASH)."""
    from telethon.sync import TelegramClient

    from socmint.ingest.telegram_channels import session_path

    s = get_settings()
    if not s.has("telegram_api_id", "telegram_api_hash"):
        raise typer.BadParameter("set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first")
    with TelegramClient(session_path(), int(s.telegram_api_id), s.telegram_api_hash) as c:
        typer.echo(f"Logged in as a Telethon session: {bool(c.is_user_authorized())}")


@app.command()
def demo() -> None:
    """Offline end-to-end demo on synthetic fixtures (zero keys, no internet). Uses data/demo.db."""
    from socmint.demo import run_demo

    s = run_demo()
    typer.echo("\n================ SOCMINT-India demo (synthetic data only) ================")
    typer.echo(f"DB: {s['db']}\nEmbedder: {s['embedder']}\nLoaded fixtures: {s['loaded']}")
    typer.echo(f"\nTipline verdicts: {s['tipline_verdicts']}")
    typer.echo(f"  fixture expectations met: {'all' if not s['fixture_mismatches'] else s['fixture_mismatches']}")
    typer.echo(f"  URL from threat feed -> {s['threat_feed_check']}")
    typer.echo(f"  image first sent -> {s['image_first_submission']}; re-compressed forward -> {s['image_forwarded_copy']}")
    typer.echo(f"  submissions stored (de-identified): {s['submissions']}, queued for review: {s['queued_for_review']}")
    typer.echo(f"Indicators (suspected/reported): {s['indicators']}   Registered lookalike domains: {s['registered_lookalikes']}")
    typer.echo(f"Alerts by module: {s['alerts_by_module']}")
    typer.echo(f"Top narrative: {s['top_narrative']}")
    typer.echo(f"Top possible-coordination group: {s['top_coordination']}")
    typer.echo(f"Market: {s['market_alert']}")
    typer.echo(f"Modules: {s['modules']}")
    typer.echo("\nNext: `socmint dashboard --demo` to browse every view with evidence.")


@app.command()
def dashboard(demo_db: bool = typer.Option(False, "--demo", help="Browse data/demo.db instead of the main DB."),
              port: int = 8501) -> None:
    """Start the Streamlit analyst dashboard (localhost)."""
    import os
    import subprocess

    env = dict(os.environ)
    if demo_db:
        from socmint.demo import demo_db_path

        env["SOCMINT_DB_URL"] = f"sqlite:///{demo_db_path().as_posix()}"
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path), "--server.address", "127.0.0.1",
                    "--server.port", str(port), "--server.headless", "true", "--browser.gatherUsageStats", "false"], env=env, check=False)


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
