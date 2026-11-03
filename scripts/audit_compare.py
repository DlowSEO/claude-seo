#!/usr/bin/env python3
"""Compare an SEO audit with an earlier audit of the same site.

Used by ``/seo audit`` so a repeat audit shows what has changed. No new
command: the audit asks whether to compare, then calls these steps.

Usage::

    claude-seo run audit_compare.py find example.com-audit --ids --json
    claude-seo run audit_compare.py archive example.com-audit --json
    claude-seo run audit_compare.py from-pdf old-report.pdf --out baseline.json --json
    claude-seo run audit_compare.py diff OLD.json example.com-audit/audit-data.json \
        --markdown example.com-audit/CHANGES.md --json

``diff`` writes a ``comparison`` block into the new audit data, which
``google_report.py`` renders as "What Has Changed Since the Last Audit".
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

SEVERITIES = ["Info", "Low", "Medium", "High", "Critical"]
_SEV_RANK = {s.lower(): i for i, s in enumerate(SEVERITIES)}
_ARCHIVE_FILES = ("audit-data.json", "FULL-AUDIT-REPORT.md", "ACTION-PLAN.md", "CHANGES.md")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}

# Fuzzy title matching thresholds (0-1). Same-category matches need less.
SAME_CATEGORY_MATCH = 0.8
CROSS_CATEGORY_MATCH = 0.9
CONFIDENT_MATCH = 0.92


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain an audit data object")
    return data


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _norm(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())
    return re.sub(r"\s+", " ", text).strip()


def _severity(value) -> str:
    v = str(value or "").strip().lower()
    for s in SEVERITIES:
        if v == s.lower():
            return s
    return "Info" if not v else str(value).strip().title()


def audit_date(data: dict, path: Path | None = None) -> str:
    """Best available audit date as YYYY-MM-DD."""
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    for key in ("audited_at", "date", "generated_at"):
        raw = meta.get(key) or data.get(key)
        if raw:
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                m = re.match(r"\d{4}-\d{2}-\d{2}", str(raw))
                if m:
                    return m.group(0)
    if path is not None and path.exists():
        return date.fromtimestamp(path.stat().st_mtime).isoformat()
    return date.today().isoformat()


def health_score(data: dict):
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    return summary.get("health_score")


def flatten_findings(data: dict) -> list[dict]:
    out = []
    for cat in data.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        cname = str(cat.get("name") or "Other")
        for f in cat.get("findings") or []:
            if isinstance(f, str):
                f = {"title": f}
            if not isinstance(f, dict):
                continue
            title = str(f.get("title") or f.get("issue") or f.get("name") or "").strip()
            if not title:
                continue
            out.append({
                "id": str(f.get("id") or "").strip() or None,
                "title": title,
                "category": cname,
                "severity": _severity(f.get("severity")),
            })
    return out


# ── find / archive ───────────────────────────────────────────────────────────

def find_previous(audit_dir: Path, include_ids: bool = False) -> dict:
    candidates = []
    current = audit_dir / "audit-data.json"
    paths = []
    if current.is_file():
        paths.append((current, "latest"))
    hist = audit_dir / "history"
    if hist.is_dir():
        for p in sorted(hist.glob("*/audit-data.json")):
            paths.append((p, "history"))
    seen_dates = set()
    for p, kind in paths:
        try:
            data = _load_json(p)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        d = audit_date(data, p)
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        entry = {
            "path": str(p),
            "kind": kind,
            "date": d,
            "health_score": health_score(data),
            "mode": meta.get("mode"),
            "source": meta.get("source", "audit"),
            "findings": len(flatten_findings(data)),
        }
        # The latest audit is archived into history before a new run, so the
        # same audit can appear twice. Keep the first (latest) copy.
        key = (d, entry["health_score"], entry["findings"])
        if key in seen_dates:
            continue
        seen_dates.add(key)
        candidates.append(entry)
    candidates.sort(key=lambda e: e["date"], reverse=True)
    result = {"audit_dir": str(audit_dir), "previous": candidates, "count": len(candidates)}
    if include_ids and candidates:
        data = _load_json(Path(candidates[0]["path"]))
        result["previous_findings"] = flatten_findings(data)
    return result


def archive(audit_dir: Path) -> dict:
    src = audit_dir / "audit-data.json"
    if not src.is_file():
        return {"archived": False, "reason": "no previous audit-data.json in this folder"}
    data = _load_json(src)
    d = audit_date(data, src)
    dest = audit_dir / "history" / d
    n = 2
    while dest.exists() and (dest / "audit-data.json").is_file() and \
            (dest / "audit-data.json").read_bytes() != src.read_bytes():
        dest = audit_dir / "history" / f"{d}-{n}"
        n += 1
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in _ARCHIVE_FILES:
        p = audit_dir / name
        if p.is_file():
            shutil.copy2(p, dest / name)
            copied.append(name)
    for pdf in audit_dir.glob("*.pdf"):
        shutil.copy2(pdf, dest / pdf.name)
        copied.append(pdf.name)
    findings = audit_dir / "findings"
    if findings.is_dir():
        shutil.copytree(findings, dest / "findings", dirs_exist_ok=True)
        copied.append("findings/")
    if "meta" not in data or not isinstance(data.get("meta"), dict):
        archived = _load_json(dest / "audit-data.json")
        archived["meta"] = {"audited_at": d}
        _write_json(dest / "audit-data.json", archived)
    return {"archived": True, "path": str(dest / "audit-data.json"), "date": d, "files": copied}


# ── baseline from an earlier PDF ─────────────────────────────────────────────

_SEV_RE = r"(Critical|High|Medium|Low|Info)"


def _date_from_text(text: str) -> str | None:
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        return m.group(0)
    m = re.search(r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
                  r"September|October|November|December)\s+(\d{4})\b", text, re.I)
    if m:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
    m = re.search(r"\b(January|February|March|April|May|June|July|August|September|"
                  r"October|November|December)\s+(\d{1,2}),\s+(\d{4})\b", text, re.I)
    if m:
        return date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
    return None


def parse_report_text(text: str) -> dict:
    """Rebuild approximate audit data from the text of an earlier PDF report."""
    lines = [ln.strip() for ln in text.splitlines()]
    data: dict = {"summary": {}, "categories": [], "meta": {
        "source": "pdf-text", "confidence": "approximate"}}
    m = (re.search(r"\b(\d{1,3})\s*/\s*100\s*(?:SEO\s+)?Health\s+Score", text, re.I)
         or re.search(r"Health\s+Score\D{0,40}?(\d{1,3})\s*/\s*100", text, re.I | re.S)
         or re.search(r"\b(\d{1,3})\s*/\s*100\b", text))
    if m and 0 <= int(m.group(1)) <= 100:
        data["summary"]["health_score"] = int(m.group(1))
    d = _date_from_text(text)
    if d:
        data["meta"]["audited_at"] = d

    current = None
    in_categories = False
    for i, ln in enumerate(lines):
        if not ln:
            continue
        if re.match(r"^\d+\.\s*Audit Categories$", ln, re.I):
            in_categories = True
            continue
        if in_categories and re.match(r"^\d+\.\s+(Core Web Vitals|Search Console|Indexation|Action Plan|"
                                      r"Recommendations|Data Sources)", ln, re.I):
            in_categories = False
            current = None
            continue
        cm = re.match(r"^\d+\.\d+\s+(.{3,80})$", ln)
        if in_categories and cm:
            current = {"name": cm.group(1).strip(), "findings": []}
            data["categories"].append(current)
            continue
        if current is None:
            continue
        sm = re.match(r"^Score:\s*(\d{1,3})(?:\.\d+)?\s*/\s*100", ln, re.I)
        if sm:
            current["score"] = int(sm.group(1))
            continue
        fm = re.match(r"^(.{6,160}?)\s+" + _SEV_RE + r"$", ln)
        if fm and not ln.lower().startswith(("recommendation", "score")):
            current["findings"].append({"title": fm.group(1).strip(), "severity": fm.group(2)})
            continue
        if re.fullmatch(_SEV_RE, ln) and i > 0 and lines[i - 1]:
            prev = lines[i - 1]
            if not current["findings"] or current["findings"][-1]["title"] != prev:
                current["findings"].append({"title": prev, "severity": ln})
    return data


def from_pdf(pdf: Path) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"error": "pypdf is not installed. Read the PDF directly and write the baseline "
                         "JSON by hand in the audit-data.json shape, with meta.source set to "
                         "\"pdf-manual\".", "data": None}
    reader = PdfReader(str(pdf))
    try:
        attachments = reader.attachments or {}
    except Exception:  # noqa: BLE001 - malformed name trees should not stop the text route
        attachments = {}
    for name, blobs in attachments.items():
        if name.endswith("audit-data.json") and blobs:
            data = json.load(io.BytesIO(blobs[0]))
            data.pop("comparison", None)
            meta = data.setdefault("meta", {})
            meta["source"] = "pdf-embedded"
            meta.setdefault("audited_at", audit_date(data))
            return {"data": data, "method": "embedded", "confidence": "exact"}
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    data = parse_report_text(text)
    if not data["meta"].get("audited_at"):
        data["meta"]["audited_at"] = date.fromtimestamp(pdf.stat().st_mtime).isoformat()
    found = len(flatten_findings(data))
    warning = None
    if not found or data["summary"].get("health_score") is None:
        warning = ("Little could be read from this PDF. Read it directly and fill in the "
                   "health score, category scores and finding titles by hand.")
    return {"data": data, "method": "text", "confidence": "approximate",
            "findings": found, "warning": warning}


# ── diff ─────────────────────────────────────────────────────────────────────

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or the to with "
    "page pages url urls site".split())


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t not in _STOPWORDS and not t.isdigit()}


def _similarity(a: str, b: str) -> float:
    """Title similarity that ignores counts and word order.

    "Canonical tags missing on 18 pages" and "Canonical tags missing on 9
    pages" are the same issue; so are "No author information on blog posts"
    and "Blog posts have no author information"."""
    na = re.sub(r"\d+", "", _norm(a))
    nb = re.sub(r"\d+", "", _norm(b))
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = _tokens(a), _tokens(b)
    dice = (2 * len(ta & tb) / (len(ta) + len(tb))) if ta and tb else 0.0
    if not (ta and tb):
        return seq
    # Shared words carry most of the weight so "missing" vs "duplicate" or
    # "titles" vs "descriptions" keep two issues apart.
    return 0.7 * dice + 0.3 * seq


def match_findings(old: list[dict], new: list[dict]):
    """Return (pairs, unmatched_old, unmatched_new). Pairs carry a score."""
    pairs = []
    old_left = list(range(len(old)))
    new_left = list(range(len(new)))

    # 1. Stable ids.
    for j in list(new_left):
        fid = new[j]["id"]
        if not fid:
            continue
        for i in old_left:
            if old[i]["id"] and old[i]["id"] == fid:
                pairs.append((i, j, 1.0, "id"))
                old_left.remove(i)
                new_left.remove(j)
                break

    # 2. Title similarity, best first.
    scored = []
    for i in old_left:
        for j in new_left:
            same_cat = _norm(old[i]["category"]) == _norm(new[j]["category"])
            s = _similarity(old[i]["title"], new[j]["title"])
            if s >= (SAME_CATEGORY_MATCH if same_cat else CROSS_CATEGORY_MATCH):
                scored.append((s, i, j))
    scored.sort(reverse=True)
    for s, i, j in scored:
        if i in old_left and j in new_left:
            pairs.append((i, j, round(s, 3), "title"))
            old_left.remove(i)
            new_left.remove(j)
    return pairs, old_left, new_left


def _google_updates_between(start: str, end: str) -> list[dict]:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import seo_updates  # noqa: E402
        return seo_updates.updates_between(start, end)
    except Exception:  # noqa: BLE001 - comparison still works without the ledger
        return []


def compare(old: dict, new: dict, old_path: Path | None = None, new_path: Path | None = None) -> dict:
    old_date = audit_date(old, old_path)
    new_date = audit_date(new, new_path)
    old_f = flatten_findings(old)
    new_f = flatten_findings(new)
    pairs, gone, added = match_findings(old_f, new_f)

    persisting, review = [], []
    for i, j, score, how in pairs:
        o, n = old_f[i], new_f[j]
        before = _SEV_RANK.get(o["severity"].lower(), 0)
        after = _SEV_RANK.get(n["severity"].lower(), 0)
        trend = "same" if before == after else ("better" if after < before else "worse")
        item = {"title": n["title"], "category": n["category"], "severity": n["severity"],
                "previous_severity": o["severity"], "trend": trend}
        persisting.append(item)
        if how == "title" and score < CONFIDENT_MATCH:
            review.append({"previous": o["title"], "current": n["title"], "similarity": score})

    def _cats(data):
        out = {}
        for c in data.get("categories") or []:
            if isinstance(c, dict) and c.get("name") is not None:
                out[_norm(c["name"])] = (str(c["name"]), c.get("score"))
        return out

    oc, nc = _cats(old), _cats(new)
    categories = []
    for key, (name, score) in nc.items():
        prev = oc.get(key, (name, None))[1]
        delta = None
        if isinstance(prev, (int, float)) and isinstance(score, (int, float)):
            delta = round(score - prev, 1)
        categories.append({"name": name, "previous": prev, "current": score, "delta": delta})

    oh, nh = health_score(old), health_score(new)
    old_meta = old.get("meta") if isinstance(old.get("meta"), dict) else {}
    new_meta = new.get("meta") if isinstance(new.get("meta"), dict) else {}
    notes = []
    if old_meta.get("source") in ("pdf-text", "pdf-manual"):
        notes.append("The previous audit was rebuilt from its PDF report, so issue matching is approximate.")
    if old_meta.get("mode") and new_meta.get("mode") and old_meta["mode"] != new_meta["mode"]:
        notes.append(f"The previous audit was a {old_meta['mode']} audit and this one is a "
                     f"{new_meta['mode']} audit, so issue counts are not directly comparable.")

    return {
        "previous": {"date": old_date, "health_score": oh, "source": old_meta.get("source", "audit"),
                     "path": str(old_path) if old_path else None},
        "current": {"date": new_date, "health_score": nh},
        "health_delta": (round(nh - oh, 1) if isinstance(oh, (int, float)) and isinstance(nh, (int, float)) else None),
        "categories": categories,
        "resolved": [old_f[i] for i in gone],
        "new": [new_f[j] for j in added],
        "persisting": persisting,
        "counts": {"resolved": len(gone), "new": len(added), "persisting": len(persisting),
                   "worse": sum(1 for p in persisting if p["trend"] == "worse"),
                   "better": sum(1 for p in persisting if p["trend"] == "better")},
        "google_updates": _google_updates_between(old_date, new_date),
        "review_matches": review,
        "notes": notes,
    }


def _fmt_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
        return f"{d.day} {d.strftime('%B %Y')}"
    except (TypeError, ValueError):
        return str(iso)


def to_markdown(c: dict, domain: str = "") -> str:
    prev, cur = c["previous"], c["current"]
    out = [f"# What has changed since the last audit{': ' + domain if domain else ''}", ""]
    out.append(f"Previous audit: {_fmt_date(prev['date'])}. This audit: {_fmt_date(cur['date'])}.")
    if c.get("health_delta") is not None:
        sign = "+" if c["health_delta"] > 0 else ""
        out.append(f"Health score: {prev['health_score']} to {cur['health_score']} ({sign}{c['health_delta']:g}).")
    k = c["counts"]
    out += ["", f"- Resolved: {k['resolved']}", f"- New: {k['new']}",
            f"- Still open: {k['persisting']} ({k['worse']} worse, {k['better']} better)", ""]
    if c["categories"]:
        out += ["## Category scores", "", "| Category | Previous | Now | Change |", "|---|---|---|---|"]
        for cat in c["categories"]:
            d = cat["delta"]
            ch = "" if d is None else (f"+{d:g}" if d > 0 else f"{d:g}")
            out.append(f"| {cat['name']} | {cat['previous'] if cat['previous'] is not None else 'n/a'} | "
                       f"{cat['current'] if cat['current'] is not None else 'n/a'} | {ch} |")
        out.append("")
    for key, heading in (("resolved", "Resolved since the last audit"), ("new", "New since the last audit")):
        if c[key]:
            out += [f"## {heading}", ""]
            out += [f"- {f['title']} ({f['category']}, {f['severity']})" for f in c[key]]
            out.append("")
    if c["persisting"]:
        out += ["## Still open", ""]
        for f in c["persisting"]:
            change = "" if f["trend"] == "same" else f", was {f['previous_severity']}"
            out.append(f"- {f['title']} ({f['category']}, {f['severity']}{change})")
        out.append("")
    if c["google_updates"]:
        out += ["## Google updates between the two audits", ""]
        for u in c["google_updates"]:
            end = f" to {_fmt_date(u['end'])}" if u.get("end") else ""
            out.append(f"- {u['name']}: {_fmt_date(u['date'])}{end}")
        out += ["", "Ranking and traffic movement in this period may reflect these updates as well as changes to the site.", ""]
    for n in c.get("notes") or []:
        out.append(f"Note: {n}")
    return "\n".join(out).rstrip() + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────

def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="List earlier audits in an audit folder")
    f.add_argument("audit_dir")
    f.add_argument("--ids", action="store_true", help="Include the latest audit's findings")
    f.add_argument("--json", action="store_true")

    a = sub.add_parser("archive", help="Copy the current audit into history/<date>/")
    a.add_argument("audit_dir")
    a.add_argument("--json", action="store_true")

    r = sub.add_parser("from-pdf", help="Rebuild a baseline from an earlier PDF report")
    r.add_argument("pdf")
    r.add_argument("--out", required=True)
    r.add_argument("--json", action="store_true")

    d = sub.add_parser("diff", help="Compare two audit data files")
    d.add_argument("previous")
    d.add_argument("current")
    d.add_argument("--markdown", help="Also write a CHANGES.md summary here")
    d.add_argument("--domain", default="")
    d.add_argument("--no-write", action="store_true", help="Do not add the comparison to the current file")
    d.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    try:
        if args.cmd == "find":
            _emit(find_previous(Path(args.audit_dir), args.ids), args.json)
        elif args.cmd == "archive":
            _emit(archive(Path(args.audit_dir)), args.json)
        elif args.cmd == "from-pdf":
            res = from_pdf(Path(args.pdf))
            if res.get("data") is not None:
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                _write_json(out, res["data"])
                res = {k: v for k, v in res.items() if k != "data"}
                res["out"] = str(out)
            _emit(res, args.json)
            return 0 if "error" not in res else 1
        elif args.cmd == "diff":
            old_p, new_p = Path(args.previous), Path(args.current)
            old, new = _load_json(old_p), _load_json(new_p)
            c = compare(old, new, old_p, new_p)
            if not args.no_write:
                new["comparison"] = c
                _write_json(new_p, new)
            if args.markdown:
                Path(args.markdown).write_text(to_markdown(c, args.domain), encoding="utf-8")
            _emit(c if args.json else {"health_delta": c["health_delta"], **c["counts"],
                                       "review_matches": len(c["review_matches"])}, args.json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _emit({"error": str(exc)}, getattr(args, "json", False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
