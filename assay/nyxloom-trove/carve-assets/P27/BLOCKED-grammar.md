# P27 work item 6 — BLOCKED: the three witnesses do not support one lossless rule

Frozen at main `016863a4792e2cc6c3ef1eb472cb91314f109cb2`.
Toolchain: `go version go1.25.12 linux/amd64` in `tester-unified-go:local`
(`sha256:0c435ca1b705…f8ce`), `--network=none`, `GOTOOLCHAIN=local`, `GOPROXY=off`.

A-172 requires the carver to "probe and freeze the exact mapping before ACTIVE"
and says "a three-witness contradiction is a product decision". Work item 6
requires BLOCKED "rather than choosing the current parser or srdm behavior by
authority". This is that BLOCKED, with all three witnesses recorded.

## What the probe established, and it is not what A-172 expected

**1. The position range is genuinely half-open — proven, not inferred.**

Adjacent blocks *share* a boundary position. From `witness/coverage-commit2.out`:

```text
calc.go:11.29,12.12 1 1     <- ends at 12.12
calc.go:12.12,14.3  1 1     <- starts at 12.12
```

and from `witness/coverage-shapes.out`:

```text
shapes.go:28.22,29.2 1 1    <- ends at 29.2
shapes.go:29.2,31.3  1 1    <- starts at 29.2
```

If `end` were inclusive, position `12.12` would belong to two blocks at once.
It does not. `[start, end)` on `(line, column)` is therefore established by
observation. Two end conventions coexist inside that: a block extended to its
closing brace ends at `Rbrace + 1` (`4.24,6.2` where `}` is at column 1;
`12.12,14.3` where `\t}` puts it at column 2), and a block ending at a
following token ends *at* that token (`28.22,29.2` ends at the `{` on line 29).

**2. The end-column-1 case A-172 names as the discriminator does not
discriminate.**

A-172's stated reason for the freeze is that "a real end-column-1 fixture
distinguishes the two". An end column of 1 is producible —
`witness/coverage-edge.out`:

```text
edge.go:6.24,7.1 1 1
edge.go:7.1,9.2  1 1
edge.go:10.1,10.9 1 1
```

but it distinguishes nothing, for a structural reason: a block that *ends* at
column 1 of line N is ended by a token at column 1 of line N, and that token
begins the very next block, which then *starts* at line N. So line N is
attributed under either rule. Computed over that witness:

| rule | executed lines |
|---|---|
| inclusive `startLine..endLine` | 6, 7, 8, 9, 10 |
| half-open (drop `endLine` when `endCol == 1`) | 6, 7, 8, 9, 10 |

Identical. Worse, producing an end column of 1 at all required source that
**gofmt rewrites** (`gofmt -l` lists `edge.go`); no gofmt-clean file in any of
the three probes produced one. So the refinement A-172 mandates is
observationally equivalent to the rule it was written to displace, on every
input a real consumer repository would contain.

**3. The real defect is block extent versus statement identity, and the
profile cannot settle it.**

The profile carries a positional extent and `numStmts`, a *count*. It never
carries the statements' own positions. `calc.go:21.27,26.2 2 1` says "two
statements somewhere between line 21 column 27 and line 26 column 2". Which
two lines hold them is absent from the artifact.

Both candidate rules therefore attribute lines that begin no statement. Whole
of `calc.go` at commit2, computed:

| witness | executable lines | count |
|---|---|---|
| inclusive rule | 4,5,6,11,12,13,14,15,21,22,23,24,25,26,29,30,31 | 17 |
| half-open rule | 4,5,6,11,12,13,14,15,21,22,23,24,25,26,29,30,31 | 17 |
| hand manifest (source bytes) | 5,12,13,15,22,25,30 | 7 |

The ten false lines are the four function signatures (4, 11, 21, 29), four
closing braces (6, 14, 26, 31 — the fifth, line 16, escapes only by luck of
block placement), and the two statement continuations 23 and 24. The pinned tool's
own arithmetic sides with the manifest: `go test` reported **71.4% of
statements**, which is 5/7, exactly the manifest's totals — never 6/17 or
13/17.

## The trap this would have shipped

The two-commit fixture makes it concrete. Changed lines on the head side of
`repo/app/calc.go` are 17–31. Computed three ways:

| rule | changed executable | covered | missing | changed-line coverage |
|---|---|---|---|---|
| inclusive | 9 — 21,22,23,24,25,26,29,30,31 | 6 | 3 | **66.7%** |
| half-open | 9 — 21,22,23,24,25,26,29,30,31 | 6 | 3 | **66.7%** |
| hand manifest | 3 — 22,25,30 | 2 | 1 | **66.7%** |

**All three agree on the percentage and disagree completely on the line sets.**
A test comparing percentages passes under any rule, including one that reports
a closing brace (line 26) and two statement continuations (23, 24) as covered
changed lines. This is exactly A-208's failure mode in a new setting: "both
Assay and Topos can agree on PASS while measuring nothing… the test compares
only their booleans." A-208 already ruled that qualification must bind the
complete shared semantic tuple, and explicitly carried that requirement into
P28's Go work.

Which is why this cannot be settled by precedent. `assay`'s current
`go_cover.py` expands `range(start, end + 1)` and discards the column
(`_parse_pos` partitions on `.` and returns the line only). srdm's
`covergate/profile.go` does the same — `for l := start; l <= end; l++`, columns
discarded. P28's entire purpose is to qualify Assay *against* that
implementation on a shared tuple. If P27 adopts srdm's convention, P28
compares two implementations of one convention and proves nothing, which is
the circularity A-172's own reason paragraph warns about.

## The product decision required

The line-granular common model (`FileCoverage.executed` / `missing` as line
sets) presumes per-line statement truth. `coverage.py` supplies exactly that
for Python. `go test -coverprofile` structurally cannot. Someone with product
authority has to choose what Assay's Go R1 claim *means*:

1. **Accept block-extent attribution and say so.** Cheapest; keeps one common
   model. Go R1 then reports non-statement lines as covered/missing, so a
   changed-brace-only commit can read 1/1 covered. Requires an explicit
   documented narrowing of what a Go R1 line claim asserts, and P28's
   comparison must then bind the tuple at block granularity, not line
   granularity.
2. **Obtain real statement positions.** Truthful line-granular Go coverage,
   but it needs a source-side statement oracle (a Go AST helper). That is new
   capability, it collides with P29's helper protocol, and P27's scope forbids
   widening — so it is a re-carve of P27 or a reassignment to P29, not an
   in-scope repair.
3. **Narrow the Go R1 claim to block granularity in the artifact.** Most
   truthful, but the artifact shape is v4 and P21 owns it; P27 is explicitly
   forbidden to revise the v4 schema/verdict contract, so this needs P21's
   owner.

All three change what a published Go R1 verdict asserts, and options 2 and 3
reach outside P27's scope. Per work item 6 and A-172 the carver does not pick
one by authority.

**What is safe to freeze now regardless of the ruling:** the half-open
*position* semantics of item 1 (proven), the pinned image/toolchain inputs, the
fixture topology and its two real profiles, the independent manifest, and the
locked missing-tool artifact — none of which depend on the line-attribution
choice. **What cannot be frozen:** the expected v4 R1 artifact's `Coverage`
line sets, and therefore the locked acceptance suite for work items 5, 6 and
the line/column attribution break in work item 9.
