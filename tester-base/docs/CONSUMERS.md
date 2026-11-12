# tester-base — consumers guide (HOW)

## Bootstrap order (nothing published yet)
```bash
# 1. the base - the shim source is a checked copy of CIU's source; the hash
#    covers both tester-base build inputs (cmru/build-initial-standalone.sh does the same)
TESTER_BASE_SOURCE_HASH="$(cd tester-base && sha256sum Dockerfile ksm-optin.c | sha256sum | cut -d' ' -f1)"
docker buildx build --load --build-arg "TESTER_BASE_SOURCE_HASH=$TESTER_BASE_SOURCE_HASH" \
  -t tester-base:local tester-base/
# 2. the gate image on top of it, with the supported builder: it supplies the Run-Gate wheel context
#    and the base's resolved identity (tester-unified's own README has the full flow). In the ZERO state
#    there is no tester-unified:local yet, so the base is passed explicitly: cmru's tester-gate preflight
#    then probes in tester-base itself. Prerequisites: cmru installed (step 1 of docs/BOOTSTRAP-FROM-ZERO.md),
#    the Run-Gate wheel built in run-gate-project/dist, and the CMRU_TESTER_* variables of that guide exported.
BUILDX_BUILDER=default tester-unified/build-image.sh --base-image tester-base:local \
  --wheel-dir run-gate-project/dist --tag tester-unified:local
#    wheel-builder shares the base: cmru/build-initial-standalone.sh builds tester-base:local first
#    when it is absent and then wheel-builder:local FROM it
# 3. gates / cmru work
TESTER_BASE_IMAGE=tester-base:local CGROUP_PARENT_DEV_GATES=$CGROUP_PARENT_DEV_GATES \
  (cd tester-base && run-gate contract)
# 4. cmru releases tester-base (controller)
```
**Builder note.** `FROM tester-base:local` resolves the base in the *local* image store, which only
the `docker` driver builder (`BUILDX_BUILDER=default`) can see; a remote builder such as this
estate's `mdt-managed` fails to resolve it (measured: the base would be pulled from a registry
instead, and a local-only tag does not exist there). That is why step 2 selects the default builder,
and why `--load` is needed on a non-`docker` builder in step 1. After the release the base is a
registry reference and any builder works.

After the first release the controller replaces the three pin sites with the published digest:
the `ARG TESTER_UNIFIED_BASE_IMAGE=` default in `tester-unified/Dockerfile`, the
`ARG WHEEL_BUILDER_BASE_IMAGE=` default in `wheel-builder/Dockerfile`, and
`[environments.host].image` in `run-gate.root.toml`
(`ghcr.io/volkb79-2/tester-base@sha256:<digest>` as reported by cmru).

## Using it as a base
```dockerfile
ARG TESTER_UNIFIED_BASE_IMAGE=tester-base:local
FROM ${TESTER_UNIFIED_BASE_IMAGE}
USER root
# add your venv and your uid-1003 identity here; the base creates none
```

## The contract lane
```bash
TESTER_BASE_IMAGE=<ref> CGROUP_PARENT_DEV_GATES=$CGROUP_PARENT_DEV_GATES run-gate contract
```
There is no default image; an unset `TESTER_BASE_IMAGE` is a configuration error. Every container
is placed in the gate tier, offline, `--init`, uniquely named and removed by exact name.

## KSM helper contract

tester-base builds `/usr/local/lib/ksm-optin.so` from `ksm-optin.c` and
advertises the fixed path with `org.vbpub.tester.ksm-optin`. The source in this
build context is a byte-for-byte mirror of CIU's canonical
`ciu/src/ciu/data/ksm-optin.c`; `tests/test_source_hash.py` fails if the copies
drift. The image build uses `-nostdlib` and checks for zero `DT_NEEDED` entries
with `readelf` when available. The shim is not enabled for every process by the
image. Run-Gate creates a lane-scoped `/etc/ld.so.preload` file when it starts
an ephemeral command or Assay container.

The constructor is silent on stdout and stderr whether the kernel accepts or
rejects `PR_SET_MEMORY_MERGE`; there is no verbose-banner setting. The contract
lane compiles and loads the real shim in a subprocess and checks output under
both forced syscall-result cases using a hook compiled only for that test.

The current shim's raw syscall is x86-64-specific. The amd64 tester-base stage
builds and advertises it; the arm64 stage omits both the library and its label
so ARM64 builds continue to work. Run-Gate's one-line missing-label notice is
the expected behavior on an ARM64 image until CIU provides an ARM64 shim.
When the image is not local, Run-Gate's KSM probe follows the same unavailable
shim path; with no other label-dependent preload, Docker's normal run can pull
the image and the first run proceeds without KSM injection.

Run-Gate opts those containers into KSM by default. These settings can opt out
for a specific lane or for all lanes under a config file:

```toml
schema_version = 1

[defaults]
ksm = false

[lanes.identical-workload]
kind = "command"
environment = "tester-unified"
argv = ["python3", "-m", "pytest", "tests"]
# A lane may set ksm = true to override the default.
```

The resolution order is `--ksm` / `--no-ksm`, non-empty `RUN_GATE_KSM=1|0`,
the lane's `ksm`, project `[defaults].ksm`, central `[defaults].ksm`, then the
built-in `true`. Empty `RUN_GATE_KSM` is absent. The CLI and environment
settings affect one invocation and never rewrite TOML. This mirrors CIU's
single run-scoped override point in `governance.resolve_ksm_optin` and
`--ksm` / `--no-ksm` (CIU S15.18); `--no-ksm` means Run-Gate does not inject
its shim, and cannot turn off an image's own KSM opt-in.

If KSM is requested but the selected image has no `org.vbpub.tester.ksm-optin`
label, Run-Gate prints one notice and continues without its injection. When
`io_sync = "relaxed"` is also selected, the generated read-only
`/etc/ld.so.preload` file contains both the KSM shim and the image-advertised
eatmydata library. The per-run `ksm: merging_pages=N (X MiB) ...` line uses a
best-effort host `pages_sharing` delta; it can include unrelated KSM activity.

KSM is useful when processes hold identical anonymous pages. A measured probe
merged about 359 MiB of identical pages, while three pytest workers shared
about 1 MiB from roughly 390 MiB of anonymous memory and produced negative net
profit. That workload gained little; set `ksm = false` when the lane's
measurements show the same pattern.

## Build arguments (the pins, and how they change)
Every pin is a reviewed edit of the Dockerfile, never a build-time surprise:

| Build arg | Pins | Bump by |
|---|---|---|
| `DOCKER_APT_PRIMARY_FPR` | the only primary key the Docker apt key file may hold (the SET of primary fingerprints must equal exactly this) | a deliberate key rotation |
| `DOCKER_CE_CLI_VERSION` | the exact `docker-ce-cli` package version (the contract lane asserts the installed version equals it) | editing the line to a newer version Docker's repository serves |
| `SHELLCHECK_VERSION`, `SHELLCHECK_SHA256_X86_64`, `SHELLCHECK_SHA256_AARCH64` | the shellcheck release and the sha256 of each architecture's upstream asset | the three together |
| `TESTER_BASE_SOURCE_HASH` | not a pin: the sha256 of the `sha256sum` records for `tester-base/Dockerfile` and `tester-base/ksm-optin.c`, written to `org.vbpub.tester-base.source-hash` by `cmru/build-initial-standalone.sh`; a local `tester-base:local` is reused only when it matches both build inputs, rebuilt when stale, and refused when it is not a tester-base build; a plain `docker build` leaves it empty, which reads as stale | the bootstrap script supplies it |

The `FROM` digest is the one `cmru versions resolve tester-base` records under the 14-day age
window (see the DESIGN-GUIDE "Version pins").

## Releasing
`cmru release` runs the steps in `cmru.toml`: build once, test that image, then (after cmru wrote the
new `VERSION`) tag and push it. Nobody types a ref, a version or a digest, and the push step needs
`GITHUB_USERNAME` and `GITHUB_PUSH_PAT` in its environment (cmru's `required_env`); without them it
refuses before any registry call. See the DESIGN-GUIDE "One build, one image" for the order and for what a
failed push leaves behind.

### Cleaning up abandoned release candidates
Each release attempt builds `tester-base:release-<source-commit>`; abandoned retries leave their tags
behind. List them and remove each one by its exact tag (never by a filter):

```bash
docker image ls --format '{{.Repository}}:{{.Tag}}' tester-base | grep '^tester-base:release-'
docker rmi tester-base:release-4b233baa4cfb        # one exact tag per command
```

## When a gate needs another tool
First ask which side owns it. A real low-level OS binary: mark the test `os_integration` and run
it in the VM lane. A userland package a registered gate lane executes: add it to the Dockerfile
with the failing lane's evidence, update the contract lane and the DESIGN-GUIDE "Rejected" table.
