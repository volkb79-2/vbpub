# AI agent tool discovery

MDT should tell coding agents what is already available before they install a
second copy of a tool or choose a slower workaround. The durable information
belongs in the image inventory; repository instruction files should only point
at that inventory and describe project-specific policy.

## Recommended consumer-repository layout

Use `AGENTS.md` as the canonical, short shared file:

```text
AGENTS.md       canonical shared instructions
CLAUDE.md       contains: @AGENTS.md
```

Codex and OpenCode discover repository `AGENTS.md` files. Reasonix exposes
`AGENTS.md` as project memory. Claude Code's native project file is
`CLAUDE.md`; its import syntax lets the adapter contain only `@AGENTS.md`.
Projects may still add tool-specific instructions, but shared build/test/tool
facts should have one source.

Start from [`templates/AGENTS.md`](../templates/AGENTS.md). Keep it concise:

- list the important capability groups, not every transitive package;
- tell the agent how to locate the live inventory;
- record repository build, test and safety constraints;
- do not treat an installed executable as permission to use it;
- do not duplicate exact versions that the image generates at build time.

## Authoritative in-image data

The human-readable release inventory is advertised by `/etc/os-release`:

```bash
manifest=$(sed -n 's/^IMAGE_MANIFEST=//p' /etc/os-release)
sed -n '1,220p' "$manifest"
```

The fuller installed-tool snapshot is:

```text
/usr/local/share/modern-debian-tools-python-debug/installed-tools-manifest.md
```

The canonical release manifest contains the curated first-party wheels, AI
CLIs, inspection/security tools, runtimes and selected system packages. The
installed-tool snapshot adds the Python environment and lower-level package
inventory. These files describe availability; the repository's `AGENTS.md`
describes when and how a tool may be used.

## Why the image does not install a global project `AGENTS.md`

Project instruction discovery is rooted in the repository or agent home, and
the precedence rules differ between CLIs. A file under `/usr/local/share` is
therefore stable documentation but is not automatically loaded as a project
instruction. Writing a global instruction file into every CLI's persisted home
would also override a user's personal policy and could become stale across
image upgrades.

MDT instead ships an example and authoritative inventories. Consumer repos opt
in by committing their small adapters. This makes the instruction visible in
code review and keeps permissions and test commands owned by the project.

## Maintenance contract

When the curated toolset changes:

1. update the image resolver/install source of truth;
2. update manifest probes and tests so the generated inventory proves the tool
   exists and records the resolved version;
3. update the stable capability summary in the template only when a category
   changes materially;
4. never paste a generated version table into `AGENTS.md` or `CLAUDE.md`.

## References

- [Codex custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md.md)
- [Claude Code project memory and imports](https://code.claude.com/docs/en/memory)
- [OpenCode rules and AGENTS.md discovery](https://opencode.ai/docs/rules)
- [Reasonix documentation](https://reasonix.io/docs/)
- [AGENTS.md interoperability project](https://agents.md/)
