#!/usr/bin/env python3
"""
Site-wide GEO page extraction and scoring export for ``/seo geo --site``.

Replaces the "Screaming Frog custom JavaScript + LLM API" workflow with a
two-step pipeline that keeps the expensive part (LLM judgement) small:

1. ``extract``  fetches each page's raw server HTML (what AI crawlers see, as
   they do not execute JavaScript), pulls out headings, passages, lists,
   tables, schema, dates and authorship, classifies the page type, computes a
   rule-based structure score, and writes compact scoring batches. Only the
   highest-signal passages per page go into a batch, so Claude reads a few
   thousand tokens per page instead of the full HTML.
2. ``export``   merges Claude's single-pass batch scores with the rule-based
   data into a CSV (and optional XLSX) with one row per URL.

All scores are ESTIMATES. The rule-based structure score is a heuristic and
the AI judgement score is one model's single-pass reading; neither is a
Google or AI-platform metric.

Usage:
    claude-seo run geo_pages.py extract --site https://example.com --out geo-site/
    claude-seo run geo_pages.py extract --sf-csv internal_html.csv --out geo-site/
    claude-seo run geo_pages.py extract --url https://a.com/x --url https://a.com/y --out geo-site/
    claude-seo run geo_pages.py export --out geo-site/ --xlsx
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401
    _HTML_PARSER = "lxml"
except ImportError:
    _HTML_PARSER = "html.parser"

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from sitemap_discovery import _bounded_fetch, discover_sitemaps  # noqa: E402

DEFAULT_MAX_PAGES = 25
HARD_MAX_PAGES = 500
DEFAULT_BATCH_SIZE = 5
PASSAGES_PER_PAGE = 8
PASSAGE_WORD_CAP = 180
MIN_PASSAGE_WORDS = 10
SPLIT_SECTION_WORDS = 220
OPTIMAL_MIN, OPTIMAL_MAX = 134, 167
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_SITEMAP_BYTES = 10 * 1024 * 1024

# Weights for the rule-based structure score. Sum = 100.
STRUCTURE_WEIGHTS = {
    "answer_up_front": 15,
    "passage_length": 15,
    "heading_structure": 15,
    "question_headings": 10,
    "lists_tables": 10,
    "evidence": 10,
    "schema": 10,
    "freshness_authorship": 10,
    "server_rendered": 5,
}

# Combined estimate = AI judgement weighted above the rule-based structure score,
# because the judgement covers query fit and passage quality the rules cannot see.
JUDGEMENT_WEIGHT = 0.6
STRUCTURE_WEIGHT = 0.4

_SKIP_PATH_RE = re.compile(
    r"/(tag|tags|author|feed|wp-json|wp-admin|cart|checkout|my-account|login|search)(/|$)"
    r"|/page/\d+/?$|\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|xml|css|js)$",
    re.I,
)
_DEFINITION_RE = re.compile(r"^[^.?!]{2,80}?\b(is|are|refers to|means|describes)\b", re.I)
_STAT_RE = re.compile(
    r"(\d[\d,.]*\s?(%|percent|per cent))|([£$€]\s?\d)|(\b\d[\d,.]*\s?(million|billion|thousand)\b)",
    re.I,
)
_ATTRIBUTION_RE = re.compile(
    r"\b(according to|source:|study|survey|research|reported by|data from|published by|found that)\b",
    re.I,
)
# A heading counts as a question when it ends with "?" or opens with a wh-word
# followed by at least two more words ("How it works", not "Why SEO Agent").
_QUESTION_RE = re.compile(r"(\?\s*$)|^(what|why|how|when|where|who|which)(\s+\S+){2,}", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# URL collection
# ---------------------------------------------------------------------------


def _normalise(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def _same_site(url: str, origin_host: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    bare = origin_host.lower().removeprefix("www.")
    return host == bare or host == f"www.{bare}"


def prioritise_urls(urls: Iterable[str], max_pages: int) -> list[str]:
    """Dedupe, drop non-content URLs, then prefer shallow paths (homepage first).

    Shallow pages are usually the service, category and pillar pages a client
    cares about most, so they win when the sitemap is bigger than the budget.
    Original order breaks ties.
    """
    seen: dict[str, int] = {}
    for index, url in enumerate(urls):
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        norm = _normalise(url)
        if _SKIP_PATH_RE.search(urlparse(norm).path) or norm in seen:
            continue
        seen[norm] = index

    def depth(url: str) -> int:
        return len([seg for seg in urlparse(url).path.split("/") if seg])

    ordered = sorted(seen, key=lambda u: (depth(u), seen[u]))
    return ordered[: max(1, min(max_pages, HARD_MAX_PAGES))]


def _sitemap_locs(content: bytes) -> tuple[list[str], list[str]]:
    """Return (page_urls, child_sitemaps) from a urlset or sitemapindex."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return [], []
    tag = root.tag.rsplit("}", 1)[-1]
    locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
    if tag == "sitemapindex":
        return [], locs
    return locs, []


def urls_from_site(site: str, max_child_sitemaps: int = 10) -> tuple[list[str], list[str]]:
    """Collect page URLs from the site's sitemaps. Returns (urls, warnings)."""
    warnings: list[str] = []
    discovery = discover_sitemaps(site)
    if discovery.get("error"):
        return [site], [f"Sitemap discovery failed: {discovery['error']}; analysing the homepage only"]
    origin = discovery.get("target") or site
    origin_host = urlparse(origin).hostname or ""
    sitemap_urls = [entry["url"] if isinstance(entry, dict) else entry for entry in discovery.get("found", [])]
    if not sitemap_urls:
        return [origin + "/"], ["No sitemap found; analysing the homepage only. Pass --urls-file or --sf-csv for more pages."]

    pages: list[str] = []
    queue = list(sitemap_urls)
    visited: set[str] = set()
    fetched = 0
    while queue and fetched < max_child_sitemaps + len(sitemap_urls):
        sm_url = queue.pop(0)
        if sm_url in visited:
            continue
        visited.add(sm_url)
        if not _same_site(sm_url, origin_host):
            warnings.append(f"Skipped cross-host sitemap {sm_url}")
            continue
        fetched += 1
        result = _bounded_fetch(sm_url, MAX_SITEMAP_BYTES)
        if result["error"] or result["too_large"]:
            warnings.append(f"Could not read sitemap {sm_url}: {result['error'] or 'too large'}")
            continue
        locs, children = _sitemap_locs(result["content"])
        pages.extend(u for u in locs if _same_site(u, origin_host))
        queue.extend(children)
    if not pages:
        pages = [origin + "/"]
        warnings.append("Sitemaps listed no pages; analysing the homepage only")
    if (origin + "/") not in pages:
        pages.insert(0, origin + "/")
    return pages, warnings


def urls_from_csv(path: str) -> tuple[list[str], list[str]]:
    """Read a Screaming Frog export (Internal > HTML) or any CSV with a URL column.

    Screaming Frog rows are kept only when they are HTML, returned 200 and are
    marked Indexable, so the page set matches what the user already filtered.
    """
    warnings: list[str] = []
    urls: list[str] = []
    if os.path.isdir(path):
        # A Screaming Frog export folder, e.g. from a headless crawl.
        found = next(
            (os.path.join(path, n) for n in ("internal_html.csv", "internal_all.csv") if os.path.isfile(os.path.join(path, n))),
            None,
        )
        if found is None:
            raise ValueError("No internal_html.csv or internal_all.csv in that folder")
        path = found
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = {name.lower().strip(): name for name in (reader.fieldnames or [])}
        url_col = next((fields[k] for k in ("address", "url", "page", "loc") if k in fields), None)
        if url_col is None:
            raise ValueError("CSV has no Address/URL column")
        ctype_col = fields.get("content type")
        status_col = fields.get("status code")
        index_col = fields.get("indexability")
        skipped = 0
        for row in reader:
            if ctype_col and "html" not in (row.get(ctype_col) or "").lower():
                skipped += 1
                continue
            if status_col and (row.get(status_col) or "").strip() not in ("200", ""):
                skipped += 1
                continue
            if index_col and (row.get(index_col) or "").strip().lower() not in ("indexable", ""):
                skipped += 1
                continue
            urls.append((row.get(url_col) or "").strip())
    if skipped:
        warnings.append(f"Skipped {skipped} CSV rows that were not indexable 200 HTML pages")
    return urls, warnings


# ---------------------------------------------------------------------------
# Page analysis
# ---------------------------------------------------------------------------


def _words(text: str) -> int:
    return len(text.split())


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate_words(text: str, cap: int) -> str:
    words = text.split()
    return text if len(words) <= cap else " ".join(words[:cap]) + " …"


def _json_ld_blocks(soup: BeautifulSoup) -> list[dict]:
    blocks: list[dict] = []
    for tag in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                blocks.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)
    return blocks


def _schema_types(blocks: list[dict]) -> list[str]:
    types: set[str] = set()
    for block in blocks:
        value = block.get("@type")
        for t in value if isinstance(value, list) else [value]:
            if isinstance(t, str):
                types.add(t)
    return sorted(types)


def classify_page(url: str, schema_types: list[str], text: str) -> str:
    """Coarse page type from schema first, then URL, then body keywords."""
    st = {t.lower() for t in schema_types}
    path = urlparse(url).path.lower()
    lowered = text.lower()
    if path in ("", "/"):
        return "homepage"
    if re.search(r"^/(pricing|plans)/?$", path):
        return "pricing"
    if st & {"product", "productgroup", "offer", "softwareapplication"} or re.search(r"/(product|products|shop)/", path):
        return "product"
    if "faqpage" in st or re.search(r"/(faq|faqs)/?", path):
        return "faq"
    if "howto" in st or re.search(r"\b(step 1|how to|tutorial)\b", lowered[:3000]):
        return "guide"
    if st & {"article", "blogposting", "newsarticle"} or re.search(r"/(blog|news|insights|articles?|guides?)/", path):
        return "article"
    if re.search(r"/(category|categories|collections?)/", path):
        return "category"
    if st & {"localbusiness", "service"} or re.search(r"/(services?|solutions?)/", path):
        return "service"
    if re.search(r"/(about|contact|team)", path):
        return "company"
    if re.search(r"\b(add to (cart|basket)|buy now)\b", lowered):
        return "product"
    return "other"


def _main_container(soup: BeautifulSoup):
    for tag in soup(["script", "style", "noscript", "template", "svg", "form"]):
        tag.decompose()
    for tag in soup.find_all(["nav", "header", "footer", "aside"]):
        tag.decompose()
    return soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"}) or soup.body or soup


def _passage_signals(text: str, heading: str) -> list[str]:
    signals: list[str] = []
    first_sentence = _SENTENCE_SPLIT_RE.split(text, maxsplit=1)[0]
    if _DEFINITION_RE.search(first_sentence):
        signals.append("definition")
    if heading and _QUESTION_RE.search(heading) and _words(first_sentence) <= 40:
        signals.append("direct_answer")
    if _STAT_RE.search(text):
        signals.append("statistic")
    if _ATTRIBUTION_RE.search(text):
        signals.append("attribution")
    return signals


def extract_passages(main) -> list[dict]:
    """Group main-content text into heading sections, splitting long sections.

    A passage is the text under one heading (paragraphs, list items as lines,
    table rows). Sections over SPLIT_SECTION_WORDS are split on block
    boundaries so each passage stays close to a citable length.
    """
    sections: list[dict] = []
    current = {"heading": "", "level": 0, "blocks": []}
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "ul", "ol", "table", "dl"]):
        if el.name in ("h1", "h2", "h3", "h4"):
            if current["blocks"]:
                sections.append(current)
            current = {"heading": _clean(el.get_text(" ")), "level": int(el.name[1]), "blocks": []}
            continue
        # Nested lists/paragraphs are captured by their outermost block.
        if el.find_parent(["ul", "ol", "table", "dl"]):
            continue
        if el.name in ("ul", "ol"):
            items = [_clean(li.get_text(" ")) for li in el.find_all("li", recursive=False)]
            text = "\n".join(f"- {i}" for i in items if i)
        elif el.name == "table":
            rows = [" | ".join(_clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])) for tr in el.find_all("tr")]
            text = "\n".join(r for r in rows if r.strip(" |"))
        else:
            text = _clean(el.get_text(" "))
        if text:
            current["blocks"].append({"kind": el.name, "text": text})
    if current["blocks"]:
        sections.append(current)

    passages: list[dict] = []
    for section in sections:
        chunk: list[dict] = []
        for block in section["blocks"]:
            chunk_words = sum(_words(b["text"]) for b in chunk)
            if chunk and chunk_words + _words(block["text"]) > SPLIT_SECTION_WORDS:
                passages.append({"heading": section["heading"], "level": section["level"], "blocks": chunk})
                chunk = []
            chunk.append(block)
        if chunk:
            passages.append({"heading": section["heading"], "level": section["level"], "blocks": chunk})

    result: list[dict] = []
    total_words = sum(_words(b["text"]) for p in passages for b in p["blocks"]) or 1
    running = 0
    for p in passages:
        text = "\n".join(b["text"] for b in p["blocks"])
        words = _words(text)
        position = round(running / total_words, 2)
        running += words
        if words < MIN_PASSAGE_WORDS:
            continue
        result.append({
            "id": f"p{len(result) + 1}",
            "heading": p["heading"],
            "text": text,
            "words": words,
            "position": position,
            "in_optimal_range": OPTIMAL_MIN <= words <= OPTIMAL_MAX,
            "has_list": any(b["kind"] in ("ul", "ol", "dl") for b in p["blocks"]),
            "has_table": any(b["kind"] == "table" for b in p["blocks"]),
            "signals": _passage_signals(text, p["heading"]),
        })
    return result


def _passage_rank(p: dict) -> float:
    score = len(p["signals"]) * 2.0
    if 40 <= p["words"] <= 200:
        score += 2
    if p["in_optimal_range"]:
        score += 1
    if p["position"] <= 0.3:
        score += 1
    if p["has_list"] or p["has_table"]:
        score += 0.5
    return score


def select_passages(passages: list[dict], limit: int = PASSAGES_PER_PAGE) -> list[dict]:
    """Keep the lead passage plus the highest-signal others, in document order."""
    if len(passages) <= limit:
        return passages
    lead = passages[0]
    rest = sorted(passages[1:], key=_passage_rank, reverse=True)[: limit - 1]
    keep = {lead["id"], *(p["id"] for p in rest)}
    return [p for p in passages if p["id"] in keep]


def _find_dates(soup: BeautifulSoup, blocks: list[dict]) -> dict:
    dates = {"published": None, "modified": None}
    for block in blocks:
        dates["published"] = dates["published"] or block.get("datePublished")
        dates["modified"] = dates["modified"] or block.get("dateModified")
    for prop, key in (("article:published_time", "published"), ("article:modified_time", "modified")):
        meta = soup.find("meta", attrs={"property": prop})
        if meta and meta.get("content") and not dates[key]:
            dates[key] = meta["content"]
    if not dates["published"]:
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            dates["published"] = time_tag["datetime"]
    return {k: (str(v) if v else None) for k, v in dates.items()}


def _has_author(soup: BeautifulSoup, blocks: list[dict]) -> bool:
    if any(block.get("author") for block in blocks):
        return True
    if soup.find("meta", attrs={"name": "author"}) or soup.find("a", rel="author"):
        return True
    return bool(soup.find(class_=re.compile(r"\b(author|byline)\b", re.I)))


def structure_score(page: dict) -> tuple[int, dict]:
    """Rule-based structure score (0-100). A triage heuristic, not a platform metric."""
    w = STRUCTURE_WEIGHTS
    passages = page["passages_all"]
    parts: dict[str, float] = {}

    early = [p for p in passages if p["position"] <= 0.3]
    parts["answer_up_front"] = w["answer_up_front"] if any(
        {"definition", "direct_answer"} & set(p["signals"]) for p in early
    ) else (w["answer_up_front"] / 3 if early else 0)

    near_optimal = sum(1 for p in passages if 100 <= p["words"] <= 200)
    parts["passage_length"] = w["passage_length"] * min(1.0, near_optimal / 3)

    headings = page["headings"]
    h1_count = sum(1 for h in headings if h["level"] == 1)
    has_h2 = any(h["level"] == 2 for h in headings)
    parts["heading_structure"] = (
        (5 if h1_count == 1 else 0) + (5 if has_h2 else 0) + (5 if not page["heading_skips"] else 0)
    ) * w["heading_structure"] / 15

    sub = [h for h in headings if h["level"] in (2, 3)]
    q_ratio = (sum(1 for h in sub if _QUESTION_RE.search(h["text"])) / len(sub)) if sub else 0
    parts["question_headings"] = w["question_headings"] * min(1.0, q_ratio / 0.3)

    parts["lists_tables"] = (w["lists_tables"] / 2) * (int(page["list_count"] > 0) + int(page["table_count"] > 0))

    has_stat = any("statistic" in p["signals"] for p in passages)
    parts["evidence"] = (w["evidence"] / 2) * (int(has_stat) + int(page["external_citations"] > 0))

    parts["schema"] = (w["schema"] / 2) * (int(bool(page["schema_types"])) + int(page["schema_matches_type"]))

    parts["freshness_authorship"] = (w["freshness_authorship"] / 2) * (
        int(bool(page["dates"]["published"] or page["dates"]["modified"])) + int(page["has_author"])
    )

    parts["server_rendered"] = w["server_rendered"] if (not page["is_spa"] and page["word_count"] >= 150) else 0

    rounded = {k: round(v, 1) for k, v in parts.items()}
    return round(sum(parts.values())), rounded


_TYPE_SCHEMA = {
    "product": {"product", "productgroup", "offer", "softwareapplication"},
    "article": {"article", "blogposting", "newsarticle"},
    "guide": {"howto", "article", "blogposting"},
    "faq": {"faqpage", "qapage"},
    "service": {"service", "localbusiness", "professionalservice"},
    "homepage": {"organization", "website", "localbusiness"},
    "company": {"organization", "aboutpage", "contactpage", "localbusiness"},
    "category": {"collectionpage", "itemlist"},
    "pricing": {"product", "softwareapplication", "offer", "service"},
}


def analyse_html(url: str, html: str) -> dict:
    """Pure function: raw HTML in, page record out. Network-free for testing."""
    from render_page import _is_spa  # local import: keeps Playwright import cost off the hot path

    soup = BeautifulSoup(html, _HTML_PARSER)
    blocks = _json_ld_blocks(soup)
    schema_types = _schema_types(blocks)
    title = _clean(soup.title.get_text()) if soup.title else ""
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_description = _clean(meta_desc_tag.get("content", "")) if meta_desc_tag else ""
    dates = _find_dates(soup, blocks)
    has_author = _has_author(soup, blocks)

    headings = [
        {"level": int(h.name[1]), "text": _clean(h.get_text(" "))}
        for h in soup.find_all(["h1", "h2", "h3", "h4"])
        if _clean(h.get_text(" "))
    ]
    skips = any(b["level"] - a["level"] > 1 for a, b in zip(headings, headings[1:]))

    main = _main_container(BeautifulSoup(html, _HTML_PARSER))
    host = (urlparse(url).hostname or "").removeprefix("www.")
    external = 0
    for a in main.find_all("a", href=True):
        link_host = (urlparse(urljoin(url, a["href"])).hostname or "").removeprefix("www.")
        if link_host and link_host != host:
            external += 1
    media = {
        "images": len(main.find_all("img")),
        "video": bool(main.find(["video"]) or main.find("iframe", src=re.compile(r"youtube|vimeo|wistia", re.I))),
    }
    passages = extract_passages(main)
    text = " ".join(p["text"] for p in passages)
    page_type = classify_page(url, schema_types, text)

    page = {
        "url": url,
        "page_type": page_type,
        "title": title,
        "meta_description": meta_description,
        "h1": next((h["text"] for h in headings if h["level"] == 1), ""),
        "headings": headings,
        "heading_skips": skips,
        "schema_types": schema_types,
        "schema_matches_type": bool({t.lower() for t in schema_types} & _TYPE_SCHEMA.get(page_type, set())),
        "dates": dates,
        "has_author": has_author,
        "external_citations": external,
        "media": media,
        "list_count": len(main.find_all(["ul", "ol"])),
        "table_count": len(main.find_all("table")),
        "word_count": _words(text),
        "is_spa": _is_spa(html),
        "passages_total": len(passages),
        "passages_in_optimal_range": sum(1 for p in passages if p["in_optimal_range"]),
        "passages_all": passages,
    }
    page["structure_score"], page["structure_breakdown"] = structure_score(page)
    page["passages"] = select_passages(passages)
    return page


def fetch_and_analyse(url: str) -> dict:
    result = _bounded_fetch(url, MAX_PAGE_BYTES)
    base = {"url": url, "final_url": result["final_url"], "status_code": result["status_code"]}
    if result["error"]:
        return {**base, "error": result["error"]}
    if result["too_large"]:
        return {**base, "error": "Page larger than 5 MB; skipped"}
    if "html" not in (result["content_type"] or "").lower():
        return {**base, "error": f"Not HTML ({result['content_type'] or 'unknown type'})"}
    if result["status_code"] and result["status_code"] >= 400:
        return {**base, "error": f"HTTP {result['status_code']}"}
    html = result["content"].decode("utf-8", errors="replace")
    page = analyse_html(result["final_url"] or url, html)
    return {**base, **page, "error": None}


def _batch_record(page: dict) -> dict:
    """The compact view Claude scores. Excludes everything the rules already measured."""
    return {
        "url": page["url"],
        "page_type": page["page_type"],
        "title": page["title"],
        "meta_description": page["meta_description"],
        "h1": page["h1"],
        "outline": [f"{'#' * h['level']} {h['text']}" for h in page["headings"][:30]],
        "schema_types": page["schema_types"],
        "word_count": page["word_count"],
        "structure_score": page["structure_score"],
        "passages": [
            {
                "id": p["id"],
                "heading": p["heading"],
                "words": p["words"],
                "position": p["position"],
                "signals": p["signals"],
                "text": _truncate_words(p["text"], PASSAGE_WORD_CAP),
            }
            for p in page["passages"]
        ],
    }


def run_extract(urls: list[str], out_dir: str, batch_size: int, delay: float, warnings: list[str]) -> dict:
    os.makedirs(os.path.join(out_dir, "batches"), exist_ok=True)
    pages: list[dict] = []
    for index, url in enumerate(urls):
        if index and delay:
            time.sleep(delay)
        pages.append(fetch_and_analyse(url))
        print(f"[{index + 1}/{len(urls)}] {url}", file=sys.stderr)

    ok = [p for p in pages if not p.get("error")]
    batch_files: list[str] = []
    for start in range(0, len(ok), batch_size):
        name = f"batch-{start // batch_size + 1:02d}.json"
        with open(os.path.join(out_dir, "batches", name), "w", encoding="utf-8") as handle:
            json.dump([_batch_record(p) for p in ok[start:start + batch_size]], handle, indent=1, ensure_ascii=False)
        batch_files.append(os.path.join("batches", name))

    stored = [{k: v for k, v in p.items() if k != "passages_all"} for p in pages]
    with open(os.path.join(out_dir, "pages.json"), "w", encoding="utf-8") as handle:
        json.dump({"pages": stored, "warnings": warnings}, handle, indent=1, ensure_ascii=False)

    batch_chars = sum(os.path.getsize(os.path.join(out_dir, f)) for f in batch_files)
    return {
        "pages_requested": len(urls),
        "pages_analysed": len(ok),
        "pages_failed": [{"url": p["url"], "error": p["error"]} for p in pages if p.get("error")],
        "batches": batch_files,
        "approx_scoring_input_tokens": batch_chars // 4,
        "warnings": warnings,
        "out_dir": out_dir,
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

SCORE_FIELDS = ("judgement_score", "target_queries", "citation_potential", "priority", "gaps", "top_fix")
CSV_COLUMNS = [
    "url", "page_type", "estimated_geo_score", "judgement_score", "structure_score",
    "citation_potential", "priority", "confidence", "target_queries", "best_passage",
    "weakest_passage", "gaps", "top_fix", "passages_total", "passages_in_optimal_range",
    "schema_types", "has_dates", "has_author", "server_rendered", "word_count", "title",
]


def _load_scores(out_dir: str) -> dict[str, dict]:
    scores: dict[str, dict] = {}
    score_dir = os.path.join(out_dir, "scores")
    if not os.path.isdir(score_dir):
        return scores
    for name in sorted(os.listdir(score_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(score_dir, name), encoding="utf-8") as handle:
            data = json.load(handle)
        for item in data if isinstance(data, list) else data.get("pages", []):
            if isinstance(item, dict) and item.get("url"):
                scores[_normalise(item["url"])] = item
    return scores


def _clamp(value, low: int, high: int) -> Optional[int]:
    try:
        return max(low, min(high, round(float(value))))
    except (TypeError, ValueError):
        return None


def merge_rows(pages: list[dict], scores: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for page in pages:
        if page.get("error"):
            continue
        score = scores.get(_normalise(page["url"]), {})
        judgement = _clamp(score.get("judgement_score"), 0, 100)
        structure = page["structure_score"]
        estimate = round(JUDGEMENT_WEIGHT * judgement + STRUCTURE_WEIGHT * structure) if judgement is not None else None
        passages = {p["id"]: p for p in page.get("passages", [])}

        def label(pid):
            p = passages.get(pid)
            return f"{pid}: {p['heading'] or '(no heading)'}" if p else ""

        queries = score.get("target_queries") or []
        rows.append({
            "url": page["url"],
            "page_type": page["page_type"],
            "estimated_geo_score": estimate,
            "judgement_score": judgement,
            "structure_score": structure,
            "citation_potential": score.get("citation_potential", ""),
            "priority": score.get("priority", ""),
            "confidence": score.get("confidence", ""),
            "target_queries": "; ".join(
                f"{q.get('query')} ({q.get('fit', '?')})" if isinstance(q, dict) else str(q) for q in queries
            ),
            "best_passage": label(score.get("best_passage_id")),
            "weakest_passage": label(score.get("weakest_passage_id")),
            "gaps": "; ".join(score.get("gaps") or []),
            "top_fix": score.get("top_fix", ""),
            "passages_total": page["passages_total"],
            "passages_in_optimal_range": page["passages_in_optimal_range"],
            "schema_types": ", ".join(page["schema_types"]),
            "has_dates": bool(page["dates"]["published"] or page["dates"]["modified"]),
            "has_author": page["has_author"],
            "server_rendered": not page["is_spa"],
            "word_count": page["word_count"],
            "title": page["title"],
        })
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows.sort(key=lambda r: (priority_order.get(str(r["priority"]).lower(), 4), r["estimated_geo_score"] if r["estimated_geo_score"] is not None else 101))
    return rows


def run_export(out_dir: str, xlsx: bool) -> dict:
    with open(os.path.join(out_dir, "pages.json"), encoding="utf-8") as handle:
        pages = json.load(handle)["pages"]
    scores = _load_scores(out_dir)
    rows = merge_rows(pages, scores)

    csv_path = os.path.join(out_dir, "geo-pages.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    outputs = {"csv": csv_path}
    if xlsx:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
        except ImportError:
            outputs["xlsx_error"] = "openpyxl not installed; CSV written only"
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "GEO pages (estimated)"
            ws.append(CSV_COLUMNS)
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for row in rows:
                ws.append([row[c] for c in CSV_COLUMNS])
            ws.freeze_panes = "A2"
            note = wb.create_sheet("About these scores")
            note.append(["All scores are estimates for prioritising work. They are not Google or AI-platform metrics."])
            note.append([f"Estimated GEO score = {JUDGEMENT_WEIGHT:.0%} AI judgement (single pass) + {STRUCTURE_WEIGHT:.0%} rule-based structure score."])
            xlsx_path = os.path.join(out_dir, "geo-pages.xlsx")
            wb.save(xlsx_path)
            outputs["xlsx"] = xlsx_path

    scored = [r for r in rows if r["estimated_geo_score"] is not None]
    unscored = [r["url"] for r in rows if r["estimated_geo_score"] is None]
    return {
        **outputs,
        "pages": len(rows),
        "scored": len(scored),
        "unscored_urls": unscored,
        "average_estimated_geo_score": round(sum(r["estimated_geo_score"] for r in scored) / len(scored)) if scored else None,
        "average_structure_score": round(sum(r["structure_score"] for r in rows) / len(rows)) if rows else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Site-wide GEO page extraction and estimated scoring export")
    sub = parser.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("extract", help="Fetch pages and write scoring batches")
    src = ex.add_mutually_exclusive_group(required=True)
    src.add_argument("--site", help="Site URL; pages come from its sitemaps")
    src.add_argument("--sf-csv", help="Screaming Frog 'Internal > HTML' export, or any CSV with an Address/URL column")
    src.add_argument("--urls-file", help="Text file with one URL per line")
    src.add_argument("--url", action="append", help="Page URL (repeatable)")
    ex.add_argument("--out", required=True, help="Output directory")
    ex.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help=f"Page budget (default {DEFAULT_MAX_PAGES}, max {HARD_MAX_PAGES})")
    ex.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"Pages per scoring batch (default {DEFAULT_BATCH_SIZE})")
    ex.add_argument("--delay", type=float, default=1.0, help="Seconds between requests (default 1.0)")

    exp = sub.add_parser("export", help="Merge scores into CSV/XLSX")
    exp.add_argument("--out", required=True, help="Directory used for extract (reads pages.json and scores/*.json)")
    exp.add_argument("--xlsx", action="store_true", help="Also write geo-pages.xlsx")

    args = parser.parse_args()

    if args.command == "export":
        print(json.dumps(run_export(args.out, args.xlsx), indent=2))
        return 0

    warnings: list[str] = []
    if args.site:
        urls, warnings = urls_from_site(args.site)
    elif args.sf_csv:
        urls, warnings = urls_from_csv(args.sf_csv)
    elif args.urls_file:
        with open(args.urls_file, encoding="utf-8") as handle:
            urls = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    else:
        urls = args.url
    selected = prioritise_urls(urls, args.max_pages)
    if len(urls) > len(selected):
        warnings.append(f"{len(urls)} URLs found; analysing the {len(selected)} highest-priority (shallowest) pages. Raise --max-pages to include more.")
    summary = run_extract(selected, args.out, max(1, args.batch_size), max(0.0, args.delay), warnings)
    print(json.dumps(summary, indent=2))
    return 0 if summary["pages_analysed"] else 1


if __name__ == "__main__":
    sys.exit(main())
