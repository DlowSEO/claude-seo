---
name: seo-strategy
description: "Strategic SEO planning: industry-specific strategy and roadmaps, SERP-overlap topic clustering, programmatic SEO at scale, competitor comparison/alternatives pages, and e-commerce SEO (product schema, Google Shopping). Use when user says SEO plan, SEO strategy, topic clusters, keyword clustering, programmatic SEO, comparison pages, alternatives pages, or e-commerce SEO."
user-invocable: true
argument-hint: "[plan <type> | cluster <seed> | programmatic | competitor-pages | ecommerce <url>]"
license: MIT
metadata:
  author: AgriciDaniel
  version: "3.0.0"
  category: seo
---

# SEO Strategy & Planning

Planning what to build, rather than auditing what exists. Replaces the former
seo-plan, seo-cluster, seo-programmatic, seo-competitor-pages, and
seo-ecommerce skills.

## Modes

| Mode | Module | Covers |
|---|---|---|
| `plan <type>` (default) | `modules/plan/REFERENCE.md` | Industry-specific strategy, competitive analysis, implementation roadmap |
| `cluster <seed>` | `modules/cluster/REFERENCE.md` | SERP-overlap topic clustering, hub-and-spoke architecture, internal linking |
| `programmatic` | `modules/programmatic/REFERENCE.md` | Pages at scale: templates, URL patterns, thin-content safeguards, index bloat prevention |
| `competitor-pages` | `modules/competitor-pages/REFERENCE.md` | "X vs Y" and "alternatives to X" pages, feature matrices, conversion optimization |
| `ecommerce <url>` | `modules/ecommerce/REFERENCE.md` | Product schema, Google Shopping visibility, marketplace keyword gaps |

Cluster output feeds plan; plan can invoke programmatic or competitor-pages
as implementation tracks. Chain them rather than treating modes as silos.
