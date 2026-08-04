# Plan — Host-Managed `buildkitd` Container + Buildx `remote` Driver

Status: **DECIDED, implementing** (see `git log` for the commit that lands
alongside this doc). Authored 2026-08-03, superseding the earlier
native-systemd-daemon draft after verifying the actual root cause. Scope:
replace the `docker-container`-driver builders (`mdt-governed-v1`,
`pwmcp-governed-v1`) with one host-managed, rootless `buildkitd` **container**
— not a native daemon — reliably placed in its own slice, reached by Buildx
via the `remote` driver over a Unix socket.

Companion docs: `docs/BUILD-ARCHITECTURE.md` (the `cgroup-parent` driver-opt
unreliability finding this plan closes), `README.md` (dev-tier slice model),
`CGROUP-NOTES.md` (BFQ/mount-flag facts inherited unchanged).

---

## 0. Why (recap)

Buildx's `cgroup-parent` driver-opt does not reliably place a
`docker-container`-driver builder under a named slice with Docker's systemd
cgroup driver (source-verified in `BUILD-ARCHITECTURE.md`; confirmed live —
all inspected `buildx_buildkit_*` containers sit in `system.slice`, not
`dev-background.slice`, despite the daemon-wide default). **The bug is
specific to Buildx's own driver code, not to `--cgroup-parent` in general** —
verified live:

```
$ docker run --rm --cgroup-parent=dev-buildkitd.slice --cgroupns=host alpine sh -c 'cat /proc/self/cgroup'
0::/dev.slice/dev-buildkitd.slice/docker-<id>.scope
```

A plain `docker run --cgroup-parent=...`, bypassing Buildx's driver
machinery entirely, places correctly. That single fact removes the entire
native-systemd-daemon branch of the original plan: there is no need to
extract a raw `buildkitd` binary or hand-write privilege/namespace plumbing
— running BuildKit **as a container**, created directly (never through
`buildx create`), gets reliable placement for free, using exactly the same
mechanism CIU governance and every other dev-tier container already rely on.

## 1. Target slice: `dev-buildkitd.slice`

Direct child of `dev.slice` (same dash-derived nesting as the other two
tiers), a **sibling**, not nested under `dev-background.slice`: buildkitd is
a shared, long-lived service serving every project's builds, not a
per-invocation background job — killing it mid-build (an OOM-first tier's
whole ethos) is worse than killing one test-runner container, and an
active build IS latency-sensitive to whoever is waiting on it, unlike idle
background soak work.

```
dev.slice                     (IO ceiling only — DEV_IO_CAP_PCT, unchanged)
├─ dev-interactive.slice      (devcontainer / IDE)
├─ dev-background.slice       (test/build/gate containers)
└─ dev-buildkitd.slice        (NEW — the host-managed BuildKit worker)
```

Since there is exactly one persistent container under this slice, its
resource properties are set **at the slice level only** — not duplicated as
`docker run --memory`/`--cpus` flags on the container itself. One number,
one place to look, no risk of the two disagreeing.

```ini
# units/dev-buildkitd.slice.in
[Unit]
Description=Host-managed BuildKit worker — shared build cache, reliable slice placement
Before=slices.target

[Slice]
MemoryHigh=@DEV_BUILDKITD_MEMORY_HIGH@
MemoryMax=@DEV_BUILDKITD_MEMORY_MAX@
MemorySwapMax=@DEV_BUILDKITD_MEMORY_SWAP_MAX@
CPUWeight=@DEV_BUILDKITD_CPU_WEIGHT@
CPUQuota=@DEV_BUILDKITD_CPU_QUOTA@
IOWeight=@DEV_BUILDKITD_IO_WEIGHT@
# NO absolute IO cap here — inherits dev.slice's DEV_IO_CAP_PCT-derived
# ceiling, same "one estate ceiling, not one per child" reasoning as the
# other two tiers.
```

**`CPUQuota` is a hard cap, `CPUWeight` is proportional share** — the two
compose (weight decides who wins under contention; quota is an absolute
ceiling regardless of contention). Decided: `CPUQuota` auto-detects to
`(nproc - 2)` cores at install time when left unset in `host-setup.env`
(floored at 1 core) — mirrors the *already-established* `IO_DEV_PATH`
auto-discovery convention in this same file, not a new pattern. An explicit
value in `host-setup.env` always overrides the auto-detected one.

**Every other property here (`MemoryHigh`/`Max`/`SwapMax`, `CPUWeight`,
`IOWeight`) follows a separate, simpler rule: if left empty in
`host-setup.env`, that directive is omitted from the rendered unit
entirely** — "not set" means "not applied," not "apply some fallback
number." `install.sh`'s `render()` now strips any line that resolves to a
bare `Key=` after substitution. Ships with concrete non-empty defaults
(6G/10G/20G/100/50 respectively, same disclaimer as every other tier: review
against your own host, not a number this project can pick for you) but any
of them can be individually blanked.

**Concurrency sizing:** confirmed this needs to cover concurrent
multi-project builds (mdt release, pwmcp, anything else that adopts the
shared builder), not just one build at a time — reflected in the proposed
Memory defaults being sized above a single build's footprint; revisit with
real numbers once usage is observed.

## 2. The container: rootless BuildKit, no native daemon

```bash
docker run --rm --name mdt-buildkitd \
  --cgroup-parent=dev-buildkitd.slice \
  --device /dev/fuse \
  --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined \
  -v /run/mdt-buildkitd:/run/buildkit \
  -v mdt-buildkitd-cache:/home/user/.local/share/buildkit \
  "$DEV_BUILDKITD_IMAGE" \
  --addr unix:///run/buildkit/buildkitd.sock
```

- **Rootless image variant** (`moby/buildkit:<tag>-rootless`), not a
  hand-rolled privilege setup. Placement is unaffected by root-vs-rootless —
  cgroup accounting is enforced by the kernel on the outer cgroup regardless
  of the UID of processes inside it — but a build daemon executes arbitrary
  Dockerfile `RUN` content, and rootless is the meaningfully safer default
  for exactly that reason, not merely a tolerable one. The `--device
  /dev/fuse --security-opt seccomp=unconfined --security-opt
  apparmor=unconfined` trio is the same relaxation the mdt devcontainer
  already grants itself, for the identical FUSE reason (`fuse-overlayfs`
  inside the rootless namespace) — not a new category of host exposure.
- **Version: one shared image tag for every consumer** (decided) — cmru
  itself is not version-sensitive (it just invokes `docker buildx bake`
  against whatever builder is configured); the constraint lands on
  individual projects' Dockerfiles, which are capped at whatever BuildKit
  features the one shared version supports. BuildKit's client/server
  protocol tolerates version skew via capability negotiation (the same
  reason Docker's own several-versions-behind embedded BuildKit already
  works fine today) — a somewhat different `docker buildx` client version is
  not expected to be a problem against the pinned server.
- **Transport: Unix socket** (decided) — `-v /run/mdt-buildkitd:/run/buildkit`
  bind-mounts a host directory in so `buildkitd`'s socket is host-visible,
  then the SAME host directory bind-mounts into the devcontainer. No TLS
  cert lifecycle to manage (the alternative, a Docker network + bare `tcp://`
  listener, still needs BuildKit's own mTLS provisioned to avoid an
  unauthenticated build-execution endpoint on the shared network — a real
  added piece deliberately avoided by choosing the socket).
- **Persistent cache**: a named volume (`mdt-buildkitd-cache`), not
  per-consumer — this is the whole point of a shared builder over N separate
  `docker-container` builders each with their own cold cache.

## 3. Systemd unit — supervises `docker run` directly, no bespoke script

No native `buildkitd` binary, so no privilege plumbing to author — but the
*container's* lifecycle still needs supervision (start at boot, restart on
crash, clean recreation if config changes). Rather than writing a
drift-detecting Python/bash "ensure" script (the `ensure-release-builder.sh`
pattern), this uses systemd's own service supervision directly: `docker run`
(foreground, no `-d`) *is* the `ExecStart=` — a well-established pattern for
running Docker containers under systemd. `Restart=` gives crash recovery for
free; a config change just needs `systemctl restart` after re-running
`install.sh`. No new script, no idempotency logic to get right by hand.

```ini
# units/mdt-buildkitd.service.in
[Unit]
Description=mdt host-setup — host-managed BuildKit worker (dev-buildkitd.slice)
Documentation=file:/etc/mdt/host-setup.env
After=docker.service
Wants=docker.service

[Service]
Type=simple
RuntimeDirectory=mdt-buildkitd
ExecStartPre=-/usr/bin/docker rm -f mdt-buildkitd
ExecStartPre=-/usr/bin/docker pull @DEV_BUILDKITD_IMAGE@
ExecStart=/usr/bin/docker run --rm --name mdt-buildkitd \
  --cgroup-parent=dev-buildkitd.slice \
  --device /dev/fuse \
  --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined \
  -v /run/mdt-buildkitd:/run/buildkit \
  -v mdt-buildkitd-cache:/home/user/.local/share/buildkit \
  @DEV_BUILDKITD_IMAGE@ \
  --addr unix:///run/buildkit/buildkitd.sock
ExecStop=/usr/bin/docker stop -t 30 mdt-buildkitd
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`RuntimeDirectory=mdt-buildkitd` is systemd's own mechanism for
`/run/mdt-buildkitd` — created with correct ownership before `ExecStart`,
cleaned up after, no manual `mkdir` in `install.sh` (which would need to
repeat itself every boot anyway, since `/run` is tmpfs).

Note the service unit itself carries no `Slice=` — it's a thin supervisor
around the `docker` CLI client, negligible resource use; only the
**container** it creates (via `--cgroup-parent`) does the real work and
needs the tier's accounting.

## 4. Reaching it from the devcontainer — optional mount

```jsonc
// templates/devcontainer.json, in "mounts" (commented out by default —
// see rollout note below)
// "source=/run/mdt-buildkitd,target=/run/mdt-buildkitd,type=bind"
```

```jsonc
// containerEnv
// "BUILDKIT_HOST": "unix:///run/mdt-buildkitd/buildkitd.sock"
```

`docker buildx create --name host-buildkitd --driver remote
"$BUILDKIT_HOST"` then works exactly like the existing `docker-container`
builders, no other client-side change.

**Decided: optional, commented out**, same style as the template's other
optional mounts (e.g. the `/etc/letsencrypt` line already there) — every
consumer repo vendors this file into its own `.devcontainer/`, so an
unconditional entry would break container start for anyone who hasn't also
run the updated `install.sh` on their host yet. Uncomment once this is the
assumed baseline.

## 5. `ensure-release-builder.sh` and consumer wiring

**Postponed, TBD** — this plan installs the host-side service; wiring
`cmru.build.toml`'s `BUILDX_BUILDER`, `ensure-release-builder.sh`'s own
health-check contract (now "is the remote reachable and the right version,"
not "recreate the container on drift" — there's no longer a
per-consumer container to drift), and pwmcp's equivalent are explicitly
deferred to a follow-up once the host-side half is validated with a real
build.

## 6. `--force` reinstall

`install.sh --force` now backs up any existing `/etc/mdt/host-setup.env` to
`host-setup.env.bak-<timestamp>` and re-seeds it from the current
`host-setup.env.example` — needed because this plan adds new variables
(`DEV_BUILDKITD_*`) that an already-installed host's config predates. Without
`--force`, install.sh keeps its existing "never touch a config that's
already there" behavior.

## 7. Migration / rollback

- **Client-side rollback is cheap:** `docker buildx use default` (or
  recreate a `docker-container` builder) reverts any consumer instantly.
- **Service-side:** `systemctl disable --now mdt-buildkitd.service`, remove
  the unit + slice + named volume, matches the existing "Uninstall" section's
  style.
- **Coexistence during rollout:** nothing requires retiring
  `mdt-governed-v1`/`pwmcp-governed-v1` on day one; `docker buildx use`
  switches per-invocation.

## 8. Remaining open questions

1. **Concurrency sizing numbers** — confirmed needed, real Memory
   High/Max/SwapMax figures still TBD from observed usage rather than the
   proposed starting points.
2. **`ensure-release-builder.sh`'s new contract** — postponed (§5).
3. **Named volume GC** — `mdt-buildkitd-cache` grows unbounded like any
   BuildKit cache; whether/how to bound it (BuildKit's own `--oci-worker-gc`
   flags, or leave to manual `docker system df`/`buildctl du` review) is
   not decided yet.

## 9. First real build — found+fixed a rootless-sandbox bug (2026-08-04)

First consumer build (dstdns's `docker buildx bake all-services --load`
against a `remote` driver builder pointed at this unit's socket) failed on
every image with a `RUN` step: `runc run failed: ... error mounting "proc"
to rootfs at "/proc": ... operation not permitted`. Root cause: this host's
runc/kernel combination cannot recursively unshare a second user+mount
namespace from inside the container's own unprivileged one — needed for
BuildKit's per-`RUN`-step process sandbox — even with
`kernel.unprivileged_userns_clone=1` and both `seccomp=unconfined` and
`apparmor=unconfined` already set. Verified in isolation (a throwaway
rootless `buildkitd` with the same flags, probed directly via `buildctl`)
before touching the shared unit.

**Fix:** added `--oci-worker-no-process-sandbox` to `ExecStart=` in
`units/mdt-buildkitd.service.in` — BuildKit's own documented flag for this
exact case (its startup warning names "running as an unprivileged user"
explicitly). Trade-off: a build step could in principle signal another
process in the same worker (no per-step process-namespace isolation); for a
shared, non-multi-tenant dev builder this is the correct trade, not a
workaround outside the rootless design. Applied live via
`systemctl daemon-reload && systemctl restart mdt-buildkitd.service`;
confirmed the running worker reports
`org.mobyproject.buildkit.worker.oci.process-mode: no-sandbox` and a real
`apt-get install` `RUN` step now completes.
