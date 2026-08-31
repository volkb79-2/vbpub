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

## `fe9aaf7c` — feat(assay): keep Go block extents and correct them (A-239 items 1+3)

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

---

## `4408622b` — docs(assay): Wave C checkpoint 1 — LOG/REPORT/BRIEF, and file B053

LOG entries for the two commits above, the REPORT (every witness named with its
derived result and the naive rule's answer beside it), BRIEF-1, and B053 (the
`lit.go` laundering this wave does not fix and cannot at line granularity).

**Gate: PASS on this commit** — all 11 phases,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`. Transcript in REPORT §6.
Gate run 1 before it had REFUSED (`GATE_EXIT=1`, uncommitted changes) while
being reported to me as "exit code 0" — REPORT §7.

**Tests added/changed:** none.

---

## `428f69e2` — feat(assay): declare the Go helper as package data, and correct my own blocker claim

**Files**

| file | what |
|---|---|
| `pyproject.toml` | package-data gains `helpers/go/stmtpos/*.go` + `go.mod` |
| `tests/test_go_helper_is_packaged.py` | new — 6 tests |
| `nyxloom-trove/decisions.md` | A-394, A-395 (controller rulings, `vbpub@8fd9dd68`), A-396 (my correction) |
| `nyxloom-trove/4-backlog.md` | B054 |
| `reports/assay-WAVE-C-go-BRIEF-1.md` | packaging bullet retracted in place |
| `reports/assay-WAVE-C-go-REPORT.md` | gate transcript, the two exit-code incidents, the retraction |
| `reports/assay-WAVE-C-go-BRIEF-2.md` | new |

**What and why.** The controller answered both decision asks (A-394: register
Go at R1, but strictly last, after the guard chain; A-395: do not weaken
`test_cli_run.py`'s helpers assertion, add a parallel Go-lane one proven
against the real toolchain).

The packaging entry is added, but **not for the reason I gave**. I had claimed
its absence was a shipping blocker; it is not. Measured with one
`python -m build`: with the whole `package-data` stanza deleted the wheel still
carries the helper AND the schema, because `setuptools_scm` installs a git file
finder and `include_package_data` defaults to true under pyproject metadata.
The declaration is kept and rescoped as explicitness for the
git-metadata-absent build `fallback_version` anticipates; the false claim is
retracted at every site it reached (A-396). The new test asserts the OUTCOME —
the helper is in the wheel and resolves from inside the scratch venv — never
the mechanism, so it survives the correction.

Side finding filed as B054: `test_verdict_schema_is_packaged.py`'s docstring
states that same mechanism as a measurement, and a re-run refutes it, so its
named negative is currently unreachable. Filed, not patched — three options,
and choosing between them is a real call.

**Tests added:** `tests/test_go_helper_is_packaged.py` — source-tree presence,
no `require` directives, stdlib-only imports, the wheel's own namelist, venv
resolution, and the `go run .` one-directory layout. Its first draft matched
the word "requirements" inside the `go.mod`'s own comment, so the check now
parses directives instead of substring-matching; the comment says so, because a
test that reads comments as directives is testing the documentation.

**Gate: PASS on this commit** (run 3) — 11 phases,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, no failures. The
`wheel-installed` phase builds and installs a real wheel, so the new packaging
test ran against a genuine install, not the source tree.

---

## `<pending>` — docs(assay): Wave C checkpoint 2 — gate verdict for 428f69e2

Docs only: REPORT §6 run 3, BRIEF-2's gate line, this entry. No source, test or
packaging file changes, so the gate-green claim for `428f69e2` stands.

---

## `<pending>` — feat(assay): the statement-attribution chain, end to end (generation 2)

**Generation 2, fresh session, seeded from BRIEF-1 + BRIEF-2.** BRIEF-2 §5's
task list items 1–6; item 7 (registration, A-394) and item 8 (srdm covergate
qualification, F008-A5) are NOT done and are handed on in BRIEF-3.

**What landed, in the order BRIEF-2 §5 gave.**

1. **`adapters/base.py`** — `requires_statement_attribution: bool` as the sixth
   protocol attribute, plus `statement_blocks`, a NEW hook, never an overload
   of `statement_spans`. Signature recorded as **A-397**, with its four
   sub-rulings (new method; `repo_top` as a named parameter and the one narrow
   amendment to the path contract; `StatementBlockReport`/`HelperInvocation`
   defined here rather than importing `verdict.Helper`; the refusal reads the
   attribute directly, never `getattr(..., False)`).
2. **The A-392 refusal** — `evaluate._check_statement_attribution`, called at
   the head of BOTH `evaluate_coverage` and `evaluate_targets`.
   `ERROR`/`UNREADABLE_ARTIFACT`, the same pair `attribute_statements` uses,
   because what cannot be done is READING the artifact as statement truth. No
   new reason code: that vocabulary is closed on the wire and this wave cuts no
   schema.
3. **The two new members on every other adapter** — `python.py`,
   `javascript.py`, `sql.py` (SQL raises its own `_UNREACHABLE`, matching how
   it treats every other R1 method), plus the four `FakeAdapter` copies.
   `tests/test_adapters_go_registration.py` now asserts
   `requires_statement_attribution is True` and `external_tools == ("go",)`
   **together**, since either half alone is the broken state.
4. **The key join, exposed rather than duplicated** —
   `evaluate.resolve_coverage_keys`, built on a new private
   `_repo_path_by_raw_key` that `_normalized_profile_files` now inverts. One
   loop, one collision refusal, both directions. The runner borrows it instead
   of re-deriving `normalize_coverage_key` + `project_prefix`, so the file the
   oracle READS is provably the file the evaluator JUDGES (A-385/A-367).
5. **The runner seam** — `runner._attribute_statements_for_lane`, called
   between `check_empty_coverage` and the mode fork, after `repo_top` resolves.
   Before the fork on purpose: both modes judge the same profile. It branches
   on the adapter's own declaration and never on a language name.
   `on_helper_invoked` is an additive, default-`None` callback, the same
   frozen-signature channel `on_base_resolved`/`on_added_resolved` already use.
6. **`adapters/go_stmtpos.py`** (new) — the Python half of the oracle: locates
   the shipped helper, forces `GOPROXY=off GOWORK=off GOTOOLCHAIN=local
   GOFLAGS=-mod=mod`, runs `go run .` with cwd = the helper directory, pins the
   output schema, and refuses every partial shape. Inputs are validated BEFORE
   the toolchain is probed, so a stale artifact is reported as a stale artifact
   rather than as a missing toolchain.
7. **`GoAdapter`** — `requires_statement_attribution = True`,
   `external_tools = ("go",)` (B047 item 2; A-253 already owns the mechanism),
   and a real `statement_blocks`. The module docstring's "never shells out,
   never imports `subprocess`, `external_tools = ()`" paragraph was true and is
   now false; it is rewritten to scope A-087 rather than quietly contradict it.

**Tests added (27):** `test_evaluate_statement_attribution_guard.py` (4 — both
directions, including the control that would go red if the guard read the flag
alone), `test_adapters_go_stmtpos_invoker.py` (13 — the document reader and
every refusal, plus a two-sided schema pin that reads the shipped `.go`
source's own constant), `test_runner_statement_attribution_wiring.py` (8 — the
seam: five naive lines become two judged; repo-relative paths anchored at
`repo_top`; the prefix-stripping key round-trip; identity reported exactly
once; the A-391 mismatch as a payload-free ERROR claim; and two controls).

**Thirteen pre-existing Go tests went red, correctly, and were repaired rather
than weakened.** All thirteen judge committed, pre-generated coverprofiles with
no toolchain (A-042/A-087/A-107), which A-392 now refuses. Seven go through
`conftest.as_pre_oracle_attributed` (flag set, line sets untouched); six —
`test_canary_go_pipeline.py` — go through a named `_PreOracleGoAdapter`
subclass with the declaration downgraded. Both shortcuts are documented at the
site and filed together as **B055**, because the honest fix for the first is
F008-A4 (fixture regeneration) and the second needs a decision this generation
did not have the budget to make well.

**Docs, landing with the work rather than after it (AGENTS.md):** README's
"What assay is not (yet)" now states what landed AND that `judge.language =
"go"` is still refused, with the toolchain requirement flagged; DESIGN-GUIDE
§11 gains "Go statement positions come from the SOURCE, never from the
profile" (the impossibility proof, the three options, why a subprocess, why a
new hook, why extents not containment, why refusal, why a guard, and what is
NOT fixed); CONSUMERS.md gains a Go section with the two refusals an adopter
will actually meet, spelled as real output.

**The wrapper-vs-job exit-code trap fired a THIRD time.** The full-suite run
was reported to me as "exit code 0" while the log's own `PYTEST_EXIT=1` and
pytest printed `13 failed, 3808 passed`. Caught only because the marker was
appended and read in a separate step. Recorded in REPORT §7.

**Gate: PASS on `c85c703a`** (run 4) — 11 phases,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, read from the log in a
separate step from the wrapper. The wheel it installed was
`assay-4.0.1.dev20+gc85c703a`, so the artifact names the commit judged rather
than my say-so. `self-hosted-lane-passed` is the load-bearing phase here: it
drives assay's own R1 lane through the new runner seam on a `python` adapter
declaring `requires_statement_attribution = False`, which is the control that
the seam costs every other language nothing. Full transcript in REPORT §14.

Devcontainer `pytest tests/`: **3846 passed, 13 skipped**, `PYTEST_EXIT=0`
from the job's own marker.

---

# Generation 3 (fresh session, seeded from BRIEF-1 + BRIEF-2 + BRIEF-3 + the DA-3 ruling)

Inherited tip `4a326ddc`, gate-green at `c85c703a` with only a docs commit
after it — verified myself (`git log`, clean tree) rather than taken from
BRIEF-3's word. `tester-unified-go:local` confirmed already present
(`docker images`), so nothing was rebuilt.

**Item 1 / A-394 — `GoAdapter` registered at `{"R1"}`. DONE.** One
`RegistryEntry` in `cli._built_in_registry`, plus the fallout BRIEF-3
predicted and one piece it did not.

The predicted fallout: `cli.py`'s own registry docstring explains why each
language sits at which rigor level and asserted in so many words that Go "has
no producer path wired in at any rigor level yet (P22)" — now false. It is
REWRITTEN rather than deleted, because the load-bearing half of A-394 is the
SEQUENCING, and a later reader who saw only the finished entry could not
recover why registration had to come last. README's "still no" paragraph
(which generation 2 wrote deliberately expecting revision) and
CONSUMERS.md's Go section both now say R1 resolves, with the toolchain
requirement stated before anything else an adopter might plan around.

**The unpredicted piece, and it is the one worth reading (A-399).**
Registration silently VOIDED a regression test's subject.
`test_run_refuses_an_unregistered_language_with_a_resolvable_infrastructure_fact`
(B012/B013 round-3 finding N-6) used `language = "go"` purely as a cheap way
to reach `cli.py`'s adapter-refusal `refuse_lane` call — the exact site where
the infrastructure facts were once not forwarded. With `go` registered, that
lane resolves an adapter and takes `runner.py`'s `MISSING_EXTERNAL_TOOL`
preflight instead: a DIFFERENT call site. The test would have kept passing
while no longer executing the defect's own site, and nothing would have said
so. That is worse than a red test, and it is the A-387/A-395 "editing the
sentence that was telling the truth" shape one step subtler — no assertion
had to be weakened for it to happen. Repaired by changing the LANGUAGE to
`sql` (registered at R2 only, A-242, so it supplies exactly the
config-valid/registry-refused state `go` used to) and leaving every assertion
alone.

The other affected test genuinely INVERTS, and is renamed to say so:
`ERROR`/`BAD_LANE_CONFIG` → `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL`, exit 2
→ exit 3. The two assertions that did NOT change carry the weight:
`not marker.exists()` (the preflight precedes the lane's command, so a
machine without Go never runs a suite it cannot judge) and schema validity
(a NEW refusal path inherits P17/A-139's obligation to emit a complete
auditable artifact). A second test was added as the anti-vacuity control,
asking the registry directly — the inverted test alone would also pass if
`go` were still unregistered and something unrelated produced exit 3 — and it
pins R2/R3 as refused, so the frozenset cannot widen silently.

**Item 4 / B047 item 3 / A-398 — the `go-cover` producer vocabulary. DONE.**
A-354 did not close this question, it named its own reopening condition (a
build that can run a Go lane), and A-394 met it. `("go-test", "covdata")` is
open; the refusal machinery A-354 built is untouched and still tested against
`lcov`/`cobertura`, which is what proves opening one format did not open the
door for all of them.

The half worth arguing is that the key stays **OPTIONAL**. The test for
REQUIRED is not "more than one producer", it is "do the producers DISAGREE
about what the artifact MEANS". `coverage-istanbul-json` is required because
`vitest-v8` and `istanbul` disagree about whether a line ran (A-346).
`go-test` and `covdata` do not: `go tool covdata textfmt` CONVERTS counter
data into `cmd/cover`'s own text format — same header, same records, same
instrumenter — so assay's parser is producer-independent and no reading turns
on the declared name. Requiring a key that changes no answer is ceremony, and
ceremony shaped like a safety property is its own dishonesty. The names still
earn their place as PROVENANCE: `covdata` evidence comes from a
`go build -cover` binary doing real work and can cover code no unit test
executes.

**A load-bearing environment fact this generation measured** (it changes what
is buildable for items 2 and 5, and BRIEF-1's committed probe comment
understates it): this devcontainer's `/tmp` IS reachable by the Docker daemon
— `docker inspect` on this container's own mount table gives
`/tmp <= /home/vb/mdt--mounted-folders/tmp` and
`/workspaces/vbpub <= /home/vb/volkb79-2/vbpub`. BRIEF-1's probe script says
`/tmp` "is not visible to the Docker daemon at the same path", which is true
as written and was the right reason to tar-pipe — but the obstacle is a path
TRANSLATION, not an absence, and srdm's own `tools/gate.sh:48-61` already
derives exactly this mapping the same way for its bind mounts. That makes a
transparent `go` shim feasible (the pattern `tester-unified-go/Dockerfile`
itself blesses: "`tools/go` (a wrapper around this image) is how a cockpit
gets Go ergonomics without a cockpit Go"), which is what items 2 and 5 need
and what BRIEF-4 hands on in detail.

**Registration's fallout was wider than BRIEF-3 predicted, and one piece was
a real design correction (A-400).** The full suite found three more red tests
beyond the predicted set. One is the literal registry-capability declaration
(`test_the_built_in_registry_names_exactly_the_languages_this_build_reaches`)
going red exactly as designed — that is the test working. The other two are
the docs gate's mutation-operator checks, and they matter: that gate derived
its required operator set as *every registered language's* operators, with a
comment promising it would "expand BY ITSELF" the day a Go adapter was
registered. It did expand by itself, in the WRONG direction — A-394 registers
`go` at R1 only, so the gate began demanding documentation for three `go:*`
operators no lane in this build can reach, which is exactly the outcome
A-287's ruling exists to prevent. The mechanism written to honour a ruling
produced that ruling's stated failure, because it assumed a Go registration
would come with a mutation path. Corrected by scoping the derivation to
languages registered **at R2** (the level at which a mutation operator is
reachable at all). It changes no set today, which is the point: it restores
the intended behaviour and stays correct for a later package that gives Go a
real R2. What caught it was the companion test's non-empty vacuity guard,
written against a hypothetical and fired on a real event two waves later.

**The wrapper-vs-job exit-code trap fired a FOURTH time**, and this instance
is the most persuasive one yet: the harness's own structured completion
notification said "exit code 0" while the log's appended marker said
`PYTEST_EXIT=1` with `3 failed, 3845 passed`. Trusting it would have shipped
the A-400 defect with a passing suite cited as evidence it was fine. Recorded
in REPORT §21.

**Items 2 (F008-A5), 3 (F008-A4) and 6's remainder were NOT started**, and no
acceptance box is ticked — see REPORT §18 for the reason on each. §17 records
what was established about covergate from its own source (its expansion rule
is byte-for-byte the one assay just removed, so the two disagree by
construction and assay is the correct side), and §19 records the measured
environment fact that makes the remaining items buildable.

**Gate: run 5 RED, run 6 PASS on `91b05186`.** Run 5 was my own error — I
created `BRIEF-4.md` untracked while the gate was already running, and the
self-hosted lane refused `NO_MEASUREMENT/DIRTY_TREE`. Recorded rather than
quietly re-run, because the suite inside it was green (`3845 passed, 13
skipped`) and reporting that as a pass would have been effortless. It was not
a pass. Run 6, on a clean tree: 11 phases,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, with the installed wheel
`assay-4.0.1.dev23+g91b05186` naming the judged commit itself. Read from the
log in a separate step. Full transcripts in REPORT §22.

`self-hosted-lane-passed` is the load-bearing phase for this change and for a
new reason: it drives assay's own R1 lane through the freshly-changed
`_built_in_registry`, which every language resolution passes through, so a
bad rigor frozenset or a failed `GoAdapter` import would take assay's own
lane down rather than surfacing in a unit test. `topos-qualified` and
`cmru-b006a-qualified` exercise the same changed function from two real
downstream consumers.

**The trap's running total for this wave is now SIX** (REPORT §23), two of
them during this generation's closing sequence and both delivered as the
harness's own structured completion notification: the second full-suite run
("exit code 0" over `PYTEST_EXIT=1`), and **gate run 5 ("exit code 0" over
`GATE_EXIT=1` on a gate that refused its self-hosted lane)** — the incident
AGENTS.md's rule was written from, reproduced exactly, on the gate. Nothing
about run 5 looked risky; the suite inside it was green. That is the argument
for the rule being unconditional rather than reserved for suspicious commands.
