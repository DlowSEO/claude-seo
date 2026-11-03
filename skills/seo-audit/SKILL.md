---
name: seo-audit
description: "Full website SEO audit with parallel subagent delegation. Crawls up to 500 pages, detects business type, delegates to up to 15 specialists (8 always + 7 conditional), generates health score. Use when user says audit, full SEO check, analyze my site, or website health check."
user-invocable: true
argument-hint: "[url] [--mode snapshot|full] [--compare latest|none|<pdf or folder>]"
license: MIT
metadata:
  author: Dan Lowry
  version: "3.0.0"
  category: seo
---

# Full Website SEO Audit

## Process

0. **Start the tracker and ask the setup questions together.** Before crawling:

   a. Start the progress tracker: `claude-seo run audit_progress.py start {domain}-audit --domain <domain>`
      (add `--mode <mode>` once known and `--compare` if a comparison will run; calling `start`
      again resets it). Then create a task list with the native task tool (TodoWrite or the
      task list), one item per stage: Setup, Homepage, Crawl, Specialists, Scoring, Comparison
      (only when comparing), Report. Mark each item in progress and completed as you go, so the
      person can see the audit moving in the chat. See Progress Tracking below.
   b. Check for an earlier audit of this site: `claude-seo run audit_compare.py find {domain}-audit --ids --json`.
   c. Ask everything in **one** message, then wait for the answer. Skip any question whose
      answer was passed as a flag (`--mode`, `--compare`).

   > **1. Audit depth.** A **Snapshot Audit** (top-level findings only, report capped at 10
   > pages, good for a quick read on a small site) or a **Full Audit** (comprehensive crawl and
   > findings across every category, recommended for larger or e-commerce sites)?
   >
   > **2. Comparison** (only if `find` returned an earlier audit): This site was audited on
   > {date} (health score {score}). Should this report show what has changed since then?
   > (Recommended: yes.)
   >
   > If `find` returned nothing, add one line instead of question 2: "If this site has been
   > audited before, share the earlier PDF report or its audit folder and the report will
   > show what has changed. Otherwise this will be treated as the first audit."

   Don't guess the mode from site size alone; a small site owner may still want the full
   depth, and a large e-commerce owner may want a snapshot first. See Audit Modes below for
   what each changes. If several earlier audits exist, offer the most recent and mention
   the others by date.

   d. **If comparing**, prepare the baseline before any new output is written:
      - Earlier audit in this folder: `claude-seo run audit_compare.py archive {domain}-audit --json`
        and use the returned `path` as the baseline.
      - A PDF the person shared: `claude-seo run audit_compare.py from-pdf <file.pdf> --out {domain}-audit/history/baseline-<date>/audit-data.json --json`.
        `method: embedded` is exact (PDFs made by `google_report.py` carry their own data). `method: text` is
        approximate: read the PDF yourself, check the health score, category scores and
        finding titles in the baseline file, and correct them before continuing.
      - A folder the person shared: use its `audit-data.json` as the baseline.
      - Refresh Google's update log so the comparison can name updates between the two
        audits: `claude-seo run seo_updates.py --refresh-if-stale 7 --json`. If it cannot
        connect, carry on; the bundled log is used.
      - Give the specialists the earlier findings list from step b (or the baseline file) and
        tell them: reuse the earlier finding's `id` when reporting the same issue, and give
        every new finding a short kebab-case `id` describing the issue type
        (`missing-meta-description`, `noindex-key-pages`). Stable ids make the comparison exact.

1. **Render homepage**: use `claude-seo run render_page.py <url> --mode auto --json` to capture raw HTML, rendered HTML, extracted text, SPA status, and accessibility data when needed
2. **Detect business type**: analyze homepage signals per seo orchestrator
3. **Crawl site**: follow internal links up to the mode's page cap, respect robots.txt
4. **Delegate to subagents** (if available, otherwise run inline sequentially):
   - `seo-technical` -- robots.txt, sitemaps, canonicals, schema, Core Web Vitals, security headers
   - `seo-content` -- E-E-A-T, readability, thin content, AI citation readiness
   - `seo-performance` -- LCP, INP, CLS measurements
   - `seo-visual` -- screenshots, mobile testing, above-fold analysis
   - `seo-geo` -- AI crawler access, llms.txt, citability, brand mention signals
   - `seo-local` -- GBP signals, NAP consistency, reviews, local schema, geo-grid and review intelligence where API access allows (spawn when Local Service industry detected: brick-and-mortar, SAB, or hybrid business type)
   - `seo-google` -- CWV field data (CrUX), URL indexation (GSC), organic traffic (GA4) (spawn when Google API credentials detected via `claude-seo run google_auth.py --check`)
   - `seo-backlinks` -- Backlink profile data: DA/PA, referring domains, anchor text, toxic links (spawn when Moz or Bing API credentials detected via `claude-seo run backlinks_auth.py --check`, or always include Common Crawl domain-level metrics)

   Strategy work (topic clustering, programmatic pages, e-commerce product SEO) is
   not delegated during an audit -- surface it as a recommendation to run
   `/seo strategy` instead.

5. **Score** -- aggregate into SEO Health Score (0-100)
6. **Persist audit artifacts** -- write all outputs under `{domain}-audit/`. Set
   `meta.audited_at` (today, ISO date) and `meta.mode` in `audit-data.json`, and give every
   finding an `id` (see step 0d).
7. **Compare** (only when comparing) -- run
   `claude-seo run audit_compare.py diff <baseline audit-data.json> {domain}-audit/audit-data.json --markdown {domain}-audit/CHANGES.md --domain <domain> --json`.
   This adds a `comparison` block to `audit-data.json`, which the PDF renders as "What Has
   Changed Since the Last Audit". Check every pair in `review_matches`: if two titles are
   not the same issue, give the current finding a new `id` and run `diff` again. Open
   `FULL-AUDIT-REPORT.md` with a short "Since the last audit" paragraph: the score change,
   what was resolved, what is new, and any Google update that rolled out in between
   (`comparison.google_updates`), without claiming the update caused a change.
8. **Report** -- generate prioritized action plan and optional PDF/HTML report

## Progress Tracking

Long audits need a visible sign of progress. The task list in the chat (step 0) works in the
terminal, the VS Code panel and the desktop app; update it at every stage change. The progress
file drives the optional status line bar under the prompt. Update it with one call per stage,
passing `--detail` with a few words at most. `...` below stands for
`claude-seo run audit_progress.py`.

| When | Command |
|---|---|
| Homepage render starts | `... stage {domain}-audit render` |
| Crawl starts / finishes | `... stage {domain}-audit crawl` / `... --status done --detail "142 pages"` |
| Specialists spawned | `... stage {domain}-audit specialists --done 0 --total <n>` |
| Each specialist returns | `... stage {domain}-audit specialists --done <k> --total <n>` |
| Scoring | `... stage {domain}-audit score` |
| Comparison | `... stage {domain}-audit compare` |
| Report writing | `... stage {domain}-audit report` |
| Finished | `... finish {domain}-audit --score <health score>` |

The person can add the bar to their Claude Code status line during `/seo setup` (see the `seo` skill).

If a progress call fails, carry on with the audit; tracking never blocks the work.

## Audit Modes

| | Snapshot Audit | Full Audit |
|---|---|---|
| Positioning | Free/quick-look audit, small sites | Comprehensive audit, larger or e-commerce sites |
| Crawl cap | 50 pages | 500 pages |
| Specialists | Same delegation rules (8 always + conditional) | Same delegation rules (8 always + conditional) |
| Per-specialist findings | Top 3-5 highest-severity findings only, still evidence-backed | Full findings list |
| `FULL-AUDIT-REPORT.md` length | Capped at 10 pages: Executive Summary, health score, top 5 critical issues, top 5 quick wins, one condensed paragraph per category (score + top findings), no exhaustive per-page tables | Full Report Structure below, no length cap |
| `ACTION-PLAN.md` | Critical + High priority items only | All four phases |
| PDF generation | Optional, same condensed content | Recommended (see Output Files) |

Both modes still run the full crawl-and-analyze pipeline and every finding still needs evidence
per the standard below, snapshot mode trims *how much* gets reported, not the rigor behind it.
Note in the report header which mode produced it, and that Snapshot Audit findings represent a
sample, not exhaustive coverage, so the reader knows to ask for a Full Audit if they want more.

## Crawl Configuration

```
Max pages: 500 (full) / 50 (snapshot)
Respect robots.txt: Yes
Follow redirects: Yes (max 3 hops)
Timeout per page: 30 seconds
Concurrent requests: 5
Delay between requests: 1 second
```

## Output Files

- `{domain}-audit/FULL-AUDIT-REPORT.md`: Comprehensive findings (Full Audit) or a 10-page-max condensed summary (Snapshot Audit, see Audit Modes)
- `{domain}-audit/ACTION-PLAN.md`: Prioritized recommendations (Critical > High > Medium > Low)
- `{domain}-audit/audit-data.json`: Structured audit envelope for report generation
- `{domain}-audit/CHANGES.md`: What has changed since the previous audit (only when comparing)
- `{domain}-audit/history/<date>/`: Earlier audits kept for comparison
- `{domain}-audit/findings/*.md`: Per-category specialist findings (`technical.md`, `content.md`, `schema.md`, `performance.md`, `visual.md`, etc.)
- `{domain}-audit/screenshots/`: Desktop + mobile captures (if Playwright available)
- **PDF Report** (recommended): Generate a professional A4 PDF using `claude-seo run google_report.py --type full --data {domain}-audit/audit-data.json --domain <domain> --output-dir {domain}-audit/`. This produces a white-cover enterprise report with TOC, executive summary, charts (Lighthouse gauges, query bars, index donut), metric cards, threshold tables, prioritized recommendations with effort estimates, and implementation roadmap. The PDF carries a copy of `audit-data.json` inside it, so a later audit can compare against the PDF alone; add `--no-embed-data` only if asked. Always offer PDF generation after completing an audit. Client-facing PDFs must use the Ad Rank One Design System (see below).

## Report Design: Ad Rank One Design System (required)

Every client-facing audit report (PDF or HTML) must be built with the
**Ad Rank One Design System** (`@aro/design-system`). Do not use the default
white-cover `google_report.py` styling for client deliverables.

- **Source:** `~/Documents/Claude audits/Ad Rank One Design System/` (zip copy
  alongside it). This is the only approved copy; any older "aro Design System"
  folder or notes are superseded.
- **Read first:** `README.md`, then `guidelines/guidelines/brand-rules.md`,
  `documents-and-decks.md` and `components.md`, plus the `.prompt.md` for each
  component you use.
- **Loading:** link `styles.css` (imports `_ds_bundle.css`, tokens and the
  Adobe Fonts kit) and `_ds_bundle.js` after React (`_vendor/`). Components are
  on `window.AroDS`; wrap everything once in `AroRoot`.
- **Page structure:** open on `DocCover` (void, aurora mesh, title, "Prepared
  for", date written out, fixed registered details), then one `DocPage` per A4
  page. Never hand-build page frames, covers or logos.
- **Components for audit content:** `DataTable` for figures, `StatBand` /
  `BeforeAfter` for results, `Checklist` for what the client gets, `Steps` for
  the action plan sequence, `FactsStrip` for at-a-glance facts.
- **Style glue with `--aro-*` tokens only** (documents use the `--aro-doc-*`
  A4 scale). Paper ground for on-screen PDFs, white `--aro-surface` if printed.
- **Brand rules that are easy to break:** Degular / Degular Display only (no
  serif, no mono); no eyebrows or kickers above headings; no italics for
  emphasis; section headings at h2 size, sentence case; jade means proof,
  violet means links only; aurora only on the cover; no pills; British English,
  no dashes as punctuation; never invent figures or claims; never use the
  tagline "Found by humans. Found by machines."

If the design system folder is missing, stop and ask the user for it rather
than falling back to unbranded output.

## Structured Audit Data Envelope

Write `{domain}-audit/audit-data.json` with this shape so `claude-seo run google_report.py --type full --data {domain}-audit/audit-data.json --domain <domain> --output-dir {domain}-audit/` can generate a report even when Google API data is unavailable:

```json
{
  "meta": {
    "audited_at": "YYYY-MM-DD",
    "mode": "full|snapshot"
  },
  "summary": {
    "health_score": 0,
    "business_type": "detected type",
    "top_findings": [],
    "quick_wins": []
  },
  "categories": [
    {
      "name": "Technical SEO",
      "score": 0,
      "what_works": [],
      "findings": [
        {
          "id": "short-kebab-case-issue-type",
          "title": "Finding title",
          "severity": "Critical|High|Medium|Low|Info",
          "description": "Evidence-backed detail",
          "recommendation": "Specific fix"
        }
      ]
    }
  ],
  "action_plan": {
    "phases": [
      {"name": "Phase 1: Critical Fixes", "timeframe": "Week 1", "items": []},
      {"name": "Phase 2: High-Impact Improvements", "timeframe": "Weeks 2-3", "items": []},
      {"name": "Phase 3: Content & Authority", "timeframe": "Month 2", "items": []},
      {"name": "Phase 4: Monitoring & Iteration", "timeframe": "Ongoing", "items": []}
    ]
  },
  "artifacts": {
    "findings_dir": "findings/",
    "screenshots_dir": "screenshots/"
  }
}
```

## Evidence Standard

This applies whether specialists are delegated to or the audit runs inline. A finding is not
done until it cites what was actually observed on this site:

- Name the exact affected URL(s). If an issue spans multiple pages, give the count plus a
  representative sample of URLs ("14 of 32 crawled pages," not "several pages").
- Quote or show the actual value found: an HTML snippet, header, meta content, measured
  number, or screenshot reference. Never assert a violation without the evidence that proves it.
- State how many pages/instances were checked, so "0 found" reads differently from "not checked."
- Label inferred claims as inferred and say why; don't present inference as direct observation.
- If the same recommendation would apply to any site regardless of what was crawled, it's not
  a finding yet, dig for the site-specific instance before including it.

Findings that fail this standard get cut before they reach `audit-data.json`, not softened.

## Scoring Weights

| Category | Weight |
|----------|--------|
| Technical SEO | 22% |
| Content Quality | 23% |
| On-Page SEO | 20% |
| Schema / Structured Data | 10% |
| Performance (CWV) | 10% |
| AI Search Readiness | 10% |
| Images | 5% |

## Report Structure

### Executive Summary
- Overall SEO Health Score (0-100)
- Business type detected
- Top 5 critical issues
- Top 5 quick wins

### Technical SEO
- Crawlability issues
- Indexability problems
- Security concerns
- Core Web Vitals status

### Content Quality
- E-E-A-T assessment
- Thin content pages
- Duplicate content issues
- Readability scores

### On-Page SEO
- Title tag issues
- Meta description problems
- Heading structure
- Internal linking gaps

### Schema & Structured Data
- Current implementation
- Validation errors
- Missing opportunities

### Performance
- LCP, INP, CLS scores
- Resource optimization needs
- Third-party script impact

### Images
- Missing alt text
- Oversized images
- Format recommendations

### AI Search Readiness
- Citability score
- Structural improvements
- Authority signals

## Priority Definitions

- **Critical**: Blocks indexing or causes penalties (fix immediately)
- **High**: Significantly impacts rankings (fix within 1 week)
- **Medium**: Optimization opportunity (fix within 1 month)
- **Low**: Nice to have (backlog)

## DataForSEO Integration (Optional)

If the DataForSEO extension is installed and its MCP tools are available, use them to enrich the audit with live data: real SERP positions, backlink profiles with spam scores, on-page analysis (Lighthouse), business listings, and AI visibility checks. This extension is not bundled -- skip silently when unavailable.

## Google API Integration (Optional)

If Google API credentials are configured (`claude-seo run google_auth.py --check`), spawn the `seo-google` agent to enrich the audit with real Google field data: CrUX Core Web Vitals (replaces lab-only estimates), GSC URL indexation status, search performance (clicks, impressions, CTR), and GA4 organic traffic trends. The Performance (CWV) category score benefits most from field data.

## Error Handling

| Scenario | Action |
|----------|--------|
| URL unreachable (DNS failure, connection refused) | Report the error clearly. Do not guess site content. Suggest the user verify the URL and try again. |
| robots.txt blocks crawling | Report which paths are blocked. Analyze only accessible pages and note the limitation in the report. |
| Rate limiting (429 responses) | Back off and reduce concurrent requests. Report partial results with a note on which sections could not be completed. |
| Timeout on large sites (500+ pages) | Cap the crawl at the timeout limit. Report findings for pages crawled and estimate total site scope. |
