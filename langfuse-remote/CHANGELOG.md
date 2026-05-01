# langfuse — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.6.0 — 2026-05-01

- **Issue #4 (Q2): name/ID resolvers** — added three new subcommands so agents
  no longer need to know Langfuse UUIDs in advance:
  - `resolve-trace <id-or-name>` — resolves by UUID (direct GET) or by name
    (`?name=` filter, returns most-recent match).
  - `resolve-session <id-or-name>` — resolves by exact session ID or by
    case-insensitive ID-prefix (Langfuse sessions have no user-visible name
    field; the ID itself is the human handle).
  - `resolve-project <id-or-name>` — resolves by UUID or case-insensitive name
    from `GET /api/public/projects` (list-and-filter; no server-side filter
    available on this endpoint).
  - All resolvers follow the canonical pattern from hcloud-remote: try direct
    ID lookup first, fall back to filter/list, surface known names on miss.
  - Added `_looks_like_uuid` helper (rejects pure-numeric strings to avoid false-positive ID lookups).
- **Issue #1 (Q3): --wait on ingestion** — The Langfuse public ingestion
  endpoint (`POST /api/public/ingestion`) is fire-and-forget at the API level:
  it returns HTTP 207 immediately upon acknowledging the batch, then processes
  events asynchronously with no action-ID or job-ID in the response. There is
  nothing to poll, so `--wait` cannot be implemented faithfully and is
  intentionally absent. This is documented in the module docstring. To confirm
  a trace was indexed after `trace-ping`, use `get-traces <id>` after a short
  delay (typically < 2 s on Langfuse Cloud).

## 0.5.0 — 2026-04-28

- Added `whoami` subcommand: emits `{instances: [{name, host, default, source}], count, default}` for `agent-plus refresh` (framework 0.7+). Lists configured Langfuse instances with their hosts only — public/secret keys are never included. Soft failure: returns `{instances: [], error: ...}` and exit 0 when no instances are configured (env vars unset and config file missing). Sources: `LANGFUSE_BASE_URL/_PUBLIC_KEY/_SECRET_KEY`, prefixed env vars (`LANGFUSE_<NAME>_BASE_URL` etc.), and the JSON config file (default `~/.config/langfuse/instances.json`).
- Added `refresh_handler` block to `plugin.json` declaring `whoami --json` with `identity_keys: ["instances"]` and `failure_mode: "soft"`.
- Bumped `agent_plus_version` floor to `>=0.7`.

## 0.4.0 — 2026-04-28

- Moved to osouthgate/agent-plus-skills marketplace.
- Renamed `savedTo` envelope field to `payloadPath` (CLI output, SKILL.md, README, CHANGELOG).

## Unreleased

### Added
- `--shape-depth <1|2|3>` top-level flag — controls how deep `payloadShape` recurses in the `--output` envelope. Default is `3` (two layers — crucial for `get-traces`, reveals `traces[0].observations.length` so the agent knows the observation count without Reading). Only affects `--output`. [2026-04-24]

### Changed
- Default `payloadShape` depth is now **3** (was 1). Directly motivated by the observed `get-traces` workflow where agents wrote three inspect scripts to `/tmp/` just to count observations by name — first script guessed the wrong path (`data.observations` vs `data.traces[0].observations`), wasting a round trip. At depth=3 the shape is visible in the envelope; one `Read` + `jq` does the rest. SKILL.md rewritten with a detailed "Offloading large responses" section explaining the anti-pattern to avoid. [2026-04-24]

### Fixed
- `--output` no longer silently drops list-shaped payloads. The raw list is written to disk unchanged; the envelope reports `payloadType: "list"` + `payloadLength` instead of `payloadKeys`/`payloadShape`, plus head/tail item previews. [2026-04-24]

### Added
- `--output <path>` top-level flag (place before the subcommand) — writes the full JSON payload to disk and prints a compact envelope (`payloadPath`, `bytes`, `payloadKeys`, `payloadShape`) instead. Use for large dumps (`get-traces` over many IDs, `monitor-user` for a chatty user, full session fetches) that are wasteful to route through the model's context window. [2026-04-24]
- `payloadShape` field on the `--output` envelope — shallow type + size map for each top-level key (e.g. `{"traces": {"type": "list", "length": 1}}`) so the agent can decide which key to drill into without scanning the file. Addresses the observed workflow where agents wrote manual inspect scripts to figure out where nested arrays lived. [2026-04-24]

### Changed
- SKILL.md `allowed-tools` now includes `Bash(python3 *langfuse*:*)` alongside `Bash(langfuse:*)` for consistency with the four other plugins. Lets the skill be invoked both through the `bin/` shim and via direct `python3 path/to/langfuse …` calls without a fresh permission prompt. [2026-04-23]

## 0.1.0

Initial release.

### Added
- `export-prompts` / `import-prompts` — dump and restore all prompts with all versions, labels, tags. Preserves version identity for backup and cross-environment migration.
- `migrate-prompts --from <instance> --to <instance>` — one-shot cross-instance copy.
- `trace-ping` — send a smoke-test trace, print the trace URL.
- `health [--all]` — GET `/api/public/health` on the active or all configured instances.
- `list-instances` / `show-instance` — resolve multi-instance config.
- Named instances via env prefixes (`LANGFUSE_<NAME>_BASE_URL`, …) or JSON config at `$LANGFUSE_CONFIG`.
- `.env` autoloading, walking up from cwd, loading `LANGFUSE_*` keys without overwriting shell env. (Note: precedence here is shell-wins, unlike the newer hermes/coolify/hcloud plugins which are project-wins — kept as-is to avoid breaking existing workflows.)
