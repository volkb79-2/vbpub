# mdt host-setup — dev-tier resource governance (cgroup v2 slices)

Prepares a Docker host so devcontainers and the test/build/gate containers
they spawn run in **bounded systemd slices** instead of the host's default
(unlimited) cgroup — the host-side counterpart of the
`"--cgroup-parent=dev-interactive.slice"` runArg shipped in
[`../templates/devcontainer.json`](../templates/devcontainer.json), the
`cgroup_parent: dev-background.slice` that ciu governance injects into compose
stacks, and `/etc/docker/daemon.json`'s `cgroup-parent` (the daemon-wide
fallback for anything that names no parent at all — this companion is the
**sole owner** of that file now, merging its managed keys into whatever else
is already there rather than overwriting it). Placement is CREATE-time only
and can never be expressed from inside a container or image — see
[`../docs/CONTAINER-DOCTRINE.md`](../docs/CONTAINER-DOCTRINE.md) and the
"Host resource governance" section of
[`../DEVCONTAINER-LIFECYCLE.md`](../DEVCONTAINER-LIFECYCLE.md).

**[`CGROUP-NOTES.md`](CGROUP-NOTES.md) is the conceptual half of this
directory:** what a slice unit fundamentally *cannot* express — and therefore
why there is a script and a timer here at all — plus the BFQ caveats. On a BFQ
host `IOWeight` does not mean what it says; read that before changing any
weight in `host-setup.env`.

## Tiering model

```
dev.slice                    ONE absolute IOPS/bandwidth/CPU/swap ceiling —
                              covers every child combined, even interactive/
                              IDE activity must not be able to starve
                              production. Every child below ALSO carries its
                              OWN tighter CPU/swap sub-ceiling so no single
                              tier can alone claim this whole parent budget.
├── dev-interactive.slice    devcontainers (IDE + AI agents) via
│                             devcontainer.json runArg
├── dev-gates.slice          gate and lane containers + placed lane leaves
│                             rg-<token> — the admission capacity object;
│                             see "dev-gates: why" below. ALSO gets a
│                             tighter IOPS sub-ceiling (bandwidth untouched)
├── dev-background.slice     long-running dev stacks (dstdns, ...) — explicit
│                            opt-in (compose cgroup_parent, docker run
│                            --cgroup-parent) OR caught by the Docker
│                            daemon-wide default (/etc/docker/daemon.json)
├── dev-memory_min_guaranteed.slice   individually-governed containers with a
│                            real memory.min floor (opt-in; see CGROUP-NOTES.md
│                            "Per-container memory.min guarantees")
└── dev-buildkitd.slice      host-managed BuildKit worker (mdt-buildkitd.
                             service) — a shared, long-lived builder, not
                             a per-invocation job; see
                             plan-buildkitd-service.md. ALSO gets the same
                             tighter IOPS sub-ceiling as dev-gates.slice
```

**CPU, swap, and zswap-writeback all get the same "one ceiling at dev.slice,
one tighter sub-ceiling per child" treatment as IO:**
- `CPUQuota`: `dev.slice` auto-detects `nproc - DEV_CPU_RESERVE_CORES`
  (default 1); every child auto-detects `nproc - DEV_SUBSLICE_CPU_RESERVE_CORES`
  (default 3) independently.
- `MemorySwapMax`: auto-detected from this host's real total swap at
  `DEV_SWAP_CASCADE_PCT`% (default 80), cascading — `dev.slice` = 80% of
  host swap, each child = 80% of `dev.slice`'s derived number, and the
  reactive watcher's per-container swap cap (below) = 80% again of whichever
  child applies.
- `MemoryZSwapWriteback`: explicit on all five. **Does not cascade from
  `dev.slice`** — it's a plain per-cgroup toggle, verified live (2026-09-12)
  to NOT propagate a parent's own value down; each slice states its own
  intent. `dev.slice`'s own value is a safety anchor only, since a `0` on
  ANY ancestor silently disables writeback for the whole subtree below it
  (`CGROUP-NOTES.md` "zswap writeback — who may page to disk" has the full
  mechanism).

`dev.slice` holds dev LOAD only, nothing else — the host-level
`cgprofile-host-daemon` is a host deployment, not dev load, and ships its own
top-level `cgprofile.slice` alongside the `cgroup-profiler` stack instead of
living under here (design amendment A1/D-29, `run-gate-project/nyxloom-trove/
DESIGN-2026-09-12-liveness-placement-admission.md`).

| Tier | Who joins | Character |
|---|---|---|
| `dev-interactive.slice` | devcontainers (IDE + AI agents) via devcontainer.json runArg | responsive: soft-protected working set (`MemoryLow`), generous `MemoryHigh`, cold tail compressed into zswap and allowed to drain to disk from there (`DEV_INTERACTIVE_ZSWAP_WRITEBACK`), never OOM-killed |
| `dev-gates.slice` | gate/lane containers + placed lane leaves `rg-<token>`, via explicit `docker run --cgroup-parent` or `$CGROUP_PARENT_DEV_GATES` | the admission capacity object: bounded memory+swap, `systemd-oomd` kills inside the tier first — a gate is disposable, a stack is not |
| `dev-background.slice` | long-running dev stacks (dstdns, ...), via compose `cgroup_parent`, explicit `docker run --cgroup-parent`, **or** the Docker daemon-wide default | bounded: hard memory+swap caps (relaxed swap given ample host swap — size for yours), `systemd-oomd` kills inside the tier first |
| `dev-buildkitd.slice` | exactly one container: the host-managed rootless BuildKit worker (`mdt-buildkitd.service`, plain `docker run --cgroup-parent=`, never through a Buildx driver — see `plan-buildkitd-service.md`) | one shared build cache across every consuming project; hard `CPUQuota` auto-detected as `nproc - DEV_SUBSLICE_CPU_RESERVE_CORES` cores (default 3, same as every other child) unless overridden; active-build latency-sensitive, so `CPUWeight`/`IOWeight` sit between interactive's and background's; also gets a tighter runtime IOPS sub-ceiling (bandwidth untouched), same as `dev-gates.slice` |
| (production tiers) | e.g. `wings.slice` for game servers | owned elsewhere — this companion never touches them, it only keeps dev work from starving them |

`dev.slice` (the shared parent) carries the **one** absolute IOPS/bandwidth
ceiling for every child combined — not one per tier. That is
deliberate: even interactive/IDE work must not be able to starve production
I/O, so a build storm AND a heavy IDE session together still can't exceed the
estate's single cap. `CPUWeight`/memory/OOM policy stay per-child, since
interactive, background, and the shared builder genuinely need different
shapes there.

Genuine **per-container** guarantees (so N concurrent gate containers can't
each individually hog the tier) are a separate, complementary mechanism:
explicit `docker run --memory`/`--cpus`/`--device-*-iops` flags in whatever
spawns the container (e.g. `cmru`'s tester-gate) — a slice's own limits only
bound the *whole tier combined*, never one container in it. See
`AGENTS.md` in the repo root ("Host cgroup placement for spawned containers").

Weights (`CPUWeight`/`IOWeight`) settle contention *between* the two child
tiers; `dev.slice`'s `io.max` bounds the **whole estate** absolutely so a
build storm (or a heavy interactive session) can't saturate the disk even when
production is momentarily idle (its next burst must not queue behind it). IO
weights need the BFQ scheduler (installed/selected by this setup); the io.max
caps work on any scheduler.

⚠️ The shipped IO weights (dev-interactive 100, dev-background 10) are a true
10:1 *because both stay ≤ 100*. systemd rescales `IOWeight` above 100 into
BFQ's 1..1000 range, so "1000 vs 100" would be 1.81:1, not 10:1 — express IO
ratios by lowering the loser, never raising the winner.
[CGROUP-NOTES.md §BFQ](CGROUP-NOTES.md#bfq-caveats) has the mapping table.

## Managed BuildKit backend (mandatory)

The installer owns one rootless `mdt-buildkitd.service` in
`dev-buildkitd.slice` and registers one durable Buildx remote builder named
`mdt-managed` at `unix:///run/mdt-buildkitd/buildkitd.sock`. This is the only
normal backend for MDT builds, including release builds. The service is a
plain `docker run --cgroup-parent=dev-buildkitd.slice`; it does not rely on a
Buildx container-driver cgroup option.

The installer verifies Docker and Buildx before changing host state, waits for
the service socket, verifies the service container's image/cgroup/labels, then
creates or verifies the remote builder. It installs `/etc/profile.d/mdt-buildkit.sh`
with `BUILDX_BUILDER=mdt-managed` and the socket endpoint. The devcontainer
template repeats both variables explicitly and bind-mounts `/run/mdt-buildkitd`;
the mount is mandatory, so a missing host prerequisite fails container start.

The accidental-worker guard is an event-driven systemd service. It inspects
reserved `buildx_buildkit_*` and `buildkit_buildkit_*` candidates, approves only
the exact managed service identity (name, configured image, cgroup parent, and
labels), and logs every decision. `BUILDX_ACCIDENTAL_CONTAINER_POLICY` is the
closed vocabulary: `terminate` (default) removes an unapproved BuildKit worker;
`report-only` detects and logs it without removal. Missing or invalid policy
configuration stops the guard rather than weakening the boundary.

The wizard is slice-first. For every governed slice it asks `MemoryMin`,
`MemoryLow`, `MemoryHigh`, and `MemoryMax`; an operator may enter an explicit
empty value where that slice should omit a directive. It explains `MemoryMin`
as hard hierarchical protection, `MemoryLow` as soft best-effort protection,
`MemoryHigh` as soft reclaim throttling, and `MemoryMax` as the hard RAM cap.
It converts systemd binary units to KiB and rejects/re-prompts unless every
configured chain satisfies `Min <= Low <= High <= Max`. It also checks child
high/max/min totals against live host facts. `DEV_MEMORY_MIN_GUARANTEED_CEILING`
is the single authoritative root `dev.slice` MemoryMin and is mirrored on the
guaranteed sibling; `DEV_MEMORY_LOW/HIGH/MAX` are the other root controls.
Each child slice flow includes `CPUWeight`, `CPUQuota`, `IOWeight`, swap, and
zswap choices. See [`../docs/BUILD-ARCHITECTURE.md`](../docs/BUILD-ARCHITECTURE.md#managed-buildkit-backend)
for the design decisions and [`../docs/CONSUMERS.md`](../docs/CONSUMERS.md) for
pasteable consumer configuration.

## dev-gates: why

`dev-gates.slice` is a SIBLING of `dev-interactive.slice`/`dev-background.slice`
under `dev.slice`, not a child of either — every gate/lane container AND
every placed lane leaf (`rg-<token>`, created by a future host-level daemon
under `scripts/cgroup-profiler/`, out of this project's own scope) lives
under it instead of sharing `dev-background.slice` with long-running dev
stacks. It is the **admission capacity object**: its `memory.max` is what
`run-gate` (RG-56) and ciu v8 (S16.6.1) key admission decisions on, and its
`memory.pressure` is the pressure signal admission reads — not swap usage
(design doc `run-gate-project/nyxloom-trove/
DESIGN-2026-09-12-liveness-placement-admission.md` D-6/D-19).

Filed from a real, measured incident (memory `soulmask-memory-pressure-
findings.md`, 2026-08-04): gates used to share `dev-background.slice` with the
~4 GiB `dstdns` stack, so a gate started with only ~2 GiB of headroom before
`dev-background.slice`'s own `memory.high` began throttling it — an unrelated
long-running stack was silently eating a gate's budget. `dev-gates.slice`
gives gates their own ceiling, `systemd-oomd` protection (a gate is
disposable; a stack is not, D-19), and a `MemorySwapMax` sized against this
host's own real swap capacity rather than dev-background's.

**`dev-gates.slice` contains dev LOAD only — it does not hold the profiler
daemon.** An earlier design draft placed a `dev-infra.slice` for
`cgprofile-host-daemon` here too; that was withdrawn (design amendment A1/D-29,
same doc): the daemon is a host DEPLOYMENT, not dev load, and ships its own
top-level `cgprofile.slice` with the `cgroup-profiler` stack instead — see
that project's own docs, out of this project's scope. `dev.slice`'s job stays
"contain dev load and nothing else."

**Reactive per-container backstop (RW-32/D1).** `mdt-dev-cap-watcher.py`
also watches `dev-gates.slice` and applies a default `MemoryMax`/
`MemoryHigh`/`MemorySwapMax` the instant a new unlabelled `docker-*.scope`
appears there (`WATCHER_PER_CONTAINER_GATES_MEMORY_MAX`, default `4G` —
deliberately NOT the shared `WATCHER_PER_CONTAINER_MEMORY_MAX` default of
`1G`, which would be below the ~2 GiB of headroom the 2026-08-04 incident
says a gate already needed and would re-create it inside the tier built to
fix it). Today's run-gate lane containers pass no `--memory` of their own
and are exactly the containers this watcher exists for — the moment any
consumer honours `CGROUP_PARENT_DEV_GATES`, they land here instead of
`dev-background.slice`, unlabelled, and this is what keeps one runaway
lane from silently consuming the whole 6 G tier. This is the COARSE,
tier-wide backstop — it COMPOSES with, and is not withdrawn by, a future
daemon's per-lane placement caps (D-20/D-25): placement gives an exact
ceiling to a lane that asks for one, this watcher still catches whatever a
lane does not ask for.

**Sizing choices (RW-32/D2, D3).** `ManagedOOMSwap=kill` is deliberately
ABSENT from `dev-gates.slice.in` (unlike `dev-background.slice`'s own,
which keeps it): D-19 lists only the pressure kill, and this tier's
`MemorySwapMax` (auto-detected, see "Tiering model" above) exists
specifically to make swap a disposable lane's relief valve — an oomd
*swap* kill triggers on host swap usage and would kill whichever lane is
using the allowance this unit deliberately grants it, on a signal D-6
rejects ("swap usage is never the gate; PSI is"). See
`units/dev-gates.slice.in`'s own comment. `CPUWeight=20`/`IOWeight=10` —
identical to `dev-background.slice`'s own — stand as designed (D-19): a
fourth same-weight sibling does lower interactive's share under
simultaneous gates + background contention (the shared non-interactive
weight rises from 20, dev-background alone, to 40 once dev-gates.slice
joins it, so interactive's CPUWeight share drops from `200/220` (~91%) to
`200/240` (~83%) — the same ratio on the IOWeight side, `100/110` to
`100/120`, since both tiers scale interactive's own weight by the same
10x). **That 83%/83% pair is not the actual worst case** (round-2 review
S16): `dev-buildkitd.slice` (`CPUWeight=50`/`IOWeight=50`) and
`dev-memory_min_guaranteed.slice` (declares neither directive, so
systemd's own default of 100 applies to both) are also runnable
siblings. Recomputed from every rendered unit's own weight, all five
siblings contending at once: CPU `200/(200+20+20+50+100)` = **51.3%**
(`200/290` = 69.0% with buildkitd alone added, before the guaranteed
tier's default-100 also joins); IO `100/(100+10+10+50+100)` = **37.0%**.
Accepted because all five tiers rarely saturate CPU/IO simultaneously in
practice, and cgroup weights only bite under real contention in the first
place (an idle sibling claims nothing); revisit once real usage data
exists (design §7), not by guessing further.

Env keys (`host-setup.env.example`): `DEV_GATES_MEMORY_HIGH`/`_MAX`/
`_SWAP_MAX`/`_CPU_WEIGHT`/`_CPU_QUOTA`/`_IO_WEIGHT`/`_OOM_PRESSURE_LIMIT`/
`_ZSWAP_WRITEBACK`, sized for a 16 GiB host and expected to be re-tuned from
real admission-usage data (design doc §7: "measure first"); also gets a
runtime IOPS sub-ceiling from `DEV_SUBSLICE_IOPS_PCT` (dev.slice section, no
separate gates-specific key). `WATCHER_PER_CONTAINER_GATES_MEMORY_MAX` (the
reactive watcher's own knob, above). `CGROUP_PARENT_DEV_GATES=dev-gates.slice` travels the same
export path as `CGROUP_PARENT_DEV_INTERACTIVE`/`_BACKGROUND` —
`templates/devcontainer.json`'s `containerEnv` — with the same consumer
fallback rule (D-24), precisely stated: it protects a **devcontainer that
was not rebuilt** (the variable is simply absent from its environment until
recreated), **not** a **host that was not upgraded** (the slice unit
missing). That case is **NOT self-announcing**: a `--cgroup-parent` naming
a slice with no unit file fails **open** — systemd auto-creates an
unlimited transient slice and the container starts normally
(`units/docker-scope-default-limits.conf.in`, `AGENTS.md`), which is
exactly why the `docker-.scope.d` backstop exists. It is caught by
`mdt-host-check.sh` (`dev-gates.slice has no unit file`, a hard FAIL) and
by a consumer that verifies `LoadState=loaded` before launch, as
`AGENTS.md` already requires — **not** by docker (round-2 review B8;
round-1 review S10 asked for this rule stated precisely, and the round-1
repair's substitute claim — that `docker run` itself fails outright — was
wrong in the fail-open direction and is withdrawn here).

**Rebuild your devcontainer.** `containerEnv` changes only take effect on
container **recreate** — `CGROUP_PARENT_DEV_GATES` is not visible inside an
already-running devcontainer until it is rebuilt, even on a host that has
fully upgraded. Both operator sequences below end at `mdt-host-check.sh`;
after that, rebuild/recreate the devcontainer too so the new variable
actually reaches whatever tool inside it reads it (round-1 review S10).

**Onward propagation — declared here, not yet read anywhere (round-1
review S4, RW-32/D4).** `CGROUP_PARENT_DEV_GATES` is exported by this
package; no downstream consumer reads it yet — each is its own future work,
filed to the controller's backlog after this package merges, not part of
this package:
- `run-gate`'s own default `--cgroup-parent` (`run-gate-project/{SPEC.md,
  CONSUMERS.md}`) — still documents only the interactive/background pair;
- `cmru`'s tester-gate (`cmru/src/cmru/tester_gate.py`) — forwards only
  `CGROUP_PARENT_DEV_BACKGROUND` into spawned gate containers;
- `srdm`'s gate script (`shared-ramdisk-depot-manager/tools/
  cgroup-parent.sh`) — exports only the old pair.

Until one of these adopts it, every gate/lane container keeps landing in
`dev-background.slice` (today's placement, per D-24) regardless of whether
a host has `dev-gates.slice` installed — which is exactly what this
session's own `run-gate.py smoke` gate run shows in its argv (`--cgroup-
parent dev-background.slice`).

**The socket carrier's mount (RW-31/A2, D-30, M5).** `templates/
devcontainer.json` bind-mounts `/run/cgprofile` (the profiler daemon's
control-socket directory, `ctl.sock`) into the devcontainer; run-gate's
`transport = auto` prefers it when present and answering, else falls back
to `docker exec` — nothing breaks on a devcontainer that predates the
mount, `auto` simply resolves to exec forever for it. Group access needs no
new plumbing: the existing `--group-add ${localEnv:DOCKER_GID}` runArg is
exactly the socket's own group (`root:docker`, mode `0660`). Host
prerequisite: `mounts` entries are Docker `--mount`, which refuses a
missing bind source, so host-setup ships and applies a `tmpfiles.d` entry
that creates `/run/cgprofile` (mode `0770`, owner `root:docker`) — see
"Quick start"/"Upgrading a host that already runs mdt host-setup" below;
`mdt-host-check.sh` reports the
directory's own mode/owner and separately whether the daemon's socket is
present (`INFO socket carrier available`) or not yet (`INFO exec carrier
only (daemon socket absent)` — a live daemon that predates the listener, or
none installed at all, are both this state and both fine, exec is the
permanent fallback). The daemon package (P6) re-asserts the directory's own
owner/mode at its own start as belt-and-braces; this package only creates
it and gets out of the way. Opt out by deleting the `mounts` line.

## Changes

**2026-09-12 (RG-55 P8, round-1 repairs + M5):** `dev-gates.slice` shipped
(this section); round-1 review B1-B6 fixed (cap-watcher coverage,
renderer-test host-mutation guard, unbounded-upgrade detection in
`install.sh`/`check.sh`, corrected upgrade sequence below, a withdrawn-
design residue, renderer-test coverage of `install.sh`'s own wiring);
`ManagedOOMSwap=kill` dropped from `dev-gates.slice.in` (D2); M5 lands the
`/run/cgprofile` devcontainer mount + host-setup's `tmpfiles.d` entry
(D-30/A2). `TODO.md` is NOT edited by this package (dirty in the shared
checkout, see round-1 review S13/RW-32 D5) — the operator paste-in line is
in this package's own REPORT
(`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P8-REPORT.md`,
"Operator: add to TODO.md"). Round-2 review B7/B8 fixed: `check.sh`'s
byte-size parser now handles half-GiB and percentage values without a
bash syntax error or a false FAIL; the D-24 fallback note's substitute
claim about `docker run` was corrected to the verified fail-open
behavior. The tmpfiles entry is now `/etc/tmpfiles.d/mdt-cgprofile.conf`
(mdt-prefixed, matching every other drop-in this host ships).

**Ordering constraint (round-2 review S19, made explicit):** install this
version of host-setup on the host **BEFORE** anyone rebuilds a
devcontainer from `templates/devcontainer.json` — the new `/run/cgprofile`
mount is Docker `--mount`, which refuses to start a container at all when
the bind source directory does not exist, and that directory only exists
once this package's `install.sh` has run. Rebuilding a devcontainer from
an updated template against a host that has not yet been upgraded will
fail to start, not merely run with a missing feature.

**Fresh host, never ran mdt host-setup before:** the existing "Quick start"
section below already covers you end to end — it now seeds/renders
`dev-gates.slice` and the `/run/cgprofile` tmpfiles.d entry along with
everything else, one pass, nothing additional to do. Only a host that ran
`install.sh` BEFORE `dev-gates.slice` existed needs the different sequence
immediately below.

## Upgrading a host that already runs mdt host-setup

**From before `dev-gates.slice` existed:** a plain re-run of `install.sh`
alone renders `dev-gates.slice` UNBOUNDED, not merely "using old defaults" —
verified by reading `install.sh` end to end (round-1 review B4): its
`--force` branch (`cp` to back up, `cp` the example over `/etc/mdt/
host-setup.env`, echo "REVIEW IT and re-run to apply edits") has **no
`exit`** and falls straight through into sourcing the fresh config and
running apt-get, every unit render, the `daemon.json` merge, `daemon-
reload`, `systemctl start` of every slice and `systemctl enable --now` of
the timer/buildkitd/watcher — i.e. `--force` doesn't just re-seed the file,
it ACTIVATES the example's 16 GiB-host numbers estate-wide, live, in the
same run. On a host that isn't 16 GiB that is a real, if brief,
misconfiguration of every OTHER tier too, not only the new one — avoid it
for a routine key-pickup. `install.sh` now also prints a named `WARN` for
every key your `/etc/mdt/host-setup.env` predates (see below), so this is
no longer a silent trap even if you do forget a step. The additive sequence
below is one render pass and never discards existing tuning:

```bash
sudo cp /etc/mdt/host-setup.env /etc/mdt/host-setup.env.bak-$(date +%F)
diff <(grep -oE '^[A-Z_]+=' host-setup/host-setup.env.example | sort) \
     <(grep -oE '^[A-Z_]+=' /etc/mdt/host-setup.env | sort)      # shows the
                                                                   # new keys
sudo vi /etc/mdt/host-setup.env    # paste in the missing keys (the
                                    # dev-gates.slice block, DEV_CAP_GATES_
                                    # MEMORY_MAX); size them for this host
sudo ./install.sh                  # ONE render/install/activate pass --
                                    # now fails if a declared key is missing
sudo mdt-host-check.sh             # verify: dev-gates.slice's effective
                                    # memory.max/high now FAIL loudly (not
                                    # print unbounded silently) if a key is
                                    # still missing; /run/cgprofile exists
```
Then rebuild/recreate your devcontainer (above).

`sudo ./install.sh --force` remains available, but only for a scheduled
maintenance window (it re-renders and ACTIVATES every tier from the
example's own numbers live, across the whole estate, the instant it runs —
see above) — never for a routine key pickup. `sudo ./install.sh --wizard`
is the interactive alternative to the manual diff/edit steps above: it
backs up the same way, then walks every section it knows with your
EXISTING values pre-filled as defaults so Enter reproduces prior tuning
walks every governed slice's four memory controls and CPU/IO settings with
existing values pre-filled, and rejects hierarchy or live-host aggregate
violations before rendering.

## Quick start

```bash
sudo ./install.sh --wizard         # preferred: derive live host values and write the config
# or: sudo ./install.sh              # seed only; fill host-specific values before re-running
sudo vi /etc/mdt/host-setup.env    # only needed for the manual path
sudo ./install.sh --with-baseline  # re-render + measure disk ceilings (~4 min saturated IO — quiet window!)
sudo mdt-host-check.sh             # verify
```

Or, instead of the hand-edit step: `sudo ./install.sh --wizard` walks
`host-setup.env.example`'s own sections interactively (IO device, IO cap
percentages, every governed slice's four memory controls, CPU/IO weights,
the memory-min-guaranteed ceiling, buildkitd, Docker daemon.json keys) and
proposes starting numbers scaled off THIS host's own live `/proc/meminfo`
instead of the shipped example's fixed figures — Enter accepts the shown
default at every step, and it falls
through into the same render/apply logic either way. Both paths write the
same `/etc/mdt/host-setup.env`; the raw file is still there for operators
who'd rather edit it directly (`--wizard` never replaces it silently — it
only runs when you pass the flag, and backs up any existing config first).

Then recreate the containers that should be governed (placement is
create-time): rebuild the devcontainer, `docker compose up -d --force-recreate`
the test stacks.

## What gets installed

| Artifact | Target | Role |
|---|---|---|
| `units/dev.slice.in`, `units/dev-interactive.slice.in`, `units/dev-background.slice.in`, `units/dev-gates.slice.in`, `units/dev-memory_min_guaranteed.slice.in`, `units/dev-buildkitd.slice.in` | `/etc/systemd/system/*.slice` | the tiers — **rendered** from `/etc/mdt/host-setup.env` |
| `units/mdt-buildkitd.service.in` | `/etc/systemd/system/mdt-buildkitd.service` (rendered, enabled) | host-managed rootless BuildKit worker — `docker run --cgroup-parent=dev-buildkitd.slice` as `ExecStart=`, see `plan-buildkitd-service.md` |
| `units/mdt-buildkit-guard.service`, `scripts/mdt-buildkit-guard.py` | systemd (enabled) + `/usr/local/sbin/` | Docker-events guard for unapproved Buildx/BuildKit workers |
| `scripts/mdt_buildkit_builder.py` | `/usr/local/sbin/mdt-buildkit-builder.py` | idempotent `mdt-managed` remote registration/verification; never creates a container-driver worker |
| `etc/profile.d/mdt-buildkit.sh` | `/etc/profile.d/` | host-shell `BUILDX_BUILDER` and `BUILDKIT_HOST` exports |
| `units/docker-scope-default-limits.conf.in` | `/etc/systemd/system/docker-.scope.d/50-default-limits.conf` | D-G8 backstop — a generous "never truly unbounded" floor for EVERY container's transient scope, regardless of which slice (or none) it named |
| `units/mdt-host-slices.service` | systemd (enabled) | boot-time apply of the runtime half |
| `units/mdt-host-slices.timer.in` | systemd (enabled) | periodic re-apply (default 5min) — now a backstop, see "Persistence model" |
| `units/mdt-io-cap-watcher.service` | systemd (enabled, unconditional) | reactive per-container IO caps via `docker events` |
| `units/mdt-dev-cap-watcher.service` | systemd (enabled iff `INOTIFY_OK=1`) | reactive per-container `MemoryMax` via inotify on cgroupfs |
| `scripts/mdt-apply-dev-caps.sh` | `/usr/local/sbin/` | runtime half (see below) |
| `scripts/mdt-io-cap-watcher.sh` | `/usr/local/sbin/` | `docker events` watcher — instant counterpart to the sweep's per-container IO caps |
| `scripts/mdt-container-caps.lib.sh` | `/usr/local/sbin/` | shared `_mdt_*` matching/cap-application functions — sourced by both of the above, one definition of "how a container gets capped" |
| `scripts/mdt-dev-cap-watcher.py` | `/usr/local/sbin/` (iff `INOTIFY_OK=1`) | inotify watcher — instant counterpart to the sweep's per-container `MemoryMax` |
| `scripts/mdt-slice-audit.py` | `/usr/local/sbin/` | read-only audit — logs a `[WARN]` for any `memory.min`/`memory.low` under `dev.slice` that is a silent no-op because an ancestor lacks its own value (second `ExecStart=` on the same service/timer) |
| `scripts/mdt-io-baseline.py` | `/usr/local/sbin/` | fio benchmark → `/var/lib/mdt/io-baseline.env` (30-day cache) |
| `mdt-host-setup-wizard.py` (a sibling of `install.sh`, not under `scripts/`, because it is never installed onto the host) | not installed — run from this directory via `install.sh --wizard`, or directly as `sudo ./mdt-host-setup-wizard.py` to reconfigure an already-installed host | interactive `/etc/mdt/host-setup.env` builder (see Quick start); template surgery on `host-setup.env.example`, never generated from scratch |
| `scripts/check.sh` | `/usr/local/sbin/mdt-host-check.sh` | health check, non-zero exit on failure |
| `etc/modules-load.d/bfq.conf`, `etc/udev/rules.d/60-bfq-scheduler.rules` | `/etc/…` (`mdt-` prefixed) | BFQ at boot so IO weights bite |
| (merged, not copied) | `/etc/docker/daemon.json` | `cgroup-parent` (D-G7 default) + `live-restore`/log rotation — this is the file's ONE owner now; every key is merged in, nothing else in the file is touched |

## Persistence model — why units AND a service/timer

The managed BuildKit service and named remote are persistent state. Re-rendering
the service and restarting it interrupts active builds; the installer says so
and waits for the new socket before verifying/reusing the builder. Existing
`mdt-managed` drift is refused rather than deleted. Schedule the restart, run
host setup, then rebuild devcontainers so their mandatory socket mount and
explicit environment are present. A running devcontainer is not required to
register the builder.

Reboot-survival works in three layers; each exists because the previous one
cannot express the next:

1. **Static slice units** (`/etc/systemd/system/*.slice`) — memory knobs,
   weights, `ManagedOOM*`, zswap-writeback policy (systemd ≥ 256), and
   deliberately **tight** static IO caps as boot-window fallback. Survive
   reboot and `daemon-reload` by themselves; zero runtime machinery. Rendered
   from `host-setup.env` at install time so per-host tuning stays in one
   reviewable file.
2. **Boot service + periodic timer** (`mdt-host-slices.service/.timer` →
   `mdt-apply-dev-caps.sh`) — everything units *can't* declare:
   - the **measured** whole-estate IO caps on `dev.slice` (`DEV_IO_CAP_PCT`%
     of the fio baseline — covers `dev-interactive.slice` +
     `dev-background.slice` combined) — `systemctl set-property --runtime`,
     reapplied each boot;
   - **per-container** caps for `buildx_buildkit_*`, `*test-runner*` and
     devcontainer scopes (`SWEEP_IO_CAP_PCT`% io.max; bench and buildkit
     additionally get `IOWeight=1` — the devcontainer does **not**, it is the
     IDE): docker scopes are *transient*, they only exist while the container
     runs, so no unit file can pre-configure them, and buildkit workers are
     created on demand by buildx AND — source-verified, see
     [BUILD-ARCHITECTURE.md](../docs/BUILD-ARCHITECTURE.md) — Buildx's
     `cgroup-parent` driver-opt is unreliable under the systemd cgroup driver,
     so they can never be placed under `dev.slice` via compose either; this
     is still their only *placement-independent* governance, full stop, not a
     backstop for a placement mechanism that also works. **This sweep is the
     BACKSTOP, not the primary mechanism:** `mdt-io-cap-watcher.service`
     applies the same caps within the same second a matching container
     starts, via `docker events` rather than periodic re-scanning — see
     "Reactive counterpart" below for why `docker events` and not inotify
     (the `mdt-dev-cap-watcher.py` pattern). The sweep still matters for
     whatever the watcher missed across its own restart window, and it
     shares every line of matching/application logic with the watcher
     (`mdt-container-caps.lib.sh`) so the two can't drift apart. Anything
     `cmru`/mdt itself spawns directly gets explicit
     per-container flags instead (see "Tiering model" above) — this sweep
     (and the watcher) are only for containers nobody's own code controls
     the invocation of;
   - a **cgroup2 mount-flag check**: `memory_recursiveprot` (without which
     every slice-level `MemoryLow`/`MemoryMin` silently stops protecting the
     container pages below it) is a systemd boot default, but a runtime
     remount can strip it — `CGROUP2_FLAGS=warn|fix` in the env file;
   - a **read-only ancestor-chain audit** (`mdt-slice-audit.py`, a second
     `ExecStart=` on the same service): even with `memory_recursiveprot`
     correctly mounted, `memory.min`/`memory.low` protection is bounded by
     EVERY ancestor cgroup's own value, not just the one it's set on — a
     value declared anywhere under `dev.slice` (a stack's governance config,
     a hand-set property, a future per-container mechanism) is a complete
     no-op if `dev.slice`/`dev-background.slice`/`dev-interactive.slice`
     themselves don't ALSO carry one (which, as shipped, they mostly don't —
     see `CGROUP-NOTES.md`). This never applies anything; it only logs to
     the journal so the gap is discoverable instead of silent.

**Reactive counterpart to layer 2's per-container caps.**
`mdt-io-cap-watcher.service` (`mdt-io-cap-watcher.sh`) watches `docker
events` for container starts and applies the same
test-runner/buildkit/devcontainer IO caps within the same second, so the
timer sweep above is a backstop rather than the only mechanism. This is a
DIFFERENT reactive mechanism than `mdt-dev-cap-watcher.py`'s inotify watch
(RW-32/D1, above) on purpose, not by oversight: that watcher watches a
FIXED, already-known cgroup path (`dev-interactive.slice`/
`dev-background.slice`/`dev-gates.slice`) because its targets ARE reliably
placed there at create time — it only reacts to attributes WITHIN a cgroup
Docker already placed correctly. `buildx_buildkit_*` workers are the case
layer 2's own bullet above already documents as NOT reliably placed
anywhere (Buildx's `cgroup-parent` driver-opt unreliable under the systemd
cgroup driver) — there is no fixed path to inotify-watch for them in the
first place. Verified live 2026-09-12: one landed at a malformed, unnested
`system.slice/dev-background.slice:docker:<id>`, a name that only *looks*
like the real slice. `docker events` sidesteps this because it comes from
the daemon's own bookkeeping regardless of where a container's cgroup ended
up — the same property that makes it the right tool for `buildx_buildkit_*`
made it the simpler choice for `*test-runner*`/devcontainer matches here
too, rather than splitting those onto `mdt-dev-cap-watcher.py`'s mechanism
and leaving only `buildx_buildkit_*` on this one. Both reactive watchers
share the "restart/failure modes" mitigation the alternatives-considered
paragraph below settled on: `Restart=always`, and the periodic sweep kept
running as backstop for the restart window.
3. **Create-time placement** — the one thing the host cannot do at all:
   containers join their tier only where they are *created*
   (devcontainer.json `runArgs`, compose `cgroup_parent:`, or the Docker
   daemon-wide default in `daemon.json`). Graceful degradation: if the unit
   file is missing, systemd invents a transient *unlimited* slice of the same
   name and the container starts normally — the `docker-.scope.d` backstop
   (D-G8, see "What gets installed") exists specifically to put a floor under
   that failure mode.

**The `daemon.json` `cgroup-parent` default does not reach BuildKit's own
build-step execution (verified live, 2026-09-12).** It correctly places a
normal `docker run`/`docker create` container (confirmed: the devcontainer
itself lands at `dev.slice/dev-interactive.slice/docker-<id>.scope` with the
expected `io.max`) — but a plain `docker build` or `docker buildx build`
using the **default `docker` driver** (no separate buildx worker container at
all; the build executes inside `dockerd`'s own embedded BuildKit) does not
reliably land its RUN-step exec cgroup under the real `dev.slice` hierarchy
either: caught one live mid-build at a malformed, unnested
`system.slice/dev-background.slice:docker:<id>` — a flat, colon-suffixed name
that only *looks* like the real `dev-background.slice`, sitting outside
`dev.slice` entirely and outside any of this host's IO governance. This is
the same class of problem already documented above for Buildx's
`docker-container` driver (`cgroup-parent` unreliable under the systemd
cgroup driver) — it isn't limited to separately-created buildx workers; the
default, no-driver-specified build path has it too.

**Use `mdt-buildkitd` for anything that actually needs governed build IO** —
it's the one build path confirmed working, precisely because it sidesteps
this: a plain `docker run --cgroup-parent=dev-buildkitd.slice`
(`mdt-buildkitd.service`), never through any Buildx driver, so there's no
BuildKit-internal cgroup-naming logic in the way — `dev-buildkitd.slice`
shows the correct `io.max`/`io.weight` every time. `templates/devcontainer.json`
ships the socket bind mount + `BUILDKIT_HOST=unix:///run/mdt-buildkitd/buildkitd.sock`;
once a consumer's devcontainer sets `BUILDKIT_HOST`,
`scripts/finalize_container_environment.py`'s `setup_buildkit_builder()`
validates and reuses the exact `mdt-managed` remote on every container start.
A missing or inconsistent variable, endpoint, builder, or host service is an
error; it never falls through to the embedded builder or silently creates a
per-container worker.

Alternatives considered for layer 2: a boot-only oneshot misses buildkit
workers created mid-session; a docker-events watcher daemon reacts instantly
but is a long-running process with restart/failure modes — the idempotent
timer sweep was, for a while, the smallest thing that stays correct.
**Sub-interval enforcement did end up mattering (2026-09-12)**:
`mdt-io-cap-watcher.service` is exactly the docker-events hook this
paragraph anticipated, with the timer kept as backstop — `Restart=always`
plus that backstop is judged sufficient mitigation for the restart/failure-
mode concern that originally held this back (systemd's own restart
machinery, not a hand-rolled retry loop).

Full reasoning for each gap, and why raw cgroupfs writes lose to
`set-property`: [CGROUP-NOTES.md](CGROUP-NOTES.md).

## The IO baseline

`mdt-io-baseline.py` measures 4 sustained ceilings (r/w IOPS at 4k QD32, r/w
bandwidth at 128k QD8, libaio, incompressible buffers, ramp+runtime defaults
10+40s) and caches them as `KEY=VALUE` in `/var/lib/mdt/io-baseline.env`
(atomic write, 30-day freshness, `--force` to remeasure). **It saturates the
disk for ~4 minutes** — run it in a quiet window.

The caps derived from it sit in a **60–80% band** of the measured ceiling:
`DEV_IO_CAP_PCT=60` for the whole `dev.slice` estate (it bounds 10–15+
containers across both tiers together — protects PRODUCTION from the tier,
but not tier members from each other), `SWEEP_IO_CAP_PCT=80` per
bench/buildkit/devcontainer container (protects tier members from each
other — for buildkit specifically, its *only* governance, since Buildx
placement under `dev.slice` doesn't work at all; see above). Never 100% — a
saturated device queues everything behind the burst, which is the stall the
tiering exists to prevent; below ~60% you are just throttling ordinary work.
Where both apply, cgroup limits nest and the stricter wins — the two are
complementary layers answering different questions, not a redundant pair to
collapse into one number.

**Bootstrapping from gstammtisch.** `install.sh` copies
`/var/lib/gstammtisch/io-baseline.env` to `/var/lib/mdt/io-baseline.env` on
first run if the latter doesn't exist yet and the former does, rather than
re-running the ~4min benchmark — mdt owns its own copy at its own canonical
path from then on (no runtime cross-reference between the two companions).

**Sharing the measurement with ciu.** ciu governance caps individual compose
services from the same file format (deriving `read_iops` as 2/3 of
`RIOPS_MAX` — same band), but searches its own path, *not* `/var/lib/mdt/`.
Measure once and point ciu at it, so the tier caps and the per-service caps
can't disagree:

```bash
echo 'CIU_GOV_BASELINE_PATH=/var/lib/mdt/io-baseline.env' >> /etc/environment
```

(Or set `IO_BASELINE_ENV=/var/lib/ciu/io-baseline.env` in `host-setup.env` and
let ciu find it at its own default.) Reusing a baseline measured on comparable
hardware: point `IO_BASELINE_ENV` at it or copy the file.

## Verification

`mdt-host-check.sh` checks: `memory_recursiveprot` mount flag, unit presence +
activity (`dev.slice`, `dev-interactive.slice`, `dev-background.slice`),
effective cgroupfs values (including `io.bfq.weight` next to `io.weight` —
under BFQ only the former is what schedules), zswap-writeback policy,
`dev.slice`'s `io.max` + baseline freshness, the `docker-.scope.d` backstop's
presence, BFQ scheduler, timer enablement, and lists every running
container's cgroup parent. Exit 0 = no failures (warnings possible).

The one failure it reports as FAIL rather than WARN is a missing
`memory_recursiveprot`: with that flag absent every `MemoryLow`/`MemoryMin` in
both tiers protects nothing, while `systemctl show` still reports the value you
set. See [CGROUP-NOTES.md §5](CGROUP-NOTES.md#5-cgroup2-mount-options--not-a-unit-setting-at-all).

## Uninstall

```bash
sudo systemctl disable --now mdt-host-slices.timer mdt-host-slices.service mdt-buildkitd.service \
        mdt-io-cap-watcher.service mdt-dev-cap-watcher.service
sudo docker volume rm mdt-buildkitd-cache 2>/dev/null || true
sudo rm /etc/systemd/system/{dev,dev-interactive,dev-background,dev-gates,dev-memory_min_guaranteed,dev-buildkitd}.slice \
        /etc/systemd/system/mdt-buildkitd.service \
        /etc/systemd/system/mdt-io-cap-watcher.service /etc/systemd/system/mdt-dev-cap-watcher.service \
        /etc/systemd/system/docker-.scope.d/50-default-limits.conf \
        /etc/systemd/system/mdt-host-slices.{service,timer} \
        /usr/local/sbin/{mdt-apply-dev-caps.sh,mdt-io-cap-watcher.sh,mdt-container-caps.lib.sh,mdt-dev-cap-watcher.py,mdt-slice-audit.py,mdt-io-baseline.py,mdt-host-check.sh} \
        /etc/modules-load.d/mdt-bfq.conf /etc/udev/rules.d/60-mdt-bfq-scheduler.rules
sudo systemctl daemon-reload
sudo rm -rf /etc/mdt /var/lib/mdt        # config + cached baseline
# containers keep their (now transient, unlimited) slices until recreated.
# /etc/docker/daemon.json is NOT removed here — it's a merge, not a wholesale
# install; manually drop the cgroup-parent/live-restore/log-opts keys you no
# longer want and `systemctl restart docker` if you do.
```
