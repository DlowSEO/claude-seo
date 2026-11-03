#!/usr/bin/env python3
"""
Primary-source Google updates query tool.

Reads ``data/google-updates.json`` and surfaces filtered views of the
confirmed Google ranking / spam / QRG / product / schema updates
since March 2024. Every entry cites a Google-owned URL.

The companion file lists *unverified* third-party claims separately. Those
entries are explicitly NOT used to drive recommendations until they are
confirmed against ``status.search.google.com``.

Usage::

    python scripts/seo_updates.py                  # latest 10
    python scripts/seo_updates.py --since 2025-06  # ISO yyyy or yyyy-mm filter
    python scripts/seo_updates.py --kind core
    python scripts/seo_updates.py --json
    python scripts/seo_updates.py --unverified     # show 3rd-party claims awaiting check
    python scripts/seo_updates.py --refresh        # pull Google's Search Status Dashboard
    python scripts/seo_updates.py --refresh-if-stale 14 --between 2026-08-01 2026-10-31

``--refresh`` reads Google's own JSON feed
(https://status.search.google.com/incidents.json) and caches it in
``~/.config/claude-seo/google-updates-live.json``. Every query merges the bundled
ledger with that cache, so audits see updates released after this version
shipped. The bundled file stays the reviewed record; the cache only adds
dashboard incidents, each citing its status.search.google.com page.
"""

from __future__ import annotations

import argparse
import json
import sys
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "google-updates.json"
STATUS_FEED = "https://status.search.google.com/incidents.json"
STATUS_BASE = "https://status.search.google.com/"
RANKING_PRODUCT = "rGHU1u87FJnkP6W2GwMi"


def cache_path() -> Path:
    override = os.environ.get("CLAUDE_SEO_UPDATES_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".config" / "claude-seo" / "google-updates-live.json"


def _load_bundled() -> dict:
    with _DATA_FILE.open() as fh:
        return json.load(fh)


def _load_cache() -> dict | None:
    p = cache_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("updates"), list) else None


def _same_update(a: dict, b: dict) -> bool:
    if a.get("source") and a.get("source") == b.get("source"):
        return True
    if a.get("kind") != b.get("kind"):
        return False
    try:
        gap = abs((date.fromisoformat(a["date"]) - date.fromisoformat(b["date"])).days)
    except (KeyError, ValueError):
        return False
    return gap <= 3


def _load() -> dict:
    """Bundled ledger plus any dashboard entries fetched since it shipped."""
    data = _load_bundled()
    cache = _load_cache()
    if not cache:
        return data
    merged = list(data.get("updates", []))
    for entry in cache["updates"]:
        match = next((u for u in merged if _same_update(u, entry)), None)
        if match is None:
            merged.append(entry)
        else:
            # The dashboard knows when a rollout finished; the ledger may not.
            for key in ("end", "duration"):
                if entry.get(key) and not match.get(key):
                    match[key] = entry[key]
    data["updates"] = merged
    data["live_refreshed"] = cache.get("fetched_at")
    return data


def _kind_for(incident: dict) -> str:
    name = str(incident.get("external_desc") or "").lower()
    if "core update" in name and "spam" in name:
        return "core+spam"
    if "core update" in name:
        return "core"
    if "spam" in name:
        return "spam"
    if "discover" in name:
        return "discover"
    if "review" in name or "helpful content" in name:
        return "core"
    if incident.get("service_key") == RANKING_PRODUCT:
        return "ranking"
    return "incident"


def incident_to_entry(incident: dict) -> dict | None:
    """Map one Search Status Dashboard incident to a ledger entry."""
    begin = incident.get("begin")
    name = str(incident.get("external_desc") or "").strip()
    if not begin or not name:
        return None
    try:
        start = datetime.fromisoformat(str(begin).replace("Z", "+00:00"))
    except ValueError:
        return None
    end_raw = incident.get("end")
    end = None
    duration = None
    if end_raw:
        try:
            end_dt = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
            end = end_dt.date().isoformat()
            hours = int((end_dt - start).total_seconds() // 3600)
            duration = f"{hours // 24} d {hours % 24} h"
        except ValueError:
            end = None
    uri = str(incident.get("uri") or "")
    if not re.fullmatch(r"incidents/[A-Za-z0-9_-]+", uri):
        uri = f"products/{RANKING_PRODUCT}/history"
    note = ""
    recent = incident.get("most_recent_update")
    if isinstance(recent, dict) and recent.get("text"):
        note = str(recent["text"]).strip().splitlines()[0][:200]
    return {
        "date": start.date().isoformat(),
        "end": end,
        "duration": duration,
        "name": name,
        "kind": _kind_for(incident),
        "service": incident.get("service_name"),
        "source": STATUS_BASE + uri,
        "notes": note or "From Google's Search Status Dashboard.",
    }


def refresh(timeout: float = 15.0) -> dict:
    """Fetch Google's incident feed and write the local cache."""
    try:
        import requests  # type: ignore
        resp = requests.get(STATUS_FEED, timeout=timeout,
                            headers={"User-Agent": "claude-seo-updates/1.0"})
        resp.raise_for_status()
        incidents = resp.json()
    except ImportError:
        import urllib.request
        req = urllib.request.Request(STATUS_FEED, headers={"User-Agent": "claude-seo-updates/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as fh:  # noqa: S310 - fixed Google host
            incidents = json.load(fh)
    if not isinstance(incidents, list):
        raise ValueError("Unexpected response from the Search Status Dashboard")
    entries = [e for e in (incident_to_entry(i) for i in incidents if isinstance(i, dict)) if e]
    payload = {
        "source": STATUS_FEED,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updates": sorted(entries, key=lambda e: e["date"]),
    }
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return {"refreshed": True, "count": len(entries), "cache": str(p), "fetched_at": payload["fetched_at"]}


def cache_age_days() -> float | None:
    p = cache_path()
    if not p.is_file():
        return None
    return (time.time() - p.stat().st_mtime) / 86400


def refresh_if_stale(max_age_days: float) -> dict:
    age = cache_age_days()
    if age is not None and age < max_age_days:
        return {"refreshed": False, "reason": f"cache is {age:.1f} days old"}
    try:
        return refresh()
    except Exception as exc:  # noqa: BLE001 - offline audits fall back to the bundled ledger
        return {"refreshed": False, "reason": f"could not reach the dashboard: {exc}"}


def updates_between(start: str, end: str) -> list[dict]:
    """Updates that were rolling out at any point between two ISO dates."""
    lo, hi = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    for u in _load().get("updates", []):
        try:
            began = date.fromisoformat(u["date"])
            finished = date.fromisoformat(u["end"]) if u.get("end") else began
        except (KeyError, ValueError):
            continue
        if began <= hi and finished >= lo and u.get("kind") in {
                "core", "spam", "core+spam", "discover", "ranking", "incident"}:
            out.append({k: u.get(k) for k in ("date", "end", "name", "kind", "source")})
    out.sort(key=lambda u: u["date"])
    return out


def _filter(
    updates: Iterable[dict],
    *,
    since: str | None = None,
    kinds: set[str] | None = None,
) -> list[dict]:
    out = list(updates)
    if since:
        # Accept yyyy or yyyy-mm
        if len(since) == 4:
            since_date = date(int(since), 1, 1)
        elif len(since) == 7:
            y, m = since.split("-")
            since_date = date(int(y), int(m), 1)
        else:
            since_date = date.fromisoformat(since)
        out = [u for u in out if date.fromisoformat(u["date"]) >= since_date]
    if kinds:
        out = [u for u in out if u.get("kind") in kinds]
    out.sort(key=lambda u: u["date"], reverse=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query the primary-source Google updates changelog."
    )
    parser.add_argument(
        "--since",
        help="ISO date (yyyy, yyyy-mm, or yyyy-mm-dd) to filter from.",
    )
    parser.add_argument(
        "--kind",
        action="append",
        choices=("core", "spam", "core+spam", "policy", "qrg", "product", "schema", "cwv",
                 "discover", "ranking", "incident"),
        help="Filter to one or more kinds (repeatable).",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Maximum number of entries to show (default 10).",
    )
    parser.add_argument(
        "--unverified",
        action="store_true",
        help="Show only the unverified third-party claims awaiting "
             "confirmation against status.search.google.com.",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Fetch Google's Search Status Dashboard feed and update the local cache.",
    )
    parser.add_argument(
        "--refresh-if-stale", type=float, metavar="DAYS",
        help="Refresh only when the cache is missing or older than DAYS.",
    )
    parser.add_argument(
        "--between", nargs=2, metavar=("START", "END"),
        help="Updates rolling out at any point between two ISO dates.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    refresh_result = None
    if args.refresh:
        try:
            refresh_result = refresh()
        except Exception as exc:  # noqa: BLE001
            refresh_result = {"refreshed": False, "reason": str(exc)}
    elif args.refresh_if_stale is not None:
        refresh_result = refresh_if_stale(args.refresh_if_stale)

    if args.between:
        found = updates_between(*args.between)
        payload = {"between": args.between, "count": len(found), "updates": found,
                   "refresh": refresh_result}
        if args.json:
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            for u in found:
                end = f" to {u['end']}" if u.get("end") else ""
                print(f"  {u['date']}{end}  [{u['kind']}]  {u['name']}")
        return 0

    if refresh_result is not None and not args.json and not (args.since or args.kind or args.unverified):
        print(json.dumps(refresh_result))
        if args.refresh:
            return 0 if refresh_result.get("refreshed") else 1

    data = _load()

    if args.unverified:
        result = {
            "last_verified": data.get("last_verified"),
            "source_of_truth": data.get("source_of_truth"),
            "unverified": data.get("unverified", []),
        }
        if args.json:
            json.dump(result, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(f"Source of truth: {result['source_of_truth']}")
            print(f"Last verified:   {result['last_verified']}")
            print()
            if not result["unverified"]:
                print("(no unverified third-party claims tracked)")
            for entry in result["unverified"]:
                print(f"  {entry['date']}: {entry['claim']}")
                print(f"    status: {entry['status']}")
                for s in entry.get("third_party_sources", []):
                    print(f"    third-party: {s}")
                print(f"    primary-check: {entry['primary_source_check']}")
                print()
        return 0

    kinds = set(args.kind) if args.kind else None
    filtered = _filter(data["updates"], since=args.since, kinds=kinds)[: args.limit]

    if args.json:
        json.dump(
            {
                "source_of_truth": data["source_of_truth"],
                "last_verified": data["last_verified"],
                "live_refreshed": data.get("live_refreshed"),
                "refresh": refresh_result,
                "filter": {"since": args.since, "kinds": list(kinds or [])},
                "count": len(filtered),
                "updates": filtered,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    print(f"Source of truth: {data['source_of_truth']}")
    print(f"Last verified:   {data['last_verified']}")
    if data.get("live_refreshed"):
        print(f"Live refresh:    {data['live_refreshed']}")
    print(f"Showing {len(filtered)} updates"
          f"{' since ' + args.since if args.since else ''}"
          f"{' kind=' + '/'.join(kinds) if kinds else ''}")
    print()
    for u in filtered:
        print(f"  {u['date']}  [{u['kind']:<10}]  {u['name']}")
        print(f"      source: {u['source']}")
        print(f"      notes:  {u.get('notes', '')}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
