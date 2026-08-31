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

### Run 3 — PASS, on commit `428f69e2` (the current tip)

Re-run after the packaging declaration, its test, A-394..A-396 and B054 landed,
because run 2's verdict belongs to `4408622b` and to nothing after it.

```text
ASSAY_GATE_PHASE=wheel-installed
... (11 phases, identical sequence to run 2) ...
ASSAY_GATE_PHASE=cmru-b006a-qualified
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0
```

11 phases, completion marker present, exit 0, no `FAILED`/`ERROR` lines. This
run matters more than run 2 for the packaging change specifically: the gate's
own `wheel-installed` phase builds a real wheel and installs it into a clean
run-venv, so `tests/test_go_helper_is_packaged.py` executed against a genuine
install rather than the source tree — which is the shape A-335 was recorded
for.

**The only commit after this run is docs-only** (this section, the LOG entry
and BRIEF-2's gate line). No source, test or packaging file changes after
`428f69e2`. Nothing in this report claims green on a commit the gate did not
judge.

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

---

# Generation 2 (fresh session, seeded from BRIEF-1 + BRIEF-2)

## 9. Scope items — state after generation 2

| # | item | state |
|---|---|---|
| 1 | A-239's shape carved concretely (`blocks` + new hook + core join) | **DONE** — the adapter/runner wiring generation 1 left open is landed and green |
| 2 | the oracle itself (B047 item 1) | **DONE** (gen 1), and now actually INVOKED — `adapters/go_stmtpos.py` is its Python half |
| 3 | fixture regeneration (F008-A4) | **not started** — and generation 2 added a reason to do it: B055 |
| 4 | `external_tools = ("go",)` (B047 item 2) | **DONE** |
| 5 | `judge.coverage.producer` for `go-cover` (B047 item 3) | **not started** |
| 6 | gate envelope `helpers[]` (B047 item 5) | **PARTIAL** — the producer side exists (`HelperInvocation` + `on_helper_invoked`, reported exactly once per lane, asserted); the `run_lane` → `Verdict.helpers` plumbing and A-395's parallel CLI test are NOT done |
| 7 | srdm covergate qualification (F008-A5) | **not started** — blocked behind registration |

**Registration (A-394) is deliberately NOT done.** The chain it was sequenced
behind is now complete, so the next generation may do it; this one stopped at
its checkpoint boundary rather than landing a registry change it could not
also gate-verify and qualify. `judge.language = "go"` therefore remains
refused, and README says so in those words.

**No acceptance box is ticked.** F008-A3/A4/A5 stay `absent` in
`2-product-definition.md`. The instruction was to tick only once a real Go
verdict is statement-granular end to end, provable by a real run — which
requires registration (step 7) and a toolchain. Neither exists yet. The chain
is proven at every seam by tests; the end-to-end claim is not, and claiming it
would be the A-067-class defect this report already records once.

## 10. What generation 2 built, and what proves each piece

| piece | where | what proves it |
|---|---|---|
| the protocol surface | `adapters/base.py` — `requires_statement_attribution`, `statement_blocks`, `StatementBlockReport`, `HelperInvocation` | `test_adapters_go_registration.py` (the Go pair asserted together); A-397 records the signature |
| the A-392 refusal | `evaluate.py` `_check_statement_attribution`, called from both entry points | `test_evaluate_statement_attribution_guard.py` — 4 tests, **both directions** |
| the ONE key join, exposed | `evaluate.resolve_coverage_keys` over `_repo_path_by_raw_key`; `_normalized_profile_files` now inverts it | `test_runner_statement_attribution_wiring.py::test_the_oracle_receives_the_key_the_evaluator_will_judge_not_the_raw_one` — a module-prefixed artifact key, stripped by the adapter, reaching the oracle as the repo path |
| the runner seam | `runner._attribute_statements_for_lane` | 8 tests: 5 naive lines become 2 judged; paths + anchor; identity once; A-391 mismatch → payload-free ERROR; contradiction → refusal; 2 controls |
| the oracle invoker | `adapters/go_stmtpos.py` | 13 tests over the document reader and every refusal, including a two-sided schema pin |

**The headline assertion is the one worth re-reading.** In
`test_the_runner_corrects_a_block_profile_before_the_verdict_is_computed`, a
profile whose artifact claims five executed lines `{3,4,5,6,7}` produces a
claim with `executable == 2`. Skip the correction and it is 5, and the verdict
is about function signatures and closing braces. That gap is the whole wave.

## 11. Thirteen pre-existing tests went red, and what was done about it

A-392's guard refused thirteen Go tests that judge committed coverprofiles
with no toolchain. **None was deleted and none was weakened to green.** Seven
were routed through `conftest.as_pre_oracle_attributed`, which sets the flag
and leaves the line sets untouched; six (`test_canary_go_pipeline.py`) through
a named `_PreOracleGoAdapter` subclass with the declaration downgraded to
`False`.

The distinction between those seven matters and is stated at the site: for
`test_adapters_go_python_equivalence.py` and `test_adapters_go_registration.py`
the profiles are HAND-BUILT line sets that are already statement-granular, so
the flag merely says what is true. For `test_adapters_go_union_fidelity.py`
they are parsed from the committed `hello.out`, and those sets are the naive
expansion A-234 already records as stale — the flag there is a placeholder for
F008-A4, not a claim.

Filed as **B055**, with acceptance criteria that include a test which goes RED
if the shipped adapter's declaration is ever flipped, so the double cannot
quietly become the product.

## 12. Decision asks (open)

**DA-3. Item 6's remaining half.** `HelperInvocation` is produced and reported
exactly once per lane through `on_helper_invoked`, but nothing yet carries it
into `Verdict.helpers`. A-395 rules the shape (do not weaken
`test_cli_run.py`'s `"helpers" not in document`; add a parallel Go-lane test
proven against `tester-unified-go:local`, never a mock). That parallel test
**cannot run in the registered gate**: `tester-unified:local` has no Go
toolchain, and the gate is the only thing that certifies green. So the proof
has to be a recorded probe in `carve-assets/P27-recarve/` (generation 1's
pattern) rather than a gate-run test — or the gate lane needs a Go image,
which is a bigger change than this wave. **Question: is a recorded probe the
accepted evidence shape for A-395's parallel test, or does item 6 wait for a
gate that can run Go?** Not blocking items 3/5/7; blocking item 6's closure.

## 13. Third live instance of the "read the status from the job" trap

The full `pytest tests/` run was reported to me as **"exit code 0"** while the
log's own appended marker said `PYTEST_EXIT=1` and pytest printed `13 failed,
3808 passed, 13 skipped in 344.74s`. Identical in shape to the two incidents
in §7, and caught the same way — the marker was appended by the job and read
in a separate step. Had the wrapper been believed, this report would have
recorded a green suite on a commit with thirteen red tests, and the thirteen
would have been the ones proving the wave's own guard works.

## 14. Gate — run 4, PASS on commit `c85c703a`

Command, from `/workspaces/vbpub`:

```sh
bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-c-go
```

**Which commit it judged, from the gate's own output rather than my assertion:**
the `wheel-installed` phase built and installed
`assay-4.0.1.dev20+gc85c703a-py3-none-any.whl` — `setuptools_scm` derives that
suffix from the commit under judgment, so the artifact names `c85c703a`
itself.

Verdict read in a SEPARATE step from the wrapper (LESSONS L4, and see §13 —
the wrapper for this very run reported "exit code 0", which would have been
right this time and was wrong twice before, which is exactly why it is not
what gets read):

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
ASSAY_GATE_PHASE=cmru-b006a-qualified
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0
```

All 11 phases, completion marker present, exit 0, no `FAILED`/`ERROR` lines.

**The phase that matters most for this change is `self-hosted-lane-passed`.**
It runs assay's own R1 lane over assay's own diff through the installed wheel
— so the new `_attribute_statements_for_lane` seam, the exposed
`resolve_coverage_keys` join and A-392's guard all executed on a real lane
against a real repository, on a `python` adapter declaring
`requires_statement_attribution = False`. That is the control this wave most
needed: the seam must cost every other language exactly nothing, and a
mistake there (a guard reading the flag alone, a join that drifted) would have
refused or mis-keyed assay's own lane rather than showing up in a unit test.

Separately, `pytest tests/` in the devcontainer: **3846 passed, 13 skipped**,
`PYTEST_EXIT=0` — read from the job's own appended marker, up from `3808
passed, 13 failed` before the thirteen Go tests of §11 were repaired.
`pytest` green is not gate green (A-335); both are recorded because they
answer different questions.
