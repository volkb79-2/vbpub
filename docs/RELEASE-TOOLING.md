# Release Tooling — **cmru**

vbpub builds & releases every product with **cmru** (Configurable Multi Release Utility).
One config, one installed CLI. The normative contract is
[`cmru/docs/SPEC.md`](../cmru/docs/SPEC.md) — start at *"S-CLI — CLI at a glance"*.

> **cmru releases; ciu builds-and-runs.** If you're unsure which tool owns a build, read
> [`docs/ciu-vs-cmru.md`](ciu-vs-cmru.md) — roles, the "double bake", and the one-question
> border (*is this artifact published for external consumption?*).

## Files

| File | Tracked? | Purpose |
|---|---|---|
| `cmru.orchestration.toml` | committed | Estate selection, dependency order, and cleanup. No project commands. |
| `cmru.secret.toml` | gitignored | Repository credential: `[github] token = "…"`. |
| `<project>/cmru.secret.toml` | gitignored | Optional project override; deep-merged over the root secret. |
| [`cmru.project.sample.toml`](../cmru.project.sample.toml) | committed | Template for a portable project `cmru.toml`. |
| `<project>/cmru.toml` | committed | Complete project release and step contract. |
| [`cmru.orchestration.toml`](../cmru.orchestration.toml) | committed | Estate order/dependencies/cleanup only. |
| `<project>/cmru.vars` | gitignored | Generated `KEY=VALUE` build vars passed between steps. |
| `cmru` console script | installed | Canonical entry point for every verb. |
| [`cmru.release.sh`](../cmru.release.sh) | committed | Convenience wrapper for the complete estate release. |
| [`cmru/build-initial-standalone.sh`](../cmru/build-initial-standalone.sh) | committed | Fresh-checkout CMRU bootstrap. |

Token resolution (SPEC S2.4): `$GITHUB_PUSH_PAT`/`$GITHUB_TOKEN` → deep merge root
and selected project secret files. A committed `cmru.toml` credential is rejected.

## Workflow

```bash
cmru status                             # preview what would be released (read-only)
./cmru.release.sh                      # one-shot: detect → tag → push → build → publish
./cmru.release.sh --dry-run            # preview tags, no writes
cmru changelog --project assay --backfill-tag assay-v0.1.0  # migrate a missed history entry
cmru build --project cmru             # retained isolated gate + build_step; no publish
cmru publish --project cmru           # run the declared push step
cmru cleanup --remove-assets 30d      # prune old releases/GHCR versions
cmru --help                           # all verbs
```

Versions are SemVer from git tags via setuptools-scm (see [VERSIONING.md](VERSIONING.md));
untagged commits build as `X.Y.Z.devN+g<sha>`. Retained transactions are the recovery path;
the advertised post-tag resume behavior is currently an open
[CMRU KI-06](../cmru/KNOWN_ISSUES_TODO_BACKLOG.md#ki-06--retained-release-resume-does-not-satisfy-the-documented-already-tagged-idempotency--open),
so preserve and inspect a failed worktree rather than assuming a bare re-run will republish a tag.

## Automatic source-first history

Each CMRU-managed project receives `CHANGES.md` automatically; no project-level
opt-in, script, or starter file is needed. The release transaction writes and commits
the entry before its isolated gate. Tagged releases use their version as the heading;
image-only and delegated flows use a source-revision heading plus a persisted source
cursor. `release.changelog` is only for an alternate project-relative path or the
explicit `false` opt-out. See [`cmru/README.md`](../cmru/README.md) for the one-time
backfill command when a tag predates this policy.

## Auto-released set vs. on-demand

`orchestration.project_order` in `cmru.orchestration.toml` lists what `status`/`release` act on:
**ciu, cmru, nyxloom, assay, topos, modern-debian-tools-python-debug, pwmcp, tls-edge**.
Empyrion translation remains delegated and on-demand:

- **empyrion-translation** — portable on-demand project contract; invoke from its directory,
  or add it to orchestration only after reviewing its release policy.

## Notes

- GitHub credentials come from env, repository-root `cmru.secret.toml`, or an
  explicit project-local overlay; they are never committed.
- The pipeline is config-driven; no project logic is hardcoded in the orchestrator.
- **Strict configuration:** CMRU accepts only a project `cmru.toml` or an explicit
  `cmru.orchestration.toml` selected with `--config`. Old config filenames, aliases, and
  shell-sourced runner settings are rejected.
