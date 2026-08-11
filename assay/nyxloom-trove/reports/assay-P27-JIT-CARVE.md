# P27 — Go gate and adapter resolution — Sol JIT carve

Carver: C-sol-1 (Opus xhigh Claude Code session, per A-216).
Frozen against main `016863a4792e2cc6c3ef1eb472cb91314f109cb2`, clean.
Route packet: `.worktrees/_control/assay-P20-P32/carver/P27.md`.
Assets: `nyxloom-trove/carve-assets/P27/`.

## Result

**NOT READY. Work item 6 is BLOCKED on a product decision under A-172.**

The probe did what A-172 asked — three real witnesses, a pinned toolchain, no
appeal to precedent — and the answer is that no single lossless block-to-line
rule exists. The full evidence is `carve-assets/P27/BLOCKED-grammar.md`; the
computed numbers are in `carve-assets/P27/probe-results.json`.

Four of the nine work items are frozen and dispatchable the moment the ruling
lands. Three cannot be frozen, because their oracles *are* the disputed rule.

| work item | disposition |
|---|---|
| 1 — gate image identity/caches/version assertions | frozen (`pinned-environment.json`) |
| 2 — extend the registered `tester-unified` gate | frozen (same, plus the unchanged gate id and cgroup rule) |
| 3 — module derivation via offline `go list -m -json` | frozen (probe 6 confirms the packet's contract is implementable verbatim) |
| 4 — materialize the two-commit module | frozen (`fixture/`, both real profiles reproduced) |
| 5 — compare exact v4 R1 artifact against an independent expectation | **BLOCKED** — the expectation's `Coverage` line sets are the disputed rule |
| 6 — freeze the half-open block-to-line grammar | **BLOCKED** — this is the decision |
| 7 — module rename / nested repository | frozen in contract; its assertions reuse item 5's expectation, so its artifact comparison waits |
| 8 — locked complete missing-tool artifact | frozen (`expected/missing-tool-v4.json`, accepted by both v4 layers) |
| 9 — controlled breaks | frozen except the line/column attribution break, which is the disputed rule |

## What the JIT pass established

**The half-open position semantics are proven, and that is the good news.**
Adjacent cover blocks share a boundary position — `11.29,12.12` ends exactly
where `12.12,14.3` begins; `28.22,29.2` ends where `29.2,31.3` begins. An
inclusive end would put one position in two blocks. So `[start, end)` over
`(line, column)` is observed fact, not a reading of srdm's source.

**A-172's named discriminator does not discriminate.** Its stated reason is
that "a real end-column-1 fixture distinguishes the two". End column 1 is
producible (`edge.go:6.24,7.1`) but only in source that `gofmt` rewrites, and
it separates nothing: the column-1 token that ends one block begins the next,
so both candidate rules attribute the same lines. On every gofmt-clean shape
probed — function bodies, if/else, switch with unindented cases, bare blocks,
multi-line conditions — no end column of 1 occurred at all. Freezing the
half-open *line* refinement would therefore have been a no-op wearing the
costume of rigor.

**The real defect is one the profile cannot fix.** A cover block carries a
positional extent and `numStmts`, a count — never the statements' own
positions. `calc.go:21.27,26.2 2 1` says "two statements somewhere in lines
21–26". Over the whole of `calc.go`, either candidate rule marks 17 lines
executable where the source has 7 statements; the ten extras are four function
signatures, four closing braces, and two continuation lines of a single
statement. The pinned tool's own arithmetic agrees with the manifest, not with
either rule: `go test` reported 71.4%, which is 5/7.

**The trap that would have shipped.** On the real two-commit diff, the
inclusive rule, the half-open rule, and the hand manifest all report **66.7%
changed-line coverage** while attributing disjoint line sets — 6 covered lines
{21,22,23,24,25,26} versus 2 {22,25}. A percentage oracle cannot tell them
apart, and the losing rules count a closing brace and two continuations as
covered changed lines. That is precisely A-208's failure mode, which was
written after P25 shipped a demonstrated vacuous qualification, and which
A-208 explicitly carried into P28's Go tuple.

**Why precedent cannot settle it.** Assay's `go_cover.py` expands
`range(start, end + 1)` and discards the column; srdm's `covergate/profile.go`
does the same with `l <= end`. P28 exists to qualify Assay *against* covergate
on a shared semantic tuple. Adopting covergate's convention would make P28
compare two implementations of one convention — the circularity A-172's own
reason paragraph names.

## The decision required

Assay's common model (`FileCoverage.executed`/`missing` as line sets) presumes
per-line statement truth. `coverage.py` supplies it for Python; `go test
-coverprofile` structurally cannot. The owner must choose what a Go R1 line
claim asserts:

1. **Accept block-extent attribution, documented.** One common model, cheapest.
   Go R1 then reports non-statement lines as covered, so a brace-only change
   can read 1/1 covered; P28 must bind its tuple at block granularity.
2. **Obtain real statement positions** via a source-side Go AST helper. Truthful
   and line-granular, but it is new capability that collides with P29's helper
   protocol and exceeds P27's scope — a re-carve or a reassignment, not a repair.
3. **Narrow the Go R1 claim to block granularity in the artifact.** Most
   truthful, but v4 is P21's and P27 is forbidden to revise it.

My reading, offered as input and not as a ruling: option 1 is the only one that
fits P27's scope, and it is survivable *if* the narrowing is written into the
design guide and P28's tuple is re-specified at block granularity in the same
decision — otherwise A-208's requirement silently degrades. Option 2 is the
right end state and belongs with P29. What must not happen is option 1 adopted
tacitly by letting the current parser stand, because that is the circular
qualification A-172 was written to prevent.

## Premise probes

Six, all recorded with exact commands and digests in `probe-results.json`:
the fixture profile versus the manifest (agrees, 5/7); half-open and
end-column-1 (proven; discriminates nothing); the two-commit rule comparison
(the 66.7% contradiction); the locked artifact against both v4 layers
(accepted); and the real `go list -m -json` (one `Main:true`, offline, and it
also emits `Dir`/`GoMod`, which the adapter must tolerate without reading as
identity).

Toolchain for every probe: `go version go1.25.12 linux/amd64` in
`tester-unified-go:local` `sha256:0c435ca1b705…f8ce`, `--network=none`,
`GOTOOLCHAIN=local`, `GOPROXY=off`, `GOWORK=off`, under the verified
`dev-background.slice`. The devcontainer has and acquires no Go toolchain.

## Requirement-to-oracle traceability

O1 (real toolchain, real module, exact R0+R1 artifact) — fixture and both real
profiles are frozen; the *expected artifact* half waits on the ruling.
O2 (module path from `go.mod`, no hardcoded prefix) — frozen; probe 6 supplies
the real query, and the rename case is contract-frozen.
O3 (multi-line blocks, columns, comments, `doc.go`, `_test.go`, partial and
empty coverage against an independent manifest) — the manifest exists and the
real profiles exercise every named shape; **the comparison rule it would be
checked against is the blocked decision**, so O3 cannot be declared satisfiable
yet. This is the honest reason P27 is NOT READY rather than READY-with-caveats:
O3 is the package's central oracle.

## Pre-dispatch adversarial specification review

Not run here, deliberately. Under A-216 that leg belongs to a fresh Opus xhigh
child forked from `CR-opus-0` and commissioned by the controller, precisely so
the carver cannot review its own carve. P20–P26's carve reports embedded a
self-run review; that is the practice A-216 replaced. The controller must still
run it — on whatever carve follows the ruling, not on this blocked one.

## Successor dispositions

- **P28** inherits A-208 unchanged and now with a second concrete instance: the
  Go percentage can agree across rules that measure different lines. Whatever
  the ruling, P28's covergate comparison must bind the line/block sets, not the
  percentage.
- **P29** is the natural owner of option 2. If the ruling picks option 2, P29's
  helper protocol grows a statement-position responsibility and P27 must be
  re-carved to consume it.
- **B001** (A-215, after P28, before P29) is untouched by this: its preferred
  source-oriented SQL adapter returns byte-span mutation sites, which is a
  different axis from coverage line attribution. Worth noting the parallel
  though — a byte-span-to-line mapping will face the same "what does a line
  claim mean" question, and settling Go first gives SQL a precedent.
- The one-hop item carried in from P26's review — "when a future packet freezes
  a closed grammar, pin at least one break per clause, not per function" —
  applies directly to whatever grammar the ruling produces, and to the
  missing-tool terminal already frozen here. Recorded as consumed by this carve
  in the sense that it shaped the probe design; it should be re-issued to
  P27's eventual implementer.
