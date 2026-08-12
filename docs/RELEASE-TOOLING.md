# Release Tooling — **cmru**

vbpub builds & releases every product with **cmru** (Configurable Multi Release Utility).
One config, one CLI, `cmru.*`-named files. The normative contract is
[`cmru/docs/SPEC.md`](../cmru/docs/SPEC.md) — start at *"S-CLI — CLI at a glance"*.

> **cmru releases; ciu builds-and-runs.** If you're unsure which tool owns a build, read
> [`docs/ciu-vs-cmru.md`](ciu-vs-cmru.md) — roles, the "double bake", and the one-question
> border (*is this artifact published for external consumption?*).

## Files (all `cmru.`-prefixed)

| File | Tracked? | Purpose |
|---|---|---|
| [`cmru.toml`](../cmru.toml) | committed | The one config: github, targets, orchestration, projects. **No secrets.** |
| `cmru.secret.toml` | gitignored | Token overlay: `[github] token = "…"` (or use `$GITHUB_PUSH_PAT`). |
| [`cmru.sample.toml`](../cmru.sample.toml) | committed | Template for `cmru.toml`. |
| `<project>/cmru.build.toml` | committed | Per-project step config a project's build script consumes. |
| `<project>/cmru.vars` | gitignored | Generated `KEY=VALUE` build vars passed between steps. |
| `cmru.py` / `cmru.*.sh` | committed | Entry point + discoverable per-verb shims. |

Token resolution (SPEC S2.4): `$GITHUB_PUSH_PAT`/`$GITHUB_TOKEN` → `cmru.secret.toml` → never `cmru.toml`.

## Workflow

```bash
./cmru.status.sh                       # preview what would be released (read-only)
./cmru.release.sh                      # one-shot: detect → tag → push → build → publish
./cmru.release.sh --dry-run            # preview tags, no writes
./cmru.changelog.sh --project assay --backfill-tag assay-v0.1.0  # migrate a missed history entry
./cmru.build.sh   --project cmru       # build artifact only
./cmru.publish.sh --project cmru       # upload artifact + .sha256
./cmru.cleanup.sh --remove-assets 30d  # prune old releases/GHCR versions
./cmru.py --help                       # all verbs
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

`orchestration.project_order` in `cmru.toml` lists what `status`/`release` act on:
**ciu, cmru, nyxloom, assay, topos, modern-debian-tools-python-debug, pwmcp, tls-edge**.
Empyrion translation remains delegated and on-demand:

- **empyrion-translation** — date-tagged game asset; `./cmru.build.sh`/`./cmru.publish.sh --project empyrion-translation`.

## Notes

- GitHub credentials come from env or `cmru.secret.toml`, never committed.
- The pipeline is config-driven; no project logic is hardcoded in the orchestrator.
- **Legacy status:** the pre-cmru `release-manager/` package and the
  `release-all.py` / `release-runner.py` shims are gone. A runtime fallback for
  `release.toml` still exists, however; it conflicts with the stricter S-CLI.4 claim
  and is tracked as [CMRU KI-05](../cmru/KNOWN_ISSUES_TODO_BACKLOG.md#ki-05--s-cli4-says-legacy-release-configuration-is-removed-while-the-runtime-preserves-it--open).
  New projects must use `cmru.toml`.
