# Commands

Eight commands. Depth lives in flags and modes, not the top-level menu.

| Command | What it does |
|---|---|
| `/seo audit <url>` | Full site audit: crawl, parallel specialists, health score, prioritized action plan |
| `/seo analyse <url>` | Single-page + technical analysis. Focus modes: `--focus technical\|schema\|sitemap\|images\|hreflang` |
| `/seo content <url>` | E-E-A-T content quality analysis. `content brief <topic>` generates a competitive brief |
| `/seo local <url>` | Local SEO: GBP, NAP, citations, reviews, geo-grid rank tracking |
| `/seo geo <url>` | Generative Engine Optimization: AI Overviews, ChatGPT, Perplexity readiness |
| `/seo backlinks <url>` | Backlink profile: toxic links, anchor distribution, competitor gap (free APIs) |
| `/seo strategy [mode]` | Planning: `plan <type>`, `cluster <seed>`, `programmatic`, `competitor-pages`, `ecommerce <url>` |
| `/seo google [cmd] <url>` | Google APIs: GSC, PageSpeed Insights, CrUX, GA4, Indexing |

## Removed vs claude-seo v2.x

Merged: page/technical/schema/sitemap/images/hreflang → `analyse` · content-brief → `content` · maps → `local` · plan/cluster/programmatic/competitor-pages/ecommerce → `strategy`

Cut: flow, sxo, drift, image-gen, dataforseo, and all bundled extensions (extension mechanism retained; nothing ships by default).
