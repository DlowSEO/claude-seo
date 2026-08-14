# Claude SEO Assistant

A simplified SEO toolkit for Claude Code. A cleaner fork of [claude-seo](https://github.com/AgriciDaniel/claude-seo) by Daniel Agrici (MIT).
Same analysis depth, a fraction of the surface area.

**8 commands. 8 skills. 8 agents. Zero bundled extensions.**

The original grew to 25 sub-skills, 18 agents, and 30+ commands — and the most
common piece of user feedback was that it had become confusing to navigate.
This build collapses overlapping skills into single entry points and cuts the
long tail, without losing the underlying analysis content (merged skills keep
their source material as internal modules).

## Install

```bash
git clone --depth 1 https://github.com/DlowSEO/claude-seo.git
bash claude-seo/install.sh
```

Requires Python 3.10+, Git, and Claude Code.

## Commands

| Command | What it does |
|---|---|
| `/seo audit <url>` | Full site audit: crawl, parallel specialists, health score, action plan |
| `/seo analyse <url>` | Page + technical analysis (`--focus technical\|schema\|sitemap\|images\|hreflang`) |
| `/seo content <url>` | E-E-A-T content analysis; `content brief <topic>` for briefs |
| `/seo local <url>` | Local SEO: GBP, NAP, citations, reviews, geo-grid |
| `/seo geo <url>` | AI Overviews / ChatGPT / Perplexity readiness |
| `/seo backlinks <url>` | Backlink profile via free APIs |
| `/seo strategy [mode]` | Planning: plan, cluster, programmatic, competitor-pages, ecommerce |
| `/seo google [cmd]` | GSC, PageSpeed Insights, CrUX, GA4, Indexing APIs |

## What changed vs claude-seo v2.x

**Merged** — page, technical, schema, sitemap, images, hreflang → `analyse` ·
content-brief → `content` · maps → `local` · plan, cluster, programmatic,
competitor-pages, ecommerce → `strategy`

**Cut** — flow, sxo, drift, image-gen, and all bundled extensions. The
extension mechanism remains; nothing installs by default. These features
live on in the [original repo](https://github.com/DlowSEO/claude-seo).

MIT licensed, like the original.
