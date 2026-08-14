---
name: seo-content
description: "Content quality analysis and content brief generation. E-E-A-T assessment per Google Quality Rater Guidelines, thin content detection, readability, AI citation readiness — plus competitive content briefs with per-section word counts and page-type templates. Use when user says content quality, E-E-A-T, content audit, thin content, content brief, or writing brief."
user-invocable: true
argument-hint: "[url | brief <topic>]"
license: MIT
metadata:
  author: Dan Lowry
  version: "3.0.0"
  category: seo
---

# Content Analysis & Briefs

Two modes of one job: assessing content that exists, and briefing content
that doesn't yet. Replaces the former seo-content and seo-content-brief skills.

## Mode 1 — Analyse (default, given a URL)

Follow `modules/content/REFERENCE.md`: E-E-A-T signals, readability, depth,
thin content detection, AI citation readiness.

## Mode 2 — Brief (given `brief <topic>` or a topic/keyword)

Follow `modules/content-brief/REFERENCE.md`: competitive brief with per-section
word counts, competitor scoring, keyword guidance, page-type templates.
Supports new-page briefs and improve-existing-page briefs (URL + topic).

When a content analysis (Mode 1) surfaces gaps, offer to generate an
improvement brief (Mode 2) for the same URL — this is the intended loop.
