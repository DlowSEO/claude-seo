"""Audit progress tracker and status line."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import audit_progress  # noqa: E402


def test_progress_moves_forward_and_finishes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_SEO_HOME", str(tmp_path / "home"))
    d = tmp_path / "example.com-audit"
    data = audit_progress.start(d, "example.com", "full", compare=False)
    assert data["stages"]["compare"]["status"] == "skipped"
    first = audit_progress.percent(data)
    data = audit_progress.set_stage(d, "crawl", None, "142 pages", None, None)
    assert data["stages"]["setup"]["status"] == "done"
    mid = audit_progress.percent(data)
    data = audit_progress.set_stage(d, "specialists", None, None, 3, 6)
    later = audit_progress.percent(data)
    assert first < mid < later < 100
    assert "Specialists 3/6" in audit_progress.render_line(data)
    data = audit_progress.finish(d, 71)
    assert audit_progress.percent(data) == 100
    assert "health score 71" in audit_progress.statusline()


def test_statusline_is_empty_without_a_recent_audit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_SEO_HOME", str(tmp_path / "home"))
    assert audit_progress.statusline() == ""
    d = tmp_path / "a-audit"
    audit_progress.start(d, "a.com", None, compare=True)
    assert "a.com" in audit_progress.statusline()
    state = json.loads((tmp_path / "home" / "progress.json").read_text())
    state["updated"] -= audit_progress.FRESH_SECONDS + 10
    (tmp_path / "home" / "progress.json").write_text(json.dumps(state))
    assert audit_progress.statusline() == ""


def test_install_statusline_never_replaces_an_existing_one(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"statusLine": {"type": "command", "command": "my-line"}, "model": "x"}))
    res = audit_progress.install_statusline(settings, "python3 audit_progress.py statusline")
    assert res["installed"] is False
    assert json.loads(settings.read_text())["statusLine"]["command"] == "my-line"

    fresh = tmp_path / "fresh.json"
    fresh.write_text(json.dumps({"model": "x"}))
    res = audit_progress.install_statusline(fresh, "python3 audit_progress.py statusline")
    saved = json.loads(fresh.read_text())
    assert res["installed"] and saved["model"] == "x"
    assert saved["statusLine"]["command"].endswith("statusline")
    again = audit_progress.install_statusline(fresh, "python3 audit_progress.py statusline")
    assert again["reason"] == "already installed"
