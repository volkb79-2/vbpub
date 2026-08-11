# P27 work item 6 — the Go block-to-line question: evidence, impossibility proof, and ruling

Frozen at main `016863a4792e2cc6c3ef1eb472cb91314f109cb2`; corrected and
extended 2026-08-11 after the mandatory CR-opus-0 carve review
(`.worktrees/_control/assay-P20-P32/carve-review-P27.md`, verdict PARTIALLY
CONFIRMED).
Toolchain for every witness: `go version go1.25.12 linux/amd64` in
`tester-unified-go:local` (`sha256:0c435ca1b705…f8ce`), `--network=none`,
`GOTOOLCHAIN=local`, `GOPROXY=off`.

**Status: A-O19 is RULED — option 2** (build a real source-side
statement-position oracle). See decision A-217. This document is the evidence
that produced the ruling, kept because the reasoning is what makes the ruling
survivable.

## 1. The impossibility proof

Two files, **both `gofmt`-clean**, both compiled and executed by the pinned
toolchain, produce a **byte-identical** coverage profile:

```go
// collision-colA.go                 // collision-colB.go
package app                          package app
                                     
func F(a, b int) int {   // 3        func F(a, b int) int {   // 3
	x := a +             // 4        	x := a               // 4
		b                // 5        	return x             // 5
	return x             // 6        	// pad                 // 6
}                        // 7        }                        // 7
```

Both emit exactly:

```text
mode: atomic
example.invalid/coll/f.go:3.22,7.2 2 1
```

`witness/coverage-collision.out`, sha256
`f6c593a961f742b3c809d213f290b499bfebec677d8853197e5cc6d44df14da8` — the same
bytes from both sources.

Statement truth differs: colA's two statements begin on lines **4 and 6** (the
`x := a + b` assignment spans 4–5, so `return x` is on 6); colB's begin on
lines **4 and 5** (line 6 is a comment).

A block-to-line rule is a function of the profile. Identical input forces
identical output. The two correct answers differ. Therefore **every** rule
derivable from profile data alone — inclusive expansion, half-open expansion,
or any not yet imagined — is wrong on at least one of these two files. This is
a reproduction, not an inference from the cover tool's shape.

**What the format lacks, precisely:** a cover block encodes a positional extent
plus `numStmts`, a *cardinality*. The positions of the statements inside that
extent are encoded nowhere — not in the columns, not in block ordering, not in
inter-block relationships. Recovering them requires re-deriving statement
boundaries from the source text. That is A-O19 option 2, and it is now the
ruling.

## 2. Why the original four witnesses were not sufficient

The carve's first version claimed the witnesses "do not support one lossless
rule". That wording was **false**, and the carve review demonstrated it by
constructing a rule that passes all four:

> **R1** — if `startLine == endLine` → `{startLine}`; else if `numStmts == 1`
> → `{startLine + 1}`; else if `numStmts == 2` → `{startLine + 1, endLine - 1}`

R1 matches the manifest exactly on `coverage-commit1.out`, `coverage-commit2.out`,
`coverage-shapes.out` and `coverage-edge.out`. The four fixtures were
**non-discriminating**, not insufficient-in-principle — this project's own
A-143/A-150 class one layer up: a wrong rule and a right rule indistinguishable
to every available fixture.

R1 dies on a shape the original four never contained — a **multi-line block that
starts at a statement**. `witness/seg.go`:

```text
example.invalid/seg/seg.go:3.19,4.11 1 1
example.invalid/seg/seg.go:4.11,6.3 1 1
example.invalid/seg/seg.go:7.2,8.4 1 0
```

Truth is `{4, 5, 7}`. The third block spans lines 7–8 and its statement sits on
`startLine` (cover's `list[0].Pos()` path), the *opposite* offset from the
multi-line blocks above it in the same file, where the statement sits after
`startLine`. R1 yields `{4, 5, 8}`. No single offset rule satisfies both. Every
statement-start block among the original four (`calc.go:15.2`,
`shapes.go:11.2/24.1/32.2/41.2`) was single-line, which is exactly why the gap
went unseen.

Both new witnesses are now locked. Any future line-attribution oracle must be
checked against them, or it will again be satisfiable by a rule fitted to the
fixtures.

## 3. The over-approximation relation — corrected

The first version of this document said the rules "disagree completely on the
line sets" and called them **disjoint**. That was wrong. Corrected:

Block extent is a **strict over-approximation** of the statement-line set.
Every statement's first line necessarily lies inside its own block's extent, so
for the *executable* and *executed* sets the naive rule's answer is a superset
of the truth. On the two-commit fixture the manifest's covered set `{22, 25}` is
a strict subset of the inclusive rule's `{21, 22, 23, 24, 25, 26}`, and `{30}` a
strict subset of `{29, 30, 31}`.

**The superset relation does not extend to the missing set**, and the exception
is witnessed rather than hypothetical. `witness/lit.go` (`gofmt`-clean):

```text
example.invalid/lit/lit.go:3.14,4.18 1 1
example.invalid/lit/lit.go:4.18,4.30 1 0
example.invalid/lit/lit.go:5.2,6.10 2 1
```

Line 4 is `f := func() int { return 7 }`. The func-literal body block
`4.18,4.30` never ran (count 0), but the enclosing block `3.14,4.18` did, and
executed-wins-on-overlap promotes line 4 to *executed*. An uncovered statement
is laundered by a covered neighbour on the same line, so a truly-missing
statement line can be absent from the rule's missing set. In fairness this
particular collapse is line granularity's own limit — `coverage.py` shares it —
unlike the comment and closing-brace cases below, which are specific to block
extent.

Why the correction is decision-relevant rather than cosmetic: containment plus
an exact statement count is an oracle a *genuinely disjoint* relation would have
ruled out. It is available to P28 for the executable/executed sets, and it is
the shape P28's independence should rest on.

## 4. What a percentage oracle cannot see

On the real two-commit diff (changed lines 17–31 on the head side):

| rule | changed executable | covered | missing | changed-line coverage |
|---|---|---|---|---|
| inclusive | 9 — 21,22,23,24,25,26,29,30,31 | 6 | 3 | **66.7%** |
| half-open | 9 — identical | 6 | 3 | **66.7%** |
| hand manifest | 3 — 22,25,30 | 2 | 1 | **66.7%** |

All three report the same percentage over different line sets. A
percentage-comparing oracle separates none of them, and the losing rules count a
closing brace (26) and two statement continuations (23, 24) as covered changed
lines. This is A-208's failure mode — agreement in form, different measurements
in substance.

Precedent cannot break the tie: `assay`'s `go_cover.py` expands
`range(start, end + 1)` and discards the column; srdm's `covergate/profile.go`
does the same with `l <= end`. P28 exists to qualify Assay *against* covergate,
so adopting covergate's convention makes that comparison circular — what A-172's
own reason paragraph warns of.

## 5. A-172's position premise is disproved, and that closure is recorded

A-172 asserts "A real end-column-1 fixture distinguishes the two." It does not,
and the result is stronger than the first carve claimed. An end column of 1
requires a block to end at a token in column 1. `Rbrace + 1` is always ≥ 2.
`if`/`for`/`range`/`switch`/`select`/function bodies cannot have their `{` begin
a line, because Go's semicolon insertion forbids it. Every other block end is
`stmt.End()`, column ≥ 2. That leaves only a bare block statement's `{` at
column 1 — `witness/edge.go`'s case — and that identical position always begins
the successor block, so the line is attributed either way.

So inclusive and half-open are line-identical on all witnesses and structurally
line-equivalent in valid Go. **The position half of A-172 has no observable
consequence, and `go_cover.py`'s existing `range(start, end + 1)` is correct
under either reading.** Recorded as closed in decision A-218. This strengthens
rather than weakens the case for option 2: it removes the one axis that could
have been settled without a product ruling, leaving exactly one live question.

## 6. Why option 2, and the counter-precedent it has to answer

The ruling is option 2. Two things the record needs, both raised by the review:

**The real blockers were not simply "scope forbids widening".**
`src/assay/adapters/go.py` *is* in `scope.touch`. The actual obstacles are that
A-163 assigns the helper protocol to P29 and leaves P27 "only adds the helper
declaration"; that work item 3 forbids "a hand parser/partial path validator";
and that `src/assay/coverage_parsers/go_cover.py` — the consumer that would need
statement positions — has **no scope status at all** (see §7).

**A-087 is the obvious counter-precedent and it does not transfer.** P08 shipped
a narrow hand-written Go lexer rather than a toolchain helper, on the grounds
that a packaged `go run` helper would be unproved in `tester-unified`. The
asymmetry that defeats the analogy: `has_executable_code` is *allowed to be
conservative* — it returns strict `true` on anything it cannot parse, so a wrong
answer is a false FAIL, which fails closed. Statement positions must be
**exact**; a wrong position is a wrong published verdict with no fail-closed
direction. Conservatism is unavailable, so the P08 trade does not apply here.

For whoever builds it: `golang.org/x/tools/cover`'s public `Profile.Boundaries()`
does byte-offset interpolation between block positions and no AST work — it does
not solve this. The algorithm that *produces* these blocks is `cmd/cover`'s own
instrumenter (golang/go `src/cmd/cover/cover.go`, BSD-3-Clause):
`Visit` / `addCounters` / `statementBoundary`. It is deterministic, so re-running
it over the same source reproduces the same blocks byte-for-byte, and retaining
each statement's own position instead of discarding it into a bare count is a
small adaptation. Adapt, do not invent.

## 7. Two defects that the ruling does not fix, and that P27's re-carve must

**7.1 `src/assay/coverage_parsers/go_cover.py` has no scope status.** It
implements the very rule work item 6 exists to freeze, and the handoff lists it
only under "Context to read first" (read-only). DOCTRINE §3 and A-132 both
require an explicit status for a load-bearing file. The consequence is not
neutral: as frozen, the scope permits **only option 1** (the one needing no
edit) and structurally blocks options 2 and 3 — so P27's own frontmatter encoded
the tacit selection that the carve's continuation section insisted must not
happen. Under the option-2 ruling this file must move to `scope.touch` (or to
`scope.forbid` with an explicit note naming the package that owns it).

**7.2 O3's negative is broken under either reading and must be rewritten.** It
reads: *"Inclusive line expansion that attributes a non-statement closing line
disagrees with the independent manifest."* Under AUTHORING a negative is what a
**broken** version does, so O3 declares inclusive expansion broken — meaning
option 1 would have shipped a product exhibiting its own frozen oracle's
negative. Read the other way, if "inclusive" means loosely "not half-open", the
negative is **unfalsifiable**, because §5 shows inclusive and half-open are
line-equivalent. Either reading is a defect, and ruling A-O19 does not resolve
it. Under option 2 the negative should be rewritten against the real failure:
a rule that derives statement lines from block extents rather than from source
statement boundaries disagrees with the manifest on `seg.go`, and cannot
distinguish `collision-colA.go` from `collision-colB.go` at all.

## 8. Consequences for P28

Whatever granularity P28 ends up binding, its independence cannot come from
covergate, which shares the inclusive convention — at block granularity the two
implementations would agree *by construction*, institutionalizing exactly the
circularity A-172 exists to prevent. Under the option-2 ruling P28 gets what it
needs honestly: real statement positions make the hand manifest comparable at
statement granularity, which is where A-208's "complete shared semantic tuple"
was always meant to be checked.
