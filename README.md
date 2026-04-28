# agent-plus-skills

osouthgate's marketplace of [agent-plus](https://github.com/osouthgate/agent-plus) service wrappers — Claude Code plugins that collapse slow, multi-step API dances into one fast call across deploys, databases, cloud, billing, and logs.

This repo is a *marketplace* — a curated collection of plugins that share the agent-plus conventions (stdlib-only Python, name-resolution, `--wait` on async mutations, value-stripping on lists). The framework itself lives at [`osouthgate/agent-plus`](https://github.com/osouthgate/agent-plus); this repo is where the individual service wrappers ship.

## Install

Once published:

```bash
claude plugin marketplace add osouthgate/agent-plus-skills
claude plugin install <skill-name>@agent-plus-skills
```

Update later:

```bash
claude plugin marketplace update agent-plus-skills
```

## Marketplace convention

Anyone can run their own marketplace by publishing a repo named `<user>/agent-plus-skills` and tagging it with the `agent-plus-skills` topic. Discovery is by GitHub topic search; trust is per-marketplace. The framework is one repo (`agent-plus`), marketplaces are many (`*/agent-plus-skills`).

## Status — Phase 1

This marketplace is bootstrapping. Migration plan:

- **Now (Phase 1):** 4 Tier 3 plugins (the leaf, single-file wrappers) move from the framework repo into this marketplace.
- **After extension API lands:** 6 Tier 2 plugins follow once the framework exposes the hooks they need.

Track progress in [`osouthgate/agent-plus`](https://github.com/osouthgate/agent-plus).

## License

MIT, see [LICENSE](./LICENSE).
