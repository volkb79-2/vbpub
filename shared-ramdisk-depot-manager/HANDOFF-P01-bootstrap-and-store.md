# srdm P01 — project bootstrap + the release store

> Session handoff written 2026-08-03 by the design session that produced
> Amendments 1 and 2 of the master plan. Authored per
> `nyxloom/reference/AUTHORING.md`. **Frontmatter is deliberately omitted**:
> `srdm` has no `nyxloom.toml` yet, so there are no `[gates.*]` ids to
> reference and `nyxloom lint` would reject it. Step 1 below creates them;
> the *next* package ships with valid frontmatter.

---

## Why this package exists — carry these insights forward

A fresh agent has none of the discussion that produced this. These are the
findings that shaped the design; losing any of them re-opens a solved
problem. Every one is verified, not recalled.

**F-a — mount propagation.** A containerized Wings whose `/var/lib/pterodactyl`
bind uses Docker's default `rprivate` never sees host-side mounts or unmounts
under the volumes root, and a *torn-down* host mount lives on inside Wings as
a ghost. That caused a production outage on 2026-07-31. The fix is
`propagation: rslave` on `/var/lib/pterodactyl` **and**
`/var/lib/docker/containers`, and it is already live on the case-study node
(verified via `docker inspect`). `rslave` requires the host peer group to be
`shared` — verified there: `/` is `shared:1`, `/run` is `shared:5`.
**srdm's host-bind driver mounts and unmounts under that exact tree, so this
is a hard precondition it must check and refuse on, not document.**

**F-b — the chown walk.** `Filesystem.Chown` walks the whole server root and
calls `Lchownat` on every entry with no already-correct-owner skip
(`server/filesystem/filesystem.go:253-294` at v1.13.1). A chown against a
read-only mount returns `EROFS` even when the ownership already matches. The
call site is `server/power.go:207-214`, gated by
`system.check_permissions_on_boot`, default `true` (`config/config.go:238`).
**Consequence: `host-bind` + `access: ro` cannot work on stock Wings.** F1
(`../wings-patchstack/`) fixes it; `check_permissions_on_boot: false` is the
coarser zero-patch alternative.

**A correction to the operational record.** `scripts/gstammtisch-guide/SOULMASK-TMPFS.md`
reports the EROFS failure as specific to an instance that is both `ROLE=main`
and `TMPFS=1`. The code does not support that scoping — the walk crosses every
mount boundary, so **every** consumer of a read-only bind fails, not just the
population source. Treat the narrower reading as an observation artifact and
confirm on the first host-bind rehearsal.

**The setuid trap in F1.** A root chown with *identical* uid/gid is not a
no-op: it strips setuid, setgid and file capabilities from non-directories.
Measured, not assumed:

```
chmod 4755 f ; chown 1000:1000 f   (already 1000:1000)  →  4755 becomes 755
setcap cap_net_raw+ep c ; chown 1000:1000 c             →  capability gone
chmod 2755 d ; chown 1000:1000 d   (a directory)        →  2755 unchanged
```

So vanilla's walk incidentally strips those bits from every non-directory on
every boot. A naive "skip when uid/gid match" removes that hardening
silently. F1's predicate is therefore narrower — it also requires the entry
to be a directory or to carry neither bit. Measured on the case-study node:
**zero** setuid/setgid files and **zero** fscaps across the shared tmpfs and
every server volume, so the strict branch never fires there.

**Teardown does not free memory while a consumer holds the bind.** A game
container keeps the tmpfs superblock — and every page — alive in its own
`rprivate` namespace after the host unmounts. In `provider` mode Wings'
auto-disposal guarantees release; **in `host-bind` mode there is no such
callback, so srdm must resolve holders itself and refuse.** This is a
correctness gate, not politeness.

**Why `rw` exists and why it is single-consumer.** With one consumer, a
writable generation lets the game's own updater keep working (`AUTO_UPDATE=1`,
no egg surgery) and the game DB is unaffected — `WS/Saved` is never shared.
With two, a write is the 2026-07-29 corruption by construction: the peer
holds the deleted old `.pak` open, the tmpfs exhausts mid-write, and the
generation ends with a new `.sig` and no `.pak` — survivable for the process
that already mmap'd the old inode, fatal for the next start. Refuse, do not
warn.

---

## Context to read first

Read these, in this order, and nothing else:

1. `shared-ramdisk-depot-manager/README.md` — product, naming, exposure seam.
2. `wings-cgroups/shared-ramdisk-update-lifecycle-5-fable.md`, sections:
   **Decision log** (all 12), **Field findings**, **Layer 2 §Exposure
   drivers**, **§Publication topology**, **§Generation slices and charging**,
   **§Worker contract**, **§Privilege model**, **§Persistent release store**,
   **§Acceptance oracles 12, 15, 19–24**, **§Kickoff plan** (Phase 1 is this
   work). Skip Layer 1 entirely — that is v2.
3. `scripts/gstammtisch-guide/SOULMASK-TMPFS.md` — the system srdm replaces;
   read it for the failure modes, not the design.
4. `scripts/gstammtisch-guide/files/usr/local/sbin/soulmask_tmpfs-setup.sh`
   and `.../etc/gstammtisch/soulmask_tmpfs-paths.conf` — the working
   prototype of population, slice-charging and class splitting. Steal the
   mechanics, not the architecture.
5. `AGENTS.md` — cockpit doctrine and the cgroup-placement rule for any
   container you spawn.

## Work

1. **Bootstrap the project.** Go module `srdm`, `cmd/srdm` (single binary,
   daemon + CLI subcommands), the package skeleton from the README. Add
   `nyxloom.toml` declaring at minimum a `unit` gate and a `privileged-e2e`
   gate (see §Gate). Add `nyxloom-trove/` with `decisions.md` and
   `handoffs/`. **After this step, all further packages carry frontmatter.**
2. **Release store** (§Persistent release store). Transaction directory →
   classification (an unclassified new path blocks promotion) → per-file
   SHA-256 manifest → profile probes → ownership normalization → fsync'd
   `COMPLETE` written last → atomic channel symlink flip. Versioned manifest
   from day one.
3. **Journal** — durable per-operation records plus an events JSONL and
   journald structured events. No credentials, ever.
4. **`srdm doctor`, offline subset**: cgroup v2 + controllers +
   `memory_recursiveprot`; `srdm.slice` present with `MemoryMin` ≥ Σ class
   floors; store integrity. The mount and Wings checks arrive with P02.

**Out of scope for P01** — do not start these: publication topology and hold
services (P02), the `host-bind` exposure driver (P03), `harvest` (P04),
SteamCMD driver, anything `provider`/protocol/lease/label related (v2).

## Oracles

Copy `nyxloom/reference/AUTHORING.md` §3b's anti-pattern list into every
downstream package that asks for tests. In particular, for this package: no
wall-clock deadlines deciding a verdict, no process-global state left
mutated, no hollow tests, no no-cover pragmas, and the filesystem is an
input — use a fresh temp root per test.

- **O1 — kill-at-every-phase.** Interrupt a transaction at each phase
  boundary (after copy, after manifest, after probes, before `COMPLETE`,
  after `COMPLETE`, before the symlink flip). *Observable*: on restart the
  store contains either the previous release or the new one, never a
  half-promoted one, and the journal names which. *Negative*: a release
  visible on the channel whose manifest does not verify. *Gate*: `unit`.
- **O2 — `COMPLETE` is last and means it.** *Observable*: a store where
  `COMPLETE` exists always verifies clean against its manifest. *Negative*:
  `COMPLETE` present with a missing or mismatched file. *Gate*: `unit`.
- **O3 — unclassified path blocks promotion.** *Observable*: a transaction
  containing a path no profile rule classifies is refused, and the error
  names the path and the profile. *Negative*: it promotes and silently
  lands the path in a default class. *Gate*: `unit`.
- **O4 — manifest is per-file and content-addressed.** *Observable*: two
  transactions of byte-identical content produce identical manifests;
  flipping one byte changes exactly one entry. *Negative*: a manifest keyed
  on size or mtime. *Gate*: `unit`.
- **O5 — journal has no secrets.** *Observable*: a run using a profile with
  credential-shaped fields produces journal records containing none of them.
  *Negative*: a token appears in any record. *Gate*: `unit`.

## Gate

**Never the devcontainer** — it is the cockpit. `tester-unified`
(`tester-unified/`), giving the run-uid a full identity (passwd + group +
HOME + XDG). Any container spawned must be placed with
`--cgroup-parent=$CGROUP_PARENT_DEV_BACKGROUND` (see the blocker below).

## Known blocker to clear first

`$CGROUP_PARENT_DEV_BACKGROUND` is **unset** in the devcontainer and absent
from PID 1's environment; the host has only a flat `dev-workloads.slice` with
no interactive/background children. `AGENTS.md` forbids a hardcoded fallback,
so no build or gate container can be launched until the tier is provisioned
or an explicit `--cgroup-parent` is supplied. **This blocks F1's gate too**
(see `wings-patchstack/README.md`). Raise it before starting.

## Scope / forbid

- **Touch**: `shared-ramdisk-depot-manager/**` only.
- **Forbid**: `wings-cgroups/**` (design docs — changes there are decisions,
  not implementation), `wings-patchstack/**`, `scripts/gstammtisch-guide/**`
  (the live host's files), any Wings clone under
  `wings-cgroups/v1-legacy/build/`.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file, **STOP** — write `BLOCKED: <reason>` to the LOG, commit, and exit. Do
not improvise a workaround. A BLOCKED exit is a success mode.

Product gaps are **decisions**, not BLOCKED: file a `D-<NNN>` in
`nyxloom-trove/decisions.md` and keep working around it. Two are already
open and are listed below.

## Open decisions to record as `D-` on arrival

- **D-001** `WS/Config` classification — shared or per-instance? Needs the
  runtime write audit. Master plan §Open questions 1.
- **D-002** Retention count, default 3. Master plan §Open questions 3.
- **D-003** Does `srdm.slice` carry `MemoryMin` from a unit file shipped by
  srdm, or is it admin-owned IaC? The master plan says admin-owned; srdm must
  then *verify* rather than *write* it. Confirm before P02.
