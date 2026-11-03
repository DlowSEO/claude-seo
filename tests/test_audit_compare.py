"""Audit comparison: matching, archiving, PDF baselines and the report section."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import audit_compare  # noqa: E402


def _audit(day: str, score: int, findings: list[tuple], mode: str = "full") -> dict:
    cats: dict[str, list] = {}
    for cat, title, sev, *rest in findings:
        f = {"title": title, "severity": sev}
        if rest:
            f["id"] = rest[0]
        cats.setdefault(cat, []).append(f)
    return {
        "meta": {"audited_at": day, "mode": mode},
        "summary": {"health_score": score},
        "categories": [{"name": c, "score": score, "findings": fs} for c, fs in cats.items()],
    }


OLD = _audit("2026-08-04", 58, [
    ("Technical SEO", "Key service pages carry a noindex tag", "Critical", "noindex-key-pages"),
    ("Technical SEO", "Canonical tags missing on 18 pages", "Medium"),
    ("Content Quality", "No author information on blog posts", "Medium"),
    ("On-Page SEO", "Missing meta descriptions on 22 pages", "Medium"),
])
NEW = _audit("2026-11-03", 71, [
    ("Technical SEO", "Canonical tags missing on 9 pages", "Low"),
    ("Technical SEO", "Mobile LCP above 4 seconds", "High", "slow-lcp-mobile"),
    ("Content Quality", "Blog posts have no author information", "Medium"),
    ("On-Page SEO", "Duplicate meta descriptions on 22 pages", "Medium"),
])


def test_similarity_ignores_counts_and_word_order_but_not_meaning() -> None:
    sim = audit_compare._similarity
    assert sim("Canonical tags missing on 18 pages", "Canonical tags missing on 9 pages") >= 0.95
    assert sim("No author information on blog posts", "Blog posts have no author information") >= 0.8
    assert sim("Missing meta descriptions on 22 pages", "Duplicate meta descriptions on 22 pages") < 0.8
    assert sim("Missing meta descriptions", "Missing meta titles") < 0.8


def test_compare_classifies_resolved_new_and_persisting(monkeypatch) -> None:
    monkeypatch.setattr(audit_compare, "_google_updates_between", lambda a, b: [])
    c = audit_compare.compare(OLD, NEW)
    assert c["health_delta"] == 13
    assert {f["title"] for f in c["resolved"]} == {
        "Key service pages carry a noindex tag", "Missing meta descriptions on 22 pages"}
    assert {f["title"] for f in c["new"]} == {
        "Mobile LCP above 4 seconds", "Duplicate meta descriptions on 22 pages"}
    persisting = {f["title"]: f for f in c["persisting"]}
    assert persisting["Canonical tags missing on 9 pages"]["trend"] == "better"
    assert persisting["Blog posts have no author information"]["trend"] == "same"
    # Word-order matches are flagged for a human check.
    assert any(r["current"] == "Blog posts have no author information" for r in c["review_matches"])


def test_ids_match_even_when_titles_change(monkeypatch) -> None:
    monkeypatch.setattr(audit_compare, "_google_updates_between", lambda a, b: [])
    old = _audit("2026-08-01", 50, [("Technical SEO", "Noindex on key pages", "Critical", "noindex-key-pages")])
    new = _audit("2026-09-01", 60, [("Indexing", "Four service pages excluded by robots meta", "High", "noindex-key-pages")])
    c = audit_compare.compare(old, new)
    assert c["counts"] == {"resolved": 0, "new": 0, "persisting": 1, "worse": 0, "better": 1}


def test_mode_mismatch_is_noted(monkeypatch) -> None:
    monkeypatch.setattr(audit_compare, "_google_updates_between", lambda a, b: [])
    c = audit_compare.compare(_audit("2026-08-01", 50, [], "snapshot"), _audit("2026-09-01", 60, [], "full"))
    assert any("not directly comparable" in n for n in c["notes"])


def test_find_and_archive_keep_history(tmp_path: Path) -> None:
    d = tmp_path / "example.com-audit"
    d.mkdir()
    (d / "audit-data.json").write_text(json.dumps(OLD))
    (d / "ACTION-PLAN.md").write_text("plan")
    found = audit_compare.find_previous(d, include_ids=True)
    assert found["count"] == 1 and found["previous"][0]["date"] == "2026-08-04"
    assert found["previous_findings"][0]["id"] == "noindex-key-pages"

    res = audit_compare.archive(d)
    assert res["archived"] and (d / "history" / "2026-08-04" / "ACTION-PLAN.md").is_file()
    # The archived copy and the live copy are the same audit, listed once.
    assert audit_compare.find_previous(d)["count"] == 1
    assert (d / "audit-data.json").is_file(), "archive copies, it never removes the live audit"


def test_archive_without_audit_is_a_no_op(tmp_path: Path) -> None:
    assert audit_compare.archive(tmp_path)["archived"] is False


def test_parse_report_text_rebuilds_scores_and_findings() -> None:
    text = "\n".join([
        "FULL SEO AUDIT REPORT", "example.com", "58/100", "SEO Health Score", "4 August 2026 Full Audit",
        "2. Audit Categories", "2.1 Technical SEO", "Score: 52/100", "Findings",
        "Key service pages carry a noindex tag Critical", "Canonical tags missing on 18 pages Medium",
        "2.2 Content Quality", "Score: 61/100", "Thin location pages", "High",
        "3. Action Plan", "Something Low",
    ])
    data = audit_compare.parse_report_text(text)
    assert data["summary"]["health_score"] == 58
    assert data["meta"]["audited_at"] == "2026-08-04"
    flat = audit_compare.flatten_findings(data)
    assert [(f["category"], f["title"], f["severity"]) for f in flat] == [
        ("Technical SEO", "Key service pages carry a noindex tag", "Critical"),
        ("Technical SEO", "Canonical tags missing on 18 pages", "Medium"),
        ("Content Quality", "Thin location pages", "High"),
    ]


def test_markdown_summary_has_no_em_dashes(monkeypatch) -> None:
    monkeypatch.setattr(audit_compare, "_google_updates_between", lambda a, b: [
        {"date": "2026-09-24", "end": "2026-10-08", "name": "September 2026 Spam Update"}])
    md = audit_compare.to_markdown(audit_compare.compare(OLD, NEW), "example.com")
    assert "—" not in md and "–" not in md
    assert "September 2026 Spam Update: 24 September 2026 to 8 October 2026" in md


def test_report_renders_comparison_and_embeds_data(tmp_path: Path, monkeypatch) -> None:
    google_report = pytest.importorskip("google_report")
    if google_report.HTML is None:
        pytest.skip("weasyprint not installed")
    pypdf = pytest.importorskip("pypdf")
    monkeypatch.setattr(audit_compare, "_google_updates_between", lambda a, b: [])
    data = dict(NEW)
    data["comparison"] = audit_compare.compare(OLD, NEW)
    res = google_report.generate_report("full", data, "example.com", tmp_path, output_format="both")
    assert res["error"] is None
    html = next(Path(f) for f in res["files"] if f.endswith(".html")).read_text()
    assert "What Has Changed Since the Last Audit" in html
    pdf = next(Path(f) for f in res["files"] if f.endswith(".pdf"))
    baseline = audit_compare.from_pdf(pdf)
    assert baseline["method"] == "embedded"
    assert "comparison" not in baseline["data"]
    assert baseline["data"]["summary"]["health_score"] == 71
