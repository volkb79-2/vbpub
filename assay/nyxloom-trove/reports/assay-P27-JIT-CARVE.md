# P27 — Go gate and adapter resolution — Sol JIT carve

Carver: C-sol-1 (Opus xhigh Claude Code session, per A-216).
Frozen against main `016863a4792e2cc6c3ef1eb472cb91314f109cb2`, clean.
Route packet: `.worktrees/_control/assay-P20-P32/carver/P27.md`.
Assets: `nyxloom-trove/carve-assets/P27/`.

> **UPDATED 2026-08-11.** The mandatory CR-opus-0 carve review returned
> **PARTIALLY CONFIRMED** (`.worktrees/_control/assay-P20-P32/carve-review-P27.md`):
> it confirmed the central finding and supplied a stronger proof than this report
> originally had, and it found three real defects in what was committed. A-O19 has
> since been **ruled by the operator: option 2** (decision A-217; A-172's
> disproved position premise closed as A-218). Corrections are folded in below
> and marked; the "Corrections after review" section lists them in one place.
> P27 is still **not dispatchable** — it must be re-carved around option 2.

## Result

**NOT READY. Work item 6 is BLOCKED on a product decision under A-172.**
*(Now ruled — option 2, A-217. Still not dispatchable: re-carve required.)*

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
changed-line coverage** over different line sets — 6 covered lines
{21,22,23,24,25,26} versus 2 {22,25}. A percentage oracle cannot tell them
apart, and the losing rules count a closing brace and two continuations as
covered changed lines. That is precisely A-208's failure mode, which was
written after P25 shipped a demonstrated vacuous qualification, and which
A-208 explicitly carried into P28's Go tuple.

*Corrected 2026-08-11: this paragraph first called those sets **disjoint**. They
are not. `{22,25} ⊂ {21,…,26}` and `{30} ⊂ {29,30,31}` — block extent is a
strict over-approximation of the statement-line set, because every statement's
first line necessarily lies inside its own block's extent. The correction is
decision-relevant, not cosmetic: containment plus an exact statement count is an
oracle available to P28 that a genuinely disjoint relation would have ruled out.
One precision: the superset relation holds for the executable and executed sets
but **not** for the missing set, because executed-wins-on-overlap can promote a
line whose own statement never ran — witnessed in `witness/lit.go`, where an
uncovered func-literal body is laundered by its covered enclosing block.*

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

### Ruled: option 2 (A-217)

The operator ruled option 2 on 2026-08-11. Recorded above as written, because a
carve report that quietly rewrites its own recommendation after the fact is worth
less than one that shows what was recommended and what was decided.

I do not dispute the ruling, and the review's evidence strengthens it beyond what
I had. My paragraph argued option 1 on *scope fit*, explicitly not on correctness
— and two things the review established make the scope argument much weaker than
it looked. First, the impossibility is now unconditional (the collision pair), so
option 1 is not "coarser but defensible", it is knowingly wrong on some inputs.
Second, option 1's cost is not confined to the permissive direction I named: a
brace- or comment-only diff inside an *untested* function reports 0/1 and FAILs
at `fail_under = 100.0` where statement truth is 0/0, and consumers meet that far
more often than the false-PASS case. A punitive false FAIL on unchanged semantics
is the kind of defect that gets a tool switched off.

Option 2 also rescues P28 honestly. Binding P28's tuple at block granularity —
which is what my option-1 reading required — would have made Assay and covergate
agree *by construction*, satisfying A-208 in form while defeating it in
substance. Real statement positions make the hand manifest comparable at
statement granularity, which is where A-208 always intended the tuple to be
checked.

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

**O3's *negative* is separately defective and the ruling does not fix it.** It
reads "Inclusive line expansion that attributes a non-statement closing line
disagrees with the independent manifest". Under AUTHORING a negative is what a
broken version does, so O3 declares inclusive expansion broken — option 1 would
have shipped a product exhibiting its own frozen oracle's negative. Read the
other way, if "inclusive" means loosely "not half-open", the negative is
unfalsifiable, because inclusive and half-open are line-equivalent on every
witness (A-218). Either reading is a defect. Under the option-2 ruling the
negative must be rewritten against the real failure: a rule deriving statement
lines from block extents rather than from source statement boundaries disagrees
with the manifest on `witness/seg.go`, and cannot distinguish
`collision-colA.go` from `collision-colB.go` at all. **Found by the carve
review, not by this carve.**

## Pre-dispatch adversarial specification review

Not run here, deliberately. Under A-216 that leg belongs to a fresh Opus xhigh
child forked from `CR-opus-0` and commissioned by the controller, precisely so
the carver cannot review its own carve. P20–P26's carve reports embedded a
self-run review; that is the practice A-216 replaced.

**It has since run, with the inverted charge this report's continuation
specified — attack the BLOCKED claim rather than assess readiness. Verdict:
PARTIALLY CONFIRMED** (`.worktrees/_control/assay-P20-P32/carve-review-P27.md`).
It could not construct a lossless rule, and it produced a stronger proof than
this carve had: two `gofmt`-clean files whose profiles are byte-identical while
their statement lines differ. It also found three defects in what was committed
(below). A-216's independent leg earned its cost on its first use: the strongest
part of the current evidence and every correction below came from it, not from
me.

## Corrections after review

All four verified independently by re-running the pinned toolchain and the two
v4 layers before folding them in.

1. **"Disjoint line sets" was wrong** — strict over-approximation, with the
   missing-set exception witnessed in `lit.go`. Corrected in this report,
   `BLOCKED-grammar.md`, `probe-results.json`, the A-O19 row in `decisions.md`,
   and the route packet.
2. **The original four witnesses did not establish the conclusion.** The review
   built a rule (`R1`) that passes all four. The claim as first worded — "the
   three witnesses do not support one lossless rule" — was literally false; they
   were non-discriminating, this project's A-143/A-150 class one layer up. Two
   new witnesses are now locked: `witness/seg.go` (a multi-line block starting
   *at* a statement, which every statement-start block among the original four
   failed to be) and the `collision-col{A,B}.go` pair.
3. **The locked missing-tool artifact was broken, not frozen.** Its committed
   bytes carry four unsubstituted placeholders and fail `jsonschema` with 2
   errors and `verify_text` with 3 failures — the opposite of what this report
   and `P27-PROBE-5` claimed. I had validated a substituted copy and recorded the
   result as though the asset itself passed. Renamed to
   `expected/missing-tool-v4-template.json`, matching the convention every P25
   and P26 `expected/` file already used, with the four substitution points
   documented. This was an A-067-class defect — a recorded check whose stated
   subject was not what was checked — inside the asset set that A-176/A-206/A-214
   make READY conditional on.
4. **`src/assay/coverage_parsers/go_cover.py` has no scope status**, appearing
   only under "Context to read first". As frozen, P27's scope therefore permitted
   *only* option 1 and structurally blocked options 2 and 3. I diagnosed the
   tacit-selection risk at the controller level in this report's own continuation
   and missed it sitting in my own frontmatter. Under A-217 the file must move to
   `scope.touch` at re-carve.

One further correction to this report's own reasoning, from the review: option
2's blocker was never simply "scope forbids widening" — `src/assay/adapters/go.py`
*is* in `scope.touch`. The real obstacles are A-163 assigning the helper protocol
to P29, work item 3's ban on a hand parser, and defect 4 above. And A-087 is the
counter-precedent the ruling has to answer: P08 shipped a hand-written Go lexer
rather than a toolchain helper. It does not transfer, because
`has_executable_code` may be conservative — strict `true` on anything unparseable,
so a wrong answer fails closed — whereas a statement position must be exact and
has no fail-closed direction. That asymmetry was missing from my original
argument.

## Successor dispositions

- **P28** inherits A-208 unchanged and now with a second concrete instance: the
  Go percentage can agree across rules that measure different lines. Its
  covergate comparison must bind the line sets, not the percentage — and under
  A-217 it can do so at statement granularity, which is the only granularity at
  which the comparison is not circular (covergate shares the inclusive
  convention, so block granularity would make the two agree by construction).
- **P29** is the natural owner of option 2. The ruling picks option 2, so P29's
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
