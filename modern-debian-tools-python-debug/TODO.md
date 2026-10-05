# mdt — TODO / backlog

## Principle: ship + encourage ciu, but never ENFORCE it (no hard ciu dependency)

mdt **ships the `ciu` wheel** (the `CIU_WHEEL_*` build args bake it into the image) and **encourages**
its use — that is intentional and stays. The rule is the other direction: a repo that uses the mdt
devcontainer must **not be forced** to adopt ciu. ciu is *available*, not *required*.

Concretely:
- **Keep** the ciu wheel baked into the mdt base image (provided + encouraged).
- **No hard dependency:** mdt's base + lifecycle (the post-create flow, provided tooling) must function
  for a consumer repo that does **not** use ciu — never fail or assume a ciu-managed stack.
- **ciu-aware tooling must stay import-free of ciu:** e.g. dstdns's `scripts/container-exec.py` is
  ciu-aware (it *parses* `ciu.global.toml`/`.env.ciu` to resolve container names) but imports nothing
  from ciu and degrades gracefully when ciu config is absent. That is the pattern to follow.

**Audit TODO:** confirm nothing in the mdt base image or its post-create path hard-requires ciu
(config, CLI, or a ciu-rendered stack) to complete successfully; where ciu is used, make it optional
with a graceful no-ciu fallback.

_Captured 2026-06-23 per user direction: "we do ship ciu in mdt and encourage its use, but we do not enforce usage of ciu, so no hard dependencies for the repos using mdt."_

## Feature request: a defined host-escape companion (`mdt host-exec`/`host-shell`)

The mdt devcontainer is unprivileged with `CgroupnsMode=private` and no host
cgroupfs bind (see `devcontainer-docker-environment` in the estate memory) —
so anyone needing real host root (diagnostics, cgroup inspection, anything
`host-setup/CGROUP-NOTES.md` §5 talks about) currently hand-rolls their own
`docker run --privileged --pid=host <image> nsenter -t 1 -m -u -n -i -- <cmd>`.
Every consumer re-derives the same incantation from scratch — this session did
it ad hoc from a vbpub devcontainer, and `scripts/cgroup-profiler`'s own
`attach`/`doctor` flow *also* re-execs itself into a privileged helper
container for the same reason (its README says so explicitly). Two
independent hand-rolled implementations of the same escape already exist in
this estate.

That duplication isn't just untidy — it's a live hazard `CGROUP-NOTES.md` §5
already documents but never turned into tooling: `memory_recursiveprot` (+
`nsdelegate`, + `memory_hugetlb_accounting` since accounting was added) is a
**mount flag**, not a per-cgroup property, and gets silently stripped by "an
external `--privileged`/`--cgroupns=host` container touching the host mount
namespace" (`mdt-dev-governance-reconcile.sh`'s own comment; CGROUP-NOTES.md §5 dates an
occurrence 2026-07-17, the timer-service comment dates another 2026-08-28).
While stripped, **every** slice's `MemoryMin`/`MemoryLow` on the whole host
silently protects nothing — `systemctl show` still reports the configured
value, so there is no other symptom. `mdt-dev-governance-reconcile.timer`'s 5-minute sweep
self-heals it, but that's up to 5 minutes of real exposure per occurrence, and
the exact trigger (`--privileged` vs `--cgroupns=host`, or something else) is
still only "most likely" per the sweep script's own comment — never actually
pinned down.

Confirmed recurring again live, 2026-09-08 (gstammtisch soulmask-stall
investigation): journalctl caught `mdt-dev-governance-reconcile.sh` finding
`memory_recursiveprot`+`memory_hugetlb_accounting` missing and restoring them
mid-session, most plausibly triggered by one of the two ad hoc/tool-internal
escapes above (both were in active use in the same window; which one isn't
established). This is the mechanism that left two production game-server
containers' `memory.min` at an effective 0B for an unknown stretch of that
investigation.

**Ask:**
1. **DONE (2026-09-12):** `customization/mdt` ships `mdt host-exec -- <cmd>` /
   `mdt host-shell` as the one defined, reusable escape entry point. The
   pre-existing `customization/host-escape` (already installed at
   `/usr/local/bin/host-escape`, and already doing exactly this under a
   different name) is now a thin backward-compat wrapper delegating to it —
   nothing that already called `host-escape` breaks.
2. **DONE (2026-09-12):** `mdt host-exec`/`mdt host-shell` end with an
   automatic `mdt doctor` pass — re-checks `findmnt -no OPTIONS
   /sys/fs/cgroup` for the same flags `mdt-dev-governance-reconcile.sh` checks
   (mirrors its check + remount command exactly, kernel-gating
   `memory_hugetlb_accounting` the same way) and restores them inline if
   missing, immediately rather than waiting for the next up-to-5-minute
   timer sweep. Also runnable standalone as `mdt doctor` (`--check-only` to
   just report). Not yet run against a live occurrence of the drift — the
   host was healthy (`nsdelegate,memory_recursiveprot,memory_hugetlb_accounting`
   all present) when this was built and tested.
3. Stretch goal, still open: pin down whether `--privileged` or
   `--cgroupns=host` specifically causes the strip (or some other flag
   combination avoids it entirely) — fixing the cause beats fixing the
   symptom faster every time. `mdt host-exec` uses `--privileged --pid=host`
   (not `--cgroupns=host`), same as the original `host-escape`; this doesn't
   change that ambiguity, only closes the window faster once it happens.
4. Still open: `scripts/cgroup-profiler` is a natural first consumer of `mdt
   doctor` — it could delegate to it instead of maintaining its own
   privileged re-exec + any ad hoc mount-flag handling.
5. Still open: `mdt doctor`'s remount command is `mount -o
   remount,nsdelegate,memory_recursiveprot${HUGETLB_OPT} $CG` — copied
   verbatim from `mdt-dev-governance-reconcile.sh` for one source of truth, so it
   inherits whatever correctness properties (or gaps) that command already
   has for preserving the rest of the host's existing cgroup2 mount state;
   this ask is about auditing that command itself, not something `mdt
   doctor` changes either way.
6. **DONE (2026-09-27):** add useful help at the top-level and each
   subcommand, make `host-escape --help` show usage instead of treating it as
   a command to run on the host, and reject invalid target arguments before
   starting the privileged helper.

_Captured 2026-09-08 from a live gstammtisch investigation (soulmask periodic
stall incident); filed by Claude per operator request ("how about mdt ships a
companion script to escape the devcontainer in a defined way")._

## Bug: managed BuildKit socket group does not match consumer group

Filed 2026-09-24 after a live devcontainer connection failed with
`PermissionError: [Errno 13]`. The Docker API socket was `root:docker` mode
`0660` (GID 994, available to the consumer), while rootless BuildKit created
`/run/mdt-buildkitd/buildkitd.sock` as `1000:1000` mode `0660`. The parent
directory's permissive mode did not grant permission to connect to the socket.

Fix: retain a sticky runtime directory while BuildKit creates the socket;
before systemd marks the service ready, seal the root-owned directory against
entry changes, verify the endpoint is a non-symlink socket owned by the
running container user, then set only that socket to host group `docker` and
mode `0660`. Ownership is compared inside the container's user namespace, so
Docker `userns-remap` does not create a false mismatch. Each restart restores
the writable directory and removes a stale socket before BuildKit starts.
Consumer instructions require the existing host `DOCKER_GID` supplementary
group and explicitly reject making the socket world-accessible. Live
acceptance passed after applying the same socket ownership/mode correction:
`docker buildx inspect mdt-managed` reported the remote builder running.
Independent review on 2026-09-30 found that systemd expands `stat` format
strings before the shell; escaped specifiers, directory sealing, and the
namespace-relative owner check now have renderer assertions. The registered
smoke and release gates pass on the integrated candidate, and the final
independent review found no further actionable regressions.

## Feature request: one canonical io.cost coefficient generator — DONE 2026-09-27

Both MDT host setup and `scripts/debian-install-v2/tools/iocost-calibrate.sh`
now consume `tools/iocost_coef_gen.py`, generated from the pristine Linux
vendor source plus the ordered patch series. The generated header records the
source and patch hashes; `tools/build-iocost-generator.py --check` detects
drift. The patch set includes LVM/dm discovery, explicit file-target support,
no-COW fallback, safer argument handling, and raw partition sysfs lookup.

Partition creation, formatting, mounting, benchmarking, and cleanup are still
not automated. MDT requires its persistent benchmark file to share a
filesystem with Docker data. For future installer integration, prefer a
mounted file target on a dedicated partition; do not add partition surgery
without the existing plan/apply/readback/rollback path. See
`host-setup/plan-iocost-integration.md` and
`scripts/debian-install-v2/IO-BENCHMARK-DESIGN.md`.

_Captured 2026-09-27 after a live runtime-path audit._

## Feature request: ship `run-gate` (and the estate CLI set) in the image the way `ciu` is shipped

Filed 2026-10-03 from dstdns (tooling-boundary pass,
`dstdns/docs/proposals/TOOLING-BOUNDARY-2026-10.md` Q3). Status: OPEN.

**Observed:**
- The image bakes the ciu wheel from an immutable release (`Dockerfile:87-94`, `CIU_WHEEL_*`). It does not bake `run-gate`, which the estate's AGENTS doctrine makes the only sanctioned gate entry point.
- Every consumer therefore installs it by hand in its own finalize hook. dstdns: `env-workspace-setup-generate.sh:87-108` `ensure_estate_tools`, called from `.devcontainer/finalize.post.d/10-dstdns-ciu.sh:24`. It `pip install`s from a sibling checkout path (`$REPO_ROOT/../vbpub/run-gate-project`, `:100`) and only warns when that path is absent (`:106`), e.g. in CI or a fresh worktree.
- Consequences:
  - A devcontainer rebuild silently drops run-gate until the hook runs (dstdns memory `feedback-devcontainer-rebuild-drops-run-gate`).
  - The installed revision is whatever the sibling checkout's working tree holds, not a released, pinned artifact.
  - Each consumer re-implements the same install-if-missing logic.

**Why mdt owns it:** mdt already owns "which estate CLIs are present, at which released version" for ciu. run-gate is the same kind of dependency. It is a stdlib single-file tool with a wheel (run-gate RG-14), so the no-hard-dependency principle at the top of this file is unaffected: shipping it does not force its use.

**Proposed contract:**
- `RUN_GATE_WHEEL_{TAG,URL,SHA256}` build args, mirroring `CIU_WHEEL_*`, verified by sha256 and installed into the image's tool venv.
- `mdt version --json` (or the image label set) reports the shipped run-gate revision beside ciu's.
- A consumer that needs a newer revision than the image ships pins it in its own `requirements` and the finalize hook only upgrades. Install-from-sibling-checkout is never the default.

**Oracles:**
- A fresh container from the image, with no finalize hook run, has `run-gate --version` on PATH, matching the build arg.
- A wrong sha256 build arg fails the build.
- Controlled wrong implementation: installing from a sibling checkout path must fail the first oracle in an image built without vbpub present.

**dstdns deletes on ship:** `ensure_estate_tools`'s run-gate branch (`env-workspace-setup-generate.sh:95-107`) and the "reinstall run-gate after rebuild" doctrine.

