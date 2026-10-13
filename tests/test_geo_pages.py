"""
Tests for scripts/geo_pages.py (site-wide GEO extraction and export).

Network-free: page analysis runs on inline HTML via ``analyse_html`` and the
export step runs on a temporary pages.json + scores directory.
"""

from __future__ import annotations

import csv
import json
import os
import sys

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import geo_pages  # noqa: E402


ANSWER = (
    "Generative engine optimisation is the practice of structuring pages so AI search "
    "systems can quote them accurately. According to a 2025 study, 44% of citations "
    "come from the first third of a page. "
)
LONG_SECTION = " ".join(["Detailed supporting sentence about the topic."] * 30)

HTML = f"""<!doctype html><html><head>
<title>What is GEO? | Example</title>
<meta name="description" content="A guide to GEO.">
<meta name="author" content="Jane Doe">
<meta property="article:published_time" content="2026-09-01">
<script type="application/ld+json">{{"@context":"https://schema.org","@graph":[{{"@type":"Article"}},{{"@type":"BreadcrumbList"}}]}}</script>
</head><body>
<nav><p>Home About Contact navigation links that should never be a passage</p></nav>
<main>
<h1>Generative engine optimisation</h1>
<h2>What is generative engine optimisation?</h2>
<p>{ANSWER}</p>
<h2>How to get started today</h2>
<ul><li>Audit your top pages</li><li>Rewrite the opening answer</li><li>Add dates and authors</li></ul>
<h4>Skipped level heading</h4>
<p>{LONG_SECTION}</p>
<p>{LONG_SECTION}</p>
<table><tr><th>Platform</th><th>Share</th></tr><tr><td>ChatGPT</td><td>40%</td></tr></table>
<p><a href="https://research.example.org/paper">Source paper</a> short.</p>
</main>
<footer><p>Footer text that should be ignored by passage extraction entirely.</p></footer>
</body></html>"""


def test_analyse_html_extracts_structure() -> None:
    page = geo_pages.analyse_html("https://example.com/blog/what-is-geo/", HTML)
    assert page["page_type"] == "article"
    assert page["schema_types"] == ["Article", "BreadcrumbList"]
    assert page["schema_matches_type"] is True
    assert page["has_author"] is True
    assert page["dates"]["published"] == "2026-09-01"
    assert page["heading_skips"] is True
    assert page["external_citations"] == 1
    assert page["table_count"] == 1
    assert not page["is_spa"]
    texts = " ".join(p["text"] for p in page["passages_all"])
    assert "navigation links" not in texts
    assert "Footer text" not in texts


def test_passage_signals_and_split() -> None:
    page = geo_pages.analyse_html("https://example.com/blog/what-is-geo/", HTML)
    first = page["passages_all"][0]
    assert first["heading"] == "What is generative engine optimisation?"
    assert {"definition", "direct_answer", "statistic", "attribution"} <= set(first["signals"])
    assert first["position"] == 0.0
    # The long section under the h4 is split so no passage runs far past the split size.
    assert all(p["words"] <= geo_pages.SPLIT_SECTION_WORDS + 200 for p in page["passages_all"])
    assert sum(1 for p in page["passages_all"] if p["heading"] == "Skipped level heading") >= 2


def test_structure_score_bounds_and_breakdown() -> None:
    page = geo_pages.analyse_html("https://example.com/blog/what-is-geo/", HTML)
    assert 0 <= page["structure_score"] <= 100
    assert set(page["structure_breakdown"]) == set(geo_pages.STRUCTURE_WEIGHTS)
    assert sum(geo_pages.STRUCTURE_WEIGHTS.values()) == 100
    assert page["structure_breakdown"]["answer_up_front"] == geo_pages.STRUCTURE_WEIGHTS["answer_up_front"]
    assert page["structure_breakdown"]["freshness_authorship"] == geo_pages.STRUCTURE_WEIGHTS["freshness_authorship"]


def test_spa_shell_scores_no_server_render_points() -> None:
    shell = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    page = geo_pages.analyse_html("https://example.com/app/", shell)
    assert page["is_spa"] is True
    assert page["structure_breakdown"]["server_rendered"] == 0
    assert page["passages_total"] == 0


def test_select_passages_keeps_lead_and_order() -> None:
    passages = [
        {"id": f"p{i}", "words": 50, "position": i / 20, "signals": [], "in_optimal_range": False,
         "has_list": False, "has_table": False}
        for i in range(1, 15)
    ]
    passages[9]["signals"] = ["statistic", "attribution"]
    kept = geo_pages.select_passages(passages, limit=4)
    ids = [p["id"] for p in kept]
    assert len(kept) == 4
    assert ids[0] == "p1"
    assert "p10" in ids
    assert ids == sorted(ids, key=lambda x: int(x[1:]))


def test_classify_page() -> None:
    assert geo_pages.classify_page("https://a.com/", [], "") == "homepage"
    assert geo_pages.classify_page("https://a.com/pricing/", ["SoftwareApplication"], "") == "pricing"
    assert geo_pages.classify_page("https://a.com/x", ["Product"], "") == "product"
    assert geo_pages.classify_page("https://a.com/services/seo/", [], "") == "service"
    assert geo_pages.classify_page("https://a.com/x", [], "Step 1: open the lid") == "guide"


def test_prioritise_urls_filters_dedupes_and_prefers_shallow() -> None:
    urls = [
        "https://a.com/blog/deep/post/",
        "https://a.com/services/",
        "https://a.com/services/#top",
        "https://a.com/tag/seo/",
        "https://a.com/blog/page/2/",
        "https://a.com/image.jpg",
        "https://a.com/",
        "mailto:x@a.com",
    ]
    assert geo_pages.prioritise_urls(urls, 10) == [
        "https://a.com/",
        "https://a.com/services/",
        "https://a.com/blog/deep/post/",
    ]
    assert geo_pages.prioritise_urls(urls, 1) == ["https://a.com/"]


def test_urls_from_screaming_frog_csv(tmp_path) -> None:
    path = tmp_path / "internal_html.csv"
    path.write_text(
        "﻿Address,Content Type,Status Code,Indexability\n"
        "https://a.com/,text/html; charset=UTF-8,200,Indexable\n"
        "https://a.com/old/,text/html,301,Non-Indexable\n"
        "https://a.com/noindex/,text/html,200,Non-Indexable\n"
        "https://a.com/file.pdf,application/pdf,200,Indexable\n",
        encoding="utf-8",
    )
    urls, warnings = geo_pages.urls_from_csv(str(path))
    assert urls == ["https://a.com/"]
    assert "Skipped 3" in warnings[0]


def test_urls_from_screaming_frog_export_folder(tmp_path) -> None:
    (tmp_path / "internal_all.csv").write_text(
        "Address,Content Type,Status Code,Indexability\nhttps://a.com/x/,text/html,200,Indexable\n",
        encoding="utf-8",
    )
    assert geo_pages.urls_from_csv(str(tmp_path))[0] == ["https://a.com/x/"]


def test_sitemap_locs_handles_index_and_urlset() -> None:
    urlset = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://a.com/x</loc></url></urlset>'
    index = b'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://a.com/s1.xml</loc></sitemap></sitemapindex>'
    assert geo_pages._sitemap_locs(urlset) == (["https://a.com/x"], [])
    assert geo_pages._sitemap_locs(index) == ([], ["https://a.com/s1.xml"])
    assert geo_pages._sitemap_locs(b"not xml") == ([], [])


def test_export_merges_scores_and_writes_csv(tmp_path) -> None:
    page = geo_pages.analyse_html("https://example.com/blog/what-is-geo/", HTML)
    page = {k: v for k, v in page.items() if k != "passages_all"}
    failed = {"url": "https://example.com/broken/", "error": "HTTP 500"}
    unscored = {**page, "url": "https://example.com/other/"}
    (tmp_path / "pages.json").write_text(json.dumps({"pages": [page, failed, unscored], "warnings": []}))
    (tmp_path / "scores").mkdir()
    (tmp_path / "scores" / "batch-01.json").write_text(json.dumps([{
        "url": "https://example.com/blog/what-is-geo/",
        "judgement_score": 150,
        "target_queries": [{"query": "what is geo", "fit": "strong"}],
        "best_passage_id": "p1",
        "weakest_passage_id": "nope",
        "gaps": ["No comparison table"],
        "citation_potential": "high",
        "priority": "low",
        "confidence": "medium",
        "top_fix": "Add a summary table",
    }]))

    summary = geo_pages.run_export(str(tmp_path), xlsx=False)
    assert summary["pages"] == 2
    assert summary["scored"] == 1
    assert summary["unscored_urls"] == ["https://example.com/other/"]

    with open(tmp_path / "geo-pages.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    scored = next(r for r in rows if r["url"].endswith("what-is-geo/"))
    assert scored["judgement_score"] == "100"  # clamped
    expected = round(geo_pages.JUDGEMENT_WEIGHT * 100 + geo_pages.STRUCTURE_WEIGHT * page["structure_score"])
    assert scored["estimated_geo_score"] == str(expected)
    assert scored["best_passage"].startswith("p1: What is generative")
    assert scored["weakest_passage"] == ""
    assert scored["target_queries"] == "what is geo (strong)"
    # Scored rows with a priority sort ahead of unscored ones.
    assert rows[0]["url"].endswith("what-is-geo/")
