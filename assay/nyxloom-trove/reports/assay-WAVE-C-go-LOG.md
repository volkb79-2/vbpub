# assay Wave C (Go — the P27 re-carve) — implementer LOG

One entry per commit. Branch `feature/assay-wave-c-go`, worktree
`.worktrees/assay-wave-c-go`, branched from `main` at `25b1f7fb`.

Scope is the WAVE-PROMPT doc's "Wave C" section: 7 items, in its order.
Target: F008-A3/A4/A5 move from `absent` to `proven`.

---

## `271af037` — feat(assay): the Go statement-position oracle (B047 item 1, A-217)

**Files**

| file | what |
|---|---|
| `src/assay/helpers/go/stmtpos/stmtpos.go` | new — 639 lines, stdlib-only Go |
| `src/assay/helpers/go/stmtpos/go.mod` | new — `require`-free module, so `GOPROXY=off` needs nothing |
| `nyxloom-trove/carve-assets/P27-recarve/probe-stmtpos.sh` | new — the re-runnable probe |
| `nyxloom-trove/carve-assets/P27-recarve/stmtpos-witness-oracle.json` | new — that probe's real output |
| `nyxloom-trove/carve-assets/P27-recarve/PROVENANCE.md` | new — environment, digests, per-witness result |

**What and why.** A-217 ruled A-O19 option 2: real Go statement positions come
from a source-side oracle, because the profile cannot carry them. Its own
implementation note is "adapt, do not invent", so `stmtpos.go` is transcribed
from `cmd/cover`'s instrumenter (`golang/go` `src/cmd/cover/cover.go`,
BSD-3-Clause) — `Visit`, `addCounters`, `statementBoundary`,
`endsBasicSourceBlock`, `isControl`, `hasFuncLiteral`, `funcLitFinder`,
`findText`, `offset`, `dedup` — read out of the pinned image itself rather than
from memory. Exactly one behavioural change: where cover inserts a counter into
an edit buffer, this records the block extent plus the positions of the
statements that counter counts (`list[0:last]`, the same slice whose length
cover passes as `numStmt`).

Deliberately NOT `golang.org/x/tools/cover`'s `Profile.Boundaries()` — it
interpolates byte offsets and does no AST work (A-217 names it).

**Distribution** — B047 item 1 offered two candidates and recommended (a).
Taken: the Go source ships inside the wheel and is invoked with `go run`, so it
is covered by `judge_provenance` and needs no second artifact to pin.

**Evidence.** Probed in `tester-unified-go:local` (built this session from the
committed `vbpub/tester-unified-go/Dockerfile`; the image was not present on
this host), `go1.25.14`, `--network=none`, `GOPROXY=off GOWORK=off
GOTOOLCHAIN=local`, tar-pipe, `--cgroup-parent=dev-background.slice`. No Go
toolchain was added to the devcontainer (A-042/A-043).

Derived extents and `numStmts` reproduce **all eight** frozen profiles exactly.
Full table in `carve-assets/P27-recarve/PROVENANCE.md`. The three that decide
the wave:

* `collision-colA` → `{4,6}`, `collision-colB` → `{4,5}`, from a
  **byte-identical** profile — the A-217 impossibility proof answered.
* `seg.go` → `{4,5,7}`, `BLOCKED-grammar.md` §2's truth; the fitted rule R1
  gives `{4,5,8}` and dies.
* `fixture/commit2/calc.go` → executed `{5,12,13,22,25}`, missing `{15,30}` —
  exactly the independent hand manifest, which is not an input to the oracle.

**Toolchain delta, stated not glossed.** The frozen witnesses are `go1.25.12`;
this probe ran `go1.25.14`. Measured inert rather than assumed inert: had the
instrumenter changed, the extent join would have disagreed on at least one
witness.

**Tests added/changed:** none in this commit — no Python is wired to the helper
yet. The Python-side proof lands in the next commit, over this commit's
committed artifact.

**Frozen assets:** `carve-assets/P27/` is carver-owned and was NOT touched. New
evidence went to a new tree, `carve-assets/P27-recarve/`.

---

## `<pending>` — feat(assay): keep Go block extents and correct them (A-239 items 1+3)

**Files**

| file | what |
|---|---|
| `src/assay/coverage_parsers/model.py` | new `CoverageBlock`; `FileCoverage.blocks`; `CoverageProfile.statement_attributed` |
| `src/assay/coverage_parsers/go_cover.py` | retains columns and emits blocks; `_parse_pos` now returns `(line, col)` |
| `src/assay/statement_attribution.py` | new — the pure, language-free join |
| `tests/test_statement_attribution_go_witnesses.py` | new — 14 tests over the frozen witnesses |

**What and why — the two concrete design calls A-239 deferred to this
re-carve.**

*1. The `blocks` representation* (A-084: the package proving the need owns the
extension). `CoverageBlock` is a sibling frozen dataclass in
`coverage_parsers/model.py`, and `FileCoverage.blocks` is
`tuple[CoverageBlock, ...] | None` — the SAME `None`/populated split `excluded`
(A-008) and `branches` already keep: `None` means the format has no block
concept, not "block-based with zero blocks". Columns are stored, not just
lines, because two records can share a boundary position (`28.22,29.2` then
`29.2,31.3`) and a line-only key fuses them.

*2. The join is on the EXTENT, not on the position.* The obvious shape — "the
statement at P belongs to the block containing P" — is wrong, and `shapes.go`
witnesses why: `cmd/cover` ends a block at the START position of its own last
statement, so `29.2` is one block's end and the next block's start, and the
statement there belongs to the first only. Closed intervals match both,
half-open match neither. So the oracle reports each block's own statement list
and `attribute_statements` joins whole extents. That also makes the result
checkable: the instrumenter is deterministic, so a disagreement is real
evidence of a stale profile, and the function refuses rather than attributing.

*3. `statement_attributed` exists because the omission had to be able to go
red.* An un-corrected block-based profile is a wrong answer that looks exactly
like a right one — AGENTS.md's *masked default* (anti-pattern 3), invisible to
testing precisely because every context that would show it runs the correction.
So a parser never sets the flag, only `attribute_statements` does, and
`evaluate` refuses when an adapter requires attribution and the flag is `False`
(that guard lands with the adapter wiring, next commit).

**What is deliberately NOT claimed.** `lit.go`'s laundering is not fixed and
the tests say so: line 4 carries two counted statements (the assignment, count
1; the func-literal body, count 0), executed-wins promotes it, and that is line
granularity's own limit which `coverage.py` shares — `BLOCKED-grammar.md` §3 in
those words. What IS fixed there is the fabrication: line 3, the signature, is
gone from the executable set. Recorded as A-393 / B053.

**Tests added:** `tests/test_statement_attribution_go_witnesses.py`, 14 tests,
all reading real artifacts (the frozen carver profiles + this wave's committed
oracle output). No Go toolchain, no subprocess. Includes the anti-vacuity
control (`test_the_naive_expansion_cannot_tell_the_collision_pair_apart`), the
manifest cross-check, three refusal paths and an order-independence check for
executed-wins.
