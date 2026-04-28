# hcloud-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.3.0 — 2026-04-28

### Changed
- Migrated from the `agent-plus` framework repo to the `agent-plus-skills` marketplace repo. Install path is now `claude plugin marketplace add osouthgate/agent-plus-skills` + `claude plugin install hcloud-remote@agent-plus-skills`.
- `plugin.json` declares `agent_plus_version: ">=0.5"`.

### Verified
- Envelope contract confirmed: every dict JSON payload carries top-level `tool: {name, version}` (version read from `plugin.json` at runtime, fallback `"unknown"`); `--version` exits 0 with `hcloud-remote <version>`. No code fixes needed.

## 0.1.0 — 2026-04-23

Initial release.

### Added
- Minimal CLI for Hetzner Cloud API.
- `server list / show / reboot` — resolves by name or numeric id.
- `snapshot create / list` — filter list by `--server <name>` (client-side, since Hetzner's `bound_to` filter only matches currently-attached snapshots).
- `ssh <name>` — resolves public IPv4 and execs `ssh` (subprocess on Windows, execvp on Unix). Extra ssh args pass through after `--`.
- Layered `.env` autoloading with project-file-wins precedence. Picks up `HCLOUD_*` and `HETZNER_*` keys.

### Why this exists
- On Windows, `curl | python3 -c "..."` to parse Hetzner JSON reliably gets mangled by the bash shim (stray `||` parsed as `goto :error`). This wrapper parses in-process so `hcloud-remote server show <name>` just works.
- Scope deliberately narrow: no volumes, networks, LBs, firewalls, floating IPs, image management, or server create/destroy. If any of those become recurring needs, reach for Terraform — don't expand this.
