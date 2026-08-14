# CLAUDE.md — Claude SEO Assistant Contributor Guide

## Architecture

- `skills/seo/` — orchestrator. Routes `/seo <command>` to sub-skills.
- `skills/seo-*/SKILL.md` — one entry point per skill. Merged skills keep
  their source material in `modules/<name>/REFERENCE.md` — these are loadable
  references, NOT auto-discovered skills. Never add a SKILL.md inside modules/.
- `agents/` — 8 subagents used by `/seo audit` parallel delegation.
- `scripts/` — Python tools invoked via `claude-seo run <script.py>`.
- `hooks/` — schema validation hooks.

## Rules

1. One command per user-recognisable job. Depth goes in flags/modes, not new
   top-level commands.
2. New functionality lands as a module inside an existing skill first. A new
   top-level skill needs a job name a non-SEO developer would recognise.
3. Extensions never ship by default.
4. Every finding in reports needs evidence. Health score is a triage
   heuristic, not a Google metric.

## Testing

`python3 -m pytest tests/` — note some inherited tests reference removed
features and need pruning (see TODO below).

## TODO after fork

- Prune/adapt inherited tests referencing removed skills
- Re-record demo media
- Prune remaining DataForSEO references if the extension is never revived
