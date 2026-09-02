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
gone from the executable set. Recorded as A-393 / B055.

**Tests added:** `tests/test_statement_attribution_go_witnesses.py`, 14 tests,
all reading real artifacts (the frozen carver profiles + this wave's committed
oracle output). No Go toolchain, no subprocess. Includes the anti-vacuity
control (`test_the_naive_expansion_cannot_tell_the_collision_pair_apart`), the
manifest cross-check, three refusal paths and an order-independence check for
executed-wins.

---

## `4408622b` — docs(assay): Wave C checkpoint 1 — LOG/REPORT/BRIEF, and file B055

LOG entries for the two commits above, the REPORT (every witness named with its
derived result and the naive rule's answer beside it), BRIEF-1, and B055 (the
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
| `nyxloom-trove/4-backlog.md` | B056 |
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

Side finding filed as B056: `test_verdict_schema_is_packaged.py`'s docstring
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
site and filed together as **B057**, because the honest fix for the first is
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

---

# Generation 4 (fresh session, seeded from BRIEF-1..4 plus the controller's DA-4..DA-7 entry, `vbpub@237b9585`)

Inherited tip `4cff97bc`, a docs-only commit on gate-green `91b05186` —
verified myself (`git log --oneline -3`, `git status --short` clean) rather
than taken from BRIEF-4's word.

## `2f0cd223` — docs(assay): reword F008-A5, and record the in-image harness ruling (A-401, A-402)

Task 0. The controller's DA-4 and DA-5 landed as decision rows before any
work stood on them, and F008-A5's `text` in `2-product-definition.md` was
replaced with A-401's wording. Both rulings cite `vbpub@237b9585` rather than
a conversation.

Before recording A-402 I re-ran its measurement myself — `docker run --rm
--network=none tester-unified-go:local ...` — because A-334 applies to a
controller's measurement exactly as it applies to mine, and because the entry
being recorded is itself a correction of a conclusion reached by reading a
Dockerfile without running the image. It reproduced: `/usr/bin/python3`,
Python 3.13.5, `go version go1.25.14 linux/amd64`, uid 1003 / user `gate`,
Debian trixie.

## `8d7f8740` — fix(assay): reach the Go oracle from the shipped zipapp (A-403)

**Building the harness found a live shipping defect on the first run.** The
zipapp built at `2f0cd223` ran fine inside `tester-unified-go:local` (`assay
4.0.1.dev25+g2f0cd223`, as uid 1003, over a read-only bind mount, with
`--network=none`), and then every Go lane refused with "assay's Go
statement-position oracle is missing from the installation" — for an oracle
the archive contained the whole time.

`go run .` takes a working DIRECTORY. Inside a `.pyz` the helper's files are
zip members, so `HELPER_DIR` resolved to
`…/assay-…pyz/assay/helpers/go/stmtpos` and `is_file()` answered False. The
module's own comment had reasoned to that design from two TRUE premises ("go
run needs a real directory"; "a wheel install materialises package data as
real files") and one wrong conclusion, because a zipapp is not a wheel
install — and with no pip and no ensurepip in the Go image (A-402), the
zipapp is the ONLY install path there, so the shape that could not run the
oracle is the shape every Go consumer runs.

Fixed by reading both files through `importlib.resources` — the pattern
`verdict.schema_text` already used one module over — and staging them into a
temporary directory. Deliberately ONE path rather than a zipapp branch: a
branch taken only inside a zipapp would never be exercised by the registered
gate, which installs a wheel. The regression guard runs against the REAL
built artifact the distribution suite already produces, needs no Go
toolchain, and carries a vacuity guard asserting the zipapp's `HELPER_DIR`
really is not a real file.

Note for whoever reads the test in isolation: it can only pass on a COMMITTED
tree, because `build_release.py` builds from HEAD's OID by design. It went
red once before this commit for exactly that reason, and that is the builder
working, not the test being flaky.

## `854d20c3` — docs(assay): file B059 and decision ask DA-8

The blocker, recorded before anything was built on top of it. A Go cover
profile is keyed by IMPORT PATH; `git diff` is keyed by repo-relative path;
`GoAdapter.module_path` bridges them and nothing can set it through the CLI.
Measured in-image with the shipped zipapp, including the control run with
`module_path` supplied, which is what proves the defect is only the missing
declaration.

Filed rather than fixed because the fix is a real three-way fork: a declared
lane key (A-328's precedent — an optional `judge` key with a default, no lane
schema bump), derivation from the repository's own `go.mod` (§4.2a's
DERIVE-then-READ preference and A-007's own reasoning both point here, and the
seam does not exist), or a per-lane adapter registry (largest blast radius —
generation 3 already recorded that `_built_in_registry` is read by more things
than a grep for the adapter name shows). The project's stated preference and
its most recent precedent select DIFFERENTLY, which is what makes it a fork
rather than a gap in my reading.

## `77d9d6b9` — feat(assay): helpers[] gets its first producer (B047 item 5)

Item 6 completed, and it was larger than its filing. `Verdict.helpers` had
been validated and never populated since P33. Three parts were decisions, each
with a rejected alternative, and REPORT §28 carries them: the ROLE is the
runner's to name (an adapter that could name its own could claim
`mutation-sites` from a coverage hook); a helper with no correspondingly-judged
claim is REFUSED rather than filtered (a filter would swallow a wiring defect
too); and the correspondence rule gets ONE definition,
`verdict.supported_helper_roles`, read by the schema's own validation and by
the producer.

Landed whole, per BRIEF-4's own caution: `_check_helpers` requires a
correspondingly-judged claim per role, so a half-wired `helpers[]` is a
schema-valid document that lies.

`test_cli_run.py:406`'s `"helpers" not in document` is untouched (A-395) and
is now doing real work as the control: every non-Go lane still omits the key.

Also closes B057's third acceptance box — cheap, unticked, and worth the four
lines: the adapter the REGISTRY hands a lane is asserted to be the
undowngraded one, with `type(...) is GoAdapter` rather than `isinstance`,
deliberately, because `_PreOracleGoAdapter` is a subclass carrying the flipped
declaration and would satisfy `isinstance` while removing A-392's guard from
every Go lane.

Docs landed with the work: README (the zipapp install path and the B059 gap),
CONSUMERS.md (the real `helpers[]` shape, how to install into a Go gate image,
and the module-path workaround in code), DESIGN-GUIDE §11 (why the oracle is
staged, and why the role belongs to the call site), CHANGES.md.

## `9714361c` — test(assay): the Go qualification driver resolves judge provenance

Found by running it, not by reading it: the driver reproduced `cmd_run`'s
sequence except for its FIRST step, so the verdict carried no
`judge_provenance` and the assertion that a consumer artifact names its own
archive had nothing to read. Small, and worth its own commit because it is
what makes `artifact: "zipapp"` a measured line in REPORT §29 rather than a
claim.

**Gate: run 7 PASS on `9714361c`** — 11 phases,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, wheel
`assay-4.0.1.dev29+g9714361c` naming the judged commit, and a separate grep
for `FAILED`/`ERROR`/`DIRTY` returning nothing. Devcontainer full suite on the
same tree: `3860 passed, 16 skipped`, `PYTEST_EXIT=0`. Both verdicts read from
the logs in their own step. Full transcript in REPORT §30.

The Go qualification, run separately because neither gate image can host it:
`3 passed`, `PYTEST_EXIT=0`, against the real `go1.25.14` toolchain inside
`tester-unified-go:local`. REPORT §29 has the verdict document.

## `21708344` — docs(tester-unified-go): record the inherited Python interpreter (A-402)

DA-5's other half, closed rather than handed on. The image already carries
`/usr/bin/python3` 3.13.5 from `golang:1.25`'s trixie base and nothing in the
Dockerfile installs or mentions it — so reading the file is how you conclude
there is none, which is precisely the conclusion the controller reached before
running the image. Comment-only, and verified with a real `docker build` of
the edited file (exit 0) rather than assumed to be inert.

No `apt-get` layer, deliberately: installing a package already present is
ceremony that reads as a safety property, and it would make the image's Python
a DIFFERENT interpreter from the one a consumer inherits. The check that keeps
the comment honest lives where it can fail — the qualification module asserts
`python3 --version >= 3.11` inside the image, so a base-image change that
drops the judge's interpreter goes red instead of turning every Go
qualification into a skip that reads like "not enabled".

---

# Generation 5

## `4b5e7707` — feat(assay): derive a Go lane's module path from its own go.mod (B059, A-404)

DA-8 implemented, ruled by the controller at `vbpub@3a95459e`. The whole
change is one sentence with a lot of consequences: **the module path is the
project's fact, so assay reads it.**

**The seam.** `LanguageAdapter.for_project(*, repo_top, project_root)` —
keyword-only, returning a new adapter, called once from `evaluate_r1` the
moment `repo_top` resolves and before anything reads the profile. Every
adapter but Go returns `self`. The call site **rebinds the local name**,
which is the part I would defend hardest: the entire cost of B059 was two
spellings of one path drifting apart, and handing the bound adapter to the
statement oracle while an unbound one stayed in scope for the evaluator would
have rebuilt that drift inside its own fix.

**The parser.** `adapters/go_modfile.py` reads the `module` directive and
nothing else. I did not write it from the Modules Reference alone — the
lexical rules came out of `go1.25.14`'s own vendored
`golang.org/x/mod/modfile/{read.go,rule.go}`, read inside
`tester-unified-go:local`, and one of them is a rule I would have got wrong
from memory: **a backquoted module path is not valid input to `cmd/go`.** The
lexer scans the token happily; `rule.go`'s `parseString` then rejects any
non-`"`-prefixed token containing a quote character. Reading the real thing
cost one `docker run`.

**Two refusals, both from the closed vocabulary.** No `go.mod` at or above
the project root → `ERROR`/`BAD_LANE_CONFIG` (the same shape as
`evaluate.py`'s existing runtime "project root is not contained by its own
repository"). A profile key outside the derived module →
`ERROR`/`UNREADABLE_ARTIFACT` naming the key, the module path and the
`go.mod`. The second REPLACES B059's misattributed staleness message; that
message survives for its real subject, a key that IS under the module and
still names no file.

**Three things the ruling left to me, decided and recorded in A-404 rather
than left for a reviewer to find.** (1) The (c) refusal fires only when the
path was DERIVED — a library caller who supplies `module_path` by hand has no
`go.mod` for assay to contradict them with, and this is what lets the three
pre-existing boundary-safety tests keep both their subject and their
assertions. (2) A hand-supplied `module_path` that disagrees with the derived
one is refused rather than given a precedence (A-328's own rule). (3)
`SqlAdapter` keeps its module-wide `_UNREACHABLE` raise instead of the
ruling's `return self` default: the call site is exactly where
`normalize_coverage_key` is reached from and a SQL lane is R0,R2-only, so
`return self` there would be a line no SQL lane could execute.

**A stale-prose finding, fixed in the same commit because it is at the
boundary a consumer reads.** The CLI's module docstring and `run --help`
still said `judge.language = "go"` was refused at any level, and README's
summary still said Go had "no real adapter yet" — all three left behind by
A-394's registration two generations ago. `_built_in_registry`'s own
docstring had been updated; the user-visible text had not.

**Suite:** `3902 passed, 18 skipped`, `PYTEST_EXIT=0` (from 3860 passed / 16
skipped — 42 new tests: 24 for the directive parser, 11 for the adapter, 5
for the core seam, 2 for the in-image refusals, which are the two new skips
in a devcontainer with no Go). Not gate-green on its own (A-335); the
registered gate is below.

**Go qualification, run separately because neither gate image can host it:**
`5 passed`, `PYTEST_EXIT=0`, 28.6s, against the real `go1.25.14` inside
`tester-unified-go:local` — and now through `python3 <pyz> run …` rather than
a library driver. Every inherited assertion survived the move unchanged.
REPORT §35 has the verdict document and §36 the two refusals.

## `e7eb5241` — chore(assay): renumber Wave C backlog ids B053-B058 -> B055-B060

Main's `a050a467` (2026-09-02 00:27, from dstdns's first JavaScript lane
adoption on assay 4.0.0) filed a different **B053** ("an ERROR-outcome
verdict's detailed message is constructed but never surfaced anywhere a
consumer can read it") and **B054** ("a never-executed file matching
`coverage.include` can make `@vitest/coverage-istanbul` emit a
self-contradictory `branchMap`"). Main's ids win — the estate's precedent is
the ciu CIU-55 shift — so this branch's whole run shifts up by two:

| was | now |
|---|---|
| B053 (Go line-sharing laundering) | **B055** |
| B054 (`test_verdict_schema_is_packaged` stale docstring) | **B056** |
| B055 (the downgraded Go canary/union adapter) | **B057** |
| B056 (covergate's extent over-approximation) | **B058** |
| B057 (the CLI Go lane cannot resolve its own keys) | **B059** |
| B058 (`build_release.py` leaves `zipapp-staging/`) | **B060** |

Applied in DESCENDING order so no intermediate step collided, word-boundary
anchored, across **108 references in 25 tracked files** — the backlog,
`decisions.md` rows A-393/A-396/A-401/A-404, this LOG, the REPORT,
BRIEF-1..5, `2-product-definition.md`, `DESIGN-GUIDE.md`, `CHANGES.md`,
`carve-assets/P27-recarve/PROVENANCE.md`, three source comments and nine test
modules. **Historical briefs and logs were rewritten too**, deliberately: an
id that silently resolves to a different entry is worse than an edited
record. The map and its reason are also recorded at the top of the new B055
entry. Next free backlog id: **B061**; `decisions.md` is unaffected.

This is a mechanical rewrite and it was scripted rather than hand-edited,
which is the one case where that is the safer choice: 108 partial renames
would be worse than none. The end state is verified by `git grep -n -E
'\bB05[34]\b'` hitting only this entry and that note, and the diff was read
before committing.

## `dd1e2c46` — docs(assay): fold in main's B053, tick F008-A3, and BRIEF-6

Three things the renumbering surfaced or unblocked.

**Main's B053 changes what refusal (c) can be proven to do, and where.** Its
finding — an ERROR-outcome verdict's detailed message is constructed and then
surfaced nowhere a consumer can read it — is exactly what generation 4's own
B059 transcript showed without naming it: the CLI printed
`unit: ERROR/UNREADABLE_ARTIFACT (exit 2)` and the message had to be
recovered by probing the library. So the in-image test's name was overclaiming
and is corrected: `test_a_profile_from_a_different_module_refuses_and_names_both`
→ `..._refuses_through_the_cli`, because only the reason code is observable
there. The MESSAGE half is asserted where it is visible, at the library
boundary, by `test_a_key_outside_the_derived_module_refuses_and_names_all_three_facts`
— which also asserts the absence of the word "revision". A test whose name
claims more than its assertions check is the defect this project keeps
finding in other people's suites. **B053 is not fixed here**; the controller
paired it with B049 for a post-Wave-C patch wave.

**F008-A3 is ticked `proven`**, with four cited tests, two of which need no
toolchain (A-217's frozen collision witnesses). Its old text still said
"BLOCKED on A-217's source-side statement-position oracle … designed but not
carved", which was false in three ways. I had first decided to defer the tick
so A3/A4/A5 would "land together against one gate", then checked the premise
instead of trusting it — `grep -rl 2-product-definition tests/ tools/ gate/`
returns nothing, so no gate judges that file and the argument was about a
constraint that does not exist. The opt-in note went into the existing `text`
field rather than a new `evidence_note:` key: sixty rows use exactly
`text`/`status`/`evidence`, and a schema key with one user in a file nothing
parses is a convention invented for one sentence.

**BRIEF-6** is the generation-6 handoff. It was written into a scratchpad
OUTSIDE the worktree while gate run 8 was executing and copied in afterwards
— generation 3 lost a run to an untracked brief file, and the self-hosted
lane is right to refuse `DIRTY_TREE`.

**Gate run 9: PASS on `dd1e2c46`** — `ASSAY_REGISTERED_GATE_COMPLETE=1`,
`GATE_EXIT=0`, twelve phase markers through `independent-self-hosting-passed`,
verdict read in a separate step and a second, independent grep for
`FAILED|DIRTY_TREE|Traceback|ERROR` returning nothing. Transcript in REPORT
§39. Run 9 was not optional: the renumbering rewrote three source comments and
nine test modules, and "mechanical" is a claim about a regex, not a property
of one.

**One thing the renumbering could not rewrite: commit SUBJECTS.** Three on
this branch still carry pre-shift ids — `docs(assay): Wave C checkpoint 1 …
file B053`, `docs(assay): file B057 and decision ask DA-8 …`, and
`docs(assay): Wave C generation 5 -- A-404's record, B057 closed, B058 filed`.
History is immutable and rewriting it to tidy a reference would be a far worse
trade than an explained mismatch. Read them against the map in the
`e7eb5241` entry above: those three mean B055, B059, and "B059 closed, B060
filed" respectively.

---

## Generation 6 (2026-09-02) — F008-A4, F008-A5, B061; F008 shipped

Fresh Opus session seeded with BRIEF-1..6 plus the controller's 2026-09-02
entry at `vbpub@53eba55b` (DA-9). Inherited tip `86b4efae`, tree clean,
verified before anything was touched.

### `394c6cc2` — `fix(assay): F008-A4 — the Go coverage fixtures are real toolchain output, and their expectations are the oracle's`

Both halves in one change, which is A-234's own warning honoured rather than
quoted. One run of the new committed
`nyxloom-trove/carve-assets/P27-recarve/regenerate-fixtures.sh` inside
`tester-unified-go:local` (go1.25.14, `--network=none`,
`--cgroup-parent=dev-background.slice`, tar pipe) produced `hello.out`, both
canary profiles and `fixture-oracle.json` from the FINAL committed source
bytes — settled before the run, because a block extent is a line/column pair
and the profiles are position-bound.

The tests join the two documents with the production `attribute_statements`,
so an edit that moves a function body without a re-run refuses
`UNREADABLE_ARTIFACT` instead of attributing stale lines. Union-fidelity's
sets go `{29,30}`/`{36,37}` → `{33}`/`{39}`; a named control asserts the naive
expansion of the SAME real bytes is `{32,33,34}`/`{38,39,40}` — the concrete
form of "bytes alone would have been worse", since the old hand-authored
blocks ended at the return statement's own column and so expanded correctly by
accident.

B057 closes on all three boxes. `_PreOracleGoAdapter` is DELETED rather than
documented: with genuinely statement-attributed fixtures the shipped adapter
judges the canary, so cause-sensitivity is now proven at statement
granularity. `as_pre_oracle_attributed` → `as_statement_attributed`, which
refuses a multi-line block extent instead of trusting the caller; the oracle
reader moves to one `conftest.load_go_statement_oracle`. Stale A-234 banners
in `adapters/go.py` and `coverage_parsers/go_cover.py` record the discharge.
Devcontainer suite: **3905 passed, 18 skipped**.

### `875382d2` — `fix(assay): B061 — the statement join kept only the LAST record for a repeated block`

**Found by F008-A5's qualification, on its first run.** `go test
-coverpkg=./...` gives one record per test binary per block; srdm's profile
carries twenty per block, nineteen zeros and a one.
`attribute_statements`' `{block.extent: block}` kept the last, so a covered
block whose non-zero record was not final became `missing` — 255 lines
reported uncovered where the same profile's own line-level fold says 45.

The finding is a direct product of DA-6's "classify before naming a side"
rule. The denominator fit the extent-expansion prediction perfectly (418 <
684); the covered RATIO did not (39.0% vs 93.4%), and extent-expansion cannot
produce that. Reading the denominator alone would have shipped the defect with
"assay is stricter, by design" written next to it.

Fold is executed-wins before any count is read — the parser's own rule one
layer down. Three record orders asserted (a fix handling only "the non-zero
record comes first" passes one), an all-zero extent still missing, and the
invariant stated as a property: a correction may never downgrade a line the
parser called executed. Invisible until now because every frozen P27 witness
and every F008-A4 fixture has exactly one record per block.

### `<this commit>` — `docs(assay): F008-A5's srdm qualification, F008 shipped, M6 done`

The run itself, per DA-6 as corrected by DA-9. Synthetic two-commit repository
built inside the container by `git archive 10b174a5|83c2ff79
shared-ramdisk-depot-manager` from the bind-mounted host repo (read-only);
lane file at the module root, `source_roots = ["internal"]`, no `cwd`, srdm's
own `gate.sh:105` argv; nothing declared about the module path, nothing
committed under `shared-ramdisk-depot-manager/` in vbpub.

Result on `875382d2`'s zipapp: **assay PASS, 12 files, 418 executable, 394
covered, 94.3%**, `helpers[{statement-positions, "go version go1.25.14"}]`,
`judge_provenance.artifact = "zipapp"`. `covergate` on the byte-identical
profile: **PASS, 639/684, 93.4%**. A control lane judging `covergate`'s own
profile file returned assay's identical numbers, so every difference is a
judging rule and none is a measurement.

Classified: **266 lines of extent-expansion** (every line in `covergate`'s
larger denominator begins no statement, checked against the oracle), **zero**
on the file-absence axis (no changed file lacks records; project memory's
"covergate silently skipped P14's package" does not reproduce at this pair),
**zero** in any third category. Both tools' rules were re-implemented from
their own published descriptions and reproduce their own printed numbers,
which is what makes this a measurement. REPORT §41.

F008-A4 and F008-A5 both tick, so **F008 is `shipped`** and **M6 is `done`**.
B059's last box and all three of B057's close. B061 filed. CONSUMERS.md gains
point 7 — a worked Go lane that is the one that really ran, with the srdm-side
consequence stated: not more lines seen, a truer denominator.

**Gate run 10: PASS on `3355d238`** — `ASSAY_REGISTERED_GATE_COMPLETE=1`,
`GATE_EXIT=0`, eleven phase markers through `self-hosted-lane-passed` /
`topos-qualified` / `cmru-b006a-qualified` / `independent-self-hosting-passed`,
the installed wheel `assay-4.0.1.dev39+g3355d238` naming the judged commit, and
a SEPARATE, independent grep for `FAILED|DIRTY_TREE|Traceback` returning
nothing. Transcript in REPORT §43. Both source commits (`394c6cc2`,
`875382d2`) are under this run; the only commit after it is the docs-only one
carrying BRIEF-7, REPORT §43 and this paragraph.

**BRIEF-7** is the generation-6 record. It is a completion record rather than a
hand-off: every item on the dispatched list is done, F008 is `shipped`, M6 is
`done`, and there are no open decision asks. It was written into a scratchpad
OUTSIDE the worktree while run 10 was executing and copied in afterwards, for
the reason generation 3 lost a run.

**Process note, volunteered for the reviewer.** Every change to a tracked file
this generation was made with the Edit tool, per file, per the operator's
standing directive — no bulk rewrite scripts, which generation 5 recorded
having used three times. Two mechanical helpers were used and neither touched a
tracked file: a Python one-shot that filled the gate verdict into BRIEF-7 while
it was still an untracked scratchpad draft, and a Python script that computed
the classified table in §41 from the run's JSON artifacts. Both are analysis,
not editing.

---

## Generation 7 — the fix round after adversarial review round 1

Dispatched fresh from tip `d938ab8c`, seeded with BRIEF-1..7, the reviewer's
round-1 report and the controller's 2026-09-02 entry (`vbpub@5b6f77cc`), which
rules DA-R1 and DA-R2 and dispositions all seven should-fixes.

### `210812f6` — `docs(assay): Wave C review round 1 -- reviewer's report, verbatim`

The review committed unchanged as
`nyxloom-trove/reports/assay-WAVE-C-go-REVIEW-round1.md`, so the
fix-verification round reads it from the tree rather than from a session-local
scratchpad. Not reflowed, not annotated, not answered inline: the answers go in
`decisions.md` (A-405, A-406), here, and in the REPORT.

### `<this commit>` — `fix(assay): BLOCKER 2 -- registry.py's two stale docstring facts`

Two paragraphs of `src/assay/registry.py`'s module docstring asserted things
that are false.

The blocker proper (`registry.py:37-49`): "every adapter a built-in registry
can advertise today (Python, Go, and P34's own SQL) declares
``external_tools = ()``, so no real entry here ever exercises one." That is
A-087's premise, and this wave falsified it — `GoAdapter.external_tools` is
`("go",)` (B047 item 2) and A-394 registered the adapter at R1, so every real
Go lane exercises `run_lane`'s preflight. `cli.py:328-347` had the identical
paragraph rewritten when A-394 landed; this copy was missed. Rewritten the same
way: A-087's REASONING is kept (it is why the machinery lives in `runner`, and
that is still true and is not what went stale), its factual premise is stated
as falsified, and the sentence is quoted so a reader can see what changed.

The adjacent staleness (`registry.py:29-32`), which predates Wave C: "the CLI
… builds its own registry naming Python at ``R1`` only, and no entry at all for
Go." False since A-242 (SQL at R2) and B036/B046 (JavaScript), and falser after
A-394. Fixed in the same edit rather than filed, per the reviewer. The
replacement does not restate the entry list: a duplicate of
`cli._built_in_registry` here is precisely what rotted three times, so the
paragraph now names that function as the one authority and makes only the claim
that does not depend on its contents.

Guard: `test_a_real_built_in_registry_entry_now_exercises_the_external_tool_preflight`
asserts over the WHOLE built-in registry — not over `GoAdapter` alone, which
`test_the_go_adapter_declares_the_expected_protocol_surface` already pins —
because the stale sentence was a claim about what a registry can advertise. It
goes red if Go is unregistered, if the declaration reverts to `()`, or if the
entry is dropped: each of those would make the deleted sentence true again and
the rewritten paragraph the stale one.

### `<this commit>` — `fix(assay): BLOCKER 1 / A-405 -- //line-remapped Go files`

DA-R2, shape (ii), per the controller's 2026-09-02 entry (`vbpub@5b6f77cc`).

**The witness first, because everything else follows from it.**
`carve-assets/P27-recarve/probe-linedup.sh` builds `cmd/cover/cover_test.go`'s
own `lineDupContents` — the corpus `cover.go:1055-1060` names for exactly this
case — inside `tester-unified-go:local`, `--network=none`, and runs both the
real `go test -covermode=count -coverprofile` and assay's oracle over it. Nine
records, eight of them carrying a zero column; the oracle reproduces all nine
extents and all nine `num_stmts`. Profile, oracle document, recipe and sha256
committed; PROVENANCE.md gains an A-405 section. That discharges REPORT §5
item 4's own open question: the `dedup` replication is PROVEN.

One difference from the reviewer's transcript, stated rather than reconciled
away: my extents are one line lower (`6.21,7.25` where the review shows
`5.21,6.25`) because `lineDupContents` is a backquoted constant beginning with
a newline and `cover_test.go` writes it whole. Keeping that leading blank line
is what makes this the same file Go's own test builds.

**The three invariant sites** now say what is true — lines `>= 1`, columns
`>= 0`, a zero column meaning a `//line`-remapped position — each citing
`go/token`'s behaviour and `cover.go:1055-1060` verbatim. A NEGATIVE column
stays refused, with its own message, because nothing in `go/token` emits one.
`go_stmtpos._read_block` needed no code change (it delegates to
`StatementBlock`) but did need its comment corrected: under the old bound that
refusal would have fired on the ORACLE's own correct output and blamed the
helper for it.

**The flag is `FileCoverage.line_directive_remapped`, DERIVED from `blocks`,**
not stored: a stored flag is a second fact about the same four numbers, and
every layer that rebuilds a `FileCoverage` would have to remember to carry it.
`blocks` is already carried through every rebuild. Per FILE, conservatively —
`linedup.out` mixes a physical extent with seven virtual ones in one file and
nothing in the artifact says where the directive's scope began.

**Two consumers, and they are asymmetric on purpose.**
`attribute_statements` skips a flagged file and EMPTIES its line sets rather
than sending it to an oracle that derives physical positions (the extent
mismatch would refuse the whole artifact — the blast radius BLOCKER 1 (b)
called wrong). `evaluate_coverage`/`evaluate_targets` then refuse
`ERROR`/`BAD_LANE_CONFIG` if such a file is in the judged set, and ignore it
entirely if it is not. Emptying is safe ONLY because that second check exists;
without it this would be the silent 0/0 shape (i) was rejected for.

`BAD_LANE_CONFIG` and not `UNREADABLE_ARTIFACT`: the artifact is exactly what
`go test` wrote, and the fault is that the lane asked assay to judge a
generated file. `go_modfile._refuse`'s own docstring is the precedent, and the
reviewer named it. No new reason code, no wire field, no schema change.

Ten tests in `tests/test_go_line_directive_witness.py` (all three outcomes,
the parser on the real bytes, the oracle's own zero columns, the negative-column
control, the `dedup` ladder) plus two at the parser's own boundary. Docs:
CONSUMERS point 5 (new, and 6-8 renumbered, README's cross-reference with
them), README's Go section, DESIGN-GUIDE §"Go statement positions".

### `<this commit>` — `fix(assay): DA-R1 / A-406 -- no vacuous statement attribution`

Two guards where there was a hole.

At CONFIG LOAD, a language listed in the new
`vocabulary.STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE` may only declare a
format in its set. The fact lives in `vocabulary` rather than on the adapter
because `assay.config` must not import `assay.adapters` — `registry.py`'s own
O2 guarantee — and the check has to run at load to be worth having. The cost
is two statements of one fact, so `test_config_statement_attribution_format.py`
derives the requiring languages from `cli._built_in_registry` and asserts set
equality, with a vacuity guard.

At RUN TIME, `_attribute_statements_for_lane`'s vacuous branch is DELETED and
its "such a profile is already statement truth" retracted at the site. The
sentence was true of a case this function never sees (a line-based adapter,
which `evaluate_r1` never routes here) and false of the case it did see. The
replacement is a refusal, kept as a second independent guard for library
callers who hand-build a `Lane`.

`test_a_profile_with_no_block_bearing_files_is_attributed_vacuously` is
rewritten and renamed to assert the refusal, with two new siblings: the
line-based adapter that never reaches the seam, and the EMPTY profile
terminating at `check_empty_coverage` with no `helpers` entry and no PASS —
which is the case the deleted branch sounded like it was protecting and was
not.

One existing test moved fixture rather than assertions:
`test_a_go_r1_lane_now_resolves_and_is_refused_for_the_TOOLCHAIN_not_the_registry`
inherited `R1_LANE`'s `coverage-py-json`, which a Go lane may no longer
declare. Left alone it would still have gone green — refusing one layer
earlier, for the wrong reason, never reaching the preflight it exists to
exercise. That is A-399's decoy shape, so the fixture moved to `go-cover`.

### `<this commit>` — `fix(assay): should-fixes 1, 2, 4, 5, 6`

**5** (`go_cover`): the `CoverageBlock(...)` construction is wrapped and
re-raised through `_malformed`. `m/x.go:5.10,5.2 1 1` ends before it starts in
the COLUMN, which `_parse_block`'s `end_line < start_line` guard cannot see, so
the dataclass's `ValueError` escaped `parse` — past `_run_prepared_lane`'s and
`cli.main`'s `except AssayError` both — as an uncaught traceback: no verdict
document, and the coverage reservation never released. Tested at the parser
and through `run_lane`, where the assertions are the SHAPE (a complete verdict,
both claims, payload-free) rather than only the reason code.

**4 + 6** (`statement_attribution`): the B061 fold now cites
`/usr/local/go/src/cmd/vendor/golang.org/x/tools/cover/profile.go`'s
`ParseProfilesFromReader` (`:54`, merge loop `:91`, `Count |= b.Count` at
`:104` for `set`, `+=` at `:106` otherwise) — read inside the image, line
numbers pinned — AND transcribes that loop's other rule, the
`inconsistent NumStmt` refusal at `:100-102`. The check sits INSIDE the fold
so it sees every record rather than the survivor; a check placed after would
be invisible whenever the honest record won, which is the common shape. The
reviewer's probe (`[count=1 numStmts=1]` + `[count=0 numStmts=7]`) is the
test, with a three-order control.

**2** (`go_modfile`): three divergences from the real parser, each message
measured in-image with `go list -m` on exactly that `go.mod`. A trailing slash
is now REFUSED (`malformed module path "example.invalid/x/": trailing slash`)
rather than `rstrip`ped — and `module /` with it, which previously ran the
emptiness guard BEFORE the strip, escaped as an empty module path, and
surfaced from `normalize_coverage_key` as `ERROR`/`UNREADABLE_ARTIFACT`:
blaming the artifact for a lane fault. An unquoted argument containing `"` or
a backquote is refused (`invalid quoted string: unquoted string cannot contain
quote`), the rule the module docstring already claimed. An anti-vacuity
control asserts the ordinary path still parses.

**1** (`test_cli_run.py`, `cli.py`): the R3 decoy is re-pointed at `rust` — a
language this build ships no adapter for at all, with an assertion that says
so — restoring a CLI-level test of the unknown-language branch, which had none
after A-394. The R2 one keeps `go` and is renamed
`test_run_refuses_go_at_r2_the_language_is_registered_r1_only`, saying in its
name that it is the registered-at-another-rigor control. The two SQL
docstrings that pointed at "Go's own total non-registration" are corrected.
`cli.py`'s claim that both refusals are asserted as controls is corrected the
same way, and while there: its module docstring's third copy of the registry
list still said "JavaScript at R1 only", stale since B046. Fixed, and the
correction says why every restatement of that list keeps rotting.

### `<this commit>` — `docs(assay): Wave C generation 7 -- gate run 11 PASS on 4c3e83f4`

**Gate run 11: PASS on `4c3e83f4`** — `ASSAY_REGISTERED_GATE_COMPLETE=1`
exactly once, `GATE_EXIT=0`, eleven phase markers through
`self-hosted-lane-passed` / `topos-qualified` / `cmru-b006a-qualified` /
`independent-self-hosting-passed`, the installed wheel
`assay-4.0.1.dev45+g4c3e83f4` and the self-hosted lane's own receipt both
naming the judged commit, and a SEPARATE, independent grep for
`FAILED|DIRTY_TREE|Traceback` returning nothing. Transcript in REPORT §49.

All four source commits of this generation are under that run. Devcontainer
full suite on the same tree: **3939 passed, 11 skipped** (from 3908/11). Go
qualification in `tester-unified-go:local`: **5 passed**.

**Process note, volunteered for the reviewer.** Every change to a tracked file
this generation was made with the Edit tool or by appending a whole new file,
per the operator's standing directive — no rewrite scripts over tracked files.
Two mechanical helpers were used and neither edited one: a Python one-shot that
pretty-printed the oracle probe's stdout into `linedup-oracle.json` (an
artifact this generation created, and the normalisation is disclosed in
PROVENANCE.md exactly as the two existing oracle documents' is), and `awk` to
slice the probe log's `=== PROFILE ===` section into `linedup.out`. Both are
artifact production from a container's output, not editing.

This section and REPORT §49 are the only content in this commit; it was written
after run 11 returned, not during it.

## Generation 8 — the fix round after adversarial review round 2

### `71a59967` — `docs(assay): Wave C review round 2 -- reviewer's report, verbatim`

The round-2 file committed unchanged before any repair, sha256
`22d9185219162caae456141f29f43a15e2c77890badc70a55438211b58b2184e`, byte-identical
to the reviewer's scratch original. Verdict NOT ACCEPT: one blocker (R2-1),
three should-fixes, one decision ask (DA-R3, already ruled by the controller).

### `<this commit>` — `fix(assay): A-407 -- drop an orphaned helper where the payload went`

**BLOCKER R2-1, recorded as A-407.** The controller's ruling is the
reviewer's §3.4 prescription applied exactly, and the entry citing
`vbpub@12e028c7` (the controller-log commit that carries it).

**What was wrong.** `_attribute_statements_for_lane` fires
`on_helper_invoked` the instant `adapter.statement_blocks` returns
(`runner.py:997`) — before the join, before evaluation. Every judge refusal
downstream of that is caught by `evaluate_r1` and rendered as a payload-free
R1 claim, which is the R1 seam working as designed; but the helper entry then
had no claim to support it, so `assemble_verdict`'s B047-item-5 guard raised
past `run_lane` and the consumer got a sentence about assay's `helpers[]`
array with **no verdict document written at all**.

**Reproduced in my own hands before touching the code**, with the reviewer's
own five-lane battery (`scratchpad/g8/ldlane/work`, its `setup.sh`/`laneA..E`
copied verbatim), against a zipapp built from `71a59967` — real `go test`,
real oracle, `tester-unified-go:local`, `--network=none`,
`--cgroup-parent=dev-background.slice`. Transcript: `scratchpad/g8/ldlane-BEFORE.log`,
reproduced in REPORT §50. A/D correct; B, C and E all masked, all three with
no verdict file.

**The change is 13 lines** in `_run_prepared_lane`, immediately after
`claims += (r1_claim,)`: when `r1_claim.coverage is None`, keep only the
helpers whose role is in `supported_helper_roles((r1_claim,))`. One definition
of the correspondence rule, no second table — the same function
`Verdict._check_helpers` validates with and the same move
`_replace_highest_higher_rigor_claim_with_git_failed` already makes.
`assemble_verdict`'s guard is untouched.

**Two comments that this makes false were corrected in the same edit**, since
"the one legitimate way to hold a helper whose claim has been voided" is now
two: `assemble_verdict`'s own `:1476-1483` block, and
`_replace_highest_higher_rigor_claim_with_git_failed`'s docstring ("this
function is the one place that can take that payload away"). Leaving them
would be the drift this wave has caught three times already.

**Tests, both proven RED without the fix, not merely written.**

* `test_runner_helpers_envelope.py` gains three, all driven through the real
  `run_lane` with one synthetic `requires_statement_attribution` adapter whose
  single parameter is the oracle's `end_line`. No toolchain, so **the
  registered gate runs them**. `end_line=9` is A-391's "not the same revision"
  case: the first test asserts the verdict carries the judge's own
  `UNREADABLE_ARTIFACT` and omits `helpers`, with R0 asserted PASS as the
  vacuity guard; the second repeats the CLI's own reserve-then-`write_verdict`
  two-step and reads the document back off the filesystem, because "no verdict
  artifact" is half of what A-407 fixes and a returned object cannot prove it.
  At `71a59967` both fail with the masked message verbatim
  (`scratchpad/g8/prewt2`).
* **The third is the control, and it is not decoration.** `end_line=7` agrees
  with the profile, the lane PASSes 2/2 statement-granular, and the helper
  entry must SURVIVE. Everything else here is about a helper being dropped, so
  an unconditional drop at the same site would satisfy all of it while
  deleting `helpers[]` from every passing Go verdict in the product — and the
  real-toolchain proof of the positive is opt-in, so without this the
  registered gate held only one side of the rule. Mutation **M-G2**
  (`helpers_seen[:] = []`, drop everything) fails exactly this test and
  nothing else.
* **M-G, recorded because it SURVIVES and that is correct.** Replacing the
  `if r1_claim.coverage is None:` guard with `if True:` changes no behaviour:
  `supported_helper_roles` of a judged R1 claim already keeps
  `statement-positions`, so the filter is idempotent on the passing path. The
  `if` is an intent statement — "this is the voided-payload case" — not a
  behavioural guard, and it is kept because the controller ruled the
  prescription applied exactly. Measured rather than assumed, so round 3 sees
  it was understood rather than missed.
* `tests/qualification/test_go_r1_real.py` gains the reviewer's scenarios B
  and E, in-image through the shipped zipapp. B is a real two-package Go
  module whose generated file carries Go's own `lineDupContents` `//line`
  directives, with a head commit that touches it — A-405's ruled refusal,
  reaching a consumer for the first time. E is a real profile for the head
  tree shifted down one line, with no `//line` file anywhere, which is why
  the defect is a wave defect and not a consequence of A-405. Both assert the
  masked sentence is ABSENT from stderr and that the verdict file exists.
  At `71a59967` both fail, naming that sentence
  (`scratchpad/g8/prewt2`, `2 failed, 5 passed`); with the fix,
  `7 passed`.

Devcontainer full suite, whole `tests/` tree, no `--ignore`, devcontainer venv
(Python 3.14.6), from the worktree's `assay/`: **3943 passed, 20 skipped** at
the end of this generation, from the reviewer's 3939/18. The +2 skips are the
two new qualification tests, which skip without `ASSAY_GO_QUALIFICATION=1`;
REPORT §52 explains why generation 7's "11 skipped" was not reproducible and
why neither number was wrong.

### `<this commit>` — `test(assay): SF-R2-1/SF-R2-2 -- kill the two surviving A-405 mutants`

Both should-fixes are mutation-proved, not merely written: the mutation was
applied to a scratch worktree's committed tree, the modules re-run, and the
kill observed.

**SF-R2-1** — `test_the_same_refusal_fires_in_whole_target_mode` judged
against `repo_top=Path("/repo")`, which does not exist, so
`_resolve_whole_target` refused FIRST ("judge.targets entry 'linedup.go' does
not exist as a regular file under the project root /repo") and all three of
its assertions were satisfied by that wrong refusal. The target is now
materialised on a real `tmp_path` project, and the assertions name the
`//line` message. The file's CONTENT is never read in whole-target mode —
`_resolve_whole_target` checks existence, kind, containment, globs and
test-path only — so the fixture writes a marker and says so, rather than
pretending to be Go's corpus (which is `linedup.out`, and really is).

Mutation **M-B2** (`evaluate.py:1071`'s
`if file_cov is not None and file_cov.line_directive_remapped:` → `if False:`),
applied in `scratchpad/g8/prewt`: **the repaired test kills it**, and the
message it dies on is precisely the misdirection A-405's ordering comment
predicts —

```text
E  assert <Outcome.NO_MEASUREMENT> is <Outcome.ERROR>
E   where ... = AssayError("judge.targets entry 'linedup.go' (coverage artifact
E   key 'linedup.go') has zero executable lines in the coverage artifact -- a
E   whole-target floor refuses rather than pass on a target that was never
E   measured").outcome
```

**SF-R2-2** — the controller preferred the test to the comment downgrade, so
both were done: `test_the_oracle_is_never_ASKED_about_a_line_directive_
remapped_file` asserts the recorded call arguments directly, over a profile
carrying an ordinary block-bearing file beside a zero-column one (with a
vacuity guard that the flagged file really IS in the profile handed in), and
the filter's comment now states what makes the two guards non-redundant: this
one stops an external toolchain being RUN, inside the lane's budget, over a
source whose answer is then discarded — and it names the test.

Mutation **M-F** (`to_attribute = list(block_bearing)`), same worktree, run
over all four A-405 modules: **1 failed, 60 passed**, and the one failure is
the new test. Before it, the reviewer measured `69 passed — SURVIVED`.

### `<this commit>` — `docs(assay): SF-R2-3 -- CONSUMERS' Go refusals say what a consumer actually sees`

Both blocks presented `status: ERROR / reason_code: … / <full message>` as
the operator's output. It is not, on any surface: a judge-phase `AssayError`
is folded into the R1 claim and a `Verdict` carries no free-text cause
(A-138/A-170). The reviewer's control, on the tip, was a Python lane with a
deliberately malformed artifact: `unit: ERROR/FORMAT_MISMATCH (exit 2)`, and
nothing else.

Both are rewritten to the shape the controller ruled: a `console` block
showing what `assay run` really prints, then the refusal assay *raises*, with
an explicit statement of who can read it — a caller of
`assay.evaluate.evaluate_coverage`/`evaluate_targets`/
`assay.statement_attribution.attribute_statements`, not an `assay run`
consumer — and a pointer to main's **B053**. The `//line` block adds the one
practical consequence: `BAD_LANE_CONFIG` is shared by several distinct causes
on the Go path, so a generated file inside `source_roots` is worth checking
first when a Go lane shows it.

**DA-R3 is ruled docs-now and this is all of it.** No wire field, no new
reason code, no `diagnostics` route: `runner.py:365`'s stream is the recorded
candidate mechanism for the post-Wave-C consumer-diagnostics patch wave
(B053 + B049), not for Wave C. Note that A-407 is what makes the "the
verdict's R1 claim carries that same pair" sentence TRUE end to end — before
it, both of these refusals produced no verdict at all.
