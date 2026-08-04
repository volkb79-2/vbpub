# srdm — the plan

**This document is authoritative.** It replaces
`../../wings-cgroups/shared-ramdisk-update-lifecycle-5-fable.md` (the old
master plan) and `...-cgroups-2-fable.md` (its resources companion) as the
thing a session reads before doing anything. Those two are now **historical**
— see §Historical documents at the end for what they still hold and when to
open one.

Why it moved here, stated once so nobody re-litigates it: the old plan was a
*program* document spanning Wings and the manager, written before either
existed. Wings' share has since collapsed to one small patch (§Direction),
v1 of the manager is built and its behaviour is measured rather than
predicted, and **the old plan is now wrong in twenty-odd specific places**
where the kernel disagreed with it (§The measured ground). A plan you have to
read alongside a correction list is not a plan. This one is corrected in
place.

Companion documents in this trove, unchanged in role: `roadmap.md` (phase and
package order), `decisions.md` (every `D-NNN`), `backlog.md` (un-carved work),
`GUIDE.md` (how to operate the project and its gates), `reports/` (one LOG per
finished package).

---

## The product in one page

N game servers on one host each want their own copy of a multi-gigabyte
content tree. That means N copies in page cache, N independent updates, and no
way to say "these two servers are running the same bytes".

srdm makes that content a **release** — every file hashed, every path
classified, probes asserted, the whole thing frozen and immutable — and then a
**generation**: that release resident in tmpfs, held by per-class systemd
units that carry its memory policy, sealed read-only, and bind-mounted into
each consuming server's volume. One copy of the pages, shared by every
consumer, provably identical to the release, and structurally impossible for a
server to corrupt.

```
content on disk
  → srdm store promote    hash, classify, probe            → an immutable release
  → srdm activate         publish into tmpfs, hold, expose → a live generation
  → [servers run against the shared read-only content]
  → srdm attach/detach    add or remove a consumer
  → srdm harvest          fold an in-place update back into a NEW release
  → srdm rollback         swap back to the previous release
  → srdm gc               drop releases nothing points at
```

One static Go binary, **zero third-party dependencies** (deliberate: it keeps
the gate hermetic and the privilege surface small). No daemon — every
state-changing operation is one root process under an `flock` (D-025).
`srdm-restore.service` rebuilds every assignment at boot; `srdm reconcile`
repairs drift on demand.

### The three ideas the design rests on

1. **Intent and reality are separate; reconciliation is the comparison.** What
   an operator asked for is *recorded* (`internal/assign`). Who is actually
   holding a generation is a *kernel fact*, resolved fresh from
   `/proc/*/mountinfo` at every ask (`internal/consumer`) and never stored.
   Neither is derived from the other. Recording reality, or having to measure
   intent, is what makes systems that disagree with themselves (D-018, D-024).
2. **The charge and the policy cannot separate.** Each class's pages are
   faulted by that class's own hold-unit worker, *inside* the cgroup carrying
   that class's `memory.min`/zswap policy — never applied afterwards to pages
   charged somewhere else (D-011, D-013).
3. **Nothing becomes visible until it is whole.** Population happens in an
   operation-private tmpfs; a generation appears only as a read-only bind of
   an already-verified, already-sealed tree. Nothing is renamed into
   visibility and nothing visible was ever writable.

---

## Invariants

These are the properties that must never break. Each is pinned by an oracle;
a change that breaks one is a product decision, not a refactor.

1. **A release is immutable.** Once `COMPLETE` is written and fsync'd, its
   content never changes. Updates make a *new* release.
2. **A published generation matches its release.** Byte for byte, per the
   manifest — except where `access: rw` has deliberately been granted, which
   is marked and visible (`dirty_capable`).
3. **Per-instance state is never shared and never touched.** `WS/Saved/**` and
   anything else in an `excluded` class is never published, never bound, never
   used as content input, never modified by a transaction (D-023).
4. **The memory is genuinely shared and genuinely accounted.** One copy of the
   pages for N consumers, charged to the cgroup that carries the class's
   policy, and it comes back on teardown.
5. **Teardown never leaks.** srdm refuses to tear down content something is
   still holding, and names what to stop. Every step of an unguarded teardown
   *succeeds* and frees nothing, so the check has to be inside the operation
   rather than in front of it (D-018).
6. **A refusal always names the remedy.** A refusal with no fix attached is an
   outage with extra steps; a test asserts none ships without one.
7. **Managed content never appears under a server's host volume path** — *this
   one is waived, deliberately and boundedly, by `host-bind`.* It is why
   `provider` exposure exists as a future option, and the yardstick host-bind
   is measured against, not a property srdm currently has. The waiver's bill is
   itemized in §Exposure, not discovered.

---

## Architecture as built (v1 — complete)

```
     ┌─ store ──────────────────────────────────────────────────┐
     │ transaction → classify → per-file SHA-256 manifest →     │
     │ probes → ownership normalization → fsync'd COMPLETE →    │
     │ atomic channel symlink flip                              │
     └──────────────────────────┬───────────────────────────────┘
                                │  a verified immutable release
     ┌──────────────────────────▼───────────────────────────────┐
     │ publication                                              │
     │  1. set the generation slice's floor (BEFORE anything     │
     │     runs in it — D-016)                                   │
     │  2. mkdir + mount op tmpfs, 0700, nodev,nosuid[,noexec]   │
     │  3. start srdm-hold-<g8>-<class>.service: the worker      │
     │     populates, VERIFIES every file, seals a-w, signals    │
     │     READY, then parks (D-011/D-013)                       │
     │  4. bind → remount,ro,bind  (two calls; one would leave   │
     │     it writable)                                          │
     │  5. fsync the published-state record — only now usable    │
     └──────────────────────────┬───────────────────────────────┘
                                │  a live generation
     ┌──────────────────────────▼───────────────────────────────┐
     │ EXPOSURE DRIVER                        ← the only fork    │
     │   host-bind   bind into the volume path        ← v1, built│
     │   provider    Docker mounts + leases           ← optional │
     └──────────────────────────────────────────────────────────┘
```

**Packages** (`internal/`): `config` layout · `profile` classification and
class policy · `store` the transactional release store · `journal` durable
records + JSONL + journald · `fsx` durability primitives · `cgroupfs` v2
attribute reads · `systemdx` transient units via the systemd CLI (D-009) ·
`mountinfo` with propagation · `publish` topology, reconciliation and repair ·
`hold` units, policy, worker · `consumer` who is holding what · `expose`
drivers · `wings` the node's propagation and chown-walk facts · `harvest` ·
`assign` declared intent · `opctl` the order, the lock, restore and reconcile.

### Classes and memory policy

A profile assigns every path to a class, and a class carries its own tmpfs and
its own memory policy — which is the whole reason classes exist:

```json
{ "name": "pak",  "kind": "managed", "memory_min": 157286400,
  "zswap_max": 0, "zswap_writeback": true, "noexec": true }
{ "name": "code", "kind": "managed", "memory_min": 209715200 }
{ "name": "state","kind": "excluded", "paths": ["WS/Saved", "WS/Config"] }
```

`zswap_max: 0` means *bypass zswap entirely* — correct for incompressible pak
data (1.006× measured on the case-study node) and not the same as leaving the
knob alone, which is why these are pointers in Go. `excluded` content is
classified so it does not block promotion, and then never published.

### Exposure — and the bill for host-bind's waiver

`host-bind` binds a generation's class trees directly under
`/var/lib/pterodactyl/volumes/<uuid>/...`. That waives invariant 7, and the
consequences are known rather than discovered: Wings' own filesystem
operations (the pre-boot chown walk, disk accounting, backups, SFTP, archive
extraction) all walk that same host tree and will therefore *see* managed
content. Three preconditions refuse rather than warn:

1. `/var/lib/pterodactyl` bound `rslave` with a `shared` host peer group.
   Under Docker's default `rprivate` every mount srdm makes is invisible to
   Wings and every unmount leaves a ghost — the 2026-07-31 outage.
2. For `access: ro`, either **F1** in the running Wings build, or the node
   setting `system.check_permissions_on_boot: false`.
3. Affected consumers stopped.

---

## The measured ground

**This is the section the old plan does not have, and the reason it had to be
replaced.** Every item below is a place where the design was plausible and the
kernel disagreed. Treat any unit shape, slice name or propagation claim you
find in a historical document as a *sketch*, and probe before building on it.

| what the design assumed | what was measured |
|---|---|
| hold units are `Type=oneshot` + `RemainAfterExit=yes` | systemd 257 **reaps an active-but-empty unit's cgroup**; the content reparents and the class floor is arithmetically dead. Hold units are `Type=notify` + `NotifyAccess=main` with a worker that **parks after populating**. `Type=exec` is not enough — it marks active at *exec*, racing anything that acts on the content. A healthy hold unit's `SubState` is `running`, never `exited`. (D-011, D-013) |
| the generation aggregate is `srdm-gen-<g8>.slice` | systemd reads `-` as the slice hierarchy separator, so that nests under an auto-created `srdm-gen.slice` with `memory.min=0`, and a v2 floor is capped by **every** ancestor's — every class floor beneath it silently dead. It is `srdm-<g8>.slice`, and it gets the **sum** of the class floors, since v2 prorates a parent's protection. (D-015) |
| the slice's floor can be set as it comes up | it must be set with `systemctl set-property --runtime` *before the first hold unit starts*, and teardown must stop the slice **explicitly** — a slice stays active and keeps its cgroup after its last service exits. (D-016) |
| an open fd is the teardown hazard | an open fd makes unmount fail `EBUSY` rather than leaving a ghost. The hazard is a **surviving mount**, which is why holder resolution reads `/proc/*/mountinfo`. (D-012) |
| downstream mount copies can be filtered out by propagation tag | **they cannot.** A copy carrying `master:<our peer group>` looks downstream but can *survive the host unmount with its content readable*, and mountinfo does not distinguish that from one the unmount removes. So every mount of the superblock counts as a hold, and publication binds its op root onto itself + `MS_PRIVATE` so copies are never handed out. Marking private *after* mounting is too late. (D-019) |
| an `rw` exposure can bind the published path | a bind **inherits its source's per-mount flags**, so it cannot: `ro` and `rw` bind *different mount points of the same superblock*. (D-020) |
| making the mount writable is enough for `rw` | publication does `chmod -R a-w`, so a writable mount still refuses every uid but root — and the updater is not root. `rw` must also **unseal** and `lchown` to a declared owner; undeclared, it refuses. Prove this as an unprivileged uid: root writing through proves nothing. (D-022) |
| `systemd-run` returns and then you wait | it **blocks on the start job**, and for `Type=notify` that job ends at READY — a failing worker reports through the start call. Read the worker's status from `ExecMainStatus`, guarded on `ActiveState=failed` + `Result=exit-code`: a *running* unit's `ExecMainStatus` is 0, and believing it turns a failed start into a success. (D-017) |
| a published record proves the generation is there | it proves nothing about topology. After a reboot **every** record survives naming mounts that do not. Trusting one without asking the kernel was a real bug, fixed by `Publisher.IsComplete`. (P08b) |
| overlayfs could give vanilla Wings a writable view cheaply | **a no-op chown still forces a full data copy-up.** overlayfs does not compare values. A simulated Wings walk over a tree *already correctly owned* duplicated 40/40 files and the entire tree into the upper layer. `metacopy=on` reduces it to metadata but is **off by default**, must be enabled host-wide, and does not cover `touch`. (D-027) |

Two smaller ones that cost real time: Go's `Perm()` silently drops
setuid/setgid/sticky, so `os.Chmod(p, info.Mode().Perm())` strips them — it
was in two places until P07. And **any e2e case that publishes must own its
release ids**: the release id fixes the generation id, which fixes the
transient unit names, and two tests sharing one race for a unit systemd has
not reaped. Prefix with `t.Name()`.

---

## Direction

### 1. The cluster is an ordered cohort, and updating it is one command

**M2's exit condition.** A cluster has a **main** server and **slaves** that
connect to it. That makes a content update an *ordered cohort cycle*, and
specifically **not** a rolling one — a slave running against a main on a
different content version is a broken cluster, so "one at a time, at most one
down" is the wrong shape however attractive the downtime figure looks.

```
stop every SLAVE  →  stop MAIN  →  switch the release  →
start MAIN  →  wait for MAIN readiness  →  start every SLAVE
```

Three things follow, and each is a change to something that already exists:

- **The assignment gains roles.** `assign.Server` is `{ID, Access}` today;
  it needs `Role` (`main`|`slave`), exactly one main per profile. A v1
  assignment document has no roles and must be **refused with a fix** rather
  than migrated — guessing which server is main is guessing which one takes
  the cluster down.
- **srdm gains a power surface.** It cannot stop or start a server, and must
  **not** do it through Docker despite holding that socket: Wings owns the
  container lifecycle, and a container dying underneath it is a crash it
  will act on, racing the very swap in progress. `internal/power` talks to
  **Wings' own node API** — `POST /api/servers/<uuid>/power`, authorized by
  the `token` in Wings' `config.yml` — rather than to the Panel, so an
  update still works when the Panel is unreachable.
  `wings-cgroups/wingsctl/wingsctl.py` is the working reference. The token
  is a **secret** and goes through the journal's scrubber.
- **srdm gains a readiness gate**, below, which turns out to matter well
  beyond this.

Two generations still coexist briefly — publish happens before teardown so
that a failed publish leaves something to restart on — but the constraint is
softer than a rolling design would need, because the servers' own memory is
freed while they are stopped. It is refused up front rather than discovered
with a cohort half down.

### 2. Readiness is a log match — the same mechanism Wings already uses

A server is ready when a configured pattern appears in its **console log**.

**This is not a workaround, and an earlier draft of this document was wrong
to call it a weaker interface than a structured event.** Wings itself gates
"the server has started" on a log match: the egg's `config.startup.done` —
`"Create Dungeon Successed"` for Soulmask. A structured readiness signal
emitted by Wings would therefore be *derived from a log match*, and srdm
matching the same class of line loses nothing relative to the status quo. The
pattern also lives somewhere versioned and operator-owned — the egg — rather
than being an unversioned accident of a log format.

**Two distinct lines, and they are not interchangeable.** The v1 cgroup patch
stack already needed this distinction and encodes it:

| what | where | Soulmask value |
|---|---|---|
| Wings thinks it started | egg `config.startup.done` | `Create Dungeon Successed` |
| the game is actually serving | egg var `WINGS_CG_STEADY_MATCH` | `registe server soulmask session succeed` |

A cohort update must gate the slaves on the **second**. Starting them when
Wings merely thinks main is up risks them connecting to a server not yet
accepting sessions.

The egg's `WINGS_CG_PHASE_EVENTS` also establishes the format to adopt rather
than invent — `name=pattern`, with a `regex:` prefix to escape from substring
to regular expression. srdm's profile carries the same shape, because the
ready-line is a property of the **game build**, which is what a profile
describes, and it should travel with the content.

Two properties stay load-bearing. **Only lines from the current start may
count** — Wings recreates the container on start, so the log should be fresh,
but that must not be the only defence: anchor on a marker taken when the
start is issued. And a **timeout fails the operation** rather than falling
through to assume-ready.

**Why this reaches past M2.** §Direction 3 states the Wings contract as *two*
patches — placement, and a readiness signal for staged startup→steady bands —
on the reasoning that "the server finished starting" is a Wings-side fact
srdm could only infer. That reasoning does not survive the observation above:
the fact is already published as a log line, by an egg the operator already
maintains, and reading it is not inference. **M4's Wings contract therefore
reduces to one patch: placement.** P14 builds the matcher, so M4 will decide
with a working implementation rather than a prediction.

### 3. srdm owns node resources; Wings becomes a runner

**The decision this plan exists to record.** The old resources series (R1–R8)
put a resource-policy engine *inside* Wings, configured through `WINGS_CG_*`
egg variables. That is being replaced, on one measured asymmetry:

> **Placement and properties are two independent axes, and only placement
> needs Wings code.**

Setting `memory.min` / `cpu.weight` / `io.weight` on a slice is a host-side
operation srdm already performs for generation slices, having already learned
the hard parts (D-015, D-016). What srdm *cannot* do is put a container's
cgroup under a chosen slice — that is `HostConfig.CgroupParent`, set at create
time by whatever creates the container.

So "Wings as a pure runner" reduces to a **contract of one value**: *place
server `<uuid>`'s container under a predictable cgroup parent.* Everything
else — what the limits are, when they change, budget policy, reconciliation,
reporting — moves host-side into srdm.

Why, strongest argument first:

1. **Content and servers are the same RAM budget, and nothing reconciles them
   today.** srdm holds N GB of tmpfs under `srdm.slice` with `memory.min`
   protection; `wings.slice` holds the servers with their own floors; no
   component knows both numbers. That is a latent overcommit on the
   case-study node right now. One owner means `doctor` can check the host's
   actual RAM against the sum — the same shape of check it already does for
   class floors versus `srdm.slice`.
2. **It is the loop srdm already is**: declared intent → measure reality →
   reconcile. `opctl.Reconcile` is that engine, already built and gated.
3. **Policy lives in one file you can diff**, instead of smeared across
   per-server Panel egg variables with admin-only overrides and no validation
   feedback.
4. **The upstream story gets far easier.** "Let a node administrator place
   server containers in a cgroup slice" is an easy sell; "here is a resource
   policy engine with profiles, bands, budgets and field overlays" is not.
   The less Wings code, the less the design is hostage to review.

**What looked like a catch, and is not.** Staged startup→steady bands need a
*readiness* signal, and "the server finished starting" is a Wings-side fact.
This was written as needing a second Wings patch, on the reasoning that srdm
could otherwise only infer it. §Direction 2 retires that: the fact is already
published as a **log line named by the egg**, Wings gates on exactly such a
line itself, and reading it is not inference. So the Wings contract is
**one** small patch — placement — not two, and not eight. srdm already knows the server UUIDs from `internal/assign`, so it can
pre-create and pre-configure each slice at boot or at `attach`, closing the
"container starts before its limits exist" window exactly as D-016 closes it
for generations.

### 4. `access: rw` moves to an overlay upper layer

D-027, from measurement. Today `rw` unseals and chowns the shared generation
in place, which marks it `dirty_capable`, bars it from being shared or used as
a source, and limits it to **exactly one** consumer. Under an overlay — sealed
generation as `lowerdir`, per-server `upperdir` on that server's own volume —
the updater's writes land in the upper, bounded by what it actually touches;
the lower stays pristine and shared; other servers are unaffected; `harvest`
reads the merged view. The "write causes copy-up" row of D-027's table is the
feature here, not the cost.

The earlier review that rejected overlayfs rejected the **commit** half
(whiteouts, upper/lower reconciliation, non-atomic disk commit). That does not
apply: srdm's commit path is `harvest`, which re-walks, re-hashes and promotes
through the same `store.Promote` a staged release uses.

### 5. Acquisition: SteamCMD becomes first-class

`harvest` covers acquisition today — the game's own updater is a legitimate
source, and harvest is what makes its output trustworthy. The containerized
SteamCMD driver becomes the *unattended* path: drop in an appid and it
prepares the tree. Unprivileged uid inside the egg's own runner image,
`app_info_print` for build identity, unconditional `validate`, identity from
`appmanifest_<appid>.acf`, stage jobs confined to `srdm-stage.slice`, and one
fsync'd typed result record handed back so srdm never parses foreign data.

It needs **build-identity recording**, which does not exist — no profile can
express "capture this version string". That is one piece of work serving both
the staged and the Steam path, and it comes first.

### 6. `provider` exposure — still optional, still justified

Invariant 7 is the reason: under `provider`, release roots reach a server only
as Docker mounts in the game container's namespace, so Wings' filesystem
operations are *structurally* unable to touch managed content — disk
accounting, backups, SFTP and archive extraction as much as the chown walk.
With F1 and the overlay `rw` mode, the urgent half of that is handled, so this
is a genuine improvement rather than a blocker. Its protocol was specified in
full in the historical plan; open that document if and only if this is
actually being built.

---

## Acceptance oracles

Numbering preserved from the historical plan, because the gates, the LOGs and
`tools/canary.sh` all cite these numbers. 1–18 cover the store, the protocol
and the Wings series; the MVP gate is **19–24**, and all of them are green.

| # | asserts |
|---|---|
| 12 | hold-service charging: the unit owns ≈ class size, properties read back from the **cgroup**, no ancestor caps the floors, teardown leaves no mounts or units, `nr_dying_descendants` stable across three cycles |
| 14 | mount propagation both directions: `rprivate` reproduces the 2026-07-31 symptom (invisible mounts, ghost unmounts); `rslave` observes both |
| 15 | teardown actually frees: `memory.current` drops to ~0 and the charge returns; repeated *without* stopping the consumer, the teardown is **refused** and the holder named |
| 16 | invariant 7 under provider mode: no mount at all under `volumes/<uuid>`, asserted from `/proc/self/mountinfo` |
| 17 | F1 correctness: correctly-owned read-only subtree chowns clean; wrong-owner read-only still fails with an actionable EROFS; writable wrong-owner still repaired as vanilla does |
| 18 | F1 cost: syscall counts before and after over an unchanged tree — the "cheaper on the common path" claim ships only if measured |
| **19** | host-bind's waiver stays in bounds: content at exactly the declared class paths and nowhere else; `WS/Saved/**` never bound or shadowed; `world.db` unchanged across publish/activate/rollback/teardown |
| **20** | preconditions refuse, not warn — and neither failure surfaces as a server start error |
| **21** | `rw` is single-consumer; a written-through generation is marked, and sharing, promotion and use-as-source are refused while dirty *(to be revisited by the overlay `rw` work, which removes the single-consumer limit by construction)* |
| **22** | ephemerality is observable: write through `rw`, republish, the write is gone and the journal says so |
| **23** | `harvest` round-trip: update in place → harvest → the manifest equals a from-scratch stage of the same content; an unclassified new path blocks promotion; harvest on a running consumer is refused |
| **24** | teardown safety: `activate`, `rollback` and teardown all refused with the holder named while a consumer runs; after a clean stop they proceed and the memory returns |

The gate that runs them is srdm's own privileged systemd-in-Docker container,
not the shared `tester-unified` (D-006). `GUIDE.md` has the invocation.

---

## Terminology

**Release** — immutable, hash-verified content, identified by an
operator-chosen id. **Generation** — a release made resident: `<g8>` is the
first 8 hex of SHA-256 of the release id, and it names the slice and the hold
units. **Class** — a set of path prefixes sharing one tmpfs and one memory
policy. **Managed / excluded / structure** — the three class kinds; excluded
is per-instance state, structure is the implicit class of a directory that
exists only because a declared path lives beneath it. **Assignment** — the
durable statement of which release a profile is on and which servers consume
it. **Hold unit** — the per-class transient unit whose worker populates the
class and then parks to own its memory charges. **Operation plan** — the
volatile record of what a publication intends, used to adopt or quarantine
after a crash. **Harvest** — promoting a generation that was updated in place
into a new release.

---

## Historical documents

`../../wings-cgroups/` is **read-only history** from here on. Nothing in this
project should cite it as authority, and nothing in it should be edited to
reflect a change made here.

| document | still worth opening for |
|---|---|
| `shared-ramdisk-update-lifecycle-5-fable.md` | the **provider protocol v1** specification (complete and normative), and the L1/L1b start-attempt transaction — only if `provider` exposure is actually being built |
| `...-cgroups-2-fable.md` | the R1–R8 resource-engine design, **superseded** by §Direction 3; open it for the cgroup semantics discussion, not for the architecture |
| `...-4-codex-combined-final-remarks.md` | the review that produced rev 5; historical rationale only |
| `...-1-codex.md` | the storage-alternatives comparison, including the original overlayfs rejection that D-027 partially overturns |
| `v1-legacy/` | the frozen v1 `cgroup` patch series and the production deployment it belongs to |

Anything in those documents that contradicts this one, or contradicts a
`D-NNN` in `decisions.md`, is wrong. The measured ground above is the list of
places where that is already known to be the case.
