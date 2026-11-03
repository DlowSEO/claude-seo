#!/usr/bin/env python3
"""Audit progress tracker: a progress file, a one-line bar and a status line.

``/seo audit`` calls ``start``, ``stage`` and ``finish`` as it works. Each call
updates ``{domain}-audit/.progress.json`` and ``~/.config/claude-seo/progress.json``
and prints a one-line bar. ``statusline`` renders the bar for the Claude Code
status line; ``install-statusline`` adds it to ~/.claude/settings.json when no
status line is set yet.

Usage::

    claude-seo run audit_progress.py start example.com-audit --domain example.com --mode full
    claude-seo run audit_progress.py stage example.com-audit crawl --status done --detail "142 pages"
    claude-seo run audit_progress.py stage example.com-audit specialists --done 3 --total 7
    claude-seo run audit_progress.py finish example.com-audit --score 71
    claude-seo run audit_progress.py show example.com-audit
    claude-seo run audit_progress.py install-statusline
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

# key, label, weight (share of the bar)
STAGES = [
    ("setup", "Setup", 4),
    ("render", "Homepage", 6),
    ("crawl", "Crawl", 20),
    ("specialists", "Specialists", 45),
    ("score", "Scoring", 8),
    ("compare", "Comparison", 5),
    ("report", "Report", 12),
]
STAGE_KEYS = [k for k, _, _ in STAGES]
STATUSES = ("pending", "running", "done", "skipped")
FRESH_SECONDS = 3 * 3600      # hide a stale, unfinished audit after 3 hours
DONE_LINGER_SECONDS = 15 * 60  # keep the finished line visible for 15 minutes
BAR_WIDTH = 12
LABEL = "seo audit"


def home_dir() -> Path:
    return Path(os.environ.get("CLAUDE_SEO_HOME") or (Path.home() / ".config" / "claude-seo"))


def global_file() -> Path:
    return home_dir() / "progress.json"


def _now() -> float:
    return time.time()


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("stages"), dict) else None


def _save(audit_dir: Path, data: dict) -> None:
    data["updated"] = _now()
    _write(audit_dir / ".progress.json", data)
    _write(global_file(), data)


def _load(audit_dir: Path) -> dict:
    data = _read(audit_dir / ".progress.json")
    if data is None:
        raise SystemExit(f"No audit in progress in {audit_dir}. Run 'start' first.")
    return data


def percent(data: dict) -> int:
    total = got = 0.0
    for key, _, weight in STAGES:
        st = data["stages"].get(key, {})
        status = st.get("status", "pending")
        if status == "skipped":
            continue
        total += weight
        if status == "done":
            got += weight
        elif status == "running":
            done, tot = st.get("done"), st.get("total")
            frac = (done / tot) if isinstance(done, int) and isinstance(tot, int) and tot > 0 else 0.25
            got += weight * min(max(frac, 0.0), 0.95)
    if data.get("finished"):
        return 100
    return int(round(100 * got / total)) if total else 0


def _current(data: dict) -> tuple[str, dict] | None:
    for key, label, _ in STAGES:
        st = data["stages"].get(key, {})
        if st.get("status") == "running":
            return label, st
    for key, label, _ in STAGES:
        st = data["stages"].get(key, {})
        if st.get("status", "pending") == "pending":
            return label, st
    return None


def _elapsed(data: dict) -> str:
    end = data.get("finished") or _now()
    secs = max(0, int(end - data.get("started", end)))
    return f"{secs // 60}m{secs % 60:02d}s"


def bar(pct: int, width: int = BAR_WIDTH, ascii_only: bool = False) -> str:
    full = int(round(width * pct / 100))
    on, off = ("#", "-") if ascii_only else ("█", "░")
    return on * full + off * (width - full)


def render_line(data: dict, ascii_only: bool = False, colour: bool = False) -> str:
    pct = percent(data)
    site = data.get("domain") or "audit"
    if data.get("finished"):
        score = data.get("score")
        tail = f"done, health score {score}" if score is not None else "done"
        line = f"{LABEL}  {site}  {bar(100, ascii_only=ascii_only)} 100%  {tail}  ({_elapsed(data)})"
    else:
        cur = _current(data)
        step = ""
        if cur:
            label, st = cur
            step = label
            if isinstance(st.get("done"), int) and isinstance(st.get("total"), int):
                step += f" {st['done']}/{st['total']}"
            if st.get("detail"):
                step += f": {st['detail']}"
        line = f"{LABEL}  {site}  {bar(pct, ascii_only=ascii_only)} {pct}%  {step}  ({_elapsed(data)})"
    if colour:
        line = f"\033[1m{LABEL}\033[0m" + line[len(LABEL):]
    return line


def start(audit_dir: Path, domain: str, mode: str | None, compare: bool) -> dict:
    data = {
        "domain": domain,
        "audit_dir": str(audit_dir.resolve()),
        "mode": mode,
        "started": _now(),
        "finished": None,
        "score": None,
        "stages": {k: {"status": "pending"} for k in STAGE_KEYS},
    }
    data["stages"]["setup"] = {"status": "running"}
    if not compare:
        data["stages"]["compare"] = {"status": "skipped"}
    _save(audit_dir, data)
    return data


def set_stage(audit_dir: Path, key: str, status: str | None, detail: str | None,
              done: int | None, total: int | None) -> dict:
    data = _load(audit_dir)
    st = data["stages"].setdefault(key, {"status": "pending"})
    if status is None:
        status = "done" if (done is not None and total is not None and done >= total) else "running"
    st["status"] = status
    if detail is not None:
        st["detail"] = detail[:60]
    if done is not None:
        st["done"] = done
    if total is not None:
        st["total"] = total
    if status in ("running", "done"):
        # Everything before a stage that has started is finished or skipped.
        for k in STAGE_KEYS[:STAGE_KEYS.index(key)]:
            if data["stages"][k].get("status") in ("pending", "running"):
                data["stages"][k]["status"] = "done"
    _save(audit_dir, data)
    return data


def finish(audit_dir: Path, score) -> dict:
    data = _load(audit_dir)
    for k in STAGE_KEYS:
        if data["stages"][k].get("status") in ("pending", "running"):
            data["stages"][k]["status"] = "done"
    data["finished"] = _now()
    data["score"] = score
    _save(audit_dir, data)
    return data


def statusline(stdin_text: str = "") -> str:
    data = _read(global_file())
    if not data:
        return ""
    age = _now() - float(data.get("updated") or 0)
    if data.get("finished"):
        if _now() - float(data["finished"]) > DONE_LINGER_SECONDS:
            return ""
    elif age > FRESH_SECONDS:
        return ""
    return render_line(data, colour=True)


def statusline_command() -> str:
    """The status line runs this file directly with the runtime's Python.

    It only reads a small JSON file, so it skips the launcher's licence and
    runtime checks, which would slow every redraw of the status line."""
    script = Path(__file__).resolve()
    return f'"{sys.executable}" "{script}" statusline'


def install_statusline(settings_path: Path, command: str | None = None) -> dict:
    settings = {}
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        except ValueError:
            return {"installed": False, "reason": f"{settings_path} is not valid JSON, so it was left alone."}
    command = command or statusline_command()
    existing = settings.get("statusLine")
    if existing:
        if isinstance(existing, dict) and "audit_progress.py statusline" in str(existing.get("command", "")):
            return {"installed": True, "reason": "already installed", "command": command}
        return {"installed": False,
                "reason": "A status line is already set, so it was not replaced. To show audit progress too, "
                          f"have your status line script also run: {command}",
                "command": command}
    settings["statusLine"] = {"type": "command", "command": command, "padding": 0}
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.is_file():
        shutil.copy2(settings_path, settings_path.with_name(settings_path.name + ".claude-seo.bak"))
    _write(settings_path, settings)
    return {"installed": True, "settings": str(settings_path), "command": command}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Audit progress tracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("audit_dir")
    s.add_argument("--domain", required=True)
    s.add_argument("--mode")
    s.add_argument("--compare", action="store_true", help="This audit includes a comparison stage")
    g = sub.add_parser("stage")
    g.add_argument("audit_dir")
    g.add_argument("stage", choices=STAGE_KEYS)
    g.add_argument("--status", choices=STATUSES)
    g.add_argument("--detail")
    g.add_argument("--done", type=int)
    g.add_argument("--total", type=int)
    f = sub.add_parser("finish")
    f.add_argument("audit_dir")
    f.add_argument("--score", type=float)
    sh = sub.add_parser("show")
    sh.add_argument("audit_dir")
    sub.add_parser("statusline")
    i = sub.add_parser("install-statusline")
    i.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    i.add_argument("--command", default=None, help=argparse.SUPPRESS)
    for sp in (s, g, f, sh, i):
        sp.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "statusline":
        stdin_text = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        out = statusline(stdin_text)
        if out:
            print(out)
        return 0
    if args.cmd == "install-statusline":
        res = install_statusline(Path(args.settings).expanduser(), args.command)
        print(json.dumps(res, indent=2) if args.json else res.get("reason") or f"Installed: {res.get('command')}")
        return 0

    audit_dir = Path(args.audit_dir)
    if args.cmd == "start":
        data = start(audit_dir, args.domain, args.mode, args.compare)
    elif args.cmd == "stage":
        data = set_stage(audit_dir, args.stage, args.status, args.detail, args.done, args.total)
    elif args.cmd == "finish":
        score = args.score
        if score is not None and float(score).is_integer():
            score = int(score)
        data = finish(audit_dir, score)
    else:
        data = _load(audit_dir)
    if args.json:
        print(json.dumps({"percent": percent(data), "line": render_line(data, ascii_only=True),
                          "stages": data["stages"]}, indent=2))
    else:
        print(render_line(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
