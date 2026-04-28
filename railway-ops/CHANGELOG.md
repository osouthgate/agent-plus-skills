# railway-ops - changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.5.0 - 2026-04-28

### Changed
- Migrated from the agent-plus framework repo into the [`agent-plus-skills`](https://github.com/osouthgate/agent-plus-skills) marketplace. Install via `claude plugin marketplace add osouthgate/agent-plus-skills` then `claude plugin install railway-ops@agent-plus-skills`. `homepage`/`repository` URLs and the `Standalone` curl snippet now point at the marketplace repo. Added `agent_plus_version: ">=0.5"` to plugin.json.
- Renamed `savedTo` → `payloadPath` across bin, SKILL.md, README, CHANGELOG, and tests (5 occurrences) for consistency with the rest of the marketplace.
- Verified envelope contract: `tool: {name, version}` emit and `--version` flag both intact.

