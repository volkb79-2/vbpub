# Release Tooling — **cmru**

vbpub builds & releases every product with **cmru** (Configurable Multi Release Utility).
One config, one CLI, `cmru.*`-named files. The normative contract is
[`cmru/docs/SPEC.md`](../cmru/docs/SPEC.md) — start at *"S-CLI — CLI at a glance"*.

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
./cmru.build.sh   --project cmru       # build artifact only
./cmru.publish.sh --project cmru       # upload artifact + .sha256
./cmru.cleanup.sh --remove-assets 30d  # prune old releases/GHCR versions
./cmru.py --help                       # all verbs
```

Versions are SemVer from git tags via setuptools-scm (see [VERSIONING.md](VERSIONING.md));
untagged commits build as `X.Y.Z.devN+g<sha>`. `release` is idempotent — re-running on a HEAD
that already carries a tag finishes a half-done release.

## Auto-released set vs. on-demand

`orchestration.project_order` in `cmru.toml` lists what `status`/`release` act on:
**ciu, cmru, modern-debian-tools-python-debug, pwmcp**. Two products are `delegated`
(self-versioned) and released on demand, not in `release` all:

- **pwmcp** — version is playwright-driven (`pwmcp-v<pw>-r<N>`), computed at build; cmru's
  `delegated` flow builds → commits/pushes the build-input bump → publishes.
- **tls-edge** — `scripts/release.sh` self-manages bump/tag/build/publish but needs an
  explicit version: `echo "TLS_EDGE_VERSION=0.2.1" > tls-edge/cmru.vars && ./cmru.publish.sh --project tls-edge`.
- **empyrion-translation** — date-tagged game asset; `./cmru.build.sh`/`./cmru.publish.sh --project empyrion-translation`.

## Notes

- GitHub credentials come from env or `cmru.secret.toml`, never committed.
- The pipeline is config-driven; no project logic is hardcoded in the orchestrator.
- `release-all.py` / `release-runner.py` are kept one release as deprecation shims → use `cmru.py`.
- The pre-cmru `release-manager/` package has been retired (its source moved into `cmru/`).
