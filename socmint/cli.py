"""Typer CLI: `socmint <command>`."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import time
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

    env = dict(os.environ)
    if demo_db:
        from socmint.demo import demo_db_path

        env["SOCMINT_DB_URL"] = f"sqlite:///{demo_db_path().as_posix()}"
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path), "--server.address", "127.0.0.1",
                    "--server.port", str(port), "--server.headless", "true", "--browser.gatherUsageStats", "false"], env=env, check=False)


@app.command()
def showcase(demo_db: bool = typer.Option(False, "--demo", help="Open data/demo.db with the synthetic showcase "
                                                                   "investigations (offline); builds it if needed."),
             rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild data/demo.db first (implies --demo)."),
             port: int = 8502) -> None:
    """Start the one-page showcase dashboard (localhost): events, people/accounts and groups."""
    import os

    from socmint import demo

    env = dict(os.environ)
    if demo_db or rebuild:
        ready, why = (False, "--rebuild") if rebuild else demo.demo_db_ready()
        if ready:
            typer.echo("Using existing data/demo.db (run with --rebuild to build it again).")
            db_url = f"sqlite:///{demo.demo_db_path().as_posix()}"
        else:
            typer.echo(f"Building data/demo.db with synthetic data (offline; {why})…")
            t0 = time.monotonic()
            s = demo.run_showcase_demo()
            for k, v in s["investigations"].items():
                typer.echo(f"  {k:<8} #{v['id']}: {v['posts']} posts  sources: {v['runs']}")
            typer.echo(f"Built data/demo.db in {time.monotonic() - t0:.0f} s.")
            db_url = s["db"]
        env.update({"SOCMINT_DB_URL": db_url, "SOCMINT_OFFLINE": "1"})
    app_path = Path(__file__).parent / "showcase" / "app.py"
    typer.echo(f"Showcase: open http://127.0.0.1:{port} in your browser  (Ctrl+C to stop)")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path), "--server.address", "127.0.0.1",
                    "--server.port", str(port), "--server.headless", "true",
                    "--browser.gatherUsageStats", "false"], env=env, check=False)


@app.command()
def doctor(offline: bool = typer.Option(False, "--offline", help="Skip the reachability requests.")) -> None:
    """Read-only check before a demo: keys (set/missing), event sources, reachability, models, ports, demo data."""
    from socmint import doctor as doc

    checks = doc.run_checks(network=not offline)
    typer.echo(doc.render(checks))
    if any(c.level == doc.FAIL for c in checks):
        raise typer.Exit(1)


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


# ------------------------------------------------------------------------------------------- investigations
inv_app = typer.Typer(help="Investigations: events, subjects (public accounts) and groups.", no_args_is_help=True)
app.add_typer(inv_app, name="investigate")


def _inv_ref(ref: str) -> int:
    from socmint.investigate.runner import InvestigationNotFound, get

    try:
        return get(ref).id
    except InvestigationNotFound as e:
        raise typer.BadParameter(str(e)) from None


@inv_app.command("create")
def inv_create(
    file: Path = typer.Option(None, "--file", "-f", help="YAML file (several investigations separated by ---)."),
    kind: str = typer.Option(None, "--kind", help="event | subject | group"),
    name: str = typer.Option(None, "--name"),
    purpose: str = typer.Option(None, "--purpose", help="Required: why this target is monitored."),
    terms: str = typer.Option(None, "--terms", help="Comma-separated keywords/hashtags (event)."),
    lang: str = typer.Option("en", "--lang", help="Language of --terms (en, hi, mr, hinglish)."),
    handle: list[str] = typer.Option(None, "--handle", help="platform:handle, repeatable (subject/group)."),
    source: list[str] = typer.Option(None, "--source", help="Logical source, repeatable (default for an event: the "
                                     "no-key sources plus YouTube / Fact Check when their key is in .env)."),
    description: str = typer.Option("", "--description"),
) -> None:
    """Create investigation(s) from a YAML file or from options."""
    from pydantic import ValidationError

    from socmint.core.db import init_db
    from socmint.investigate.config import InvestigationConfig, load_file, parse_handles, source_plan
    from socmint.investigate.runner import create

    init_db()
    try:
        if file:
            cfgs = load_file(file)
        else:
            if not (kind and name):
                raise typer.BadParameter("give --file, or --kind and --name (plus --purpose)")
            cfgs = [InvestigationConfig(
                kind=kind, name=name, purpose=purpose or "", description=description,
                terms={lang: [t.strip() for t in (terms or "").split(",") if t.strip()]} if terms else {},
                handles=parse_handles(handle or []), sources=list(source or []))]
    except (ValidationError, ValueError) as e:
        raise typer.BadParameter(str(e)) from None
    for c in cfgs:
        try:
            iid = create(c)
        except ValueError as e:
            typer.echo(f"skipped {c.name!r}: {e}")
            continue
        typer.echo(f"created investigation #{iid} [{c.kind}] {c.name}  sources: {', '.join(c.sources)}")
        for logical, note in source_plan(c):
            typer.echo(f"  {logical:<18} {note}")


@inv_app.command("edit")
def inv_edit(
    ref: str = typer.Argument(..., help="Investigation id or name."),
    add_terms: str = typer.Option(None, "--add-terms", help="Comma-separated keywords/hashtags to add (under --lang)."),
    remove_terms: str = typer.Option(None, "--remove-terms", help="Comma-separated terms to remove (any language)."),
    lang: str = typer.Option("en", "--lang", help="Language of --add-terms (en, hi, mr, hinglish)."),
    add_source: list[str] = typer.Option(None, "--add-source", help="Logical source to add, repeatable."),
    remove_source: list[str] = typer.Option(None, "--remove-source", help="Logical source to remove, repeatable."),
    purpose: str = typer.Option(None, "--purpose", help="New purpose (why this target is monitored)."),
) -> None:
    """Change an investigation's terms, sources or purpose (audited with the before and after)."""
    from socmint.core.db import init_db
    from socmint.investigate.config import source_plan
    from socmint.investigate.runner import InvestigationNotFound, config_of, edit, get

    def split(v: str | None) -> list[str]:
        return [t.strip() for t in (v or "").split(",") if t.strip()]

    init_db()
    try:
        change = edit(_inv_ref(ref), add_terms=split(add_terms), remove_terms=split(remove_terms), lang=lang,
                      add_sources=list(add_source or []), remove_sources=list(remove_source or []), purpose=purpose)
    except (ValueError, InvestigationNotFound) as e:
        typer.echo(f"not changed: {e}", err=True)
        raise typer.Exit(1) from None
    for k in change["after"]:
        typer.echo(f"{k}: {change['before'][k]} -> {change['after'][k]}")
    cfg = config_of(get(change["id"]))
    for logical, note in source_plan(cfg):
        if logical in cfg.sources and note != "used":
            typer.echo(f"  {logical:<18} {note}")


@inv_app.command("list")
def inv_list() -> None:
    """List investigations."""
    from socmint.core.db import init_db
    from socmint.investigate.runner import list_all

    init_db()
    rows = list_all()
    if not rows:
        typer.echo("No investigations yet. Create one with `socmint investigate create`.")
    for r in rows:
        last = r["last_collected_at"].strftime("%Y-%m-%d %H:%M") if r["last_collected_at"] else "never"
        typer.echo(f"#{r['id']:<3} {r['kind']:<8} {r['name'][:40]:<40} posts={r['posts']:<5} accounts={r['accounts']:<4} "
                   f"collected={last}" + ("  [synthetic]" if r["synthetic"] else ""))


@inv_app.command("collect")
def inv_collect(ref: str = typer.Argument(..., help="Investigation id or name."),
                source: str = typer.Option(None, "--source", help="Only this logical source (e.g. bluesky).")) -> None:
    """Collect now from every configured source (failing sources are reported and skipped)."""
    from socmint.core.db import init_db
    from socmint.ingest.registry import sync_brands
    from socmint.investigate.runner import collect

    init_db()
    sync_brands()

    def progress(name: str, run: dict) -> None:
        typer.echo(f"  {name:<22} {run['status']:<18} fetched={run['fetched']:<5} new={run['new']:<5}"
                   + (f" [{run['mode']}]" if run.get("mode") else "")
                   + (f" {run['detail'][:150]}" if run.get("detail") else ""))

    out = collect(_inv_ref(ref), only_source=source, progress=progress)
    typer.echo(f"\nnew posts: {out['new_posts']}   categories: {out['enrich'].get('labels', {})}"
               + (f"   degraded: {out['enrich']['degraded']}" if out["enrich"].get("degraded") else ""))


@inv_app.command("analyze")
def inv_analyze(ref: str = typer.Argument(..., help="Investigation id or name.")) -> None:
    """Narratives, possible coordination and market risk, scoped to this investigation's posts."""
    from socmint.core.db import init_db
    from socmint.investigate.runner import analyze

    init_db()
    out = analyze(_inv_ref(ref))
    for m, r in out["modules"].items():
        typer.echo(f"{m:<14} {r['status']:<6} {json.dumps(r.get('result') or r.get('error'), default=str)[:240]}")


@inv_app.command("show")
def inv_show(ref: str = typer.Argument(..., help="Investigation id or name."),
             as_json: bool = typer.Option(False, "--json", help="Print the full summary as JSON.")) -> None:
    """Summary: counts, categories, top accounts/hashtags/domains, indicators, fact-checks, coordination."""
    from socmint.core.db import init_db
    from socmint.investigate.report import summary
    from socmint.investigate.runner import record_view

    init_db()
    iid = _inv_ref(ref)
    record_view(iid, "cli")
    s = summary(iid)
    if as_json:
        _print_json(s)
        return
    i, c = s["investigation"], s["counts"]
    typer.echo(f"#{i['id']} [{i['kind']}] {i['name']}" + ("  (synthetic)" if i["synthetic"] else ""))
    typer.echo(f"purpose: {i['purpose']}")
    typer.echo(f"posts {c['posts']} · sources {c['sources']} · accounts {c['accounts']} · "
               f"first {s['first_seen']} · last {s['last_seen']}")
    typer.echo(f"by source:   {s['by_source']}\nby language: {s['by_language']}\nby category: {s['by_category']}")
    typer.echo(f"top hashtags: {s['top_hashtags'][:8]}\ntop domains:  {s['top_domains'][:8]}")
    for a in s["top_accounts"][:8]:
        typer.echo(f"  account {a['platform']}:{a['handle'] or a['platform_id']} role={a['role']} "
                   f"posts={a['posts_in_investigation']} followers={a['followers']}")
    for ind in s["indicators"][:10]:
        typer.echo(f"  indicator {ind['type']} {ind['value']} ({ind['status']}) in {ind['posts_here']} posts")
    for f in s["factcheck_matches"][:5]:
        typer.echo(f"  fact-check match ({f['posts']} posts): {f['publisher']}: {f['title'][:90]}")
    for g in s["coordination_groups"][:5]:
        typer.echo(f"  possible coordination #{g['id']}: score {g['score']:.2f}, {g['accounts']} accounts, "
                   f"{g['time_span_minutes']:.0f} min: {g['sample_text'][:70]}")
    for m in s["market_alerts"]:
        typer.echo(f"  market risk: {m['title']} (score {m['risk_score']})")


@inv_app.command("export")
def inv_export(ref: str = typer.Argument(..., help="Investigation id or name."),
               fmt: str = typer.Option("json", "--format", help="json | csv")) -> None:
    """Export posts, accounts, edges, indicators, summary and config with a SHA-256 manifest (data/exports/)."""
    from socmint.core.db import init_db
    from socmint.investigate.report import export

    init_db()
    path = export(_inv_ref(ref), fmt)
    typer.echo(f"exported to {path}")


@inv_app.command("promote-group")
def inv_promote(group_id: int = typer.Argument(..., help="Coordination group id (see `investigate show`)."),
                name: str = typer.Option(..., "--name"),
                purpose: str = typer.Option(..., "--purpose", help="Required: why this group is monitored.")) -> None:
    """Promote a detected possible-coordination group to a `group` investigation."""
    from socmint.core.db import init_db
    from socmint.investigate.runner import promote_group

    init_db()
    if not purpose.strip():
        raise typer.BadParameter("purpose is required")
    typer.echo(f"created group investigation #{promote_group(group_id, name, purpose)}")


@inv_app.command("delete")
def inv_delete(ref: str = typer.Argument(..., help="Investigation id or name."),
               yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation.")) -> None:
    """Delete an investigation, its links and any posts/accounts no other investigation references."""
    from socmint.core.db import init_db
    from socmint.investigate.runner import delete_investigation, get

    init_db()
    inv = get(_inv_ref(ref))
    if not yes and not typer.confirm(f"Delete investigation #{inv.id} {inv.name!r} and its orphaned posts?"):
        raise typer.Abort()
    _print_json(delete_investigation(inv.id))


if __name__ == "__main__":
    app()
