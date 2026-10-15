#!/usr/bin/env python3
"""
Common Crawl Web Graph parser for Claude SEO.

Downloads and parses Common Crawl's domain-level web graph ranking data to
extract PageRank, harmonic centrality, crawl/ranking presence, and host counts.
No API key needed (public data).

The ranks file (~2.4 GB compressed, ~120M domains) is sorted by rank, so a
small domain can sit near the end. A lookup streams the whole file unless the
domain turns up first. If the scan stops early (time limit, network failure)
the result is "inconclusive", never "not found", and it is not cached.

Data source: s3://commoncrawl/projects/hyperlinkgraph/
Releases: Quarterly (cc-main-YYYY-mon-mon-mon)

Usage:
    python commoncrawl_graph.py example.com --json
    python commoncrawl_graph.py example.com --update --json
    python commoncrawl_graph.py --info --json
    python commoncrawl_graph.py example.com --release cc-main-2026-jan-feb-mar --json
"""

import argparse
import json
import os
import sys
import time
import zlib
from typing import Optional

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip install requests")
    sys.exit(1)

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPTS_DIR)
try:
    from backlinks_auth import get_cache_dir, load_config
    from google_auth import validate_url
except ImportError:
    print("Error: backlinks_auth.py and google_auth.py required in scripts/", file=sys.stderr)
    sys.exit(1)

# Common Crawl web graph base URL (HTTP access to S3 bucket)
CC_GRAPH_BASE = "https://data.commoncrawl.org/projects/hyperlinkgraph"

# Known recent releases (newest first).
# Update periodically: check https://commoncrawl.github.io/cc-webgraph-statistics/
# Naming convention: cc-main-YYYY-mon-mon-mon (quarterly, lowercase month abbreviations)
KNOWN_RELEASES = [
    "cc-main-2026-jan-feb-mar",
    "cc-main-2025-oct-nov-dec",
    "cc-main-2025-jul-aug-sep",
    "cc-main-2025-apr-may-jun",
    "cc-main-2025-jan-feb-mar",
    "cc-main-2024-oct-nov-dec",
]

# Graph file types — filenames include the release name as prefix
# e.g., domain/cc-main-2026-jan-feb-mar-domain-vertices.txt.gz
VERTICES_SUFFIX = "-domain-vertices.txt.gz"
EDGES_SUFFIX = "-domain-edges.txt.gz"
RANKINGS_SUFFIX = "-domain-ranks.txt.gz"

# Default time budget for one lookup. A full ranks scan takes 1-3 minutes on a
# typical connection; this plus one stalled read stays under the 10-minute
# Bash tool ceiling.
DEFAULT_SCAN_SECONDS = 480
# Seconds without data before a read counts as stalled.
READ_TIMEOUT = 60
# Compressed bytes handed to the decompressor per read.
SCAN_CHUNK_BYTES = 1024 * 1024
# Reconnects in a row (resuming with an HTTP Range request) that bring no
# new data before a download is abandoned. Reconnects that make progress
# don't count; the overall time budget still applies.
MAX_RESUMES = 3
RESUME_BACKOFF_SECONDS = 2

# Version 1 entries came from a scan capped at 500 MiB, so their negatives
# are unreliable. Only entries that found the domain or were written by a
# complete scan are trusted (see _is_definitive).
CACHE_SCHEMA = 2


def _graph_file_url(release: str, suffix: str) -> str:
    """Build the full URL for a CC web graph file."""
    return f"{CC_GRAPH_BASE}/{release}/domain/{release}{suffix}"


def _safe_float(s: str) -> Optional[float]:
    """Safely parse a float from a string, returning None on failure."""
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _get_latest_release() -> Optional[str]:
    """
    Discover the latest available CC web graph release.

    Tries known releases in order, returns the first one that exists.

    Returns:
        Release name (e.g., 'cc-main-2026-jan-feb-mar') or None.
    """
    for release in KNOWN_RELEASES:
        url = _graph_file_url(release, VERTICES_SUFFIX)
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                return release
        except requests.exceptions.RequestException:
            continue
    return None


def _get_cache_path(domain: str, release: str, data_type: str) -> str:
    """Get the cache file path for a domain's data."""
    cache_dir = get_cache_dir()
    safe_domain = domain.replace("/", "_").replace(":", "_")
    return os.path.join(cache_dir, f"{safe_domain}-{release}-{data_type}.json")


def _is_definitive(result: dict) -> bool:
    """
    True if a lookup result is safe to cache and reuse.

    A result is definitive when it found the domain, or when it says the
    domain is absent after scanning the whole ranks file. A miss from a
    partial scan proves nothing, so it must never be cached.
    """
    data = result.get("data")
    if result.get("status") != "success" or not isinstance(data, dict):
        return False
    return data.get("in_rankings") is True or data.get("scan_complete") is True


def _is_cached(domain: str, release: str) -> Optional[dict]:
    """Check if domain data is cached for a given release."""
    cache_path = _get_cache_path(domain, release, "combined")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            # Check cache age (90 days max)
            cached_time = cached.get("metadata", {}).get("cached_at", 0)
            if time.time() - cached_time < 90 * 86400 and _is_definitive(cached):
                return cached
        except (json.JSONDecodeError, IOError, AttributeError):
            pass
    return None


def _save_cache(domain: str, release: str, data: dict) -> None:
    """Save domain data to cache. Non-definitive results are not saved."""
    if not _is_definitive(data):
        return
    cache_path = _get_cache_path(domain, release, "combined")
    metadata = data.setdefault("metadata", {})
    metadata["cached_at"] = time.time()
    metadata["cache_schema"] = CACHE_SCHEMA
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)


def _row_containing(block: bytes, needle: bytes, end: int) -> Optional[list]:
    """Return the tab-split row in block[:end] that contains needle, if any."""
    i = block.find(needle, 0, end)
    if i == -1:
        return None
    start = block.rfind(b"\n", 0, i) + 1
    stop = block.find(b"\n", i, end)
    if stop == -1:
        stop = end
    return block[start:stop].decode("utf-8", errors="replace").split("\t")


def _scan_gz_for_field(url: str, value: str, deadline: float,
                       read_timeout: int = READ_TIMEOUT,
                       max_resumes: int = MAX_RESUMES) -> dict:
    """
    Stream a gzipped TSV file and find the first row with ``value`` as a
    whole tab-delimited field (not the first or last column).

    Reads to the end of the file unless the row turns up first, so a miss is
    only meaningful when the returned ``complete`` is True. A dropped
    connection is resumed with an HTTP Range request from the last byte
    received. Running past ``deadline`` (a time.monotonic() value), more than
    ``max_resumes`` reconnects in a row without new data, or corrupt data
    stops the scan with ``complete=False`` and a ``reason``.

    Raises:
        requests.exceptions.HTTPError: if the file cannot be fetched at all
            (e.g. 404 for an unknown release).

    Returns:
        dict with fields (list or None), complete, reason, rows_scanned,
        bytes_scanned and bytes_total (None if the server gave no length).
    """
    needle = f"\t{value}\t".encode("utf-8")
    decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
    pending = b""  # incomplete last line, carried into the next block
    rows = 0
    offset = 0  # compressed bytes received so far
    total = None
    failures = 0  # reconnects in a row that brought no new data

    def outcome(fields=None, complete=False, reason=None):
        return {
            "fields": fields,
            "complete": complete,
            "reason": reason,
            "rows_scanned": rows,
            "bytes_scanned": offset,
            "bytes_total": total,
        }

    while True:
        if time.monotonic() > deadline:
            return outcome(reason="time limit reached")
        request_start = offset
        headers = {"Range": f"bytes={offset}-"} if offset else None
        try:
            resp = requests.get(url, stream=True, timeout=read_timeout, headers=headers)
            try:
                if offset and resp.status_code != 206:
                    return outcome(reason=f"server ignored the resume request (HTTP {resp.status_code})")
                resp.raise_for_status()
                if total is None:
                    length = resp.headers.get("Content-Length", "")
                    total = int(length) if length.isdigit() else None

                for chunk in resp.iter_content(chunk_size=SCAN_CHUNK_BYTES):
                    if time.monotonic() > deadline:
                        return outcome(reason="time limit reached")
                    offset += len(chunk)
                    data = decompressor.decompress(chunk)
                    # Concatenated gzip members: carry on with a fresh decompressor.
                    while decompressor.eof and decompressor.unused_data:
                        rest = decompressor.unused_data
                        decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
                        data += decompressor.decompress(rest)

                    block = pending + data
                    end = block.rfind(b"\n") + 1
                    pending = block[end:]
                    fields = _row_containing(block, needle, end)
                    if fields is not None:
                        return outcome(fields=fields, complete=True)
                    rows += block.count(b"\n", 0, end)
            finally:
                resp.close()
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.Timeout) as e:
            failures = 1 if offset > request_start else failures + 1
            if failures > max_resumes:
                return outcome(reason=f"download failed at byte {offset}: {e}")
            time.sleep(RESUME_BACKOFF_SECONDS * failures)
            continue
        except zlib.error as e:
            return outcome(reason=f"corrupt gzip data: {e}")

        if total is not None and offset < total:
            # Body ended early without an error; resume from where it stopped.
            failures = 1 if offset > request_start else failures + 1
            if failures > max_resumes:
                return outcome(reason=f"download ended at byte {offset} of {total}")
            continue
        break

    if not decompressor.eof:
        return outcome(reason="file ended before the end of the gzip stream")
    if pending:
        rows += 1
        fields = _row_containing(pending, needle, len(pending))
        if fields is not None:
            return outcome(fields=fields, complete=True)
    return outcome(complete=True)


def get_domain_metrics(domain: str, release: Optional[str] = None,
                       force_update: bool = False, timeout: int = DEFAULT_SCAN_SECONDS,
                       top_referrers: int = 20) -> dict:
    """
    Get domain-level ranking metrics from Common Crawl web graph.

    Args:
        domain: Target domain (e.g., 'example.com').
        release: CC release name. Auto-detects latest if None.
        force_update: Force re-download, bypassing cache.
        timeout: Time budget in seconds for scanning the ranks file. A scan
            that runs out of time returns status "inconclusive".
        top_referrers: Legacy no-op; referring domains are not extracted.

    Returns:
        Standard response dict with domain metrics. status is "success" when
        the answer is definitive (found, or absent after a full scan) and
        "inconclusive" when the scan stopped before the end of the file.
    """
    # Clean domain
    domain = domain.lower().strip()
    if domain.lower().startswith("http"):
        if not validate_url(domain):
            return {
                "status": "error",
                "data": None,
                "error": f"Invalid or blocked URL: {domain}",
                "metadata": {"source": "commoncrawl"},
            }
        from urllib.parse import urlparse
        domain = urlparse(domain).netloc
    domain = domain.replace("www.", "")

    # Find release
    if not release:
        release = _get_latest_release()
        if not release:
            return {
                "status": "error",
                "data": None,
                "error": "Could not find any Common Crawl web graph release. Check connectivity.",
                "metadata": {"source": "commoncrawl"},
            }

    # Check cache
    if not force_update:
        cached = _is_cached(domain, release)
        if cached:
            data = cached.get("data")
            if isinstance(data, dict):
                data.pop("top_referring_domains", None)
                data.pop("referring_domains_sample", None)
            cached["metadata"]["from_cache"] = True
            return cached

    metadata = {
        "source": "commoncrawl",
        "release": release,
        "from_cache": False,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Fetch rankings file (has PageRank + harmonic centrality + reversed domain names)
    # Format: harmonicc_pos \t harmonicc_val \t pr_pos \t pr_val \t host_rev \t n_hosts
    # Rows are sorted by harmonic centrality rank, so small domains sit near
    # the end. The file lists every domain in the graph (same set as the
    # vertices file), so a complete scan is a definitive answer on its own.
    # Domains are reversed: com.google = google.com
    rankings_url = _graph_file_url(release, RANKINGS_SUFFIX)
    reversed_domain = ".".join(reversed(domain.split(".")))

    try:
        scan = _scan_gz_for_field(rankings_url, reversed_domain,
                                  deadline=time.monotonic() + timeout)
    except requests.exceptions.HTTPError as e:
        return {
            "status": "error",
            "data": None,
            "error": f"Could not fetch Common Crawl ranks file: {e}",
            "metadata": metadata,
        }
    except Exception as e:
        scan = {"fields": None, "complete": False, "reason": str(e),
                "rows_scanned": 0, "bytes_scanned": 0, "bytes_total": None}

    rankings_data = {}
    fields = scan["fields"]
    # fields[4] is the reversed hostname (e.g., com.google)
    if fields and len(fields) >= 6 and fields[4] == reversed_domain:
        rankings_data = {
            "harmonic_centrality_rank": int(fields[0]) if fields[0].isdigit() else None,
            "harmonic_centrality": _safe_float(fields[1]),
            "pagerank_rank": int(fields[2]) if fields[2].isdigit() else None,
            "pagerank": _safe_float(fields[3]),
            "n_hosts": int(fields[5]) if fields[5].isdigit() else None,
        }
    in_rankings = bool(rankings_data)

    if not in_rankings and not scan["complete"]:
        # The domain may sit in the part of the file that was never read.
        scanned, total = scan["bytes_scanned"], scan["bytes_total"]
        progress = f"{scanned / total:.0%} of" if total else f"{scanned:,} bytes of"
        return {
            "status": "inconclusive",
            "data": {
                "domain": domain,
                "in_crawl": None,
                "in_rankings": None,
                "pagerank": None,
                "pagerank_rank": None,
                "harmonic_centrality": None,
                "harmonic_centrality_rank": None,
                "n_hosts": None,
                "scan_complete": False,
                "rows_scanned": scan["rows_scanned"],
                "bytes_scanned": scanned,
                "bytes_total": total,
                "note": (
                    f"Scan stopped after {progress} the ranks file ({scan['reason']}). "
                    f"The domain is not among the top {scan['rows_scanned']:,} domains "
                    "by harmonic centrality, but it may still be in Common Crawl. "
                    "Do not report it as missing; re-run with a longer --timeout."
                ),
            },
            "error": f"Common Crawl scan incomplete: {scan['reason']}",
            "metadata": metadata,
        }

    if in_rankings:
        note = "Domain-level metrics from CC web graph. Quarterly updates."
    else:
        note = (
            f"Domain not found in the Common Crawl domain graph for this release "
            f"(all {scan['rows_scanned']:,} rows of the ranks file scanned). "
            "It may be too new, too small, or not yet linked from crawled pages."
        )

    result = {
        "status": "success",
        "data": {
            "domain": domain,
            "in_crawl": in_rankings,
            "in_rankings": in_rankings,
            "pagerank": rankings_data.get("pagerank"),
            "pagerank_rank": rankings_data.get("pagerank_rank"),
            "harmonic_centrality": rankings_data.get("harmonic_centrality"),
            "harmonic_centrality_rank": rankings_data.get("harmonic_centrality_rank"),
            "n_hosts": rankings_data.get("n_hosts"),
            "scan_complete": True,
            "note": note,
        },
        "error": None,
        "metadata": metadata,
    }

    # Cache the result
    _save_cache(domain, release, result)

    return result


def get_graph_info() -> dict:
    """
    Get information about available CC web graph releases and cache status.

    Returns:
        Standard response dict with release info.
    """
    latest = _get_latest_release()
    cache_dir = get_cache_dir()
    cached_files = []
    if os.path.exists(cache_dir):
        cached_files = [f for f in os.listdir(cache_dir) if f.endswith(".json")]

    return {
        "status": "success",
        "data": {
            "latest_release": latest,
            "known_releases": KNOWN_RELEASES,
            "cache_dir": cache_dir,
            "cached_files": len(cached_files),
            "cached_domains": [f.split("-cc-main")[0] for f in cached_files],
        },
        "error": None,
        "metadata": {
            "source": "commoncrawl",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Common Crawl Web Graph parser for Claude SEO"
    )
    parser.add_argument(
        "domain",
        nargs="?",
        default=None,
        help="Target domain to look up (e.g., example.com)",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Show available releases and cache status",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Force re-download, bypassing cache",
    )
    parser.add_argument(
        "--release",
        default=None,
        help="Specific CC release to query (e.g., cc-main-2026-jan-feb-mar)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_SCAN_SECONDS,
        help=f"Time budget in seconds for scanning the ranks file (default: "
             f"{DEFAULT_SCAN_SECONDS}). Running out of time gives an "
             f"'inconclusive' result, never 'not found'",
    )
    parser.add_argument(
        "--top-referrers",
        type=int,
        default=20,
        help="Legacy no-op; referring domains are not extracted",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

    if args.info:
        result = get_graph_info()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            data = result["data"]
            print(f"Common Crawl Web Graph Info")
            print(f"  Latest release: {data.get('latest_release', 'unknown')}")
            print(f"  Known releases: {', '.join(data.get('known_releases', []))}")
            print(f"  Cache dir:      {data.get('cache_dir', 'N/A')}")
            print(f"  Cached domains: {data.get('cached_files', 0)}")
        return

    if not args.domain:
        print("Error: domain argument required (or use --info)", file=sys.stderr)
        sys.exit(1)

    result = get_domain_metrics(
        domain=args.domain,
        release=args.release,
        force_update=args.update,
        timeout=args.timeout,
        top_referrers=args.top_referrers,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["status"] == "success" and result["data"]:
            data = result["data"]
            cached = result.get("metadata", {}).get("from_cache", False)
            release = result.get("metadata", {}).get("release", "unknown")
            print(f"Common Crawl Domain Metrics: {data.get('domain', args.domain)}")
            print(f"  Release:                   {release} {'(cached)' if cached else ''}")
            print(f"  PageRank:                  {data.get('pagerank', 'N/A')} (rank #{data.get('pagerank_rank', 'N/A')})")
            print(f"  Harmonic Centrality:       {data.get('harmonic_centrality', 'N/A')} (rank #{data.get('harmonic_centrality_rank', 'N/A')})")
            print(f"  Number of hosts:           {data.get('n_hosts', 'N/A')}")
            print(f"  Note:                      {data.get('note', '')}")
        elif result["status"] == "inconclusive" and result["data"]:
            print(f"Common Crawl lookup inconclusive: {result['data'].get('note', '')}")
        elif result.get("error"):
            print(f"Error: {result['error']}", file=sys.stderr)


if __name__ == "__main__":
    main()
