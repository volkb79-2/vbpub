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

### 3.1 `lit.go`'s laundering is NOT fixed (A-393, filed B055)

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

Re-run after the packaging declaration, its test, A-394..A-396 and B056 landed,
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
**B056**, not silently patched.

---

# Generation 2 (fresh session, seeded from BRIEF-1 + BRIEF-2)

## 9. Scope items — state after generation 2

| # | item | state |
|---|---|---|
| 1 | A-239's shape carved concretely (`blocks` + new hook + core join) | **DONE** — the adapter/runner wiring generation 1 left open is landed and green |
| 2 | the oracle itself (B047 item 1) | **DONE** (gen 1), and now actually INVOKED — `adapters/go_stmtpos.py` is its Python half |
| 3 | fixture regeneration (F008-A4) | **not started** — and generation 2 added a reason to do it: B057 |
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

Filed as **B057**, with acceptance criteria that include a test which goes RED
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

---

# Generation 3 (fresh session, seeded from BRIEF-1 + BRIEF-2 + BRIEF-3 + the DA-3 ruling)

## 15. Scope items — state after generation 3

| # | item | state |
|---|---|---|
| 1 | A-239's shape carved concretely | **DONE** (gen 2) |
| 2 | the oracle itself (B047 item 1) | **DONE** (gen 1/2) |
| 3 | fixture regeneration (F008-A4) | **not started** |
| 4 | `external_tools = ("go",)` (B047 item 2) | **DONE** (gen 2) |
| 5 | `judge.coverage.producer` for `go-cover` (B047 item 3) | **DONE** — A-398 |
| 6 | gate envelope `helpers[]` (B047 item 5) | **PARTIAL**, unchanged from gen 2 |
| 7 | srdm covergate qualification (F008-A5) | **not started** — but its central question is now ANSWERED from source; see §17 |
| — | **registration (A-394)** | **DONE** — this generation's first task |

**No acceptance box is ticked, and F008-A3/A4/A5 stay `absent`.** This is a
deliberate refusal, not an oversight, and §18 gives the reason for each.

## 16. What generation 3 built

| piece | where | what proves it |
|---|---|---|
| `go` registered at `{"R1"}` | `cli._built_in_registry` | `test_cli_run.py::test_a_go_r1_lane_now_resolves_and_is_refused_for_the_TOOLCHAIN_not_the_registry` (the inverted test) + `::test_go_is_registered_at_r1_only_so_the_refusal_above_is_about_the_toolchain` (the registry-direct control, pinning R2/R3 refused) |
| the N-6 regression test's subject, preserved | `test_cli_run.py`, language `go` → `sql` | A-399; the test still reaches `cli.py`'s adapter-refusal call site, which registration had silently taken away from it |
| the `go-cover` producer vocabulary | `vocabulary.COVERAGE_PRODUCERS_BY_FORMAT` | `test_config_coverage_producer.py` — both names parametrized, an unknown name (`gcov`) still refused, and the OPTIONAL-ness pinned against `COVERAGE_PRODUCER_REQUIRED_FORMATS` |

Docs landed with the work, not after it: `cli.py`'s registry docstring
(rewritten, not deleted — the sequencing argument is the part a later reader
cannot recover from the finished entry), README's Go section, CONSUMERS.md's
Go section and its producer table, CHANGES.md `[Unreleased]`.

## 17. F008-A5 — covergate's algorithm, read from its own source

This is **not** the qualification F008-A5 asks for and is not offered as one.
It is the reconnaissance that qualification needs, and it answers the
question BRIEF-3 §5 item 2 flagged as the risk ("if the two disagree, work
out which side is right before concluding assay is") **before** a run, so
that the run is interpreted rather than improvised.

**Read from `shared-ramdisk-depot-manager/tools/covergate/profile.go`,
`ParseCoverProfile`:**

```go
for l := start; l <= end; l++ {
    if count > 0 { fc.Executed[l] = true; delete(fc.Missing, l); continue }
    if !fc.Executed[l] { fc.Missing[l] = true }
}
```

**That is the naive expansion — byte for byte the rule assay just removed.**
`FileCoverage.Executable(line)` is `Executed[line] || Missing[line]`, i.e.
"this line falls inside some block's extent", so covergate counts function
signatures, `case` labels, closing braces and statement-continuation lines as
executable code. Its own doc comment states the premise explicitly: "a block
spans a range of lines and every line in that range is executable."

**So assay and covergate WILL disagree, and assay is the correct side.** The
proof is already frozen and does not depend on this reading: A-217's
`collision-colA`/`collision-colB` pair are two gofmt-clean files that emit
byte-identical profiles while their statements begin on different lines
(`{4,6}` vs `{4,5}`). A rule that is a function of the profile alone must
give both the same answer; the two correct answers differ; therefore every
profile-only rule is wrong on at least one. covergate is a profile-only rule.
This is not a defect report against covergate — A-217 anticipated it in
writing ("covergate shares the inclusive convention") and it is why binding
the two at block granularity would have satisfied A-208 "in form while
defeating it in substance".

**covergate has two real mitigations, and neither closes the gap:**
`HasExecutableCode` (`hascode.go`) parses the file and excludes one that
declares no function bodies — but that is per-FILE, not per-line, so it
cannot demote a signature line inside a file that does have functions. And
`Evaluate` counts only ADDED lines, which bounds how often the difference is
reachable without changing its direction.

**The caveat project memory records is REAL and this reading locates its
mechanism.** Memory notes covergate "silently skipped one package (P14)" in a
past run. `Evaluate`'s `fc == nil` branch is where that lives: a changed
source file absent from the profile is either `NoCode` (excluded from the
ratio entirely) or `Unmeasured` (counted uncovered). Absent-because-no-test
and absent-because-`-coverpkg`-missed-it are distinguished ONLY by
`HasExecutableCode`, and `gate.sh` passes `-coverpkg=./...` precisely to stop
packages vanishing. A package that vanished anyway lands in `Unmeasured`,
which is surfaced but is a listing, not a refusal. **Consequence for whoever
runs the qualification: a covergate/assay disagreement must first be
classified as extent-expansion (expected, assay right) or file-absence
(covergate's `Unmeasured`/`NoCode` split, a different question entirely)
before either side is called wrong.** Do not average them.

**Why the qualification was not RUN here.** Running it needs a real Go lane
through the real `assay` CLI, and this devcontainer has no Go toolchain and
must not acquire one (A-042/A-043). §19 records the mechanism that makes it
buildable and what remains to prove.

## 18. What generation 3 did NOT do, and why

**No acceptance box ticked.** F008-A3 ("a Go R1 line claim is
statement-granular") is now true of the code at every seam and is registry-
reachable for the first time, but its evidence would be a real Go lane
producing a real statement-granular claim end to end — which is item 7's
qualification, not yet run. F008-A4 and F008-A5 are untouched and unrun
respectively. Ticking A3 on the strength of unit tests plus a registry entry
would be the A-067-class defect this report already records twice, and the
temptation is greater now precisely because the chain LOOKS finished.

**The `helpers[]` remainder (item 6) was not started.** DA-3 is resolved and
the pattern is clear, but the wiring (`run_lane` → `Verdict.helpers`) plus a
qualification test is a larger piece than the remaining budget, and it must
not be half-landed: `Verdict._check_helpers` requires a
correspondingly-judged claim per role, so a partial wiring is a schema-valid
document that lies about what ran.

**Neither B057 shortcut was touched.** Its own entry says the honest fix for
the first IS F008-A4, and F008-A4 needs the toolchain path of §19. Flipping
`as_pre_oracle_attributed` off without regenerating the fixtures would turn a
documented placeholder into thirteen red tests with nothing gained.

## 19. The environment fact that unblocks items 2, 3 and 6 — measured

BRIEF-1's committed probe script states that this devcontainer's `/tmp` "is
not visible to the Docker daemon at the same path", which is why every Go
probe so far has used a tar-pipe. **True as written, and the reason it gives
is a path TRANSLATION rather than an absence.** Measured this session from
this container's own mount table:

```text
$ docker inspect "$(cat /etc/hostname)" --format '{{range .Mounts}}{{.Destination}} <= {{.Source}}{{"\n"}}{{end}}'
/workspaces/vbpub <= /home/vb/volkb79-2/vbpub
/tmp              <= /home/vb/mdt--mounted-folders/tmp
```

So a bind mount CAN be constructed for either tree by translating the source
side, which is exactly what srdm's own `tools/gate.sh:48-61` already does
(`docker inspect` on `/etc/hostname`, same derivation, and it explicitly
refuses rather than hardcoding an operator's home directory).

That makes a transparent `go` shim buildable — the pattern
`tester-unified-go/Dockerfile` blesses in its own header ("`tools/go` (a
wrapper around this image) is how a cockpit gets Go ergonomics without a
cockpit Go"). A shim is **not** the test double A-334 forbids: the real
`go1.25` toolchain compiles and runs the real code inside
`tester-unified-go:local`; only the invocation is forwarded. A mocked
`go version` would be the forbidden thing, and this is its opposite.

Two facts make it reach assay's own machinery, and both were checked:
`runner.default_scratch_root` is `tempfile.TemporaryDirectory`, so `TMPDIR`
places the lane's snapshot wherever a caller wants it — including under a
bind-mountable tree; and a lane's `env_passthrough = ["PATH"]` carries the
shim into the lane command's own environment, while `shutil.which("go")` in
A-253's preflight finds it and stops refusing.

**What is NOT yet proven, and must be before anything is claimed from it:**
that `go test -coverprofile` inside the shim writes its artifact where
assay's `safeio.reserve_output` reservation expects (B049's failure mode is
exactly a tool writing somewhere the reservation does not hold), and that the
uid inside `tester-unified-go:local` (1003) can write into the snapshot. Both
are one probe each. Nothing in this report depends on either.

## 20. Registration's fallout was WIDER than BRIEF-3 predicted — three more tests, and one was a genuine design correction

BRIEF-3 §5 predicted fallout in `cli.py`'s docstring, in "any test asserting
`judge.language = "go"` is refused", and in README. All three were real. The
full suite then found three more, and they are not clerical:

**1. `test_adapters_javascript_registration.py::test_the_built_in_registry_
names_exactly_the_languages_this_build_reaches`** — a literal dict of every
language and its rigor set, whose docstring says "adding or dropping a
language cannot happen silently -- the registry IS this build's capability
declaration". It went red exactly as designed. Updated with `"go": ["R1"]`.
This is the test working, not fallout to be tidied.

**2 and 3. `test_docs_examples_and_vocabulary.py`'s two mutation-operator
documentation tests — and this one is a real correction (A-400).** The docs
gate derived its required operator set as *every registered language's*
operators, with a comment promising "the day a later package registers a Go
adapter, this set (and the docs gate below) expands BY ITSELF."

**It did expand by itself, and in the wrong direction.** A-394 registers `go`
at `{"R1"}` only, with `generate_mutation_sites` unconditionally
`UNSUPPORTED` and R2/R3 unregistered — so the automatic expansion demanded
documentation for three `go:*` operators no lane in this build can reach.
That is precisely the outcome A-287's own ruling rejects: "documenting an
operator a consumer cannot actually run is worse than the gap it closes." The
mechanism written to honour the ruling produced the ruling's stated failure,
because it assumed a Go registration would arrive WITH a mutation path.

Corrected by scoping the derivation to languages registered **at R2** — the
level at which a mutation operator is reachable at all, since
`judge.mutation.operators` belongs to an R2 lane. **It changes no set today**
(R2-registered is `{python, sql, javascript}`, javascript has no operator
entry, so required stays python's 4 + sql's 7 and excluded stays go's 3), and
that is the point: it restores the behaviour the ruling intended and will
also be right for the later package that gives Go a real R2.

**What caught it deserves its own sentence.** The companion test asserts its
excluded set is NON-EMPTY — "a fully-registered build would make this
assertion vacuous". That vacuity guard, written against a hypothetical, fired
on a real event two waves later and is the only reason the wrong expansion
was seen instead of shipped. It is the argument for writing such guards.

## 21. FOURTH live instance of the "read the status from the job" trap

The background full-suite run was reported to me by the harness as
**"completed (exit code 0)"** while the log's own appended marker said
`PYTEST_EXIT=1` and pytest printed `3 failed, 3845 passed, 13 skipped`. The
three were §20's tests.

Same shape as §7's two and §13's third; **fourth in this wave**, and the
first one where the false green arrived as a structured completion
notification rather than as command output, which is if anything easier to
believe. Had the wrapper been trusted, this report would have recorded a
green suite on a commit whose registry change had silently made a docs gate
demand documentation for unreachable operators — the A-400 defect, shipped,
with a passing suite cited as evidence it was fine.

## 22. Gate — run 5 RED (my own fault), run 6 PASS on commit `91b05186`

### Run 5 — RED, and worth recording rather than quietly re-running

```text
ASSAY_GATE_PHASE=verdict-v9-successors-verified
tester-unified: NO_MEASUREMENT/DIRTY_TREE (exit 3)
  commit: 367bbdf5d3c5a8444e4d9204136663383ec925ee
ASSAY_GATE_DIAGNOSTIC=self-hosted-lane-red; rerunning its command for visible diagnostics
ASSAY_GATE_DIAGNOSTIC=worktree-untracked-by-assays-own-query
assay/nyxloom-trove/reports/assay-WAVE-C-go-BRIEF-4.md
3845 passed, 13 skipped in 442.64s (0:07:22)
GATE_EXIT=1
```

**My error, not the gate's**: I wrote `BRIEF-4.md` as an untracked file while
the gate was already running, and the self-hosted lane refused a dirty tree —
correctly, and by the same rule that produced generation 1's run-1 refusal.
Recorded rather than suppressed because the interesting part is what it
proves about the diagnostic: the gate named the offending file by assay's own
untracked-file query, so the cause was readable in one line instead of
inferred. Note the suite itself was green inside the wheel (`3845 passed, 13
skipped`) — a fact that would have been very easy to report as a pass. It was
not a pass. `GATE_EXIT=1` is the verdict.

### Run 6 — PASS, on commit `91b05186` (the current tip)

Command, from `/workspaces/vbpub`:

```sh
bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-c-go
```

**Which commit it judged, from the gate's own output rather than my
assertion:** the `wheel-installed` phase built and installed
`assay-4.0.1.dev23+g91b05186-py3-none-any.whl` — `setuptools_scm` derives that
suffix from the commit under judgment, so the artifact names `91b05186`
itself.

Verdict read in a SEPARATE step from the wrapper:

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

All 11 phases, completion marker present, exit 0, no `FAILED`/`ERROR`/`DIRTY`
lines. (The log carries `GATE_EXIT=0` twice — once from the gate script's own
epilogue and once from the marker I append. They agree; both are recorded so
the duplication is not mistaken for a second run.)

**The phase that matters most for this change is `self-hosted-lane-passed`,
for a new reason.** It drives assay's own R1 lane through the freshly-changed
`_built_in_registry` — the function every language resolution passes through.
A registry entry with a bad rigor frozenset, or an import that failed to
resolve `GoAdapter` at module load, would take assay's own lane down rather
than showing up in a unit test. It did not. `topos-qualified` and
`cmru-b006a-qualified` add two more real downstream consumers resolving
adapters through the same changed function.

**The only commit after run 6 is docs-only** (this section, the LOG's gate
line and BRIEF-4 §7). No source, test, packaging, vocabulary or decision-file
changes after `91b05186`.

## 23. The trap's running total for this wave: SIX, and two of them today

§7 recorded two, §13 a third, §21 a fourth. Two more fired during this
generation's own closing sequence, both as *structured completion
notifications* rather than command output:

5. The second full-suite run: notification **"completed (exit code 0)"**,
   job's own marker `PYTEST_EXIT=1`, `3 failed, 3849 passed`.
6. **Gate run 5: notification "completed (exit code 0)" over `GATE_EXIT=1`
   on a gate that REFUSED its self-hosted lane.** This is the incident
   AGENTS.md's rule was written from, reproduced exactly, on the gate itself.

Six instances in one wave, every one self-caught by appending a marker and
reading it in a separate step. The failure mode has been stable across three
generations and two different delivery channels (command output and the
harness's own notification), which is the argument for treating the rule as
unconditional rather than as a precaution for risky-looking commands. Nothing
about gate run 5 looked risky; the suite inside it was green.

---

# Generation 4 (fresh session, seeded from BRIEF-1..4 plus the controller's DA-4..DA-7 entry, `vbpub@237b9585`)

## 24. Scope state after generation 4

| # | item | state |
|---|---|---|
| 0 | A-401 (F008-A5 reworded) + A-402 (in-image harness) recorded | **DONE**, `2f0cd223` |
| 1 | the in-image harness (build the pyz, mount, run a real R1 Go lane) | **DONE as a harness; the lane run BLOCKED at R1** — §25, §26 |
| — | **A-403 — the zipapp could not reach the Go oracle at all** | **FOUND and FIXED**, `8d7f8740` — §25 |
| — | **B059 / DA-8 — no Go lane reachable through the shipped CLI can resolve its own coverage keys** | **FOUND, MEASURED, NOT FIXED** — a product/design fork, §26 and §"Decision asks" |
| 2 | `helpers[]` wiring + the DA-3 qualification test | see §28 |
| 3 | F008-A4 fixture regeneration | not started |
| 4 | F008-A5 per DA-6 | **cannot start** — blocked behind DA-8, §26 |
| 5 | the acceptance boxes | **none ticked** — §29 |

## 25. The harness works, and building it found a real shipping defect (A-403)

**The environment facts, re-verified rather than taken from DA-5's word**
(A-334 applies to a controller's measurement as much as to my own):

```text
$ docker run --rm --network=none tester-unified-go:local sh -c \
    'command -v python3; go version; id -u; id -un; grep PRETTY_NAME /etc/os-release'
/usr/bin/python3
go version go1.25.14 linux/amd64
1003
gate
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
$ docker run --rm --network=none tester-unified-go:local python3 --version
Python 3.13.5
$ docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep tester
tester-unified-go:local  1.01GB
tester-unified:local  8.94GB
```

**The harness itself**, exactly as DA-5 prescribes and as
`tools/tester-unified-gate.sh:588-602` already does it for `tester-unified`:

```text
$ python3 assay/gate/distribution/build_release.py \
      --repo /workspaces/vbpub/.worktrees/assay-wave-c-go --outdir <outdir>
ASSAY_RELEASE_PHASE=clone / closure / wheel / zipapp / sidecars / manifest
ASSAY_RELEASE_COMPLETE=4.0.1.dev26+g8d7f8740
BUILD_EXIT=0

$ HOST_ROOT="$(docker inspect "$HOSTNAME" --format \
    '{{range .Mounts}}{{if eq .Destination "/workspaces/vbpub"}}{{println .Source}}{{end}}{{end}}')"
/home/vb/volkb79-2/vbpub

$ docker run --rm --network=none --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
      --mount "type=bind,src=$HOST_ROOT,dst=/workspaces/vbpub,readonly" \
      tester-unified-go:local sh -c 'id -un; id -u; python3 --version; go version; python3 <pyz> --version'
gate
1003
Python 3.13.5
go version go1.25.14 linux/amd64
assay 4.0.1.dev25+g2f0cd223
```

So DA-5's one remaining seam — uid 1003 reading the mounted tree and writing
the verdict path — holds: uid 1003 read the zipapp out of a read-only bind
mount of the repository, and later wrote `/work/verdict.json` into a
read-write mount of a scratch tree. The fixture repository is created INSIDE
the container so git's own files are owned by the uid that will read them; no
`safe.directory` override was needed and none was used.

**Then the first real run refused, and the cause was assay's own artifact.**
Inside the zipapp, `go_stmtpos.HELPER_DIR` resolves to
`…/assay-4.0.1.dev25+g2f0cd223.pyz/assay/helpers/go/stmtpos`, and `is_file()`
on its `stmtpos.go` is `False` — a zipapp keeps package data as zip members,
not as files. `derive_statement_blocks` therefore refused with "assay's Go
statement-position oracle is missing from the installation", naming assay's
own installation as incomplete, while `zipfile.namelist()` on the same
artifact shows `assay/helpers/go/stmtpos/stmtpos.go` and `go.mod` both
present.

That is not an edge case. A-402 makes the zipapp the ONLY install path into
`tester-unified-go` (no pip, no ensurepip), so the shape that could not run
the oracle is the shape every Go consumer runs. Fixed as **A-403**: the two
files are read through `importlib.resources` — the pattern
`verdict.schema_text` already used one module over — and staged into a
temporary directory for the duration of the call, on ONE path for every
installation shape rather than as a zipapp branch, because a branch taken
only inside a zipapp would never be exercised by the registered gate (which
installs a wheel). The regression guard is
`test_distribution_build_release.py::test_the_zipapp_can_stage_the_go_oracle_into_a_real_directory`,
which runs against the REAL artifact that module already builds, needs no Go
toolchain, and asserts as a vacuity guard that the zipapp's `HELPER_DIR`
really is not a real file — so a packaging change that materialises it makes
the test say so instead of passing quietly.

**Worth naming: the module's own comment had reasoned its way to the wrong
place from two true premises** — "`go run .` needs a real directory (a
`Traversable` need not be one)" and "a wheel install always materialises
package data as real files". Both true. The conclusion (therefore use
`__file__`, not `importlib.resources`) is wrong because a zipapp is not a
wheel install, and nothing in the test suite ran the zipapp against the Go
path. One `docker run` found it.

## 26. The blocker: a Go lane through the shipped CLI cannot resolve its own coverage keys (B059 / DA-8)

With A-403 fixed, the second run got further and refused for a different
reason. This one is not fixable without a product call.

A Go cover profile keys records by IMPORT PATH. Measured, real
`go1.25.14`, real `go test ./... -coverpkg=./... -covermode=atomic`, in the
image:

```text
mode: atomic
example.invalid/harness/internal/calc/calc.go:5.24,7.2 1 1
example.invalid/harness/internal/calc/calc.go:10.29,12.2 1 1
```

`git diff` names that file `internal/calc/calc.go`. `GoAdapter.module_path`
exists precisely to strip the difference — and `cli._built_in_registry`
constructs `GoAdapter()` with `module_path = ""` ("nothing to strip"),
`_KNOWN_JUDGE_FIELDS` (`config.py:243`) has no key for it, and `assay run`
has no flag. The full transcript, the control run with `module_path`
supplied, and the proof that no fixture layout can dodge it are in **B059**.

The two consequences worth carrying forward:

* **F008-A5 cannot start.** DA-6's prescribed srdm lane would resolve srdm's
  `srdm/internal/...` keys to `shared-ramdisk-depot-manager/srdm/internal/...`
  — under no source root, matching no file. The run would produce an
  `ERROR`, not a comparison.
* **The refusal names the wrong cause.** A consumer gets "the profile and the
  working tree are not the same revision", which sends them to look at their
  commits. That half is separable and smaller, and is listed in B059's
  acceptance.

The control half of the same probe is the useful positive result, and it is
the first real end-to-end evidence for F008-A3's substance: with
`module_path` supplied, the shipped oracle ran the real toolchain inside the
image and returned

```text
helper identity     = go version go1.25.14
helper tool/path    = go /usr/local/go/bin/go
  internal/calc/calc.go: extent 5.24,7.2 numStmts=1 stmt_lines=[6]
  internal/calc/calc.go: extent 10.29,12.2 numStmts=1 stmt_lines=[11]
```

— statement lines `{6}` and `{11}`, the two `return` lines, where the rule
this wave removed would have claimed `{5,6,7}` and `{10,11,12}`, i.e. both
function signatures and both closing braces.

## 27. Decision asks (open)

### DA-8 — how does a Go lane learn its module path?

**Blocks:** F008-A3's end-to-end evidence, F008-A5 entirely, item 2's
qualification test in its `assay run` form, and item 3's fixture regeneration
in any form that goes through a lane. **Evidence:** B059, measured in-image.

The fact is unambiguous (a Go profile is keyed by import path; `git diff` is
keyed by repo-relative path; something must supply the module path). Three
shapes are defensible and they differ in more than taste:

1. **A declared lane key** — `judge.module_path` (or
   `judge.coverage.key_prefix`), optional, defaulting to `""`. Precedent is
   strong and recent: A-328 added `judge.base_source` as an optional key with
   a closed vocabulary and a default, and no lane-schema bump accompanied it,
   so this is an ordinary additive field rather than a schema cut. Cost: the
   declaration can drift from `go.mod`, and a drifted one fails as the
   staleness message above, which points at the wrong thing.
2. **Derived from the repository's own `go.mod`.** DESIGN-GUIDE §4.2a prefers
   DERIVE over READ, and A-007's own reasoning selects between them by asking
   whose fact it is: the coverage FORMAT is a fact of the lane's argv, so it
   is declared; the module path is a fact of `go.mod`, so by that same rule it
   should be derived, with a declaration (if any) kept only as the
   refuse-on-mismatch cross-check A-007 built for the format. Cost: the seam
   does not exist. `normalize_coverage_key(key)` is pure and adapters are
   registry singletons, so derivation means either a new protocol member that
   receives `repo_top` (a second protocol extension on top of A-397's) or the
   core learning a Go-specific field, which the flat protocol exists to
   prevent.
3. **A registry that builds adapters per lane** rather than holding
   instances. Most general, largest blast radius: `_built_in_registry` is
   read by `assay lanes --json`'s inventory (A-349), by the docs gate's
   derivation (A-400), and by every language resolution, and generation 3
   already recorded that a registry change is read by more things than a grep
   for the adapter name will show (REPORT §20).

I have not picked one. Option 2 is what the project's own stated preference
selects, and option 1 is what its most recent precedent selects; that is a
real fork, not a gap in my reading, so it is written here rather than
improvised (BLOCKED protocol). Whichever is chosen, B059's second half —
the misattributed "not the same revision" message — should be ruled on at the
same time, because the right message depends on which layer owns the
prefix.

## 28. Item 6 — `helpers[]` has a producer, and three parts of it were decisions

BRIEF-3 called item 6 PARTIAL: `HelperInvocation` was delivered exactly once
per lane through `evaluate_r1`'s `on_helper_invoked` channel and nothing
carried it onward. It is now whole, and B047's own filing ("confirm the
LaneResult copies `helpers[]` verbatim; nothing on assay's side beyond
documenting") understated it — `Verdict.helpers` had no producer AT ALL since
P33, whose own docstring says so.

| piece | where | what proves it |
|---|---|---|
| the sink and the channel | `runner._run_prepared_lane`, `_PreparedOutcome.helpers` | the qualification run's real `helpers[]` (§29) |
| `HelperInvocation` → `Helper`, with the ROLE named by the runner | `runner._record_statement_position_helper` | `test_runner_helpers_envelope.py::test_the_runner_supplies_the_ROLE_and_the_adapter_never_does`, which also asserts `HelperInvocation` HAS no role — the assertion would be satisfiable by copying one if it did |
| omission when no helper ran (A-230a) | `assemble_verdict` | `::test_a_lane_that_invoked_no_helper_OMITS_the_field`, and `test_cli_run.py:406`'s untouched control (A-395) |
| the LOUD refusal | `assemble_verdict` | `::test_a_helper_with_no_correspondingly_judged_claim_is_refused_LOUDLY` |
| voiding a helper with the claim it produced | `_replace_highest_higher_rigor_claim_with_git_failed` | `::test_voiding_r1_after_a_cleanup_failure_voids_its_helper_with_it`, which also asserts the resulting verdict CONSTRUCTS — that is the state that would otherwise raise |
| ONE correspondence rule | `verdict.supported_helper_roles` | `::test_supported_helper_roles_names_only_what_a_claim_set_can_support` + a vacuity guard that every declared role is reachable by some claim set |

**Three of those are decisions rather than plumbing, and each had a rejected
alternative.**

**(a) The role is the RUNNER's to name.** `HelperInvocation` deliberately
carries none. `statement_blocks` is the only protocol method returning one, so
which of `HELPER_ROLES` an invocation belongs to is a fact of the call site. An
adapter free to name its own role could claim `mutation-sites` from a coverage
hook, and `_check_helpers` would then demand an R2 payload for work that
produced an R1 one — a refusal naming the wrong thing. This is the same
layering A-397(c) records for the type split.

**(b) A helper with no correspondingly-judged claim is REFUSED, not
filtered.** `_check_helpers` raises a bare `ValueError` that no caller catches,
so `assemble_verdict` gets the same `AssayError` guard shape its two siblings
already have. A silent filter was considered and rejected: it would also
swallow a genuine wiring defect, which is AGENTS.md's masked default one axis
over. The one legitimate way to hold a helper whose claim was voided is a
cleanup-only failure, and that path drops the entry itself, at the site that
took the payload away — the same move the judgment tier already gets one line
down.

**(c) The correspondence rule has ONE definition.**
`verdict.supported_helper_roles`, read by `_check_helpers` and by the runner.
The rejected alternative was a second copy of the three-role table in the
producer; the argument against it is A-385/A-367's, and the failure shape is
specific: a producer whose table disagreed with the schema's would build a
verdict the schema then refuses uncatchably. `verify.py`'s own copy is left
alone deliberately — it reads raw JSON rather than `Claim` objects and is the
independent transcription this project keeps on purpose (the gate's pin table
has the same shape).

## 29. The qualification transcript — a real Go R1 verdict, in the image

`tests/qualification/test_go_r1_real.py`, three tests, `ASSAY_GO_QUALIFICATION=1`:

```text
$ ASSAY_GO_QUALIFICATION=1 python3 -m pytest tests/qualification/test_go_r1_real.py -q
...                                                                      [100%]
3 passed, 1 warning in 16.87s
PYTEST_EXIT=0
```

It builds the shipped zipapp with `build_release.py` (HEAD's committed OID,
offline), materialises a two-commit Go fixture INSIDE the container so git's
files are owned by uid 1003, and runs a real
`go test ./... -count=1 -coverpkg=./... -covermode=atomic -coverprofile=…`
under `--network=none` and `--cgroup-parent`. The verdict it produced, run
again by hand against the same harness so the document can be pasted rather
than described:

```json
{
  "assay_version": "qualification",
  "commit": "b7d3bb56c0dbce5135e2fa81bea89774cb2ad98a",
  "enforcement": "gate",
  "helpers": [
    {
      "identity": "go version go1.25.14",
      "resolved_path": "/usr/local/go/bin/go",
      "role": "statement-positions",
      "tool": "go"
    }
  ],
  "judge_provenance": {
    "artifact": "zipapp",
    "digest": "6f3e33bbcb6855b3cc74e8433f6c84a51adfde4b68404c321746e794d2bc664b",
    "digest_algorithm": "sha256",
    "name": "assay",
    "version": "4.0.1.dev28+g77d9d6b9"
  },
  "lane": "unit",
  "outcome": "PASS",
  "schema_version": 9,
  "scope": "S1"
}
```

and its R1 claim:

```json
{
  "coverage": {
    "considered": 1, "covered": 1, "executable": 1, "pct": 100.0,
    "missing_lines": {}, "files_missing_coverage": [],
    "branch_capability": "unavailable", "exclusion_capability": "unavailable"
  },
  "rigor": "R1", "source": "computed", "status": "PASS",
  "verified_by_assay": true
}
```

**`executable: 1` is F008-A3's whole substance, measured end to end.** The head
commit adds four lines for `Multiply` — a comment, the signature, `return a *
b`, and the closing brace — and exactly one of them begins a counted statement.
The rule this wave removed would have reported 3, because the signature and the
brace are both inside the block's own extent (`10.29,12.2`). The paired FAIL
test asserts the same property from the other side: `Guard`'s `return -1` is
the ONLY line in `missing_lines`, and the assertion is written against the
source text (`[source[line - 1].strip() for line in lines] == ["return -1"]`)
rather than against a line number, so it names what was reported rather than
merely counting it.

**One correction found by running it.** The first version of the driver
reproduced `cmd_run`'s sequence except for its first step, so the verdict
carried no `judge_provenance` and the assertion that a consumer artifact names
its own archive had nothing to read. Fixed in `9714361c`; the fix is what makes
the `artifact: "zipapp"` line above real.

**Why it drives the LIBRARY entry point.** B059/DA-8. `assay run` on any real
Go module refuses today, measured (§26), so the only path a Go consumer has is
`runner.run_lane` with an adapter they built. The module's docstring says this
in full and says what to do when DA-8 lands: the assertions are about the
verdict document, not about which entry point produced it, so they should
survive the move to `python3 <pyz> run …` unchanged.

**The image assertion is not decoration.** `test_the_go_gate_image_still_carries_the_interpreter_the_judge_needs`
asserts `python3 --version` is `>= 3.11` inside `tester-unified-go:local`, plus
`go version` and uid 1003. Without it, a base-image change that dropped the
inherited interpreter would make this module skip for a reason that reads like
"not enabled" — a disarmed qualification that looks identical to an unrequested
one. A-402's Dockerfile paragraph and this test are the same fact recorded in
the two places that can act on it.

## 30. Gate — run 7, PASS on commit `9714361c`

Command, from `/workspaces/vbpub`:

```sh
bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-c-go
```

**Which commit it judged, from the gate's own output rather than my
assertion:** the `wheel-installed` phase built and installed
`assay-4.0.1.dev29+g9714361c-py3-none-any.whl`, so the artifact names the
commit under judgment.

Verdict read from the log in a SEPARATE step:

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

All 11 phases, completion marker present, exit 0, and a separate grep for
`FAILED`/`ERROR`/`DIRTY`/`failed` returned nothing. The devcontainer full suite
on the same tree is `3860 passed, 16 skipped`, `PYTEST_EXIT=0` — the three
skips added by this generation are the Go qualification module, which is
correct: no Go exists here or in `tester-unified`.

`verdict-v9-successors-verified` is the phase that matters most for this
change. `helpers[]` now has a producer for the first time, so the drift guard
and the v9 successor set are being asked about a field that was previously
only ever absent. It passed, and no schema was cut: `helpers` has been in v9's
shape since P33 and this wave populated it, which is exactly the "no wire
field" constraint the wave prompt set.

**The trap did not fire this time, and that is worth one sentence rather than
none.** Both background jobs (the full suite, the gate) reported completion
notifications that AGREED with their own appended markers. Six instances in
three generations and two agreeing ones do not make the rule optional; they
make it cheap. The markers were read separately anyway, in their own step, and
that is the only reason this paragraph can say which was true.

## 31. What generation 4 did NOT do, and why

**F008-A4 (fixture regeneration) was not started, and cannot be.** Its honest
form regenerates `tests/fixtures/go/hello/hello.out` and the canary control
from real toolchain output and re-derives every expectation from the oracle.
That needs a Go lane whose keys resolve, which is B059. Regenerating the
profile bytes alone would replace a wrong profile with a real one still read
against expectations derived from the wrong one — A-234's own warning, and the
conflation A-O19 exists to remove.

**F008-A5 was not started, for the same reason**, and §26 records the specific
way DA-6's prescribed srdm lane fails: `srdm/internal/...` keys resolving to
`shared-ramdisk-depot-manager/srdm/internal/...`, under no source root. The
reconnaissance §17 recorded is unchanged and still correct; what is owed is
still the run.

**No acceptance box is ticked, and F008-A3/A4/A5 stay `absent`.** A3 is the
tempting one: §29 is a real, end-to-end, statement-granular Go R1 verdict
produced by the shipped artifact against the real toolchain, which is exactly
the evidence A3 asks for — except that it was produced through the library
entry point because the CLI one refuses. Ticking A3 while `assay run` cannot
judge a Go module would record a capability this build does not have at the
boundary a consumer uses. It is one ruling away, and the evidence is already
written down; that is a better state than a tick that has to be argued with.

**B057's other two boxes were not touched.** Its own text says the first IS
F008-A4, and the second needs a decision this generation had no standing to
make. The third is done (§28's registry test).

---

# Generation 5

## 32. Scope state at the start of generation 5

Inherited tip `524dd16c` (gate-verified `9714361c` plus two docs-only
commits), tree clean, verified before anything was touched. The controller's
2026-09-01 entry at `vbpub@3a95459e` rules DA-8 and dispatches six items:
(1) DA-8 → gate; (2) F008-A4 fixture regeneration through the in-image
harness; (3) F008-A5 per DA-6, classification first; (4) F008-A3's tick with
the CLI-form transcript; (5) the other boxes; (6) file B060.

| item | state |
|---|---|
| 1. DA-8 | **DONE** — A-404, the protocol member, the derivation, both refusals, 42 new tests, docs, CLI help. Gate run 8 PASS |
| 6. B060 | **DONE** |
| 4. F008-A3's tick | **DONE** — the qualification drives `assay run`, and A3 is `proven` with four cited tests |
| 2. F008-A4 | **NOT DONE** — unblocked, not started; §38 |
| 3. F008-A5 | **NOT DONE** — unblocked, and §37 is a lane-shape correction it needs first |
| 5. the other boxes | F008-A4/A5 stay `absent`; §38 |

**One item arrived mid-flight and is done:** main's `a050a467` filed a
different B053 and B054 while this branch was in flight, so every id this
wave filed shifted up by two (B053→B055 … B058→B060) across 108 references
in 25 tracked files, as its own commit `e7eb5241`. The LOG entry carries the
map. Its one substantive consequence for this wave's own work is folded into
§36: main's B053 is why refusal (c)'s MESSAGE cannot be asserted through the
CLI, only its reason code.

## 33. DA-8 — what was built, and what proves each piece

**The member.** `LanguageAdapter.for_project(*, repo_top: Path,
project_root: Path) -> LanguageAdapter`, `adapters/base.py`. Keyword-only,
returning a NEW adapter, called once per lane from `runner.evaluate_r1`
immediately after `repo_top = git.repo_top(...)` and before
`_attribute_statements_for_lane`, `evaluate_targets` or `evaluate_coverage`
sees anything. The local name is **rebound**, which is the part that is not
decoration: the whole cost of B059 was two spellings of one path drifting
apart, and a binding delivered to the statement oracle while an unbound
adapter stayed in scope for the evaluator would have rebuilt that drift
inside its own fix.

Proved by `tests/test_runner_adapter_binding.py`, whose synthetic adapter
learns a key prefix from the project exactly as `GoAdapter` learns a module
path: the profile is keyed `example.invalid/harness/pkg/mod.bnd`, the tree
has `pkg/mod.bnd`, and only the BOUND adapter can bridge them — so a PASS is
positive evidence about which object judged. Its companion is a vacuity
guard asserting the unbound adapter really cannot resolve that key and that
the unstripped path really does not exist, because without it the PASS would
prove nothing. Four more assertions cover: bound exactly once and with the
lane's own two anchors; the same binding on the changed-lines path (both
modes judge one profile, A-385); and `for_project` raising rendered as a
complete payload-free R1 claim rather than escaping as a crash.

**The derivation.** `adapters/go_modfile.py`, ~330 lines of which about half
is the citation. `find_module_declaration(repo_top, project_root)` walks
nearest-first from the project root to the repository top, reads each
candidate through `safeio.read_bounded_file` (so the snapshot's own
traversal safety applies and a 1 MiB ceiling is enforced), and returns a
`ModuleDeclaration(module_path, module_file)` — the provenance is carried
because refusal (c) is required to name the file.

**On reading the grammar rather than recalling it.** The Modules Reference
gives `ModuleDirective = "module" ( ModulePath | "(" newline ModulePath
newline ")" ) newline .`, but the lexical rules are where a hand-written
parser goes wrong, so they were read out of the toolchain that produces the
profiles this join has to match:

```console
$ docker run --rm --network=none tester-unified-go:local sh -c \
    "sed -n '540,640p' /usr/local/go/src/cmd/vendor/golang.org/x/mod/modfile/read.go"
```

Four rules came back and all four are load-bearing. `//` is the only comment
form and `/*` calls `in.Error("mod files must use // comments (not /* */
comments)")` — in **two** places, once between tokens and once inside an
identifier scan, which is why `module example.com/foo/*x*/` is a syntax
error and not a path. An identifier breaks on `in.peekPrefix("//")`, which
is what makes `module example.com/x // vanity` yield the path. `isIdent` is
a negative rule: every printable non-space rune except `( ) [ ] { } ,`. And
`rule.go`'s `parseString` unquotes only a `"`-prefixed token, erroring on
any other token containing a quote character — so a **backquoted module path
is not valid input to `cmd/go`**, which is the one thing a reasonable
implementer would have got wrong from memory (the lexer *does* scan the
token; the rule then rejects it). `module` also appears in `rule.go`'s list
of verbs that may take the factored form, and `f.add` refuses it unless it
carries exactly one argument.

`tests/test_adapters_go_modfile.py` (24 tests) asserts each accepted and
refused shape, including the quoted form and the comment line the ruling
names. Two of them exist only because the parser is narrow on purpose: an
unknown escape refuses rather than decoding (a module path is ASCII letters,
digits and `-._~+/`, so no escape can appear in a legitimate one, and a
quietly mis-decoded `\n` would produce a prefix that matches nothing), and a
line beginning with `module` **inside another directive's factored block** is
not the module directive — paren depth is tracked, because a `require ( ... )`
block's lines also start with a path token.

**Refusal (b), no `go.mod`: `ERROR`/`BAD_LANE_CONFIG`.** The honest existing
code. The vocabulary is closed (A-050) and a new member is an enum widening,
which is a schema cut and out of scope. `BAD_LANE_CONFIG` is not a stretch
to fit: `evaluate.py:615` already raises exactly it, at runtime, for "the
project root is not contained by its own repository" — the same shape of
fact (the lane's declaration and the tree disagree about where the project
is). `UNREADABLE_ARTIFACT` was considered and rejected: its own docstring
scopes it to reading an INPUT (coverage, attestation), nothing is wrong with
the coverage artifact here, and blaming it would send a consumer to re-run
their tests over a configuration fault. `MISSING_EXTERNAL_TOOL` is about
PATH and would fold a lane fault into an environment one — the same
mistake `go_stmtpos._derive`'s comment already warns about in the other
direction.

**Refusal (c), a foreign key: `ERROR`/`UNREADABLE_ARTIFACT`,** raised from
`GoAdapter.normalize_coverage_key` and naming the key, the derived module
path and the `go.mod`. Two placement questions were settled rather than
drifted into. It cannot live in the core (`_attribute_statements_for_lane`
would have to know what a module path is, which is the one thing the flat
protocol prevents) and it cannot live in `statement_blocks` (which receives
paths that have already been through the join and the project prefix, so it
can no longer see the raw key). `normalize_coverage_key` is the only place
that sees the raw key and knows the module path. It fires **only when the
path was derived** — `module_file` non-empty — which is the ruling's own
scoping, since the message it mandates names a `go.mod` that exists only in
that case; a library caller who supplies `module_path` by hand has no
`go.mod` for assay to contradict them with, so the three pre-existing
boundary-safety tests keep their subject and their assertions.

**Sub-decision the ruling left open, recorded rather than improvised:** a
hand-constructed `module_path` that DISAGREES with the derived one is
refused, not given a precedence. That is A-328's own rule pointed at this
case — refuse precedence between two sources of one fact, because whichever
loses is config nothing reads — and A-007's cross-check shape one field
over.

**`SqlAdapter` deviates from the ruling's "every other adapter returns
`self`", deliberately and in one direction only.** The single call site is
the top of `evaluate_r1`, which is exactly where `normalize_coverage_key` is
reached from, and a SQL lane is R0,R2-only (A-242), so it never arrives.
`return self` there would be a line no SQL lane could execute — the vacuity
`test_adapters_sql_unreachable_methods.py` exists to prevent — while the
raise makes a future caller who routes SQL through R1 find out loudly. The
deviation is recorded in A-404 rather than left for a reviewer to notice.

## 34. Which assertion catches a mutant that skips derivation

The ruling asks for this explicitly, so it is stated rather than implied. A
mutant that makes `GoAdapter.for_project` `return self` — leaving
`module_path` as `""` — is caught at three different distances, and the
outermost one is the one that matters:

* `test_adapters_go_for_project.py::test_a_bound_adapter_strips_its_own_modules_prefix`
  is the tightest and the least circular: it asserts the OUTCOME
  (`example.invalid/harness/internal/calc/calc.go` → `internal/calc/calc.go`),
  not that a field was set. A mutant returning `self` returns the key
  unchanged and this goes red.
* `test_runner_adapter_binding.py::test_the_bound_adapter_is_what_resolves_the_profiles_keys`
  catches it through the CORE, and is language-free: with the unbound
  adapter the key resolves to a file that does not exist, under no source
  root, so the claim is no longer a PASS. This is the assertion that would
  also catch the subtler mutant — the core calling `for_project` and then
  using the unbound adapter anyway.
* `tests/qualification/test_go_r1_real.py` catches it end to end against the
  real toolchain: `assay run` on a real Go module returns
  `ERROR`/`UNREADABLE_ARTIFACT` instead of a PASS, which is precisely the
  B059 transcript. It is the strongest evidence and the only one that needs
  Go, which is why the first two exist.

The vacuity guard for the second is its own companion test asserting the
unbound adapter genuinely cannot resolve that key — without it, a key that
happened to resolve either way would make the PASS prove nothing.

## 35. F008-A3's evidence, now through `assay run` — the CLI-form transcript

`tests/qualification/test_go_r1_real.py`, **five** tests (three inherited,
two new), `ASSAY_GO_QUALIFICATION=1`, against the real `go1.25.14` inside
`tester-unified-go:local`:

```text
$ ASSAY_GO_QUALIFICATION=1 python3 -m pytest tests/qualification/test_go_r1_real.py -q
.....                                                                    [100%]
5 passed, 1 warning in 28.60s
PYTEST_EXIT=0
```

**Every assertion survived the move from the library driver to the CLI
unchanged**, which is what BRIEF-5 §4 predicted and is recorded because the
alternative was a finding. What was deleted is the `_DRIVER` heredoc — 25
lines that reproduced `cmd_run`'s sequence in order to slip
`GoAdapter(module_path=…)` into it. What replaced it is the command a
consumer types:

```text
$ python3 /work/dist/assay-4.0.1.dev32+g4b5e7707.pyz run unit \
      --file /work/fixture/assay.toml --verdict-json /work/verdict.json \
      --require-judge-provenance
```

`--require-judge-provenance` is new and deliberate: it refuses before any
work unless assay can name the artifact it was installed from, so the
`artifact: "zipapp"` assertion cannot be satisfied by an absence. The exit
code is now asserted too (0 for the PASS scenario, 1 for the FAIL), which the
library driver could not check at all.

The verdict, re-run by hand against the same harness so it can be pasted
rather than described (`scratchpad/capture.log`):

```json
{
  "argv_effective": ["go", "test", "./...", "-count=1", "-coverpkg=./...",
                     "-covermode=atomic", "-coverprofile=.assay/cover.out"],
  "assay_version": "4.0.1.dev32+g4b5e7707",
  "commit": "fa1db7dea8d4ff99d9a6affc10f4199bc96f6c43",
  "helpers": [
    {
      "identity": "go version go1.25.14",
      "resolved_path": "/usr/local/go/bin/go",
      "role": "statement-positions",
      "tool": "go"
    }
  ],
  "judge_provenance": {
    "artifact": "zipapp",
    "digest": "ce3cccb055cfe7343c380e5a0123a1c96c54ef90a4496e95459dde6fcacacf1f",
    "digest_algorithm": "sha256",
    "name": "assay",
    "version": "4.0.1.dev32+g4b5e7707"
  },
  "lane": "unit",
  "outcome": "PASS",
  "schema_version": 9,
  "scope": "S1"
}
```

and its R1 claim:

```json
{
  "coverage": {
    "considered": 1, "covered": 1, "executable": 1, "pct": 100.0,
    "missing_lines": {}, "files_missing_coverage": [],
    "branch_capability": "unavailable", "exclusion_capability": "unavailable"
  },
  "rigor": "R1", "source": "computed", "status": "PASS",
  "verified_by_assay": true
}
```

**Nothing in this document was declared.** `judgment.resolved.language` is
`"go"`, `source_roots` is `["internal"]`, and the module path
`example.invalid/harness` that made the keys resolvable appears nowhere in
the lane file, in the argv, or on the command line — it was read out of the
fixture's own `go.mod`. That absence is the whole of A-404.

`executable: 1` is F008-A3's substance, unchanged from §29 and now produced
at the boundary a consumer uses: the head commit adds four lines for
`Multiply` — a comment, the signature, `return a * b`, the closing brace —
and exactly one begins a counted statement. The removed rule reported 3.

## 36. The two refusals, proven in-image against real profiles

The ruling requires both, and neither is asserted against a hand-written
profile: each is a real `go test` run whose real output disagrees with the
tree in the specific way the refusal is about.

**(c) — a profile from a different module.** The fixture is a repository
holding TWO Go modules: `example.invalid/harness` at the top and
`example.invalid/sub` beneath it. The lane's command is `sh -c "cd sub && go
test ./... … -coverprofile=../.assay/cover.out"`, so the toolchain really did
run in the nested module and really did key every record by
`example.invalid/sub/pkg/…`, while the lane's project root belongs to the
outer module. This is exactly the shape A-404 (d) names — nested modules
never appear in `go test ./...`'s own output, and a project root above
several modules surfaces here rather than as a silent partial measurement.

```json
{"reason_code": "UNREADABLE_ARTIFACT", "rigor": "R1", "source": "computed",
 "status": "ERROR", "verified_by_assay": true}
```

Payload-free, as an ERROR claim must be (A-136).

**The message is not observable through the CLI, and that is main's B053, not
a gap in this refusal.** `a050a467` records that an ERROR-outcome verdict's
detailed message is constructed and then surfaced nowhere a consumer can read
it — not on stderr, not in the verdict document. It is exactly what
generation 4's own B059 transcript showed without naming: the CLI printed
`unit: ERROR/UNREADABLE_ARTIFACT (exit 2)` plus commit and argv, and the
message had to be recovered by probing the library. So the proof is split on
purpose, and each half is asserted where it is actually visible:

* **the reason code**, through `python3 <pyz> run …` in the image — this
  test;
* **the message**, at the library boundary —
  `test_adapters_go_for_project.py::test_a_key_outside_the_derived_module_refuses_and_names_all_three_facts`,
  which asserts the key, the derived module path and the `go.mod` are all
  named, **and** that the word "revision" does not appear, because the
  message this replaces blamed staleness and that half of B059 is a FIX, not
  a keep.

The in-image test is named `…_refuses_through_the_cli` rather than
`…_and_names_both` for the same reason: a test whose name claims more than
its assertions check is the failure this project keeps finding in other
people's suites. **B053 is not fixed here** — the controller has paired it
with B049 for a post-Wave-C patch wave — but a consumer hitting refusal (c)
today gets a correct reason code and a message they cannot see, and that is
worth knowing before the srdm run.

**(b) — a project root in no Go module.** The identical tree with the root
`go.mod` removed. The distinction that makes this test worth running is
asserted rather than assumed: **R0 PASSES.** The lane's own command still
succeeds, because it runs inside `sub/`, which IS a module. So this is not a
`go test` failure wearing a different name — it is a lane that declares a Go
judge over a directory belonging to no Go module.

```json
{"reason_code": "BAD_LANE_CONFIG", "rigor": "R1", "source": "computed",
 "status": "ERROR", "verified_by_assay": true}
```

Both scenarios exit 2 from the shipped CLI, which the test asserts.


## 37. F008-A5: DA-6's prescribed lane shape does not work, and the reason is the key join

This is reconnaissance done while implementing DA-8, recorded here so
generation 6 does not spend a container run discovering it. **It is not a
disagreement with DA-6's substance** — the commits, the argv and the
classification rule all stand. It is the lane FILE's shape.

DA-6 prescribes `cwd = "shared-ramdisk-depot-manager"` with
`source_roots = ["shared-ramdisk-depot-manager/internal"]`. Those two
spellings together imply `project_root = /workspaces/vbpub` (the repository
top), because `judge.source_roots` are resolved against the project root and
B043's `cwd` moves only the COMMAND's working directory. Two things then
fail, and the second is the interesting one:

1. **A-404 (b) refuses.** The derivation searches at and above
   `project_root`, and there is no `go.mod` at the repository top. The lane
   would report `ERROR`/`BAD_LANE_CONFIG` before it judged anything.
2. **Even with the module path in hand, the keys would not resolve.**
   `evaluate._repo_path_by_raw_key` is `normalize_coverage_key` followed by
   `_to_repo_relative_key(..., project_prefix)`, and `project_prefix` is
   `project_root` relative to `repo_top`. With `project_root` at the
   repository top that prefix is `.`, so `srdm/internal/store/x.go` strips to
   `internal/store/x.go` and resolves to `<repo>/internal/store/x.go` — which
   does not exist. The file is at
   `shared-ramdisk-depot-manager/internal/store/x.go`.

Both are the same fact from two directions: **for a Go lane, the project root
must BE the module root.** That is not an artefact of A-404's derivation; the
key join required it before this wave, and A-404 (d)'s "one Go module per
lane, `cwd` is that module's root" is that requirement stated. It is now
documented in CONSUMERS.md point 6 and README.

**The corrected shape, which needs no change to anything DA-6 rules:**

```toml
# lives at shared-ramdisk-depot-manager/<name>.toml -- UNTRACKED, so srdm's
# tree stays exactly as committed. `measurability.check_dirty_tree` is
# scoped to `source_root_paths` (its own docstring), and this file is not
# under `internal/`, so it does not make the lane refuse DIRTY_TREE.
[lanes.coverage.judge]
language = "go"
source_roots = ["internal"]        # project-relative -> srdm/internal
base = "10b174a5"
```

with `project_root = shared-ramdisk-depot-manager`, **no `cwd` at all**, and
srdm's own `tools/gate.sh:105` argv. Facts checked while writing this:
`shared-ramdisk-depot-manager/go.mod` declares `module srdm` (a bare
non-domain path — legal, and it means profile keys are `srdm/internal/...`),
and `gate.sh:105` is verbatim `go test ./... -count=1 -coverpkg=./...
-covermode=atomic -coverprofile=/tmp/srdm-cover.out`, with `-coverpkg=./...`
carrying the comment that explains why it is load-bearing.

**One thing generation 6 must plan for that DA-6 does not mention:** `assay
run` judges the repository's HEAD, and the differential is
`10b174a5` → `83c2ff79`. Neither is `main`'s tip, and this wave may not move
the shared checkout's HEAD. So the run needs its own checkout of
`83c2ff79` — a `git worktree` outside the shared one, or a clone inside the
container — with the lane file placed in its `shared-ramdisk-depot-manager/`.
That is a harness question, not a product one, and the in-image harness
(BRIEF-5 §3) already builds a git repository inside the container for exactly
this kind of reason.

## 38. What generation 5 did NOT do, and why

**F008-A4 (fixture regeneration) was not started.** It is now unblocked —
B059 was the blocker and it is closed — but it is the larger of the two
remaining items and this generation cut at DA-8's green gate rather than
starting it with too few calls left to finish honestly. B057's own text and
REPORT §31 still state the shape it must take: regenerate
`tests/fixtures/go/hello/hello.out` and the canary control from real
toolchain output **and** re-derive every asserted line set from the oracle in
the same change. Regenerating the bytes alone would replace a wrong profile
with a real one still read against expectations derived from the wrong one,
which is A-234's own warning.

**F008-A5 was not run**, for the same budget reason; §37 is the
reconnaissance it needs and is worth more than a half-finished run.

**F008-A3 IS ticked; F008-A4 and F008-A5 are not.** A3's evidence is now what
it actually asks for — a statement-granular Go R1 verdict from the shipped
artifact, through `assay run`, against the real toolchain (§35) — and its
stale text ("BLOCKED on A-217's source-side statement-position oracle …
designed but not carved") was false in three ways after this wave, which is
worse than an untick'd box.

I first wrote that the tick should wait so A3, A4 and A5 could "land together
against one gate", then checked the premise instead of trusting it:
`grep -rl 2-product-definition tests/ tools/ gate/` returns nothing, so **no
gate judges that file at all** and the "one gate" argument was about a
constraint that does not exist. The tick is a documentation act citing four
tests, two of which run with no toolchain (A-217's frozen collision
witnesses) so the criterion is not proven only where the registered gate
cannot re-run it. Recorded because the reasoning I nearly shipped was wrong,
not merely conservative.

One thing was NOT invented on the way in: the note about the qualification
entries being opt-in went into the existing `text` field rather than a new
`evidence_note:` key. Sixty acceptance rows use exactly `text`/`status`/
`evidence`, and a schema key with one user in a file nothing parses is a
convention invented for a single sentence.

The backlog boxes that WERE discharged are ticked in `4-backlog.md` (B059's
first three) and nothing else was touched.

## 39. Gate — run 8 PASS on `1885d64e`, run 9 PASS on `dd1e2c46`

Command, from `/workspaces/vbpub`, both times:

```sh
bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-c-go
```

**Run 8, on `1885d64e`** — the DA-8 implementation plus its LOG/REPORT:

```text
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=wheel-installed
ASSAY_GATE_PHASE=attestation-hardened
ASSAY_GATE_PHASE=verdict-v5-accepted
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
ASSAY_GATE_PHASE=verdict-v6-v7-v8-hard-cut-verified
ASSAY_GATE_PHASE=verdict-v9-successors-verified
ASSAY_GATE_PHASE=topos-qualified
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0
```

Verdict read in a SEPARATE step, as always, and a second, independent grep
for `FAILED|DIRTY_TREE|Traceback|ERROR` over the same log returned nothing.

**Run 9, on `dd1e2c46`** — and it was not optional. The id renumbering
(`e7eb5241`) is described as mechanical, but "mechanical" is a claim about a
rewrite that touched **three source comments and nine test modules**, and a
word-boundary regex over 25 files is exactly the kind of change that is
obviously safe right up until it is not. Re-gating cost 13 minutes and
removes the need to take my own word for it.

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
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0
```

Same separate-step read, same independent grep.

**Devcontainer full suite** on the same tree, twice (once before the docs and
CLI-help edits, once after): `3902 passed, 18 skipped`, `PYTEST_EXIT=0`, up
from the inherited `3860 passed, 16 skipped`. The 42 new tests are 24 for the
`go.mod` directive parser, 11 for the adapter's binding and refusals, 5 for
the core seam, and 2 in-image refusal proofs — and those last two are the two
extra SKIPS, which is correct: no Go exists in this devcontainer or in
`tester-unified`.

**Go qualification**, run separately because neither gate image can host it
(DA-3/A-402): `ASSAY_GO_QUALIFICATION=1`, `5 passed`, `PYTEST_EXIT=0`, 28.6s,
against the real `go1.25.14` inside `tester-unified-go:local`.

**The wrapper-vs-job trap did not fire this generation.** Every background
job's completion notification agreed with its own appended marker — eight
instances across four generations, four of them agreeing. The markers were
read separately anyway, in their own step, which is the only reason this
sentence can say which was true rather than assuming it.

---

# Generation 6

## 40. Scope state, and what generation 6 did

Inherited tip `86b4efae` (gate-verified `dd1e2c46` plus one docs-only commit),
tree clean, verified before anything was touched. The controller's 2026-09-02
entry at `vbpub@53eba55b` rules **DA-9** and dispatches four items.

| item | state |
|---|---|
| 1. F008-A4 — fixture regeneration + re-derived expectations | **DONE**, `394c6cc2` |
| 2. F008-A5 — the srdm qualification per DA-6 as corrected by DA-9 | **DONE**, §41 |
| 3. the F008-A4/A5 acceptance boxes | **DONE** — both `proven`; **F008 is `shipped`** and **M6 is `done`** |
| 4. B057's remaining boxes | **DONE** — all three tick; the canary double is deleted, not documented |

One item was not on the list and is the most consequential thing here:
**B061**, a real defect in assay's own statement join, found by F008-A5's
qualification on its first run and fixed at `875382d2`. §41 is the reasoning
that found it, and it is the reason the classification discipline DA-6 imposed
is worth more than the run it governs.

## 41. F008-A5 — the srdm qualification, and the defect it found

### The harness

DA-9's ruling, followed exactly. A synthetic two-commit repository built
INSIDE `tester-unified-go:local` from the bind-mounted host repository, read
only:

```sh
git -c safe.directory=* --git-dir=/hostrepo/.git archive 10b174a5 shared-ramdisk-depot-manager | tar -x
git add -A && git commit -m "commit1: srdm subtree at 10b174a5"   # -> e1b39fb8
rm -rf shared-ramdisk-depot-manager
git -c safe.directory=* --git-dir=/hostrepo/.git archive 83c2ff79 shared-ramdisk-depot-manager | tar -x
git add -A && git commit -m "commit2: srdm subtree at 83c2ff79 (P14)"
```

`git diff --stat` of that pair inside the synthetic repository: **32 files
changed, 3917 insertions(+), 35 deletions(-)**, including `internal/power/*`
and `internal/opctl/update.go` — the controller's own re-check, reproduced.
**Nothing was committed under `shared-ramdisk-depot-manager/` in vbpub**; the
whole exercise lives in a container-local repository under `/work`.

The lane file sits at `shared-ramdisk-depot-manager/assay-qualification.toml`
inside that synthetic repository — BRIEF-6 §3's corrected shape, so
`project_root` IS the module root — with `source_roots = ["internal"]`, **no
`cwd`**, and srdm's own `tools/gate.sh:105` argv verbatim but for the
`-coverprofile` path, which is assay's reserved artifact. It is committed (a
third commit) rather than left untracked, because `covergate` refuses on a
dirty tree while assay's own dirty check is scoped to source roots; committing
it satisfies both, and a `.toml` outside `internal/` is judged by neither.

**Nothing declares the module path.** srdm's `go.mod` says `module srdm`, so
its profile keys are `srdm/internal/...`; A-404's derivation strips them. That
is the whole of B059's fix, exercised on a real consumer's tree for the first
time.

### The first run, and the number that did not fit

| | changed executable | covered | pct | verdict |
|---|---|---|---|---|
| `covergate` | 684 | 639 | 93.4% | PASS (75% floor) |
| assay (as shipped at `394c6cc2`) | 418 | 163 | 39.0% | **FAIL/`UNCOVERED_LINES`, 255 lines** |

The denominator fit the prediction exactly: A-217/B058 say `covergate`
over-approximates, so assay's executable set must be SMALLER, and 418 < 684.
Had I stopped there, the honest-looking conclusion was already available —
"assay is stricter by design, as A-217 predicted in writing" — and it would
have been wrong.

**The covered RATIO did not fit.** Extent-expansion inflates numerator and
denominator together; it cannot move 93.4% to 39.0%. Something was calling ~210
genuinely-covered statement lines missing, and neither of DA-6's two categories
covers that. Chasing the part that did not fit is what found the defect.

**Two controls were run before any hypothesis was tested.** First, srdm's argv
twice in the same checkout: the profiles are NOT byte-identical, but every
difference is in `cmd/srdm`, outside both tools' `internal` scope. Second, and
decisively, a control lane whose argv is `cp /work/srdm-cover-1.out
.assay/cover.out` — assay judging the *byte-identical file* `covergate`
judged. It returned `418 / 163 / 39.0%`, identical to the real lane. So the
disagreement was not a measurement difference at all; it was a judging-rule
difference, in one of the two rules.

### B061

`go test -coverpkg=./...` instruments every package into **every** test binary
and `go test` concatenates each binary's own profile section, so one block gets
one record per binary. srdm's profile is 68 761 lines and carries **20 records
per block**:

```text
$ grep -c '^srdm/internal/power/wings.go:' srdm-cover-1.out
1780
$ grep '^srdm/internal/power/wings.go:59.22,65.3 ' srdm-cover-1.out | sort | uniq -c
     19 srdm/internal/power/wings.go:59.22,65.3 1 0
      1 srdm/internal/power/wings.go:59.22,65.3 1 1
```

`go_cover.parse` folds those correctly at LINE granularity and keeps every
record unmerged in `FileCoverage.blocks` (A-239, deliberately).
`attribute_statements` then built `{block.extent: block}` — keeping whichever
record came **last** — and read `count` off it. Last is `0`, so a block the
toolchain reports as executed was attributed to `missing`.

Fixed at `875382d2` by folding repeated records executed-wins before any count
is read. The full entry, including why every frozen P27 witness and every
F008-A4 fixture has exactly one record per block and so could never have caught
this, is **B061**.

### The qualification, on the fixed build

`assay-4.0.1.dev38+g875382d2.pyz`, `python3 <pyz> run coverage --file … --verdict-json … --require-judge-provenance`, inside
`tester-unified-go:local`, `--network=none`, `--cgroup-parent=dev-background.slice`:

```text
coverage: PASS (exit 0)
  commit: 82ebfa4291727907c0c5a547ecabc8f3abc97afa
  argv: go test ./... -count=1 -coverpkg=./... -covermode=atomic -coverprofile=.assay/cover.out

R1 PASS  considered=12  executable=418  covered=394  pct=94.26
helpers: [{role: statement-positions, tool: go,
           resolved_path: /usr/local/go/bin/go, identity: "go version go1.25.14"}]
judge_provenance: {artifact: zipapp, version: 4.0.1.dev38+g875382d2, sha256: 2780f84a…}
```

and the same numbers, `418 / 394 / 94.26`, from the control lane judging
`covergate`'s own profile file. `covergate` on that file: `diff-coverage OK:
639/684 changed executable lines covered (93.4% >= 75.0% floor)`.

### The classified table — every disagreement, classified before a side was named

`covergate` was re-run at `-fail-under 100` purely to make it PRINT its
uncovered set (the threshold changes no classification), and the oracle was run
over all 26 changed `internal/` files at HEAD.

**Uncovered lines, per file:**

| file | assay | covergate | both | covergate-only, begins NO statement | covergate-only, IS a statement | assay-only |
|---|---|---|---|---|---|---|
| `internal/config/config.go` | 2 | 4 | 2 | 2 | 0 | 0 |
| `internal/doctor/doctor.go` | 1 | 1 | 1 | 0 | 0 | 0 |
| `internal/doctor/headroom.go` | 3 | 4 | 3 | 1 | 0 | 0 |
| `internal/opctl/update.go` | 10 | 20 | 10 | 10 | 0 | 0 |
| `internal/power/readiness.go` | 1 | 2 | 1 | 1 | 0 | 0 |
| `internal/power/wings.go` | 5 | 10 | 5 | 5 | 0 | 0 |
| `internal/publish/sizing.go` | 2 | 4 | 2 | 2 | 0 | 0 |
| **total** | **24** | **45** | **24** | **21** | **0** | **0** |

**Denominator**, recomputed independently by re-implementing each tool's own
published rule over the same three inputs (the `--unified=0` diff, the profile,
the oracle document) and checked against what each tool actually printed:

| rule | executable | covered | pct |
|---|---|---|---|
| `covergate` (`Executed[line] \|\| Missing[line]` over expanded extents) | 684 | 639 | 93.4% |
| assay (statement lines only) | 418 | 394 | 94.3% |

Both reproduce their tool's own output exactly, which is what makes the
classification a measurement rather than an argument.

**The classification:**

* **extent-expansion — 266 lines, assay correct (A-217/B058).** Every one of
  the 266 lines in `covergate`'s larger denominator begins no statement,
  checked line by line against the oracle. The 21 uncovered-set disagreements
  are a subset of them: closing braces and `func` signatures, the pairs visible
  in `covergate`'s own output (`[70 71 76 77 82 83 …]`).
* **file-absence — EMPTY at this range.** No changed non-test `.go` file under
  `internal/` lacks coverage records, `covergate` tagged nothing
  `[file absent from the cover profile]`, and assay's `files_missing_coverage`
  is `[]`. So `covergate`'s `NoCode`/`Unmeasured` split never fires here and
  the two tools cannot disagree on that axis. **Project memory's note that
  "covergate silently skipped P14's package" does not reproduce at this commit
  pair** — `-coverpkg=./...` is doing exactly the job its comment claims.
* **no third category.** Zero lines assay calls uncovered that `covergate`
  calls covered, and zero `covergate`-only uncovered lines that begin a
  statement. After B061, the two tools' uncovered sets stand in a strict subset
  relation.

The neutral third party (`carve-assets/P27/fixture/manifest/calc-statements.json`)
was not needed: there is no line on which the two tools disagree *about a
statement*, so nothing needs a third opinion. It remains the arbiter for the
frozen fixture pair, where it already is one
(`test_statement_attribution_go_witnesses.py`).

### The srdm-side consequence, stated plainly

**No — assay does not see P14 lines `covergate`'s floor never counted.** The
relation runs the other way: assay's executable set is a strict subset of
`covergate`'s, 418 of 684. What srdm would gain by moving is not more coverage
but a truer denominator. Today its 75% floor is measured against 684 lines of
which 266 are not code a test can execute, and when the gate fails it hands a
developer 45 lines to fix of which 21 are closing braces and signatures. Under
assay the same change is 24 real statements, and the headline number rises
(94.3% vs 93.4%) not because the bar moved but because the denominator stopped
counting punctuation.

One caveat srdm should hear with that: **`-coverpkg=./...` is load-bearing on
both sides**, and dropping it would shrink the profile, empty the file-absence
axis's protection, and — before B061 — would have hidden the defect this run
found.

## 42. What generation 6 did NOT do

**Nothing on the dispatched list.** All four items are done and both remaining
acceptance boxes tick, so F008 is `shipped` and M6 is `done`.

**No decision row was written.** B061 is a defect fix implementing a rule that
was already ruled and already stated in the code's own comment (executed-wins,
A-391); calling it a decision would inflate a bug into a design change.
Likewise no new reason code, no schema field, no protocol change — the fix is
eleven lines inside one function.

**`covergate` was not changed**, and B058 stays open as a finding about srdm's
tool rather than a patch this wave landed. That is srdm's call to make with
this evidence, not assay's to make for it.

**The determinism gap in srdm's own profile was recorded, not chased.** Two
runs of srdm's own argv in the same checkout differ in `cmd/srdm` block counts.
It is outside `internal/`, outside both tools' scope, and belongs to srdm; it
is noted here only because a reviewer re-running the harness will see it and
should know it was seen.

## 43. Gate — run 10, PASS on commit `3355d238`

Commanded from `/workspaces/vbpub`, output to a log OUTSIDE the worktree with
the caller appending its own marker, verdict read in a SEPARATE step:

```sh
bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-c-go
```

```text
$ grep -E "ASSAY_REGISTERED_GATE_COMPLETE|GATE_EXIT=" gate-run10.log
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0

$ grep "ASSAY_GATE_PHASE" gate-run10.log
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

$ grep -nE "FAILED|DIRTY_TREE|Traceback" gate-run10.log
(no hits)

$ tail -6 gate-run10.log
  Created wheel for assay: filename=assay-4.0.1.dev39+g3355d238-py3-none-any.whl …
Successfully installed assay-4.0.1.dev39+g3355d238
ASSAY_GATE_PHASE=cmru-b006a-qualified
.......                                                                  [100%]
7 passed in 11.39s
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
GATE_EXIT=0
```

Eleven phases, and the installed wheel `assay-4.0.1.dev39+g3355d238` names the
judged commit itself — the check that the gate measured this tree and not a
stale build. Devcontainer full suite on the F008-A4 tree: **3905 passed, 18
skipped** (from 3902/18); B061's three tests landed after that count and are
covered by run 10.

**The wrapper-vs-job trap did not fire, and it was still read separately.** The
harness's completion notification reported "exit code 0" — which is the
WRAPPER's status, always 0 here, since the subshell's last statement is the
`echo` that appends the marker. It happened to agree with `GATE_EXIT=0`. Nine
instances across five generations, five agreeing; the discipline is cheap and
this sentence can only say which was true because the marker was read on its
own.

**The only commit after run 10 is docs-only** — BRIEF-7, this section and the
LOG's gate paragraph. No source, test, packaging, vocabulary, fixture or
decision-file change.
