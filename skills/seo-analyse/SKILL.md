---
name: seo-analyse
description: "Single-URL and technical SEO analysis: on-page elements, technical SEO (crawlability, indexability, security, mobile, Core Web Vitals, JS rendering), schema detection/validation/generation, XML sitemaps, image optimization, and hreflang/international SEO. Use when user says analyze this page, check page SEO, technical SEO, schema, structured data, sitemap, image SEO, or hreflang."
user-invocable: true
argument-hint: "[url] [--focus technical|schema|sitemap|images|hreflang]"
license: MIT
metadata:
  author: AgriciDaniel
  version: "3.0.0"
  category: seo
---

# Page & Technical SEO Analysis

One entry point for inspecting what already exists on a site. Replaces the former
seo-page, seo-technical, seo-schema, seo-sitemap, seo-images, and seo-hreflang skills.

## Default behaviour

Given a URL with no focus flag, run the combined single-page analysis:
on-page elements, technical checks, schema, images, and performance —
following `modules/page/REFERENCE.md` as the process guide, pulling depth
from the other modules as findings warrant.

## Focus modes

When the user names a specific area (or passes `--focus`), follow the
corresponding module directly:

| Focus | Module | Covers |
|---|---|---|
| `technical` | `modules/technical/REFERENCE.md` | 9-category technical audit: crawlability, indexability, security, URL structure, mobile, CWV, structured data, JS rendering, IndexNow |
| `schema` | `modules/schema/REFERENCE.md` | Schema.org detection, validation against Google requirements, JSON-LD generation |
| `sitemap` | `modules/sitemap/REFERENCE.md` | XML sitemap analysis and generation with industry templates |
| `images` | `modules/images/REFERENCE.md` | Alt text, formats, responsive images, lazy loading, CLS prevention |
| `hreflang` | `modules/hreflang/REFERENCE.md` | Hreflang audit, validation, generation; international SEO |

## Output

Single report, sections ordered by severity (Critical / High / Medium / Low),
with evidence for every finding. Do not emit separate per-module reports.
