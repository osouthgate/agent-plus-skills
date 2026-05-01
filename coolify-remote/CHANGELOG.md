# coolify-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.4.0 — 2026-05-01

### Added

- **`--wait` streaming progress (issue #1, Q3 row: L2 -> L3)** — `deploy --wait` now
  prints each status change to stderr as it happens (poll-with-printing every 3s) instead of
  silently blocking until a terminal state. Each stderr line is prefixed with elapsed seconds:
  `[  5s] in_progress`, `[ 42s] finished`. This gives live feedback for long deployments
  without requiring SSE support. Note: Coolify does expose SSE at
  `/api/v1/deployments/{uuid}/logs` but the endpoint only streams raw log lines with no
  structured status envelope; the polling approach surfaces the same status transitions
  the UI shows, with no added complexity.

- **Structured deployment error envelope (issue #2, Q1: L2 -> L3)** — when a deploy fails,
  stdout now receives a JSON object with structured fields instead of a raw error blob:
  - `error_code` -- short token from `status_reason` (or `"deploy_failed"`)
  - `phase` -- `"build"` | `"push"` | `"runtime"` | `"unknown"`, inferred from
    `status_reason` text and `[build]`/`[push]`/`[runtime]` markers in log entries
  - `failed_step` -- last non-empty log line before the error (capped at 200 chars)
  - `hint` -- phase-specific suggestion ("Check Dockerfile and build args...", etc.)
  - `raw_error` -- original `status_reason` string for full fidelity

- `_parse_deploy_error(dep_record)` -- public helper; extracts the structured error envelope
  from any Coolify deployment dict. Handles both dict-shaped and string-shaped log entries.

- `_wait_for_deployment(client, dep_uuid, *, timeout, poll_interval)` -- new internal
  function that replaces the inline polling loop in `_deploy_and_maybe_wait`. Returns a
  typed result dict (success / error / timeout / cancelled) with `_exit_nonzero` sentinel
  on non-success paths, matching the pattern used in `hcloud-remote`.

### Fixed

- `_deploy_and_maybe_wait` referenced `args.timeout` from outer scope (would NameError at
  runtime if called from `cmd_tls_enable` or `cmd_env_sync`). Fixed by accepting `timeout`
  as an explicit parameter with a default of 600s. All callers updated.

### Tests

- `test/test_deploy_wait.py` (18 new tests): covers `_parse_deploy_error` (8 cases),
  `_wait_for_deployment` (6 cases: happy, error, timeout, cancel, HTTP retry, dedup),
  and `_deploy_and_maybe_wait` end-to-end integration (4 cases).

### Known limitation

- `skill-plus inquire --audit` Q1 (`errors_surface`) remains at Level 2 in the static
  audit because the auditor has no CLI access to run `deployment logs` and verify its
  output shape. The static probe detects `level` field filtering in the source and
  classifies it as "filter on existing structured field (L2)". At runtime, `deployment
  logs --json` returns per-finding structured records `{level, title, message, phase,
  line, source}` which matches the L3 pattern. This is a probe/tooling gap, not a
  missing capability. The `deployment logs` subcommand is the L3 implementation for
  issue #2.

## 0.3.2 — 2026-04-30

### Changed
- `whoami` now exits rc=0 (with `status: "unconfigured"` in the JSON
  envelope) when env vars are missing, aligning with the framework's
  `_run_refresh_handler` contract — rc≠0 is reserved for genuine errors.
  Matches github-remote's existing convention.

## 0.3.1 — 2026-04-30

### Added
- `whoami` subcommand: emits `{ok, base_url, default_server, server_count}` for the configured Coolify credential. Single GET `/api/v1/servers` validates the COOLIFY_URL + COOLIFY_API_KEY pair without writes. Returns `{ok: false, error: "unconfigured", hint, configured_keys}` (rc=1) when env is missing.
- `refresh_handler` block in `plugin.json` declaring `whoami --json` as the framework's identity-emitting command, with `identity_keys: ["base_url", "default_server"]` and `failure_mode: "soft"`.

### Changed
- Bumped `agent_plus_version` floor to `>=0.7` (the version of the framework that discovers `refresh_handler` from `plugin.json`).

## 0.3.0 — 2026-04-28

### Changed
- Migrated from the `agent-plus` framework repo to the standalone `agent-plus-skills` marketplace. Install via `claude plugin marketplace add osouthgate/agent-plus-skills` then `claude plugin install coolify-remote@agent-plus-skills`.
- `plugin.json` now declares `agent_plus_version: ">=0.5"` to track the marketplace contract.

### Verified
- Envelope contract: every JSON payload carries top-level `tool: {name, version}` (via `_with_tool_meta`); `--version` exits 0 with `coolify-remote <version>`. Version is read from `plugin.json` at runtime with `"unknown"` fallback. No fixes needed — implementation already conformant.

## Unreleased

### Added
- `app exec <app> -- <cmd>` — run a shell command inside the app's running Docker container. SSH-based (to `$COOLIFY_SSH_HOST` or the host parsed from `COOLIFY_URL`) + `docker exec`, because Coolify has no REST exec endpoint — every obvious path (`/applications/{uuid}/execute`, `/exec`, `/command`, `/run`, `/terminal`, `/shell`) returns 404. The web UI's terminal is WebSocket-based, not worth wrapping. Stdout/stderr/exit-code all propagate — suitable for cron/skillify patterns. Supports `-t` for TTY, `-v` to print the ssh command. Unblocks the common "I need to run a one-off check inside the container" workflow without clicking through the Coolify UI. [2026-04-23]
- SSH env vars: `COOLIFY_SSH_HOST`, `COOLIFY_SSH_USER` (default `root`), `COOLIFY_SSH_KEY`, `COOLIFY_SSH_PORT` (default 22). [2026-04-23]

### Changed
- `app exec` picks the newest matching container deterministically (sort by `CreatedAt` desc) and warns on stderr when >1 containers match the app UUID — previously took whichever `docker ps` returned first, which could silently hit the wrong side of a blue/green rollout. Exit code 3 reserved for the multi-match warning case; exit 0 still means the remote command ran. [2026-04-23]
- `app exec` defaults to `ssh -o StrictHostKeyChecking=accept-new` so first-contact from cron doesn't hang on the fingerprint prompt. Mismatched host keys still refuse (rotation is caught). Opt out with `--ssh-strict` to use ssh's own default. [2026-04-23]

### Encoded gotchas
- **Container lookup is by name prefix, not label.** Initial implementation filtered by `label=coolify.applicationId=<uuid>` — that label doesn't exist on Coolify's application containers (only on proxy-layer services). Coolify names running containers `<app-uuid>-<timestamp>`, so `docker ps --filter name=<uuid>` matches reliably across versions.
- **No REST exec endpoint exists.** Documented in the SKILL with the 8 paths we probed, so future agents don't waste time looking.
- **Windows / Git Bash MSYS path rewriting**: absolute Linux paths in arguments get mangled (`/etc/foo` → `C:/Program Files/Git/etc/foo`) before Python sees them. SKILL documents both workarounds (`sh -c '...'` wrap or `MSYS_NO_PATHCONV=1`).

## 0.1.0 — 2026-04-23

Initial release.

### Added
- Remote CLI for Coolify PaaS over its REST API.
- `app list` / `app show <name>` — resolves apps by name, UUID, or FQDN substring.
- `env list / set / sync` — upserts (POST with PATCH fallback on 422), `--verify` reads back to catch silent no-ops, `--deploy --wait` chains a redeploy and blocks to completion.
- `domain set <app> <url>` — uses the correct `domains` field (not `fqdn`, which is read-only and 422s on PATCH).
- `tls enable <app> --domain <url>` — bundled four-step flow: PATCH domain + `is_force_https_enabled`, trigger deploy, wait for Let's Encrypt cert, HEAD the HTTPS URL as smoke test.
- `deploy <app> --wait` — polls `/api/v1/deployments/<id>` every 3s to a terminal state (`finished` / `failed` / `cancelled-by-*`). Replaces hand-rolled `until curl … | python3 -c …` loops that broke on the Windows bash shim.
- `server list` — Coolify-managed hosts.
- Layered `.env` autoloading with project-file-wins precedence (same pattern as `hermes-remote`).

### Encoded gotchas (in SKILL.md)
- **Env var propagation**: Coolify stores env on write but does not inject into running containers — redeploy required. `--verify` only checks API-level storage, not the container. Caught this after `OPENAI_API_KEY` was visible in UI but empty in container.
- `fqdn` is read-only, `domains` is writable.
- Deploy trigger is `GET /api/v1/deploy?uuid=…&force=true` (not POST).
- POST `/envs` returns 422 if key exists → wrapper auto-retries with PATCH.
