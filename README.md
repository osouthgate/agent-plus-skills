# agent-plus-skills

osouthgate's marketplace of [agent-plus](https://github.com/osouthgate/agent-plus) service wrappers — Claude Code plugins that collapse slow, multi-step API dances into one fast call across deploys, databases, cloud, billing, and logs.

This repo is a *marketplace* — a curated collection of plugins that share the agent-plus conventions (stdlib-only Python, name-resolution, `--wait` on async mutations, value-stripping on lists). The framework itself lives at [`osouthgate/agent-plus`](https://github.com/osouthgate/agent-plus); the individual service wrappers ship from here.

## Install

```bash
claude plugin marketplace add osouthgate/agent-plus-skills
claude plugin install <skill-name>@agent-plus-skills
```

Update later:

```bash
claude plugin marketplace update agent-plus-skills
```

Requires the [agent-plus framework](https://github.com/osouthgate/agent-plus) v0.5+.

## What's here today

10 plugins shipping. Each is a single-file stdlib-only Python wrapper installable on its own.

### Popular service wrappers

For tools the typical Claude Code user is likely to be hitting daily.

- **github-remote** — Read-first wrapper around the GitHub REST API for PR and CI triage.
- **linear-remote** — Read-first wrapper around the Linear GraphQL API for issue context, project triage, and turning design docs into issues. Sidesteps Linear MCP's OAuth wall via a personal API key.
- **vercel-remote** — Read-first wrapper around the Vercel REST API for project inspection and incident triage.
- **supabase-remote** — Day-to-day Supabase project ops over the Management API + local CLI (SQL exec with verification, RLS audit, type generation).
- **railway-ops** — Read-first wrapper around the Railway CLI for fast environment inspection during incident triage.
- **openrouter-remote** — Balance, usage stats, model discovery with filtering, and API key management.

### Self-hosted infra wrappers

For people running their own infra.

- **langfuse-remote** — Manage Langfuse instances (cloud or self-hosted): prompt export/import, smoke-test traces, multi-instance health, per-user activity inspection.
- **hermes-remote** — Remote CLI for a Hermes Agent deployment: cron jobs, live config, chat passthrough, env inspection.
- **coolify-remote** — Remote CLI for a Coolify PaaS instance over its REST API.
- **hcloud-remote** — Narrow Hetzner Cloud slice: list/show/reboot servers, snapshots, ssh.

## Marketplace convention

Anyone can run their own marketplace by publishing a repo named `<user>/agent-plus-skills` and tagging it with the `agent-plus-skills` topic. Discovery is by GitHub topic search; trust is per-marketplace. The framework is one repo (`agent-plus`); marketplaces are many (`*/agent-plus-skills`).

## Contributing

These plugins ship from [`osouthgate`](https://github.com/osouthgate)'s personal marketplace and are tuned to my own usage. If you want changes, fork the repo and run your own marketplace — that's the design.

## License

MIT, see [LICENSE](./LICENSE).
