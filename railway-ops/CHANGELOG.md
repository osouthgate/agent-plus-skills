# railway-ops - changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.6.0 - 2026-04-28

### Added
- `whoami` subcommand: emits `{project, environment, service, projects[]}` for the configured Railway CLI session by shelling out to `railway status --json` and `railway list --json`. Used by `agent-plus refresh` (framework 0.7+) to populate `services.railway-ops.identity`. Soft failure: returns the same shape with nulls + `error: ...` and exit 0 when the CLI is missing, the user is not logged in, or no project is linked. Differs from the existing `status` subcommand which `die()`s on the same conditions.
- `refresh_handler` block in `plugin.json` declaring `whoami --json` as the framework's identity-emitting command, with `identity_keys: ["project", "environment", "service"]` and `failure_mode: "soft"`. `timeout_seconds: 15` is slightly longer than the default 10 because `railway list --json` does a server round-trip.

### Changed
- Bumped `agent_plus_version` floor to `>=0.7`.

## 0.5.0 - 2026-04-28

### Changed
- Migrated from the agent-plus framework repo into the [`agent-plus-skills`](https://github.com/osouthgate/agent-plus-skills) marketplace. Install via `claude plugin marketplace add osouthgate/agent-plus-skills` then `claude plugin install railway-ops@agent-plus-skills`. `homepage`/`repository` URLs and the `Standalone` curl snippet now point at the marketplace repo. Added `agent_plus_version: ">=0.5"` to plugin.json.
- Renamed `savedTo` → `payloadPath` across bin, SKILL.md, README, CHANGELOG, and tests (5 occurrences) for consistency with the rest of the marketplace.
- Verified envelope contract: `tool: {name, version}` emit and `--version` flag both intact.

