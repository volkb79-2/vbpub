# vbpub — public projects, helpers, and the cmru release toolchain

A monorepo of independently-versioned products that share **one** GitHub Releases page.
Everything is built and released through **cmru** (Configurable Multi Release Utility):
one config, one CLI, all release files named `cmru.*`.

## Products

| Product | Dir | Artifact | Released via |
|---|---|---|---|
| **cmru** | [`cmru/`](cmru/) | Python wheel | cmru (dogfood) — `cmru-v*` |
| **ciu** | [`ciu/`](ciu/) | Python wheel | cmru — `ciu-v*` |
| **modern-debian-tools-python-debug** | [`modern-debian-tools-python-debug/`](modern-debian-tools-python-debug/) | OCI images | cmru — `modern-debian-tools-python-debug-v*` |
| **pwmcp** (Playwright-MCP service) | [`pwmcp/`](pwmcp/) | OCI image + stack bundle | cmru *(delegated)* — `pwmcp-v<playwright>-r<N>` |
| **tls-edge** | [`tls-edge/`](tls-edge/) | tarball | cmru *(delegated, on-demand)* — `tls-edge-v*` |
| **empyrion-translation** | [`game_stuff/empyrion/`](game_stuff/empyrion/) | tarball | *(delegated, on-demand)* — date-tagged |
| plesk-mailbox-create | [`plesk-mailbox-create/`](plesk-mailbox-create/) | script tool | n/a |
| vsc-devcontainer | [`vsc-devcontainer/`](vsc-devcontainer/) | devcontainer image | n/a |

Each product has its own README with product-specific detail.

## Releasing (cmru)

The discoverable front door is the `cmru.*.sh` shims (each is a thin, self-documenting
pointer to a `cmru` verb):

```bash
./cmru.status.sh                       # preview what would be released (read-only)
./cmru.release.sh                      # one-shot: detect changed → tag → push → build → publish
./cmru.release.sh --dry-run            # preview tags only, no writes
./cmru.build.sh   --project <name>     # build artifact only
./cmru.publish.sh --project <name>     # upload artifact + .sha256
./cmru.cleanup.sh --remove-assets 30d  # prune old releases / GHCR versions
./cmru.py --help                       # all verbs
```

- **Config:** [`cmru.toml`](cmru.toml) (committed, no secrets). Template: `cmru.sample.toml`.
- **Token:** `$GITHUB_PUSH_PAT` / `$GITHUB_TOKEN`, or a gitignored `cmru.secret.toml`
  (`[github] token = "…"`). Never commit a token. (SPEC S2.4)
- **Per-project step config:** `<product>/cmru.build.toml`; generated build vars: `cmru.vars`.
- **Auto-released set** (`orchestration.project_order` in `cmru.toml`): ciu, cmru,
  modern-debian-tools-python-debug, pwmcp. Delegated products (pwmcp self-versions;
  tls-edge / empyrion-translation are on-demand) own their own versioning.
- **Contract & rationale:** [`cmru/docs/SPEC.md`](cmru/docs/SPEC.md) — start at *"S-CLI — CLI at a glance"*.
  Tooling overview: [`docs/RELEASE-TOOLING.md`](docs/RELEASE-TOOLING.md).

## Repo layout

```
cmru/            cmru source (CLI, runner, hosts), SPEC, tests
ciu/ pwmcp/ tls-edge/ modern-debian-tools-python-debug/ game_stuff/   products
scripts/         shared ops scripts (netcup, debian-install, …; needs requirements.txt)
docs/            release tooling, versioning, plans
cmru.toml  cmru.*.sh  cmru.py        the release toolchain entry points
```

> Housekeeping: see [`docs/plan-cleanup.md`](docs/plan-cleanup.md) for the leftover-file cleanup plan.
