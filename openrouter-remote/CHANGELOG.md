# openrouter-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.3.2 - 2026-04-30

### Changed
- `whoami` now exits rc=0 (with `status: "unconfigured"` in the JSON
  envelope) when env vars are missing, aligning with the framework's
  `_run_refresh_handler` contract — rc≠0 is reserved for genuine errors.
  Matches github-remote's existing convention.

## 0.3.1 - 2026-04-30

### Added
- `whoami` subcommand: emits `{ok, key_label, key_hash_prefix}` for the configured OpenRouter credential. Single GET `/key` returns the key's label; `key_hash_prefix` is the SHA-256 prefix of the bearer token (stable identifier, never the secret). Returns `{ok: false, error: "unconfigured", hint, configured_keys}` (rc=1) when OPENROUTER_API_KEY is missing.
- `refresh_handler` block in `plugin.json` declaring `whoami --json` as the framework's identity-emitting command, with `identity_keys: ["key_label", "key_hash_prefix"]` and `failure_mode: "soft"`.

### Changed
- Bumped `agent_plus_version` floor to `>=0.7` (the version of the framework that discovers `refresh_handler` from `plugin.json`).

## 0.3.0 - 2026-04-28

### Changed
- Migrated from the agent-plus framework repo into the [`agent-plus-skills`](https://github.com/osouthgate/agent-plus-skills) marketplace. Install via `claude plugin marketplace add osouthgate/agent-plus-skills` then `claude plugin install openrouter-remote@agent-plus-skills`. `homepage`/`repository` URLs and the `Standalone` curl snippet now point at the marketplace repo. Added `agent_plus_version: ">=0.5"` to plugin.json.
- Verified envelope contract: `tool: {name, version}` emit and `--version` flag both intact.

## Unreleased

### Added
- `models endpoints <id>` — per-provider endpoints from `GET /api/v1/models/{id}/endpoints`. Shows provider, quantization (fp16/bf16/fp8/int8/unknown), context length, `throughput_last_30m` (tokens/sec), `latency_last_30m`, `uptime_last_30m`, and per-provider input/output pricing. Sort by `throughput`, `latency`, `price-in`, `price-out`, `uptime`, `context`, or `name`. Motivation: the bulk `/models` list only exposes `top_provider`, but the same model id is often served by 5-10+ providers with different prices and quants — this is where the real routing decision lives. [2026-04-23]

### Changed
- `integrations` subcommand now prints a pointer to `https://openrouter.ai/settings/integrations` and exits non-zero. Previously shipped `list/add/remove` subverbs against `/api/v1/keys/integrations` — that endpoint does not exist (verified against <https://openrouter.ai/docs/guides/overview/auth/byok> and <https://openrouter.ai/docs/features/provisioning-api-keys>, 2026-04-23). BYOK provider keys are web-UI-only. Old CLI invocations get a clear pointer instead of a confusing 404. [2026-04-23]

## 0.1.0 — 2026-04-23

Initial release.

### Added
- `balance` — credits + usage + remaining from `GET /api/v1/credits` (user key).
- `balance --alert-below USD` — exits 1 if remaining credit drops under threshold. Designed for Hermes `[SILENT]` cron pattern: script-exit-code drives whether the agent reports anything.
- `usage` — aggregates `usage_daily/weekly/monthly/all-time` across every key, plus per-key breakdown sorted by monthly spend. Includes BYOK usage separately.
- `usage --key <hash-or-name>` / `--by-key` / `--json` variants.
- `models list` — full catalogue with composable filters: `--free`, `--provider`, `--supports tools,vision,reasoning,json`, `--min-context`, `--max-price-input/output` (in $/M tokens), `--search`, `--sort`, `--limit`.
- `models cheap` — shortcut for `list --sort price-in --limit 20`.
- `models show <id>` — resolves by exact id or unique suffix.
- `keys list/show/create/disable/enable/set-limit/rename/delete` via `/api/v1/keys*` (provisioning key).
- Keys referenceable by full hash, unambiguous hash prefix, or name/label — no copying 64-char hashes around.
- Layered `.env` autoloading with project-file-wins precedence. Scoped to `OPENROUTER_*` keys.

### Encoded gotchas (in SKILL.md)
- **Two distinct credential types**: user key (`OPENROUTER_API_KEY`) for completions + balance, provisioning key (`OPENROUTER_PROVISIONING_KEY`) for key management. Error messages steer to the correct one.
- **Meta-routers report price `-1`** as a sentinel meaning "varies"; displayed as `varies` and correctly excluded from `--free` (which requires price == 0 exactly).
- **New key strings returned only once** on creation — the wrapper prints a `save it now` warning to stderr.
- **POST `/keys/`** — trailing slash matters on some clients.
