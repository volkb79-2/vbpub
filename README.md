# vbpub — public projects, helpers, and the cmru release toolchain

A monorepo of independently-versioned products that share **one** GitHub Releases page.
Everything is built and released through **cmru** (Configurable Multi Release Utility):
one config and one installed CLI.

## Products

| Product | Dir | Artifact | Released via |
|---|---|---|---|
| **cmru** | [`cmru/`](cmru/) | Python wheel | cmru (dogfood) — `cmru-v*` |
| **ciu** | [`ciu/`](ciu/) | Python wheel | cmru — `ciu-v*` |
| **modern-debian-tools-python-debug** | [`modern-debian-tools-python-debug/`](modern-debian-tools-python-debug/) | OCI images | cmru — `modern-debian-tools-python-debug-v*` |
| **pwmcp** (Playwright-MCP service) | [`pwmcp/`](pwmcp/) | OCI image + stack bundle | cmru — `pwmcp-v<playwright>-r<N>` |
| **nyxloom** | [`nyxloom/`](nyxloom/) | Deterministic multi-project agent workflow control plane (offline/redesign; excluded from builds and releases) | — |
| **tls-edge** | [`tls-edge/`](tls-edge/) | tarball | cmru — `tls-edge-v*` |
| **empyrion-translation** | [`game_stuff/empyrion/`](game_stuff/empyrion/) | tarball | *(delegated, on-demand)* — date-tagged |
| plesk-mailbox-create | [`plesk-mailbox-create/`](plesk-mailbox-create/) | script tool | n/a |
| vsc-devcontainer | [`vsc-devcontainer/`](vsc-devcontainer/) | devcontainer image | n/a |

Each product has its own README with product-specific detail.

Testing is uniform across the projects that adopted the gate entrypoint:
`cd <project> && ./run-gate.py --list` discovers that project's declared
lanes (see [`run-gate-project/CONSUMERS.md`](run-gate-project/CONSUMERS.md)).

## Repository setup and initial CMRU build

CMRU itself is the first wheel to build in a fresh checkout. The bootstrap script is
deliberately independent of an installed CMRU: it uses the standalone `wheel-builder`
image to build `cmru/dist/cmru-*.whl`, then prints the commands needed to install it.

```bash
./cmru/build-initial-standalone.sh

# Run the install commands printed by the script, for example:
python3 -m venv .venv-cmru
.venv-cmru/bin/python -m pip install --no-deps cmru/dist/cmru-*.whl
export PATH="$PWD/.venv-cmru/bin:$PATH"

cmru --help
cmru dependencies
```

The script builds `wheel-builder:local` automatically when that image is absent. It
does not require `cmru.handlers` to be installed: it adds `cmru/src` only for the
bootstrap process. After installation, the `cmru` console script is the canonical
interface for this repository and for each individual project.

Before running a release gate, ensure the dedicated gate image is built from
[`tester-unified/Dockerfile`](tester-unified/Dockerfile), and keep Docker work under the
configured `$CGROUP_PARENT_DEV_BACKGROUND` slice.

## Releasing (cmru)

Use the installed `cmru` command for all verbs. The repository keeps one optional
convenience wrapper for the common full release:

```bash
cmru status                             # preview what would be released (read-only)
./cmru.release.sh                      # one-shot: detect changed → tag → push → build → publish
./cmru.release.sh --dry-run            # preview tags only, no writes
cmru changelog --project assay --backfill-tag assay-v0.1.0  # migrate a missed history entry
cmru build --project <name>            # retained isolated gate + build; no publish
cmru publish --project <name>          # run the project's declared publish step
cmru cleanup --remove-assets 30d       # prune old releases / GHCR versions
cmru --help                            # all verbs
```

`./cmru.release.sh` creates a line-flushed full `cmru.release.log` by default while the
console shows concise orchestration summaries. Add `--show-run-details` to stream raw
Docker/test output too; add `--log-append` to retain prior transcripts with a divider.

- **Config:** [`cmru.orchestration.toml`](cmru.orchestration.toml) coordinates only; every product owns a portable `cmru.toml` (committed, no secrets). Templates: `cmru.project.sample.toml`, `cmru.orchestration.sample.toml`.
- **Token:** `$GITHUB_PUSH_PAT` / `$GITHUB_TOKEN`, or the gitignored repository-root
  `cmru.secret.toml` (`[github] token = "…"`), with an optional deep-merged
  `<project>/cmru.secret.toml` override. A committed `cmru.toml` token is rejected.
  (SPEC S2.4)
- **Per-project contract:** `<product>/cmru.toml` includes release facts and runner steps; generated build vars: `cmru.vars`.
- **Release history:** CMRU creates each managed product's `CHANGES.md` before its
  isolated gate. No per-project opt-in is needed; see [`cmru/README.md`](cmru/README.md).
- **Auto-released set** (`orchestration.project_order` in `cmru.orchestration.toml`): ciu, cmru,
  assay, topos, modern-debian-tools-python-debug, pwmcp, tls-edge. Nyxloom is
  intentionally offline and excluded from the build/release set.
  Empyrion translation remains an on-demand, delegated date-tagged asset.
- **Contract & rationale:** [`cmru/docs/SPEC.md`](cmru/docs/SPEC.md) — start at *"S-CLI — CLI at a glance"*.
  Tooling overview: [`docs/RELEASE-TOOLING.md`](docs/RELEASE-TOOLING.md).

## Repo layout

```
cmru/            cmru source (CLI, runner, hosts), SPEC, tests
ciu/ pwmcp/ tls-edge/ modern-debian-tools-python-debug/ game_stuff/   products
nyxloom/      project-neutral workflow control-plane design/pilot
scripts/         shared ops scripts (netcup, debian-install, …; needs requirements.txt)
docs/            release tooling, versioning, plans
cmru.orchestration.toml  cmru.release.sh  cmru/build-initial-standalone.sh
                         estate release-toolchain configuration and bootstrap
```

> Housekeeping: see [`docs/plan-cleanup.md`](docs/plan-cleanup.md) for the leftover-file cleanup plan.
