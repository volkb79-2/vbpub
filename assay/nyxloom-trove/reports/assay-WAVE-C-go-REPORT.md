# assay Wave C (Go — the P27 re-carve) — implementer REPORT

**Status at checkpoint 1: PARTIAL. Items 1 (core half) and 2 of 7 are landed.**
This report covers what is proven so far; items 3–7 and item 1's adapter/runner
wiring are outstanding and are handed on in
`assay-WAVE-C-go-BRIEF-1.md`.

Branch `feature/assay-wave-c-go` from `main` at `25b1f7fb`.

---

## 1. Scope items — state

| # | item | state |
|---|---|---|
| 1 | A-239's shape carved concretely (`blocks` + new hook + core join) | **PARTIAL** — model, parser and core join landed and green; the protocol hook, the `evaluate` refusal and the runner wiring are NOT done |
| 2 | the oracle itself (B047 item 1) | **DONE** |
| 3 | fixture regeneration (F008-A4) | not started |
| 4 | `external_tools = ("go",)` (B047 item 2) | not started |
| 5 | `judge.coverage.producer` for `go-cover` (B047 item 3) | not started |
| 6 | gate envelope `helpers[]` (B047 item 5) | not started |
| 7 | srdm covergate qualification (F008-A5) | not started |

**No acceptance box is ticked in this report.** F008-A3/A4/A5 remain `absent`
in `2-product-definition.md` and B047's boxes remain unticked, because the
evidence for them is end-to-end behaviour that does not exist until the wiring
in item 1 lands. The oracle is proven; a Go *verdict* is not yet
statement-granular, because nothing calls the oracle yet. Claiming otherwise
would be exactly the "recorded check whose stated subject was not what was
checked" defect the P27 README already records once (A-067 class).

---

## 2. The oracle, against EVERY frozen witness

Environment, digests and the full table: `carve-assets/P27-recarve/PROVENANCE.md`.
Re-runnable by
`ASSAY=<worktree>/assay bash nyxloom-trove/carve-assets/P27-recarve/probe-stmtpos.sh`.

Real toolchain, no doubles (A-334): `tester-unified-go:local` built this
session from the committed `vbpub/tester-unified-go/Dockerfile` (it was not
present on this host), `go version go1.25.14 linux/amd64`, `--network=none`,
`GOPROXY=off GOWORK=off GOTOOLCHAIN=local GOFLAGS=-mod=mod`, tar-pipe (no bind
mount), `--cgroup-parent=dev-background.slice`. **No Go toolchain was added to
this devcontainer** (A-042/A-043).

`extents`/`numStmts` compare as SETS: a profile is position-sorted, the oracle
emits in `cmd/cover` generation order; the orders differ and the contents do
not.

| witness | extents | numStmts | oracle executed / missing | naive (shipped) executed / missing |
|---|---|---|---|---|
| `collision-colA.go` | MATCH | MATCH | `{4,6}` / `{}` | `{3,4,5,6,7}` / `{}` |
| `collision-colB.go` | MATCH | MATCH | `{4,5}` / `{}` | `{3,4,5,6,7}` / `{}` |
| `seg.go` | MATCH | MATCH | `{4,5}` / `{7}` | `{3,4,5,6}` / `{7,8}` |
| `lit.go` | MATCH | MATCH | `{4,5,6}` / `{}` | `{3,4,5,6}` / `{}` |
| `shapes.go` | MATCH | MATCH | `{5,7,18,22,29,30,32,37,39}` / `{9,11,20,24,41}` | `{4,5,6,7,17,18,21,22,28,29,30,31,32,36,37,38,39,40}` / `{8,9,11,19,20,24,41}` |
| `edge.go` | MATCH | MATCH | `{7,8,10}` / `{}` | `{6,7,8,9,10}` / `{}` |
| `fixture/commit1/calc.go` | MATCH | MATCH | `{5,12,13}` / `{15}` | `{4,5,6,11,12,13,14}` / `{15}` |
| `fixture/commit2/calc.go` | MATCH | MATCH | `{5,12,13,22,25}` / `{15,30}` | `{4,5,6,11,12,13,14,21,22,23,24,25,26}` / `{15,29,30,31}` |

Named per the wave prompt's requirement that every witness be reported with its
derived result:

- **`collision-colA` / `collision-colB`** — the impossibility proof, answered.
  One profile (`coverage-collision.out`, a single record
  `3.22,7.2 2 1`), two sources, and the oracle derives **`{4,6}`** for A and
  **`{4,5}`** for B. The control test asserts the naive expansion returns the
  same `{3,4,5,6,7}` for both, so the pair cannot pass vacuously.
- **`seg.go`** — `{4,5,7}`, `BLOCKED-grammar.md` §2's stated truth. The rule
  **R1** the carve review constructed to fit the original four witnesses gives
  `{4,5,8}`; the test asserts `8 ∉ executable`, so a rule refitted to the
  fixtures would go red here.
- **`lit.go`** — see §3. The fabrication is fixed; the laundering is not, and
  is not claimed to be.
- **`shapes.go`** — every shared-boundary pair (`28.22,29.2` → `29.2,31.3`;
  `36.35,38.9` → `38.9,40.3`) attributed correctly. Ten fabricated lines
  removed (signatures 4/17/28/36, `case` labels 6/8/19, closing braces 31/40,
  continuation 38), each asserted absent.
- **`edge.go`** — `{7,8,10}`, derived with no rule that consults an end
  column, which is what A-218 asks of any successor.
- **`fixture/commit{1,2}/calc.go`** — equals the independent hand manifest
  `manifest/calc-statements.json`, authored from source bytes before any
  profile existed and never an input to the oracle: 7 statements, 5 executed,
  2 missing, `go`'s own 71.4% = 5/7.

**Toolchain delta, not glossed.** The frozen witnesses were produced under
`go1.25.12`; this probe ran `go1.25.14`. That is *measured* inert, not assumed:
the extent join is exact, so had the instrumenter changed across those patch
releases at least one witness would have disagreed.

---

## 3. What I did NOT do, and why

### 3.1 `lit.go`'s laundering is NOT fixed (A-393, filed B053)

The wave prompt's reviewer emphasis asks whether "`lit.go`'s missing-set
failure is actually fixed, not just the executed-set". **It is not, and it
cannot be at line granularity.** Line 4 is
`f := func() int { return 7 }`, which carries two counted statements that both
genuinely begin on line 4 — the assignment (block `3.14,4.18`, count 1) and the
func-literal body's `return 7` (block `4.18,4.30`, count 0). Executed-wins
promotes line 4, and it should: the line ran.

`BLOCKED-grammar.md` §3 already says this is "line granularity's own limit —
`coverage.py` shares it". Removing it needs a column-granular verdict field,
which is a wire-schema cut this wave is explicitly forbidden to make.

What the oracle *does* fix on `lit.go` is the fabrication: line 3, the
`func H() int {` signature, was reported executable by the shipped expansion
and is not code.
`test_lit_go_drops_the_fabricated_signature_but_still_launders_line_four`
asserts **both halves**, so a future change that does fix the laundering goes
red here instead of quietly contradicting the documentation.

### 3.2 No acceptance boxes ticked, no `status:` fields changed

See §1. Evidence for F008-A3/A4/A5 is end-to-end and does not exist yet.

### 3.3 Nothing touched `verdict.py` / `verify.py` / the schema / the drift-guard

Confirmed by the diff. `blocks` and `statement_attributed` are internal
representation in `coverage_parsers/model.py` and
`assay/statement_attribution.py`; neither reaches the wire. **No schema bump is
needed and none was made** — v9 stays v9, `carve-assets/W5/` untouched.

---

## 4. Decision asks (open — a product call, not mine to improvise)

**DA-1. `GoAdapter` is not in the built-in CLI registry.** `cli.py:392-403`
registers `python` (R1/R2/R3), `sql` (R2) and `javascript` (R1/R2) only. A Go
lane therefore cannot be run through the real `assay run` today; Go is
reachable only by constructing a registry directly, as the existing Go tests
do. Item 7 (F008-A5, "qualify against srdm's own Go covergate on the same
commits") appears to require a runnable Go lane — a covergate comparison that
never runs assay's own CLI would be qualifying the parser, not the product.
Registering Go at **R1** is arguably inside F008-A5, but the wave prompt's
scope list does not name it, and its NOT-IN-SCOPE list forbids R2/R3.
**Question: is registering `go` at `{"R1"}` in `_built_in_registry` in scope
for this wave?** I have not done it.

**DA-2. `helpers[]` gets its first producer, and an existing test asserts the
opposite.** `tests/test_cli_run.py:406` asserts `"helpers" not in document`,
and `Helper.__doc__` (`verdict.py:2792-2796`) states "P33 only VALIDATES this
array; it populates nothing". Item 2's contract (`helpers[].identity` records
`go version …`) makes this wave the first producer, so that assertion becomes
false *for a Go lane* while remaining true for Python/JS lanes. Also
`Verdict._check_helpers` (`verdict.py:3164`) requires a correspondingly-judged
claim per role. **Question: confirm the intended shape — `helpers[]` present
only on a lane whose adapter actually invoked a helper, with the Python/JS
"absent" assertions kept as the control?** That is what I would build, but it
changes a shipped invariant's stated scope and B047 item 5 calls this a
verification-and-documentation item.

Neither is blocking the work that remains; both need answering before item 7.

---

## 5. What a reviewer should push on

1. **Re-run the probe yourself** —
   `nyxloom-trove/carve-assets/P27-recarve/probe-stmtpos.sh` — and confirm
   `collision-col{A,B}` really resolve to different sets from the *same*
   profile bytes. The image may need building first; it was not on this host.
2. **Confirm `GOPROXY=off` really holds.** The probe runs `--network=none`, so
   a fetch cannot succeed silently; check the `go.mod` has no `require` lines
   and that no `GOMODCACHE` was populated.
3. **Attack the extent join.** `attribute_statements` refuses on any extent or
   `numStmts` disagreement. Plant a stale profile against a mutated source and
   confirm it refuses rather than attributing; then confirm the refusal message
   names extents you can grep for in the artifact.
4. **Check the `dedup` replication.** `stmtpos.go` reproduces `cover.go`'s
   global `seenPos2` map, which bumps an end column when two blocks share an
   extent. It is package-level and therefore order-sensitive across files on
   one command line. I have **not** constructed a witness that triggers it —
   it needs two blocks with identical extents. If you can build one, do; the
   extent join would report a mismatch rather than mis-attribute, so the
   failure direction is safe, but the case is unproven.
5. **`test_the_naive_expansion_cannot_tell_the_collision_pair_apart`** is the
   anti-vacuity control for the headline claim. Delete it mentally and check
   the remaining tests would still fail if the oracle regressed to the naive
   rule.
6. **The `statement_attributed` guard is documented but not yet built**
   (§1: item 1 is partial). Until `evaluate` refuses on it, the flag is
   inert — do not read `model.py`'s docstring as a description of shipped
   behaviour.

---

## 6. Gate

`pytest tests/` green is not gate-green (A-335), and the distinction was not
academic here — see §7.

### Run 1 — REFUSED (not a red suite)

```text
tester-unified-gate: assay has uncommitted changes; commit them before running the merge gate
GATE_EXIT=1
```

The LOG/REPORT/BRIEF were still unstaged. Re-run after committing them.

### Run 2 — PASS, on commit `4408622b`

Command, from `/workspaces/vbpub`:

```sh
bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-c-go
```

Verdict read in a SEPARATE step (LESSONS L4), never as a pipe tail:

```text
ASSAY_GATE_PHASE=wheel-installed
ASSAY_GATE_PHASE=attestation-hardened
ASSAY_GATE_PHASE=verdict-v5-accepted
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
ASSAY_GATE_PHASE=verdict-v6-v7-v8-hard-cut-verified
ASSAY_GATE_PHASE=verdict-v9-successors-verified
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
--- B006(a) WI-5 qualification receipt --- (PASS, all claims R0-R3 PASS)
ASSAY_GATE_PHASE=cmru-b006a-qualified
7 passed in 11.27s
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0
```

All 11 phases, completion marker present, exit 0.

**A later commit lands after this run** (the packaging declaration, its test,
A-394..A-396 and B054), so the gate is re-run on the new tip and that verdict
is appended below. Nothing in this report claims green on a commit the gate did
not judge.

---

## 7. Two live instances of the "read the status from the job" trap

Both caught only because the job's own status was read separately, and both
would otherwise have put a false conclusion into this report — which is the
incident AGENTS.md's rule was written from.

1. **The full `pytest tests/` run was reported to me as "exit code 0"** while
   pytest itself printed `4 failed, 3811 passed, 13 skipped in 423.53s`. The
   reported status belonged to the wrapper. The 4 were pre-existing `go_cover`
   assertions comparing a whole `FileCoverage` that now carries `blocks`; three
   are now strengthened to pin the expected extents, and the fourth narrowed to
   the line sets its docstring says it is about.
2. **Gate run 1 was reported as "exit code 0"** while `GATE_EXIT=1` — it had
   refused on uncommitted changes and run nothing at all. Reading only the
   wrapper would have recorded a PASS for a gate that never executed a phase.

## 8. One claim I made and then disproved (A-396)

I stated — in BRIEF-1 §4 and twice to the controller — that
`pyproject.toml`'s package-data omitting the helper's `.go`/`.mod` files was a
**real shipping blocker**, and the controller accepted it. It is not. Building
a wheel with the ENTIRE `[tool.setuptools.package-data]` stanza deleted still
produces `assay/helpers/go/stmtpos/{stmtpos.go,go.mod}` and
`assay/schemas/verdict.schema.json`: `setuptools_scm` installs a git file
finder and `include_package_data` defaults to true under pyproject metadata.

I had reasoned from the stanza's contents without ever running a build — the
A-334 pattern, with the stanza itself as the proxy that shared my hypothesis's
assumption. One `python -m build` and one `zipfile.namelist()`, under a minute,
refuted it.

The declaration is kept but rescoped (explicitness for the git-metadata-absent
build `fallback_version` anticipates), the claim is retracted at every site it
reached, and the test written alongside deliberately asserts the OUTCOME rather
than the mechanism so it survives the correction. The stale mechanism claim
this exposed in `test_verdict_schema_is_packaged.py`'s docstring is filed as
**B054**, not silently patched.
