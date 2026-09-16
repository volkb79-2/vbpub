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

_Captured 2026-09-08 from a live gstammtisch investigation (soulmask periodic
stall incident); filed by Claude per operator request ("how about mdt ships a
companion script to escape the devcontainer in a defined way")._
