# supabase-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

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
