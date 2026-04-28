# Changelog

## Unreleased

- **Drop `agent_plus_refresh_handler: true` from per-skill entries.** Redundant with `agent-plus` 0.7.0's plugin-manifest discovery, which reads `refresh_handler` blocks straight from each plugin's `.claude-plugin/plugin.json` rather than relying on a marketplace flag. Removed from `github-remote`, `linear-remote`, `railway-ops`, `supabase-remote`, `vercel-remote`. No skill currently declares a `refresh_handler` block in its plugin.json — adding those (and the corresponding `whoami --json` subcommands) is a follow-up task. Until then, `agent-plus refresh` simply finds no handlers from this marketplace, which is the correct behavior per the new contract (silently skip plugins without declared handlers).

## 0.1.0 — 2026-04-28

- Initial marketplace scaffold (Phase 1). `marketplace.json`, README, LICENSE, .gitignore in place; `skills` array empty pending Tier 3 migration from the agent-plus framework repo.
