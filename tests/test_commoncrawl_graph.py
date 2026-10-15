"""Common Crawl ranks lookups must never turn a partial scan into "not found"."""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
from unittest.mock import patch

import pytest
import requests


_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import commoncrawl_graph as cc  # noqa: E402
import validate_backlink_report  # noqa: E402


RELEASE = "cc-main-2026-jan-feb-mar"
HEADER = "#harmonicc_pos\t#harmonicc_val\t#pr_pos\t#pr_val\t#host_rev\t#n_hosts\n"
TARGET_ROW = "49859019\t1.0787539E7\t13378003\t5.582607755092737E-9\tuk.co.staystainless\t2\n"


def _ranks_text(filler_rows: int = 5000, with_target: bool = True) -> str:
    """A ranks file shaped like the real one, with the target near the end."""
    rows = [HEADER]
    for i in range(1, filler_rows + 1):
        rows.append(f"{i}\t1.0E7\t{i}\t1.0E-8\tcom.filler{i:06d}\t1\n")
    if with_target:
        rows.append(TARGET_ROW)
    rows.append(f"{filler_rows + 2}\t9.0E6\t{filler_rows + 2}\t4.0E-9\tuk.co.zzz-last\t1\n")
    return "".join(rows)


def _gzip(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"), mtime=0)


class FakeServer:
    """Serves one payload over fake streaming responses, honouring Range."""

    def __init__(self, payload: bytes, drop_after: list | None = None,
                 truncate_after: list | None = None, honour_range: bool = True) -> None:
        self.payload = payload
        # Per request: raise a connection error after this many bytes (None = never).
        self.drop_after = list(drop_after or [])
        # Per request: end the body cleanly after this many bytes (None = never).
        self.truncate_after = list(truncate_after or [])
        self.honour_range = honour_range
        self.requests: list[dict] = []

    def get(self, url, stream=False, timeout=None, headers=None):
        start = 0
        if headers and "Range" in headers and self.honour_range:
            start = int(headers["Range"].split("=")[1].rstrip("-"))
        self.requests.append({"url": url, "start": start, "headers": headers})
        drop = self.drop_after.pop(0) if self.drop_after else None
        truncate = self.truncate_after.pop(0) if self.truncate_after else None
        return FakeResponse(self.payload, start, drop, truncate)


class FakeResponse:
    def __init__(self, payload: bytes, start: int, drop: int | None, truncate: int | None) -> None:
        self.payload = payload
        self.start = start
        self.drop = drop
        self.truncate = truncate
        self.status_code = 206 if start else 200
        self.headers = {"Content-Length": str(len(payload) - start)}

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int):
        pos, sent = self.start, 0
        while pos < len(self.payload):
            if self.drop is not None and sent >= self.drop:
                raise requests.exceptions.ChunkedEncodingError("connection reset")
            if self.truncate is not None and sent >= self.truncate:
                return
            chunk = self.payload[pos:pos + chunk_size]
            pos += len(chunk)
            sent += len(chunk)
            yield chunk

    def close(self) -> None:
        pass


class TickingClock:
    """time.monotonic() stand-in that advances one second per call."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "get_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cc, "SCAN_CHUNK_BYTES", 1024)
    monkeypatch.setattr(cc, "RESUME_BACKOFF_SECONDS", 0)
    return tmp_path


def _lookup(server: FakeServer, **kwargs) -> dict:
    with patch.object(cc.requests, "get", side_effect=server.get):
        return cc.get_domain_metrics("staystainless.co.uk", release=RELEASE, **kwargs)


def _cache_file(tmp_path) -> str:
    return os.path.join(str(tmp_path), f"staystainless.co.uk-{RELEASE}-combined.json")


def test_full_scan_finds_domain_near_end_of_ranks_file(isolated) -> None:
    payload = _gzip(_ranks_text())
    server = FakeServer(payload)

    result = _lookup(server)

    assert len(payload) > 10 * cc.SCAN_CHUNK_BYTES  # target is many reads in
    assert result["status"] == "success"
    data = result["data"]
    assert data["in_rankings"] is True and data["in_crawl"] is True
    assert data["harmonic_centrality_rank"] == 49859019
    assert data["pagerank_rank"] == 13378003
    assert data["n_hosts"] == 2
    assert os.path.exists(_cache_file(isolated))


def test_partial_scan_is_inconclusive_and_not_cached(isolated) -> None:
    # The time budget runs out a few reads into the file, well before the target row.
    server = FakeServer(_gzip(_ranks_text()))

    with patch.object(cc.time, "monotonic", TickingClock()):
        result = _lookup(server, timeout=5)

    assert result["status"] == "inconclusive"
    data = result["data"]
    assert data["in_crawl"] is None and data["in_rankings"] is None
    assert data["scan_complete"] is False
    assert 0 < data["bytes_scanned"] < data["bytes_total"]
    assert "time limit" in result["error"]
    assert "not found" not in data["note"].lower()
    assert not os.path.exists(_cache_file(isolated))


def test_absent_domain_after_full_scan_is_definitive_and_cached(isolated) -> None:
    server = FakeServer(_gzip(_ranks_text(with_target=False)))

    result = _lookup(server)

    assert result["status"] == "success"
    assert result["data"]["in_crawl"] is False
    assert result["data"]["in_rankings"] is False
    assert result["data"]["scan_complete"] is True
    with open(_cache_file(isolated)) as f:
        cached = json.load(f)
    assert cached["metadata"]["cache_schema"] == cc.CACHE_SCHEMA


def test_dropped_connection_resumes_with_range_request(isolated) -> None:
    payload = _gzip(_ranks_text())
    server = FakeServer(payload, drop_after=[len(payload) // 3])

    result = _lookup(server)

    assert result["status"] == "success"
    assert result["data"]["in_rankings"] is True
    assert len(server.requests) == 2
    resumed = server.requests[1]
    assert resumed["start"] > 0
    assert resumed["headers"] == {"Range": f"bytes={resumed['start']}-"}


def test_body_that_ends_early_is_resumed(isolated) -> None:
    payload = _gzip(_ranks_text())
    server = FakeServer(payload, truncate_after=[len(payload) // 2])

    result = _lookup(server)

    assert result["status"] == "success"
    assert result["data"]["in_rankings"] is True
    assert server.requests[1]["start"] >= len(payload) // 2


def test_flaky_connection_that_keeps_making_progress_completes(isolated) -> None:
    payload = _gzip(_ranks_text())
    # More drops than MAX_RESUMES, but every reconnect brings new data.
    server = FakeServer(payload, drop_after=[len(payload) // 8] * (cc.MAX_RESUMES + 3))

    result = _lookup(server)

    assert result["status"] == "success"
    assert result["data"]["in_rankings"] is True
    assert len(server.requests) > cc.MAX_RESUMES + 1


def test_reconnects_without_progress_give_up_as_inconclusive(isolated) -> None:
    payload = _gzip(_ranks_text())
    # First request gets a quarter of the file, then every reconnect fails at once.
    server = FakeServer(payload, drop_after=[len(payload) // 4] + [0] * 10)

    result = _lookup(server)

    assert result["status"] == "inconclusive"
    assert len(server.requests) == cc.MAX_RESUMES + 1
    assert result["data"]["bytes_scanned"] >= len(payload) // 4
    assert not os.path.exists(_cache_file(isolated))


def test_server_ignoring_range_is_inconclusive(isolated) -> None:
    payload = _gzip(_ranks_text())
    server = FakeServer(payload, drop_after=[len(payload) // 4], honour_range=False)

    result = _lookup(server)

    assert result["status"] == "inconclusive"
    assert "ignored the resume request" in result["error"]


def test_concatenated_gzip_members_are_all_read(isolated) -> None:
    text = _ranks_text()
    cut = text.index("com.filler004000")
    cut = text.rindex("\n", 0, cut) + 1
    payload = _gzip(text[:cut]) + _gzip(text[cut:])
    server = FakeServer(payload)

    result = _lookup(server)

    assert result["status"] == "success"
    assert result["data"]["harmonic_centrality_rank"] == 49859019


def test_only_whole_field_matches_count(isolated) -> None:
    text = HEADER + (
        "1\t1.0E7\t1\t1.0E-8\tuk.co.staystainless-shop\t1\n"
        "2\t1.0E7\t2\t1.0E-8\tuk.co.notstaystainless\t1\n"
        "3\t1.0E7\t3\t1.0E-8\tuk.co.staystainless.extra\t1\n"
    )
    server = FakeServer(_gzip(text))

    result = _lookup(server)

    assert result["status"] == "success"
    assert result["data"]["in_rankings"] is False


def test_last_row_without_trailing_newline_is_checked(isolated) -> None:
    server = FakeServer(_gzip(HEADER + TARGET_ROW.rstrip("\n")))

    result = _lookup(server)

    assert result["data"]["in_rankings"] is True


def test_missing_release_is_an_error_not_a_negative(isolated) -> None:
    class NotFound(FakeResponse):
        status_code = 404

        def raise_for_status(self) -> None:
            raise requests.exceptions.HTTPError("404 Client Error")

    with patch.object(cc.requests, "get", return_value=NotFound(b"", 0, None, None)):
        result = cc.get_domain_metrics("staystainless.co.uk", release="cc-main-1999-x")

    assert result["status"] == "error"
    assert not os.path.exists(os.path.join(str(isolated), "staystainless.co.uk-cc-main-1999-x-combined.json"))


def _write_legacy_entry(tmp_path, in_crawl: bool, in_rankings: bool) -> None:
    """A cache entry as written by the old 500 MiB-capped scanner."""
    entry = {
        "status": "success",
        "data": {
            "domain": "staystainless.co.uk",
            "in_crawl": in_crawl,
            "in_rankings": in_rankings,
            "pagerank": 1e-8 if in_rankings else None,
            "note": "legacy",
        },
        "error": None,
        "metadata": {"source": "commoncrawl", "release": RELEASE, "cached_at": time.time()},
    }
    with open(_cache_file(tmp_path), "w") as f:
        json.dump(entry, f)


@pytest.mark.parametrize("in_crawl", [False, True])
def test_legacy_negative_cache_entries_are_ignored(isolated, in_crawl) -> None:
    _write_legacy_entry(isolated, in_crawl=in_crawl, in_rankings=False)
    server = FakeServer(_gzip(_ranks_text()))

    result = _lookup(server)

    assert server.requests, "stale negative should trigger a fresh scan"
    assert result["metadata"]["from_cache"] is False
    assert result["data"]["in_rankings"] is True
    with open(_cache_file(isolated)) as f:
        assert json.load(f)["data"]["in_rankings"] is True


def test_legacy_positive_cache_entry_is_reused(isolated) -> None:
    _write_legacy_entry(isolated, in_crawl=True, in_rankings=True)
    server = FakeServer(b"")

    result = _lookup(server)

    assert server.requests == []
    assert result["metadata"]["from_cache"] is True


def test_save_cache_refuses_inconclusive_results(isolated) -> None:
    cc._save_cache("staystainless.co.uk", RELEASE, {
        "status": "inconclusive",
        "data": {"in_crawl": None, "in_rankings": None, "scan_complete": False},
    })
    cc._save_cache("staystainless.co.uk", RELEASE, {
        "status": "success",
        "data": {"in_crawl": False, "in_rankings": False},
    })

    assert not os.path.exists(_cache_file(isolated))


def test_validator_flags_inconclusive_cc_result() -> None:
    issues = validate_backlink_report.validate_cc_claims({
        "status": "inconclusive",
        "data": {"in_crawl": None, "in_rankings": None, "scan_complete": False},
    })

    assert len(issues) == 1
    assert "Do NOT say the domain is missing" in issues[0]["message"]
