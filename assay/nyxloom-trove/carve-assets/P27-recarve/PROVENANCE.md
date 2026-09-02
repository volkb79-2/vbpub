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
**B055**.

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

---

# F008-A4 — the committed Go coverage FIXTURES, regenerated (2026-09-02, Wave C generation 6)

A second, later probe in the same directory and under the same rules. The
subject is different: the section above proves the ORACLE against carver-frozen
witnesses; this one replaces assay's own committed test FIXTURES with real
toolchain output and derives the statement positions that make them readable.

## Why both artifacts, not just the bytes

A-234 recorded that `tests/fixtures/go/hello/hello.out` and
`tests/fixtures/canary/go/greet/greet_control.out` were hand-authored and wrong
in both coordinates. A-234's own warning is why regenerating the bytes ALONE
would have been worse than leaving them: a real profile whose block extents are
then read as statement truth is the exact conflation A-O19/A-217 exist to
remove, and it would have been harder to spot afterwards because the bytes
would now be genuine. So the run emits two artifacts — the profiles and
`fixture-oracle.json` — and the tests join them with
`assay.statement_attribution.attribute_statements` rather than reading either
alone.

## Environment

Identical to the section above except for the date: image
`tester-unified-go:local`, id
`sha256:64ba4941040ba736c9eea23aca89fdcba0cc155025cb1d438c5c259636163a6f`,
`go version go1.25.14 linux/amd64`, `--network=none`,
`--cgroup-parent=dev-background.slice`, `GOPROXY=off GOWORK=off
GOTOOLCHAIN=local GOFLAGS=-mod=mod`, `tar` pipe, no Go in the devcontainer and
none acquired (A-042/A-043).

## The command

```sh
ASSAY=/workspaces/vbpub/.worktrees/assay-wave-c-go/assay \
    bash nyxloom-trove/carve-assets/P27-recarve/regenerate-fixtures.sh
```

Run at worktree commit `86b4efae` with the fixture sources in their final,
committed form — the profiles are position-bound, so the source bytes were
settled BEFORE the run, not after.

## Files

| file | what it is |
|---|---|
| `regenerate-fixtures.sh` | the exact, re-runnable command |
| `fixture-oracle.json` | that command's `=== fixture-oracle.json ===` section, byte-for-byte, pretty-printed, with the `../oracle-src/` path prefix stripped — the same normalisation the witness document above records |

sha256 of everything the run consumed or produced:

```text
9139f745cc58b81f86a779e16ffb4f7f5330a86bea4ad8c576bb519a34019f2b  ../../../src/assay/helpers/go/stmtpos/stmtpos.go
48256ee26df1c2572bdaa9468bd7ae9e7f8999ca7db5ada3a29a80c13be91674  ../../../src/assay/helpers/go/stmtpos/go.mod
9ae18b8641f343d34ff5acf8e33b70a40bdb241d4e2d3b23d4829e952826ebf7  regenerate-fixtures.sh
718f0b11526fc64a2242fc71953c58f9f54f9e3a38f560b0fbdd3fe031050491  fixture-oracle.json
1d1c39a77f93568d747fea68a80885e89e1090d745910e8bbca0d11e0fc34ae3  ../../../tests/fixtures/go/hello/hello.go
5ac0949d8869375baa0d70fef9cfab88c13fd7bb414493918837c749210d09e1  ../../../tests/fixtures/go/hello/doc.go
e9b8b9bce19faef6bb8bd9bb412a52c10b2c09df3073facaa731475bb83e327f  ../../../tests/fixtures/go/hello/hello.out
8a0d51aab9d4b4f68db8e483291a416d3896a9eb9600dc2b4191d46e69186039  ../../../tests/fixtures/canary/go/greet/greet.go
2aef9487c1918baffc6c8142a48d8848b0df43316852b40ee43ba9a20e53889f  ../../../tests/fixtures/canary/go/greet/greet_control.out
1c2e3f0b54d2f81ce3bb3874a4d06024af18a7876e1822f26dd48a438f81d32c  ../../../tests/fixtures/canary/go/greet/greet_transformed.out
```

## The raw run output

```text
=== go-version ===
go version go1.25.14 linux/amd64
=== hello.out ===
mode: set
hello/hello.go:32.32,34.2 1 1
hello/hello.go:38.35,40.2 1 0
=== greet_control.out ===
mode: set
greet/greet.go:30.32,32.2 1 1
=== greet_transformed.out ===
mode: set
greet/greet.go:30.32,32.2 1 1
greet/greet.go:35.43,38.2 2 0
=== fixture-oracle.json ===
{"schema":1,"go_version":"go1.25.14","files":[{"path":"../oracle-src/doc.go","blocks":[]},{"path":"../oracle-src/greet.go","blocks":[{"start_line":30,"start_col":32,"end_line":32,"end_col":2,"num_stmts":1,"stmt_lines":[31]}]},{"path":"../oracle-src/greet_transformed.go","blocks":[{"start_line":30,"start_col":32,"end_line":32,"end_col":2,"num_stmts":1,"stmt_lines":[31]},{"start_line":35,"start_col":43,"end_line":38,"end_col":2,"num_stmts":2,"stmt_lines":[36,37]}]},{"path":"../oracle-src/hello.go","blocks":[{"start_line":32,"start_col":32,"end_line":34,"end_col":2,"num_stmts":1,"stmt_lines":[33]},{"start_line":38,"start_col":35,"end_line":40,"end_col":2,"num_stmts":1,"stmt_lines":[39]}]}]}
```

## What each re-derived line set proves

| fixture | real block extent | naive expansion (the OLD expectation's rule) | oracle statement truth | what the difference is |
|---|---|---|---|---|
| `hello.go` Greet | `32.32,34.2` count 1 | executed `{32,33,34}` | executed **`{33}`** | the signature line and the closing brace are inside the block and are not statements |
| `hello.go` Farewell | `38.35,40.2` count 0 | missing `{38,39,40}` | missing **`{39}`** | same, on the uncovered side — the two lines a developer could not make executable |
| `doc.go` | *no record at all* | — | **no blocks** (`"blocks": []`) | the comment-only file's exclusion is now proven from the oracle too, not only from its absence from the profile |
| `greet.go` Greet | `30.32,32.2` count 1 | executed `{30,31,32}` | executed **`{31}`** | the canary control's single statement |
| transformed `greet.go` canary func | `35.43,38.2` count **2** | missing `{35,36,37,38}` | missing **`{36,37}`** | the appended never-called function's two body statements, without its signature or brace — and `num_stmts` 2 is the profile's own arithmetic agreeing with the oracle's list length |

**The old committed bytes were wrong in a way the naive rule hid.** The old
`hello.out` said `29.34,30.42` — a block that ENDS at the return statement's
own last column, so the naive expansion `{29,30}` happened to contain the one
real statement and looked plausible. The real block is `32.32,34.2`, and the
naive expansion of THAT is `{32,33,34}`: three lines where one is executable.
That is the concrete shape of "regenerating the bytes alone would have been
worse than nothing" — the same test would have gone from asserting 2 wrong
lines to asserting 3 wrong lines, with real bytes underneath.

## What is NOT claimed

That these fixtures exercise the oracle SUBPROCESS. They do not, and cannot:
the registered gate runs in `tester-unified:local`, which has no Go
(DESIGN-GUIDE §10). What the join proves is that assay's own correction, run
over real profile bytes and real oracle output, yields statement truth — the
same standing as `stmtpos-witness-oracle.json` has for the frozen witnesses.
The subprocess path is proven separately and end to end by
`tests/qualification/test_go_r1_real.py` inside `tester-unified-go:local`
(F008-A3).
