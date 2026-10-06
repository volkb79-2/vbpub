# Bootstrapping the estate from ZERO releases

Situation: no GitHub release exists for any estate project (a fresh fork, a lost
release page, a new organisation). The estate can release itself from here, in
the order below, using only existing tools (`cmru`, the tester image build, the
projects' own gates). Nothing here is automatic: every zero-release input is an
explicit operator choice, never a silent fallback.

## Why an order is needed (the cycle)

```
cli-extended release -> needs cmru (every cmru.toml calls it) + the tester image
cmru wheel           -> needs the cli-extended WHEEL (cmru/pyproject.toml, floor 0.2.0)
tester-unified image -> installs the cli-extended wheel (pinned release asset)
```

Breaking the cycle: build the cli-extended wheel once FROM SOURCE with cmru's own
wheel builder, use it to bootstrap cmru and the image, then release cli-extended
for real and re-pin to the release.

## The order (derived from `cmru dependencies`)

Run `cmru dependencies` for the authoritative, current levels. A project is
released only after every project on a lower level (its transitive providers are
all on lower levels). The block below is checked against that graph by
`cmru/tests/test_bootstrap_doc_order.py`; if the graph changes, regenerate this
block from `cmru dependencies` (the `LEVELS` section).

```project-order
L0: cli-extended
L1: cmru
L2: ciu, run-gate-project, assay, topos, nyxloom, pwmcp, tls-edge, cgroup-profiler
L3: modern-debian-tools-python-debug
```

- L0 is the library everything adopts. L1 needs L0 as a real wheel dependency.
- L2 projects have no cli-extended dependency of their own in their pyproject; their
  release/gate tooling is cmru (declared as a build-time edge). Within a level the
  order is free (`project_order` fixes one).
- L3 stages the wheels of L0-L2 into its image (`pip/wheels.list`) and is released last.

## Steps from an empty release set

Preconditions: Docker, the wheel-builder image (`CMRU_WHEEL_BUILDER_IMAGE`) and a
governed cgroup parent (`CMRU_BOOTSTRAP_CGROUP_PARENT` or `CGROUP_PARENT_DEV_BACKGROUND`).
Run on a quiet host; every step is serial.

1. **Build cli-extended from source and bootstrap cmru (one command).**
   `CMRU_BOOTSTRAP_CLI_EXTENDED=source cmru/build-initial-standalone.sh`
   builds `libraries/cli-extended` with cmru's own wheel builder
   (`cmru.handlers wheel-build`; version `0.2.0+bootstrap.source`, the `+local` segment keeps
   it distinct from any release; override with `CMRU_BOOTSTRAP_CLI_EXTENDED_SOURCE_VERSION`),
   logs its sha256 (`cli-extended source wheel sha256=...`), stages it exactly like a
   released wheel and builds the first cmru wheel with it. Both wheels end up in `cmru/dist/`.
   Install both offline, cli-extended first (the script prints the exact commands).
   The default mode `release` (no variable) FAILS with exit 2 when no release exists and names
   this variable; it never falls back by itself. `CMRU_BOOTSTRAP_CLI_EXTENDED=<wheel-path>`
   plus `CMRU_BOOTSTRAP_CLI_EXTENDED_SHA256` is the explicit prebuilt-wheel form.
2. **Build the tester image with the local wheel.** Copy the cli-extended wheel
   (`libraries/cli-extended/dist/cli_extended-*+bootstrap.source-*.whl`) into
   `tester-unified/local-wheel/` and build with
   `--build-arg CLI_EXTENDED_RESOLVE=local --build-arg CLI_EXTENDED_WHEEL_SHA256=<logged sha256>`
   (see `tester-unified/README.md`). The image asserts the wheel carries a `+local` version.
3. **Run the cli-extended gate.** From `libraries/cli-extended`: `./run-gate.py r0-r1` and `r3`
   (the same lanes its release transaction runs).
4. **Release cli-extended** with the source-bootstrapped cmru from step 1:
   `cmru release cli-extended --set-version 0.2.0` (the first release has no prior tag).
5. **Re-pin to the release.** Copy `version`/`url`/`sha256` from
   `cli-extended-latest/latest.json` into the `CLI_EXTENDED_WHEEL_*` pin defaults in
   `tester-unified/Dockerfile`, and rebuild the image in the default `pinned` mode; the
   `local-wheel` input is no longer needed. Reinstall cmru from a normal bootstrap
   (`cmru/build-initial-standalone.sh`, default `release` mode) when convenient.
6. **Release cmru:** `cmru release cmru` (its gate runs in the pinned image; the wheel
   declares `cli-extended>=0.2.0`, now satisfied by a real release).
7. **Continue in graph order:** the remaining levels from the block above, L2 then L3
   (`cmru release <project>` each, or the whole set in `project_order`).

## Open items (not solved here, stated so they are not forgotten)

- **The tester image's base is a published mdt image.** `tester-unified/Dockerfile`
  starts `FROM ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:...`,
  which is an L3 output of THIS graph. There is NO zero-release path for it in this
  repository today: with no published base image the tester image (and so every gate
  lane) cannot be built. A fork must obtain or build that base image out of band first
  (mdt's own build tolerates an empty `pip/wheels/`, so a wheel-less first build is the
  likely route; not verified here). This is a graph-external prerequisite and an
  open item for a local-base-image target.
- **mdt `pip/wheels.list` now names `cli-extended`**, so mdt's offline `pip --no-index`
  install can resolve cmru's dependency; its resolver is generic
  (`GitHubReleases.resolve_latest("cli-extended")`) but was not run here (no network).
- **Vendored copies are outside the guard.** nyxloom compiles the cli-extended source
  into its own wheel (`nyxloom/pyproject.toml`, `nyxloomd/Dockerfile`) without a pyproject
  dependency, so the pyproject-derived guard cannot see it. Follow-up: move nyxloom to
  the wheel dependency (CX-D1), then its edge is guarded automatically.
- `scripts/debian-install-v2/bootstrap-remote.py` still fetches the cli-extended SOURCE
  subtree instead of the released wheel (CX-D3); that belongs to the W9b branch.
