# P27 re-carve — probe evidence for the statement-position oracle

**Implementer-owned, Wave C (2026-08-31).** A separate tree from
`carve-assets/P27/`, which is **carver-owned and frozen**: nothing under
`P27/` is edited by this wave. This directory holds only what the re-carve
itself measured.

## Why this exists

A-334: *a test double is not evidence about an external system.* Every claim
this wave makes about real Go toolchain behaviour is either one of the frozen
`P27/witness/` artifacts (cited, unmodified) or a fresh artifact recorded
here, produced by running the real toolchain.

## Environment

| input | value |
|---|---|
| image | `tester-unified-go:local`, id `sha256:64ba4941040ba736c9eea23aca89fdcba0cc155025cb1d438c5c259636163a6f` |
| image source | `vbpub/tester-unified-go/Dockerfile` (`FROM golang:1.25`), built 2026-08-31 with `docker build -f tester-unified-go/Dockerfile -t tester-unified-go:local /workspaces/vbpub/tester-unified-go` |
| toolchain | `go version go1.25.14 linux/amd64` |
| network | `--network=none` |
| env | `GOPROXY=off GOWORK=off GOTOOLCHAIN=local GOFLAGS=-mod=mod` |
| cgroup | `--cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND"` (resolved: `dev-background.slice`) |
| transport | `tar` pipe, no bind mount (this devcontainer's `/tmp` is not Docker-visible at the same path) |
| devcontainer Go | **none**, and none acquired (A-042/A-043) |

**Toolchain delta against the frozen witnesses, stated rather than glossed.**
`P27/BLOCKED-grammar.md` records `go1.25.12` for every frozen witness; this
probe ran `go1.25.14`. That difference is **measured inert, not assumed
inert**: the oracle's derived block extents and `numStmts` reproduce all eight
frozen profiles exactly (below). Had the instrumenter changed across those two
patch releases, the extent join would have disagreed on at least one witness
and this table would show it.

## Files here

| file | what it is |
|---|---|
| `probe-stmtpos.sh` | the exact, re-runnable command that produced the JSON below |
| `stmtpos-witness-oracle.json` | that command's stdout, byte-for-byte, with the `../witness/` path prefix stripped |

sha256:

```text
9139f745cc58b81f86a779e16ffb4f7f5330a86bea4ad8c576bb519a34019f2b  ../../../src/assay/helpers/go/stmtpos/stmtpos.go
48256ee26df1c2572bdaa9468bd7ae9e7f8999ca7db5ada3a29a80c13be91674  ../../../src/assay/helpers/go/stmtpos/go.mod
267608d1d8ac9e9421f838613dfad40b741ee2f4548c0edd68b1138fbd102692  stmtpos-witness-oracle.json
```

## The result, per frozen witness

`extents`/`numStmts` compare the oracle's derived blocks against the frozen
profile's records as SETS (a profile is position-sorted; the oracle emits in
`cmd/cover`'s generation order — the orders differ and the contents do not).
`oracle` is the statement-granular line classification; `naive` is what
`go_cover.py`'s shipped `range(start, end + 1)` expansion produces from the
same profile.

| witness | profile | extents | numStmts | oracle executed / missing | naive executed / missing |
|---|---|---|---|---|---|
| `collision-colA.go` | `coverage-collision.out` | MATCH | MATCH | `{4,6}` / `{}` | `{3,4,5,6,7}` / `{}` |
| `collision-colB.go` | `coverage-collision.out` | MATCH | MATCH | `{4,5}` / `{}` | `{3,4,5,6,7}` / `{}` |
| `seg.go` | `coverage-seg.out` | MATCH | MATCH | `{4,5}` / `{7}` | `{3,4,5,6}` / `{7,8}` |
| `lit.go` | `coverage-lit.out` | MATCH | MATCH | `{4,5,6}` / `{}` | `{3,4,5,6}` / `{}` |
| `shapes.go` | `coverage-shapes.out` | MATCH | MATCH | `{5,7,18,22,29,30,32,37,39}` / `{9,11,20,24,41}` | `{4,5,6,7,17,18,21,22,28,29,30,31,32,36,37,38,39,40}` / `{8,9,11,19,20,24,41}` |
| `edge.go` | `coverage-edge.out` | MATCH | MATCH | `{7,8,10}` / `{}` | `{6,7,8,9,10}` / `{}` |
| `fixture/commit1/calc.go` | `coverage-commit1.out` | MATCH | MATCH | `{5,12,13}` / `{15}` | `{4,5,6,11,12,13,14}` / `{15}` |
| `fixture/commit2/calc.go` | `coverage-commit2.out` | MATCH | MATCH | `{5,12,13,22,25}` / `{15,30}` | `{4,5,6,11,12,13,14,21,22,23,24,25,26}` / `{15,29,30,31}` |

### What each row proves

**`collision-col{A,B}` — the impossibility proof, answered.** Both profiles
are the same bytes (`f6c593a961f7…4da8`, one record:
`example.invalid/coll/f.go:3.22,7.2 2 1`). The oracle derives **`{4,6}` for A
and `{4,5}` for B** — the two different correct answers `BLOCKED-grammar.md`
§1 says no profile-only rule can produce. It can, because it reads the
SOURCE. The naive rule returns the same wrong `{3,4,5,6,7}` for both, exactly
as the proof predicts.

**`seg.go` — the discriminator.** Truth is `{4,5,7}`
(`BLOCKED-grammar.md` §2); the oracle derives executed `{4,5}` + missing
`{7}` = `{4,5,7}`. §2's constructed rule **R1** (`startLine+1` for a
single-statement multi-line block) yields `{4,5,8}` and dies here. The oracle
is not fitted to the original four witnesses: it derives line 7 from the fact
that the third block's statement sits at `startLine`, and lines 4/5 from the
fact that the blocks above it do not — the *opposite* offset, in the same
file.

**`lit.go` — the laundering caveat, NOT claimed fixed.** The oracle removes
line 3 (the `func H() int {` signature) from the executable set, which the
naive rule fabricated. It does **not** rescue the missing set: line 4 carries
two counted statements — the `f := func() int { return 7 }` assignment
(block `3.14,4.18`, count 1) and the func-literal body's `return 7` (block
`4.18,4.30`, count 0) — so executed-wins still promotes line 4, and the
uncovered statement is still laundered by its covered neighbour. That is
**line granularity's own limit, which `coverage.py` shares**
(`BLOCKED-grammar.md` §3 says so in those words), not a defect in the oracle:
both statements genuinely begin on line 4, and a verdict speaks in lines.
Fixing it would require a column-granular wire field, which this wave is
explicitly forbidden to cut. Recorded as decision **A-393** and as backlog
**B053**.

**`shapes.go` — the half-open / shared-boundary proof.** Every block whose
extent's end position is also the next block's start position
(`28.22,29.2` → `29.2,31.3`; `36.35,38.9` → `38.9,40.3`) is attributed
correctly, because the oracle does not intersect positions at all — it joins
on the whole extent and takes that block's own statement list. The naive rule
fabricates 10 lines here (signatures 4/17/28/36, `case` labels 6/8/19, closing
braces 31/40, continuation 38).

**`edge.go` — end-column 1, and it discriminates nothing.** A-218 closed the
inclusive-vs-half-open axis as inert, and this row is consistent with that:
the oracle derives `{7,8,10}` without any rule that consults the end column.
No behaviour here depends on the column-1 case, which is what A-218 asks of
any successor.

**`fixture/commit{1,2}/calc.go` — agreement with the independent third
witness.** `P27/manifest/calc-statements.json` was authored from source bytes
*before any profile existed*, and states 7 statements, 5 executed, 2 missing
for commit2, on lines `{5,12,13,22,25}` executed and `{15,30}` missing. The
oracle derives exactly that set. It was not fitted to the manifest: the
manifest is not an input to `stmtpos.go`, which never reads it. This is the
statement-granularity comparison A-208 always intended, and the one A-217 says
"rescues P28".
