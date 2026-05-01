# supabase-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.5.0 - 2026-05-01

### Added

- **`projects create` subcommand** — `POST /v1/projects` to create a new Supabase
  project. Required flags: `--name`, `--org-id`, `--db-pass`, `--region`. Optional:
  `--plan` (default `free`). Returns the API response JSON immediately unless `--wait`
  is passed. (Closes issue #1 Q3 row — project create L1 -> L2.)

- **`projects restore` subcommand** — `POST /v1/projects/{ref}/restore` to resume a
  paused project. Required flag: `--project`. Returns immediately unless `--wait` is
  passed. (Closes issue #1 Q3 row — project restore L1 -> L2.)

- **`--wait` / `--timeout` / `--poll-interval`** flags on both `projects create` and
  `projects restore`. When `--wait` is set, the command blocks and polls
  `GET /v1/projects/{ref}` on the given interval (default 10 s) until one of three
  terminal outcomes:
  - **success** — project status reaches `ACTIVE_HEALTHY`; emits final project JSON, exit 0.
  - **error** — status reaches a failure state (`INIT_FAILED`, `RESTORE_FAILED`,
    `INACTIVE`, `UNKNOWN`); emits result JSON, exit 1.
  - **timeout** — `--timeout` seconds elapsed (default 600 s) without a terminal
    state; emits result JSON, exit 1.

- **`wait_project_active(ref, *, timeout, poll_interval, debug)`** internal helper —
  the canonical polling implementation, testable in isolation. Mirrors the `wait_action`
  shape from `hcloud-remote`.

- DB migrations via `supabase db query` (the `sql` / `sql-inline` subcommands) are
  **synchronous** at the Supabase API level — the CLI blocks until the migration
  completes and returns results directly. No `--wait` added because there is no async
  operation to poll.

## 0.4.0 - 2026-04-28

### Added
- `whoami` subcommand: emits `{linked_project_ref, projects, count}` for the configured Supabase credential. Used by `agent-plus refresh` (framework 0.7+) to populate `services.supabase-remote.identity`. Soft failure: returns the same shape with `linked_project_ref: null` + `projects: []` + `error: ...` and exit 0 when `SUPABASE_ACCESS_TOKEN` is missing or the Management API call errors. `linked_project_ref` reads `./supabase/.temp/project-ref` (the supabase-link convention) walking up from cwd.
- `refresh_handler` block in `plugin.json` declaring `whoami --json` as the framework's identity-emitting command, with `identity_keys: ["linked_project_ref", "projects"]` and `failure_mode: "soft"`.

### Changed
- Bumped `agent_plus_version` floor to `>=0.7`.

## 0.3.0 - 2026-04-28

### Changed
- Migrated from the agent-plus framework repo into the [`agent-plus-skills`](https://github.com/osouthgate/agent-plus-skills) marketplace. Install via `claude plugin marketplace add osouthgate/agent-plus-skills` then `claude plugin install supabase-remote@agent-plus-skills`. `homepage`/`repository` URLs and the `Standalone` curl snippet now point at the marketplace repo. Added `agent_plus_version: ">=0.5"` to plugin.json.
- Verified envelope contract: `tool: {name, version}` emit and `--version` flag both intact.

## Unreleased

### Changed
- **Scrubbed customer-specific references** from the bin docstring, README, and SKILL.md. Previous text named a specific downstream consumer ("Rainshift"/"Rayna") in prose callouts and in the env-file precedence list. Replaced with generic language that communicates the same design rule — this plugin stays generic; domain-specific helpers belong in the consuming project's own repo and shell out to `sql-inline`. [2026-04-24]

### Removed
- **Dropped the `$CWD/rayna-setup/.env.local` step from the env-file precedence chain.** This was a customer-specific hardcoded directory that shouldn't ship in a public plugin. Consumers that relied on it have three equivalent migration paths: (a) rename `rayna-setup/.env.local` → `rayna-setup/.env` and it'll be picked up by the cwd-walking step, (b) symlink or copy it to `./.env.local` at the project root, or (c) pass `--env-file rayna-setup/.env.local` explicitly. Technically a breaking change for that one consumer, not for anyone else. [2026-04-24]

## 0.1.0

Initial release.

### Added
- `projects list` — Supabase Management API, resolves by name later in other commands.
- `sql <file> [--verify-rows N]` — applies a SQL file via `supabase db query --linked` or `psql` if `SUPABASE_DB_URL` is set. `--verify-rows` asserts a target row count in the same call.
- `sql-inline <query>` — one-shot read query, strips the agent-mode "untrusted data" envelope that `supabase db query` emits so downstream pipelines see plain JSON.
- `rls-audit` — one call, every table in the public schema, RLS enabled/disabled + policy count per table. Turns "is anything dangerously exposed?" from an investigation into a boolean check.
- `gen-types <outfile>` — wraps the multi-flag `supabase gen types typescript --project-id ... --schema ...` dance into one command.
- Project refs (20-char opaque strings) never appear in commands once `SUPABASE_PROJECT_REF` is set as a default.
- Layered `.env` autoloading with project-file-wins precedence. Scoped to `SUPABASE_*` keys.

### Encoded gotchas (in SKILL.md)
- **The agent-envelope gotcha**: `supabase db query` detects agent invocation and wraps results in a JSON envelope with an "untrusted data" preamble. If Claude doesn't know the preamble is there, parsing produces garbage. `sql` / `sql-inline` strip it server-side.
