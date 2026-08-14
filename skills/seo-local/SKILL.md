---
name: seo-local
description: "Local SEO and maps intelligence: Google Business Profile optimization, NAP consistency, citations, review signals and sentiment, local schema, location pages, multi-location SEO, geo-grid rank tracking, and competitor radius mapping. Use when user says local SEO, Google Business Profile, GBP, map pack, citations, reviews, or local rankings."
user-invocable: true
argument-hint: "[url | business-name]"
license: MIT
metadata:
  author: Dan Lowry
  version: "3.0.0"
  category: seo
---

# Local SEO & Maps

One skill for everything location-based. Replaces the former seo-local and
seo-maps skills, which overlapped on GBP auditing, NAP checks, and reviews.

## Process

1. Run the core local audit per `modules/local/REFERENCE.md`: GBP completeness,
   NAP consistency, citation health, review signals, local schema, location
   page quality.
2. Where API access or deeper competitive data is needed — geo-grid rank
   tracking, GBP API auditing, cross-platform review intelligence
   (Google/Tripadvisor/Trustpilot), competitor radius mapping — follow
   `modules/maps/REFERENCE.md`.

## Output

One local SEO report. Maps-derived data (grids, radius maps) appears as
evidence inside it, not as a separate deliverable.
