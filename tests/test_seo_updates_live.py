"""Google update log: dashboard incidents, the live cache and date windows."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import seo_updates  # noqa: E402

INCIDENT = {
    "id": "XhUDXP7A67iHCD2kmbVu", "begin": "2026-09-24T16:15:00+00:00",
    "end": "2026-10-08T08:00:00+00:00", "external_desc": "September 2026 spam update",
    "most_recent_update": {"text": "The rollout was complete as of October 8, 2026."},
    "service_key": "rGHU1u87FJnkP6W2GwMi", "service_name": "Ranking",
    "uri": "incidents/XhUDXP7A67iHCD2kmbVu",
}


def test_incident_maps_to_a_ledger_entry() -> None:
    e = seo_updates.incident_to_entry(INCIDENT)
    assert e["date"] == "2026-09-24" and e["end"] == "2026-10-08"
    assert e["kind"] == "spam" and e["duration"] == "13 d 15 h"
    assert e["source"] == "https://status.search.google.com/incidents/XhUDXP7A67iHCD2kmbVu"


def test_unexpected_uri_falls_back_to_the_history_page() -> None:
    e = seo_updates.incident_to_entry({**INCIDENT, "uri": "https://evil.example/x"})
    assert e["source"].startswith("https://status.search.google.com/products/")


def test_cache_adds_new_updates_without_duplicating_known_ones(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "live.json"
    monkeypatch.setenv("CLAUDE_SEO_UPDATES_CACHE", str(cache))
    future = {**INCIDENT, "id": "Zz9", "uri": "incidents/Zz9", "external_desc": "March 2027 core update",
              "begin": "2027-03-10T10:00:00+00:00", "end": None}
    entries = [seo_updates.incident_to_entry(i) for i in (INCIDENT, future)]
    cache.write_text(json.dumps({"fetched_at": "2027-03-11T00:00:00+00:00", "updates": entries}))
    data = seo_updates._load()
    names = [u["name"] for u in data["updates"]]
    assert "March 2027 core update" in names
    assert sum(1 for u in data["updates"] if u["date"] == "2026-09-24" and u["kind"] == "spam") == 1


def test_updates_between_includes_rollouts_that_overlap_the_window(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_SEO_UPDATES_CACHE", str(tmp_path / "none.json"))
    names = [u["name"] for u in seo_updates.updates_between("2026-10-01", "2026-11-03")]
    assert "September 2026 Spam Update" in names  # started before, finished inside
    assert all(u["kind"] in {"core", "spam", "core+spam", "discover", "ranking", "incident"}
               for u in seo_updates.updates_between("2024-01-01", "2026-12-31"))


def test_refresh_if_stale_survives_being_offline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_SEO_UPDATES_CACHE", str(tmp_path / "live.json"))

    def boom(timeout=15.0):
        raise OSError("offline")

    monkeypatch.setattr(seo_updates, "refresh", boom)
    res = seo_updates.refresh_if_stale(7)
    assert res["refreshed"] is False and "could not reach" in res["reason"]
