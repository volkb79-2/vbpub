# srdm-P10 — LOG

Package: `access: rw` via an overlay upper layer, and the holder recognizer
it requires
Roadmap: `../roadmap.md`, §Direction 2 / D-027
Date: 2026-08-04

---

## What was built

**`internal/expose`: the overlay exposure mode.** `access: rw` no longer
unseals the shared generation in place (D-020/D-022). Both access modes now
read `Binding.Source` from the same place — the sealed, published exposure
— and only the MOUNT differs: `ro` is still a plain read-only bind of
`Source`; `rw` overlays it, with `Source` as `lowerdir` and a per-server,
per-declared-class-path `upperdir`/`workdir` under `cfg.StateDir`
(`config.OverlayUpperDir` / `OverlayWorkDir`, new). `Binding` gained `Upper`
and `Work` fields, populated only for `rw`.

Before mounting, `mountOverlay` calls the new `mirrorDirTree`, which
recreates `Source`'s directory structure — directories only, never files —
inside `Upper` at mode `0o777`. That is what makes the writable layer
writable by whoever Wings runs the game container as, without srdm having
to know or declare that uid: overlayfs takes a directory's own metadata from
whichever layer has an entry for it, upper winning when both do, so a
directory that is never mirrored stays root-owned and sealed no matter how
permissive the mount point above it is. This is measured, not assumed —
D-035, `tools/overlay-write-perm-probe.sh` — and it retires
`PreconditionWriteOwner`, `config.Wings.WriteOwner` as anything read, and
the `Marker`/`MarkDirtyCapable` call: nothing hands the generation to a
declared owner, because nothing unseals it. `Record.DirtyCapable` stays a
field (vestigial, like `hold.Unseal` for anything but its own package) but
no `rw` exposure built by this package sets it again.

The single-consumer rule (`PreconditionSingleWrite`) is gone with it: any
number of servers may hold `rw` on the same generation, each in its own
`Upper`, because there is no longer a second writer of the same pages to
collide with (oracle 26). `Unexpose` discards a binding's `Upper`/`Work`
directory once its mount is gone — best-effort, and safe rather than lossy
because harvest is what reads a per-server merged view and every documented
`rw` flow reads it *before* `Unexpose` runs.

**`internal/consumer`: the overlay holder recognizer (D-028).** A second,
independent recognizer alongside the existing device-matched one: an
`overlay` mount whose `lowerdir=` (any colon-separated element) resolves at
or beneath one of the paths `Resolve` was asked about is a holder,
`KindOverlay`, carrying `LowerDir` instead of a meaningful `Device`. Matched
by path rather than device, the mirror image of why a bind is matched by
device — an overlay reports its own device, never the lower's, and the
lower's number appears nowhere in its `mountinfo` line, while a bind's
target path is unpredictable and its device is the only thing tying it back.
`Resolve` no longer short-circuits when it finds no device to match: a bind
holder needs one, an overlay holder does not, and that path stays valid
after srdm's own mount is gone.

**`internal/harvest`: `--from-server`, and the merged-view walk (D-035).**
`Opts.FromServer` selects whose overlay to read. A new `Exposer` interface
(`Plan` + `RWServers`; `*expose.HostBind` satisfies it) is injected via
`WithExposer`; `HostBind.RWServers` discovers current `rw` holders straight
from srdm's own mount table — no Docker, no registry, the same D-018
reasoning applied to a question srdm can answer about its own mounts
without asking anyone. Resolution: given explicitly, validated against what
`RWServers` reports (`*UnknownServerError` if it does not hold `rw`); empty
with exactly one holder, defaulted; empty with none, unchanged pre-P10
behaviour (reads the generation's own tmpfs); empty with several,
`*AmbiguousServerError` — the honest replacement for the single-consumer
limit, moved from where it cost sharing to where it actually matters.

`assemble` is restructured around a `walkJob` (root + release-relative
prefix) rather than always walking a whole class's `ContentRoot`: with a
`fromServer`, one job per declared class path, rooted at that binding's
`Target` — the live merged mount — rather than at the class's own tmpfs.
Whiteouts need no special code: a file deleted through an overlay is
invisible to an ordinary `filepath.WalkDir` of the merged mount, because the
kernel already resolved it before userspace ever sees a directory listing
(oracle 28). Classification, collision and class-moved detection are
unchanged; only *where each job reads from* differs.

**CLI wiring** (`cmd/srdm/ops.go`): `expose.WithMarker` is gone with the
concept; `newOpEnv` now passes `expose.WithStateDir(cfg.StateDir)` and wires
the same driver into `harvest.WithExposer`, so the "default when exactly
one holds rw" behaviour reaches the existing `srdm harvest` verb with no
flag needed. No `--from-server` CLI flag: choosing among several holders
would need a change to `internal/opctl.Controller.Harvest`'s signature and
its own tests, both outside this package's declared touch scope — recorded
as a gap rather than done by stretching the scope further than the two
call sites (`cmd/srdm/ops.go`, `internal/opctl/e2e_test.go`) that would not
otherwise build.

## Decisions filed

**D-035** — the overlay's upper is mirrored, world-writable, and discarded
on unexpose. Three things measured or decided, all in `decisions.md`:

- who may write with no declared owner at all (mirror the lower's directory
  tree into the upper at `0o777` — measured against a real overlay mount
  with an unprivileged uid, `tools/overlay-write-perm-probe.sh`);
- what survives of D-020 and D-022 (D-020's "different mount point" claim
  survives as "different mount type"; D-022's "never re-sealed" survives
  trivially, because the lower is never written at all now; everything else
  about the ownership model is retired);
- retained or discarded on `Unexpose` (discarded — the deciding fact is that
  harvest already has to run *before* `Unexpose`, since that is what tears
  the mount it reads down, so by the time `Unexpose` runs there is nothing
  left worth keeping).

## What the tests found

**A test bug that looked like a harvest bug.** The first whiteout oracle run
reported `Engine/libengine.so` missing from a harvested release that never
touched it. The manifest listed it; the file was not on disk at the path
the test checked. The bug was in the test, not `assemble`: a promoted
release's content lives under `<release dir>/root/`
(`store.Release.RootDir()`), not directly under the release directory —
`n.cfg.ReleaseDir(id)` names the release, not its content root. Caught by
adding a manifest dump and re-running the single test directly against a
persistent gate container rather than re-running the whole suite, which
would have cost minutes per iteration for a one-line fix.

**`Holders[0]` is not the overlay.** `TestTheOverlaysOwnDeviceNamesNothingSrdmMounted`
assumed the sole holder in a freshly-spawned namespace was the overlay it
had just mounted. It is not the sole holder: the namespace is also a
"slave" propagation namespace (D-019), so it receives propagated copies of
srdm's own `opMount`/`exposePath` binds — real holders, `KindMount`, sorted
before `KindOverlay` alphabetically. Fixed by finding the overlay entry by
`Kind` rather than assuming index 0, the same pattern the companion
privileged oracle (`TestARealOverlayWhoseLowerdirIsOurExposePathIsFound`)
already used correctly.

**Two downstream e2e files broke on the removed `expose.WithMarker`.**
`internal/opctl/e2e_test.go` (out of this package's declared touch scope)
and `internal/expose/e2e_test.go` itself both constructed a `HostBind` with
`WithMarker`, which the unit gate never caught because it does not compile
`//go:build e2e` files. Both fixed to `WithStateDir` — a mechanical,
minimal change with no behavioural effect on `internal/opctl`'s own logic,
made because leaving the build broken is worse than a one-line adjustment
to a consumer of a removed API.

**The changed-line coverage floor found two real unit-test gaps**, both
closed rather than argued around: `RWServers` and its four small helpers
(`overlayLowerDirs`, `anyLowerUnder`, `underAny`, `firstPathComponent`) had
no unit test at all — every path that reached them was privileged-e2e-only,
which the coverage gate's plain `go test ./...` never compiles. And
`Holder.String()`'s new `KindOverlay` case was covered, but the refactor
from an `if`/`default` into a three-armed `switch` made the *pre-existing*
`KindContainer` case a changed line too, and nothing had ever called
`.String()` on a `KindContainer` holder before. Both closed with direct unit
tests rather than by loosening what counts as "changed".

**Coverage measurement needs the git worktree's common dir mounted too.**
This agent's checkout is a linked `git worktree` (`.git` is a file pointing
at `/workspaces/vbpub/.git/worktrees/<name>`, outside the checkout itself),
and `tools/covergate` shells out to `git`. `tools/gate.sh coverage`'s single
bind mount of the worktree root is not enough for `git rev-list`/`git diff`
against `main` to resolve; it needed the host's `<repo>/.git` mounted
read-only at the same absolute path alongside it. Not a defect in
`gate.sh` — it assumes a plain clone, which is what a purpose-carved
worktree normally is — recorded here because the next agent in a linked
worktree will hit the exact same wall.

**The last 4 of 258 changed lines, named rather than chased.** Running
`covergate` at `-fail-under 100` (the gate itself checks 75) isolates every
uncovered changed line rather than stopping at the first failure, which is
how the two rounds above found their targets. What is left after both
rounds is `internal/expose/hostbind.go:410-411` and `:592-593`, and neither
yields to a test because neither is reachable through the caller that
exists:

- **410-411** (`mirrorDirTree`, the `filepath.Rel(lower, p)` error branch).
  `p` is never anything but a path `filepath.WalkDir(lower, …)` produced by
  walking FROM `lower`, so `p` is always `lower` joined with some suffix —
  `filepath.Rel` between a path and one of its own descendants cannot fail.
  The check exists because `filepath.Rel` returns an error and Go rewards
  checking it, not because this call site can hit one.
- **592-593** (`firstPathComponent`, `if seg == "" { return "", false }`).
  `seg` comes from `strings.Cut(filepath.ToSlash(rel), "/")` where `rel` is
  `filepath.Rel`'s own successful, non-`"."` result — which, on every input
  this function is ever called with (both here and directly, in
  `TestFirstPathComponentRejectsATargetOutsideRoot` and its neighbours),
  never begins with `/` and is never empty. No input constructed for the
  direct unit tests reproduced it either, which is itself the evidence: if
  a real `filepath.Rel` output could take this branch, one of those
  adversarial cases would have found it.

Both are the same shape: a defensive check against an error a standard
library function's SIGNATURE allows but its CONTRACT — given how this code
calls it — cannot actually produce. Deleting the checks would trade a
harmless four covered-by-inspection lines for a function that panics or
misbehaves the day that contract turns out to have an exception nobody
found yet; better a coverage report that says so by name than either
outcome.

## Gaps

- **`publish.MarkDirtyCapable` is now unreachable code.** D-035 retires the
  `Marker` interface and its `Expose`-side call; nothing anywhere in the
  tree calls `MarkDirtyCapable` anymore. `internal/publish/publish.go` is
  outside this package's touch scope (forbidden, and another agent was
  concurrently working in it), so the function itself is left in place
  rather than deleted. The READ side of `Record.DirtyCapable` is unaffected
  and stays load-bearing — a record written by an older srdm, or a future
  non-overlay driver, can still carry it, and `doctor`'s drift check earns
  its keep on those. Only the writer is dead. → backlog
  (`publish.MarkDirtyCapable is unreachable code`), and D-035 now states
  this split explicitly.
- **No `--from-server` CLI flag.** `harvest.Opts.FromServer` exists and is
  exercised directly by unit and e2e tests; the CLI-level override for
  choosing among several ambiguous rw holders would need
  `internal/opctl.Controller.Harvest`'s signature to change, which is
  outside this package's touch scope. The automatic default (exactly one
  holder) already reaches the CLI unaided, and the refusal half — several
  holders with nothing named — is real and tested without it: see
  `TestHarvestRefusesAmbiguousServersWithNoFromServerGiven` (unit,
  `internal/harvest/harvest_test.go`) and
  `TestHarvestAcceptsAnExplicitFromServerAmongSeveral` (unit, same file) for
  the two halves; `HostBind.RWServers` is what both are built on, e2e-tested
  live against `TestARealOverlayWhoseLowerdirIsOurExposePathIsFound`'s
  shape in `internal/consumer/e2e_test.go`. What is missing is only the
  CLI's ability to let an operator NAME which of several ambiguous holders
  to read — the refusal itself needs no flag to exist.
- **The residual truncate-without-unlink case.** A genuine in-place
  `open(O_WRONLY|O_TRUNC)` of a file that has never been touched since
  publication fails `EACCES` through the overlay — directories are
  mirrored, files are not. No profile this project targets updates that
  way; closing it would mean mirroring file modes too, reopening exactly
  the "grant write to an unknown uid" problem directory mirroring was
  chosen to avoid. Stated in D-035 rather than treated as a defect.
- **A stopped-but-configured server holding rw is not distinguished from a
  running one** by `RWServers` — it reads the mount table, which does not
  distinguish "the container that made this mount is still running" from
  "it exited and the mount is still there" (that question belongs to
  `internal/consumer`'s own container half, unchanged by this package).
  Latent, not new: the same is already true of every other holder check in
  this codebase.

## Verification

```
tools/gate.sh <worktree> unit       → gofmt, build, vet, all packages green
tools/gate.sh <worktree> e2e        → 473 passed, 0 failed
tools/canary-run.sh                 → 72 canaries rejected, 0 survived
tools/gate.sh <worktree> coverage   → 254/258 changed executable lines (98.4% >= 75.0% floor;
                                       the remaining 4 are named, not chased — see below)
nyxloom lint nyxloom-trove/handoffs/*.md → clean
```

4 new canaries, one per new oracle (`P10-O25-lowerdir-swapped`,
`P10-O26-shared-upper`, `P10-O27-overlay-recognizer-disabled`,
`P10-O28-from-server-ignored`).

4 existing canaries removed with the code they targeted no longer existing
(`P06-rw-multi-consumer`, `P06-never-marks-dirty`, `P07-rw-never-unsealed`,
`P07-rw-owner-optional` — the single-consumer refusal, the dirty mark and
the write-owner precondition all retired by D-035).

6 existing canaries repointed. One behaviourally, at the branch that now
selects overlay vs. plain bind rather than at the deleted `sourceBase`
(`P06-rw-binds-the-ro-side` → `P10-rw-skips-overlay`). Five because their
target lines *moved* under this package's refactor without the behaviour
they test changing at all — caught by the canary run itself reporting "the
mutation matched nothing", which is exactly the failure mode this
mechanism exists to catch: `P05-superblock-ignored` and
`P05-every-device-matches` (consumer.go's device-vs-overlay branch split),
`P07-harvest-skips-quiesce`, `P07-harvest-class-moved` and
`P07-harvest-collision-wins` (harvest.go's `assemble`/`walkJobs`
restructuring, and `class.Name != c.Name` becoming `class.Name != c` once
`walkJob.class` is already a plain string).
