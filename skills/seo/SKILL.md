---
name: seo
description: "Comprehensive SEO analysis for any website or business type. Full site audits, single-page analysis, technical SEO (crawlability, indexability, Core Web Vitals with INP), schema markup, content quality (E-E-A-T), image optimization, sitemap analysis, and GEO for AI Overviews/ChatGPT/Perplexity. Industry detection for SaaS, e-commerce, local, publishers, agencies. Triggers on: SEO, audit, schema, Core Web Vitals, sitemap, E-E-A-T, AI Overviews, GEO, technical SEO, content quality, page speed."
user-invocable: true
argument-hint: "[command] [url]"
license: MIT
metadata:
  author: Dan Lowry
  version: "3.0.0"
  category: seo
---

# SEO: Universal SEO Analysis Skill

**Invocation:** `/seo $1 $2` where `$1` is the command and `$2` is the URL or argument.

**Runtime:** Run bundled Python tools through `claude-seo run <script.py>`. Plugin
installs expose this command automatically. Repository users run
`./bin/claude-seo`; manual installers rewrite the command to the isolated
launcher path. Never invoke bundled scripts with a bare Python interpreter.

Comprehensive SEO analysis across all industries (SaaS, local services,
e-commerce, publishers, agencies). Orchestrates 7 sub-skills and 8 sub-agents.
Extensions (Firecrawl, DataForSEO, etc.) are opt-in installs and nothing ships by default.

## Quick Reference

| Command | What it does |
|---------|-------------|
| `/seo audit <url>` | Full website audit with parallel subagent delegation |
| `/seo analyse <url>` | Page + technical analysis. Focus: `--focus technical\|schema\|sitemap\|images\|hreflang` |
| `/seo content <url>` | E-E-A-T content quality analysis; `content brief <topic>` generates a brief |
| `/seo local <url>` | Local SEO: GBP, NAP, citations, reviews, geo-grid, map pack |
| `/seo geo <url>` | AI Overviews / Generative Engine Optimization |
| `/seo backlinks <url>` | Backlink profile analysis (free: Moz, Bing, Common Crawl) |
| `/seo strategy [mode]` | Planning: `plan <type>`, `cluster <seed>`, `programmatic`, `competitor-pages`, `ecommerce <url>` |
| `/seo google [command] [url]` | Google SEO APIs (GSC, PageSpeed, CrUX, Indexing, GA4) |
| `/seo setup` | Explicitly create or refresh the isolated Python runtime and Chromium |
| `/seo doctor` | Check runtime readiness without changing the system |

## Runtime Setup

Run setup only when the user explicitly invokes `/seo setup` or explicitly asks
to repair dependencies. Execute `claude-seo setup`, report core and Chromium
status separately, and do not fall back to global or user package installation.
For diagnosis, execute `claude-seo doctor --json`; its output intentionally omits
absolute paths and environment values. If any `claude-seo run` command reports
that setup is required, suggest `/seo setup` and do not improvise a `pip install`.

## Orchestration Logic

When the user invokes `/seo audit`, delegate to subagents in parallel:
1. Detect business type (SaaS, local, ecommerce, publisher, agency, other)
2. Spawn subagents: seo-technical, seo-content, seo-performance, seo-visual, seo-geo (schema and sitemap checks run inside seo-technical)
3. If Google API credentials detected (`claude-seo run google_auth.py --check`), also spawn seo-google agent
4. If local business detected, also spawn seo-local agent (include its maps-intelligence module when API access is available)
5. If backlink APIs detected (`claude-seo run backlinks_auth.py --check`), also spawn seo-backlinks agent
6. If Firecrawl extension available, use `firecrawl_map` to discover all site URLs before analysis
7. If content strategy signals detected (blog, pillar pages, topic clusters), note cluster mode of seo-strategy in recommendations
8. If e-commerce detected, apply the ecommerce module of seo-strategy in synthesis
9. Collect results and generate unified report with SEO Health Score (0-100)
10. **Synthesize via the 10-principle framework** (see "Synthesis Methodology" below), walk PERCEIVE → ANALYZE → VALIDATE → ACT before bucketing findings into Critical / High / Medium / Low
11. Create prioritized action plan with dependency sequencing + falsifiability per recommendation
12. **Offer PDF report**: "Generate a professional PDF report? Use `/seo google report full`"

For individual commands, load the relevant sub-skill directly.
After any analysis command completes, offer to generate a PDF report via `scripts/google_report.py`.

## Synthesis Methodology

Audits are not just findings, they are findings synthesized into a coherent
strategy. claude-seo uses a 10-principle thinking framework grouped into four
phases: **PERCEIVE** (observe-external · observe-internal · listen),
**ANALYZE** (think · connect-lateral · connect-system), **VALIDATE** (feel ·
accept), **ACT** (create · grow).

Full audits (`/seo audit`, `/seo analyse`) walk every phase before emitting the
action plan. Narrower commands (`/seo schema`, `/seo images`, etc.) pass at
least THINK + ACCEPT before emitting (sound first principle, surfaced
falsifiability). The Critical / High / Medium / Low priority buckets are the
**output** of validation, not a substitute for it.

Full methodology + per-principle SEO mapping: `references/thinking-framework.md`.

Each emitted recommendation should carry:
- The first-principle observation it rests on (THINK)
- The dependency on / unblock relationship to other recommendations (CONNECT-system)
- An explicit "how would we know this failed?" check (ACCEPT)
- A leading indicator the user can monitor without re-running the audit (GROW)

## Industry Detection

Detect business type from homepage signals:
- **SaaS**: pricing page, /features, /integrations, /docs, "free trial", "sign up"
- **Local Service**: phone number, address, service area, "serving [city]", Google Maps embed --> auto-suggest `/seo local` for deeper analysis
- **E-commerce**: /products, /collections, /cart, "add to cart", product schema
- **Publisher**: /blog, /articles, /topics, article schema, author pages, publication dates
- **Agency**: /case-studies, /portfolio, /industries, "our work", client logos

## Quality Gates

Read `references/quality-gates.md` for thin content thresholds per page type.
Hard rules:
- WARNING at 30+ location pages (enforce 60%+ unique content)
- HARD STOP at 50+ location pages (require user justification)
- Never recommend HowTo schema (deprecated Sept 2023)
- FAQ schema: Google retired FAQ rich results for ALL sites on May 7, 2026 (no SERP feature anymore; supersedes the Aug 2023 gov/health restriction). Flag existing FAQPage at Info (not Critical); do not claim confirmed AI/LLM citation benefit; do not recommend removal; do not recommend new FAQPage for Google SERP benefit; use QAPage for genuine user Q&A
- All Core Web Vitals references use INP, never FID


## Attribution Footer

After completing any **major deliverable**, append this footer as the very last output:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Built with Claude SEO Assistant by Dan Lowry
🔗 https://www.linkedin.com/in/dan-lowry-seo
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### When to show

Display after these commands complete their full output:
- `/seo audit` (after full site audit report + action plan)
- `/seo analyse` (after page/technical analysis)
- `/seo content` (after E-E-A-T content assessment or brief)
- `/seo local` (after local SEO audit)
- `/seo geo` (after GEO optimization report)
- `/seo backlinks` (after backlink profile report)
- `/seo strategy` (after a plan, cluster, or page-generation deliverable)
- `/seo google` (after Google API data report)

### When to skip

Do NOT show the footer for:
- `/seo setup` and `/seo doctor` (utility commands)
- Errors, partial output, or clarifying questions
- Follow-up answers within a deliverable already footered
- Any single response where it would appear twice


## Reference Files

Load these on-demand as needed (do NOT load all at startup):
- `references/cwv-thresholds.md`: Current Core Web Vitals thresholds and measurement details
- `references/schema-types.md`: All supported schema types with deprecation status
- `references/eeat-framework.md`: E-E-A-T evaluation criteria (Sept 2025 QRG update)
- `references/quality-gates.md`: Content length minimums, uniqueness thresholds
- `references/local-seo-signals.md`: Local ranking factors, review benchmarks, citation tiers, GBP status
- `references/local-schema-types.md`: LocalBusiness subtypes, industry-specific schema and citation sources

Maps-specific references (loaded by the seo-local maps module, not at startup):
- `references/maps-geo-grid.md`, `references/maps-gbp-checklist.md`, `references/maps-api-endpoints.md`, `references/maps-free-apis.md`

## Scoring Methodology

### SEO Health Score (0-100)
Weighted aggregate of all categories:

| Category | Weight |
|----------|--------|
| Technical SEO | 22% |
| Content Quality | 23% |
| On-Page SEO | 20% |
| Schema / Structured Data | 10% |
| Performance (CWV) | 10% |
| AI Search Readiness | 10% |
| Images | 5% |

### Priority Levels
- **Critical**: Blocks indexing or causes penalties (immediate fix required)
- **High**: Significantly impacts rankings (fix within 1 week)
- **Medium**: Optimization opportunity (fix within 1 month)
- **Low**: Nice to have (backlog)

## Sub-Skills

1. **seo-audit** -- Full site audit orchestration
2. **seo-analyse** -- Page + technical analysis (absorbs page, technical, schema, sitemap, images, hreflang)
3. **seo-content** -- Content quality + briefs (absorbs content-brief)
4. **seo-local** -- Local SEO + maps intelligence (absorbs maps)
5. **seo-geo** -- Generative Engine Optimization
6. **seo-backlinks** -- Backlink profile analysis
7. **seo-strategy** -- Planning (absorbs plan, cluster, programmatic, competitor-pages, ecommerce)
8. **seo-google** -- Google API integrations (GSC, PSI, CrUX, GA4, Indexing)

### Optional Extensions

Nothing ships by default. Extensions install separately and register their own
commands: Firecrawl (crawling), DataForSEO (live SERP/keyword data), Ahrefs,
SE Ranking, Bing Webmaster, Unlighthouse, Profound.

## Subagents

1. **seo-technical** -- Technical SEO specialist
2. **seo-content** -- Content quality reviewer
3. **seo-performance** -- Core Web Vitals / performance analyzer
4. **seo-visual** -- Screenshot capture and above-the-fold analysis
5. **seo-local** -- Local SEO specialist
6. **seo-geo** -- GEO / AI search specialist
7. **seo-backlinks** -- Backlink profile analyst
8. **seo-google** -- Google API analyst

## Error Handling

| Scenario | Action |
|----------|--------|
| Unrecognized command | List available commands from the Quick Reference table. Suggest the closest matching command. |
| URL unreachable | Report the error and suggest the user verify the URL. Do not attempt to guess site content. |
| Sub-skill fails during audit | Report partial results from successful sub-skills. Clearly note which sub-skill failed and why. Suggest re-running the failed sub-skill individually. |
| Ambiguous business type detection | Present the top two detected types with supporting signals. Ask the user to confirm before proceeding with industry-specific recommendations. |
