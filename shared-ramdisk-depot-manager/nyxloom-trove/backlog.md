# srdm backlog

Un-carved ideas. Nothing here is committed to.

**Where a gap goes when a package ends.** Every package LOG closes with a
"Gaps" section, and that section is the narrative — it is not the tracking.
Each gap it names must also land in exactly one of three places, or it exists
only in a file nobody re-reads:

- the **roadmap**, when a named later package already owns it (say so in that
  package's entry, so whoever carves it inherits the obligation);
- **here**, when it is real work that no package owns yet;
- a **`D-<NNN>`**, when it is a product call rather than work.

Retire an entry when it ships; a backlog that still proposes something the
tree already has is worse than an empty one.

---

- **The coverage gate silently did not measure P14's new code** (found
  2026-08-04 during the P10+P14 merge — **the highest-value item here**).
  On merged `main`, `tools/gate.sh coverage` reports `254/258 (98.4%)` —
  which is P10's standalone figure, unchanged. A differential test settles
  it: measuring from `10b174a5` (before both merges) and from `83c2ff79`
  (after P14's merge, so P10 only) returns the **identical** 254/258. P14's
  ~1,500 new non-test lines — `internal/power/*`, `internal/opctl/update.go`,
  `internal/publish/sizing.go` — contribute nothing to either numerator or
  denominator.
  Not a diff problem: `git diff --relative --unified=0 10b174a5 HEAD --
  internal` run by hand DOES list P14's files, and `merge-base` resolves
  correctly. So the loss is downstream, in the intersection with the cover
  profile — a changed line absent from the profile is counted as neither
  covered nor uncovered, it simply disappears, and the percentage stays
  reassuring. That is exactly the **vacuous pass** D-007 was written to
  prevent, returning by a different route: D-007 guarded against a `.py`
  filter skipping every `.go` file and passing at 0/0; this passes at 254/258
  while ignoring a whole package.
  First thing to check: whether `internal/power` appears in
  `/tmp/srdm-cover.out` at all after `go test ./... -coverpkg=./...`, and
  whether `HasExecutableCode` or `stripModulePrefix` drops it. Until it is
  understood, **treat any covergate percentage as a lower bound on what was
  examined, not a statement about the whole change** — and P14's real
  changed-line coverage is currently UNKNOWN, not 98.4%.
  **Note 2026-09-01 (from assay Wave C, vbpub `assay/nyxloom-trove/reports/
  assay-WAVE-C-go-REPORT.md` §17; the assay backlog entry is B058 after the
  Wave C merge, provisionally B056 on the branch):** the mechanism is located
  by reading `tools/covergate` itself. `Evaluate`'s `fc == nil` branch is
  where a changed file absent from the profile goes: it becomes `NoCode`
  (excluded from the ratio) or `Unmeasured` (counted uncovered), and the
  only thing separating the two is `HasExecutableCode`. `gate.sh` passes
  `-coverpkg=./...` precisely so packages do not vanish; one that vanished
  anyway lands in `Unmeasured`, which is a listing, never a refusal. So
  "absent because no test ran" and "absent because the profile never
  covered the package" are indistinguishable to the gate. assay's Go
  adapter is about to run this exact differential (`10b174a5` → `83c2ff79`)
  inside `tester-unified-go` as its F008-A5 qualification; its classified
  result will be pasted into that REPORT and should settle this entry's
  "first thing to check" without srdm re-deriving it.
  **Note 2026-09-02 (assay controller; Wave C shipped as assay-v4.1.0):**
  that differential ran — `10b174a5` → `83c2ff79`, real `go test ./...
  -coverpkg=./...` inside `tester-unified-go`, covergate and assay judging
  the same profile — and the "silently skipped P14" shape does **not**
  reproduce on that pair: assay's statement-granular result for the range
  is 418 changed statements / 394 covered / 94.26%, reproduced exactly by
  the wave's adversarial reviewer on the released build, with P14's files
  present in the profile and in the judged set. The differences between the
  two tools on that run are classified in vbpub
  `assay/nyxloom-trove/reports/assay-WAVE-C-go-REPORT.md` (F008-A5
  sections; the extent-expansion class is B058, and assay's own repeated-
  record defect found by the same run is B061, fixed in 4.1.0). So the
  identical-254/258 observation above is NOT explained by this branch on
  THAT pair; do not carry "covergate skipped P14" forward as a fact — it
  stays an unreproduced observation, and the `fc == nil` mechanism stays a
  real but so-far-untriggered gap. Adopting assay here is the open A-O04
  decision, unchanged by this note.
- **`tools/covergate` counts every line inside a cover block's extent as
  executable, so the floor measures more lines than Go has statements**
  (filed 2026-09-01 from assay Wave C; the vbpub assay backlog entry of the
  same title carries the full evidence — **B058 once the Wave C branch
  merges**; it was provisionally B056 on `feature/assay-wave-c-go` until
  main's own B053/B054 filings of 2026-09-02 shifted the branch's ids by two —
  this is the srdm-side pointer). `profile.go`'s
  `ParseCoverProfile` does `for l := start; l <= end; l++` and
  `Executable(line)` is `Executed[line] || Missing[line]`, so function
  signature lines, `case` labels, closing braces and statement-continuation
  lines all count as code. The doc comment states the premise ("every line
  in that range is executable") and the premise is false: assay's frozen
  witnesses `carve-assets/P27/witness/collision-col{A,B}.go` are two
  gofmt-clean files that emit BYTE-IDENTICAL cover profiles while their
  statements begin on different lines (`{4,6}` vs `{4,5}`), so no rule that
  reads only the profile can be right on both — statement positions must
  come from source (assay A-217, the `cmd/cover`-derived oracle it now
  ships as `assay/helpers/go/stmtpos`). `HasExecutableCode` is per-FILE and
  cannot demote a line; `Evaluate` counting only ADDED lines bounds how
  often the gap is reachable without changing its direction. Consequence
  for this project: the 75% floor is applied to an inflated denominator
  (extra non-statement lines inside covered blocks are "executed", inside
  uncovered blocks "missing"), so the number is neither a floor nor a
  ceiling on statement coverage. Not urgent on its own; it is the second
  reason, after the entry above, to prefer adopting the shared adapter over
  patching the copy — see the "Revisit `tools/covergate`" entry.
- **Measure the parked hold worker's own footprint** (P04). The worker is
  charged to the class cgroup alongside the content it holds, so its
  resident size comes straight off the class floor.
  `debug.FreeOSMemory()` returns the copy buffers before it parks, but a Go
  runtime is not nothing. Worth a number before the floors are calibrated
  against a real payload in P09 — and worth knowing whether a smaller
  worker would be worth writing.
- **Exercise the Docker resolver against a real daemon** (P05). The gate
  container has no Docker socket, so `internal/consumer`'s Docker half is
  tested against a stub over a unix socket and every e2e run reports the
  check as degraded. Naming a real container end to end needs a harness with
  the socket mounted — P06 builds the first harness that has containers in
  it at all.
- **A gate harness with containers in it** (P06). The container half of the
  propagation precondition — does the Wings container really bind the volume
  tree `rslave`? — is unit-tested against a stub and injected in e2e, because
  the gate container has no Docker socket and no Wings. The same harness
  would close the P05 entry above, and would let oracle 20's "neither
  failure surfaces as a server start error" be asserted against a server
  that actually starts.
- **A durability oracle.** D-008: process kill proves ordering, not
  durability. `dm-flakey` or a VM snapshot in the privileged harness would
  close it.
- **A YAML front-end for profile documents** (D-005), once the profile
  engine lands and a parser dependency is worth its weight.
- **`srdm store gc`** — retention (D-002), once there is something that
  pins a release.
- **Manifest streaming.** `BuildManifest` holds every entry in memory, and
  `harvest.assemble` holds two maps keyed on every path it copies. Fine for a
  ~2.4 GB game tree; worth revisiting together if a profile ever covers a
  tree with millions of entries.
- **Parallel hashing.** The manifest walk is single-threaded. Measure before
  optimizing: on the case-study node the store lives on the same device the
  io.max caps govern, so more concurrency may buy nothing.
- **Harvest reads the tree twice** (P07). It copies the class trees into a
  transaction and then `store.Promote` hashes the copy — two full passes over
  a multi-gigabyte tree. Hashing the copy rather than the source is the
  correct half and must stay: a release's manifest has to describe what
  actually landed on disk. Hashing WHILE copying would collapse the two and
  keep that property, at the cost of a second manifest builder to keep in
  step with the first. Measure the copy against the hash before deciding it
  is worth two implementations.
- **The containerized SteamCMD acquisition driver** (`internal/source/steam`,
  currently a doc-only stub). Wanted for MVP after all: "drop in the appid
  and it prepares the filesystem". Master plan §SteamCMD driver has the
  shape — unprivileged uid inside the egg's own runner image, `app_info_print`
  for build identity, unconditional `validate`, identity from
  `appmanifest_<appid>.acf`, stage jobs confined to `srdm-stage.slice`, and
  one fsync'd typed result record handed back so the daemon never parses
  foreign data. Note it needs the **build-identity recording** entry below,
  which is the same piece of work for the staged path.
- **No privileged e2e oracle for adoption/quarantine or reconciliation
  repair** (P08b). `AdoptOrQuarantine`, `IsComplete`, `RepairReadOnly` and
  `ClearForRepublish` are gated at the unit level against fakes for a
  mounter, a holder and a mount table. The fact still unmeasured against the
  real kernel: a genuinely killed process's leftover plan, mounts and hold
  unit, told apart from a genuinely finished operation, by real systemd and
  real `/proc/self/mountinfo` rather than by a fake standing in for either —
  the same category of fact D-011/D-013 exist because a fake would have
  guessed wrong about. Wants a `kill -9` of the daemon mid-`Publish` in the
  privileged harness, then `AdoptOrQuarantine` against what that really
  leaves behind.
- **An unparsable operation plan cannot stop its own hold unit** (P08b). If
  `internal/publish`'s durable operation plan (`OpPlanFile`) is found
  corrupted — practically only disk damage, since srdm alone ever writes it
  — `AdoptOrQuarantine` falls back to sweeping the operation's mounts by path
  and cannot recover the class names needed to `Forget` the hold unit that
  goes with them, so it is left running with nothing mounted underneath it
  (stated in `sweepOpDir`'s own comment and journaled). Closing it needs
  either a second, independent record of which units an operation started
  (defeating some of the plan's own simplicity) or a host-wide sweep of
  `srdm-hold-*.service` units against every op directory that still exists,
  which is closer to a systemd unit-listing capability `internal/hold` does
  not have yet than to a bug fix.
- **`publish.MarkDirtyCapable` is unreachable code** (P10). D-035 replaced
  in-place unsealing with an overlay, and no `rw` exposure this project
  builds anymore marks a generation dirty-capable — the `Marker` interface,
  `expose.WithMarker` and the `cmd/srdm` wiring that called it are all gone.
  Nothing calls `MarkDirtyCapable` now. It is intentionally left in
  `internal/publish/publish.go` rather than deleted: that file was out of
  P10's scope (another package was concurrently working in it), and the
  READ side of `Record.DirtyCapable` stays genuinely load-bearing — a
  record written by an older srdm, or a future non-overlay driver, can
  still carry the flag, and `doctor`'s drift check earns its keep on those.
  Only the writer is dead. Remove `MarkDirtyCapable` (and reconsider whether
  `Marker` should even be a package-level export anymore) once nothing
  plausibly still calls it.
- **Build identity is not recorded** (P07). The master plan's harvest step 4
  is "run the profile's probes; record build identity where discoverable" —
  the probes run, and there is nowhere to record a build identity, because no
  profile has a way to express one. It would want a probe kind that captures
  a value (a version file, a manifest header) rather than asserting a
  predicate, and a field in COMPLETE to hold it. Wanted by the same thing
  that wants it for staged releases, so it is one piece of work for both
  paths, not a harvest rider.

- **Revisit `tools/covergate` once the shared testing library exists.**
  This project reimplemented changed-line coverage in Go because the existing
  implementation could not be consumed standalone — it lived inside nyxloom.
  That rewrite is the sharpest evidence for extracting it: `coverage_gate.py`
  now exists FOUR times across the estate (nyxloom 455 lines, dstdns 804,
  topos 299, plus this Go copy) and the Python copies have diverged.

  The extraction is specified in
  `nyxloom/nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md`,
  queued behind nyxloom's core redesign.

  **Keep the Go implementation.** srdm is a Go project and the library ships a
  Python adapter first; this copy stays until a Go adapter exists, and it is the
  forcing function for the library's LanguageAdapter protocol (P90 oracle O3) —
  a design that cannot accommodate this project is not actually language-neutral.
  When that adapter lands, the choice is adopt-or-keep on the merits, not now.
  **Note 2026-09-01:** the library is `vbpub/assay` (`judge.language =
  "go"`, R1 changed-line coverage, statement-granular via a source-side
  oracle; registered in the CLI as of assay Wave C on
  `feature/assay-wave-c-go`, release pending). The adopt-or-keep question's
  stated blocker in assay's own record (A-O04: "whether Python enters srdm's
  toolchain/container") is void: `tester-unified-go:local` already carries
  `/usr/bin/python3` 3.13.5 by inheritance from `golang:1.25`'s trixie base
  (measured 2026-09-01; no pip, no ensurepip), and assay ships a
  stdlib-only zipapp that needs exactly an interpreter and nothing else
  (`requires-python >= 3.11`). So the adoption shape is `python3 assay.pyz
  run <lane>` inside the same container `gate.sh` already runs, beside the
  real `go`. What adoption would still cost srdm: one `assay.toml` lane
  (the wave's qualification lane is the template: `cwd` at this directory,
  `source_roots = ["internal"]`-equivalent, argv = `gate.sh:105`'s `go test`
  line, `format = "go-cover"`, `producer = "go-test"`), a `-fail-under`
  policy restated as `fail_under`, and accepting that the number will be
  LOWER than covergate's for the reason in the entry above (fewer
  non-statement lines counted). Decide on the merits after the wave's
  F008-A5 transcript exists; nothing here is committed to.
