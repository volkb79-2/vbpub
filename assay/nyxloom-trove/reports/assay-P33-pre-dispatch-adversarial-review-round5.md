# P33 — pre-dispatch adversarial specification review, ROUND 5

**Reviewer:** fresh Opus xhigh child forked from the frozen `CR-opus-0`
package-neutral carve-review orientation base (A-216).
**Reviewed at:** main `7bf860427804293bae774048f702b5b54b570bb4` (verified with
`git rev-parse HEAD`).
**Handoff input_revision:** `51668c0d4e1e7e1902dd8fe9d7f3a291471a3f98`.
**Prompt:** AUTHORING § "Pre-dispatch adversarial handoff review", read from
`nyxloom/reference/AUTHORING.md` at blob `24460b57d015e44f0f1463e2a0393b09bdafaf40`
(identical to the orientation blob; the section is reproduced verbatim in the
appendix).

## Verdict

**NOT READY.** Four blocking defects, three of them found only by running the
locked assets rather than reading them. Two are locked-asset tests that **no
legal implementation of P33 can make green**, because the assertion contradicts
another locked asset and both files sit in `scope.forbid`. That is the exact
class round 4 found and declared fixed — the fix changed a symbol name and did
not re-run the suite to see the next error.

The round-4 lesson generalises further than the carver applied it: *running the
thing for nine seconds* is what found R5-B1 and R5-B2 here too. Every blocking
finding below carries the command that produced it.

---

## What I actually ran

| # | Command | Result |
|---|---|---|
| 1 | `git rev-parse HEAD` | `7bf86042…` ✓ matches |
| 2 | `sha256sum` over all 13 assets vs README table | **all 13 match** ✓ |
| 3 | `PYTHONPATH=src python3 -m pytest …/test_acceptance_v5.py -q -p no:randomly` | **43 tests, 33 failed / 10 passed** ✓ matches `CANONICAL_COUNTS` |
| 4 | `python3 …/sweep_v4_consumers.py` | **28 consumers** ✓ matches |
| 5 | `sweep_v4_consumers.py --json` | five established consumers all present ✓; `test_self_hosting.py` = `indirect-path-from-environ` ✓ |
| 6 | Direct call of `sweep.reads_frozen_tree()` on 10 idiom variants | **7 of 10 missed**, incl. the canonical head-of-chain join |
| 7 | Direct `assay.config.load_lane_file()` against the locked suite's own fixture | **fails on 7 missing required fields** |
| 8 | `pytest -k sweep_finds_every_known_consumer` | `assert 'indirect-path-from-argv' == 'indirect-path-from-caller'` |

---

## (1) Blocking ambiguities

### R5-B1 — The three config tests still cannot pass against a correct implementation. *(BLOCKING — round-4 defect class, recurring)*

Round 4 found `config.load_lane`/`config.load` did not exist. The repair pointed
them at the real `load_lane_file` and added
`test_locked_suite_only_references_symbols_that_exist` to pin the API surface.
That test passes. **The tests still cannot pass**, for a different reason one
layer down: `_load_lane` (locked suite line 567) writes an `assay.toml` that the
real loader rejects before it ever reaches operator validation.

Observed, verbatim:

```
E  assay.errors.LaneConfigError: <tmp>/assay.toml: missing required field
   'schema_version' (this assay understands schema_version = 1)
```

I did not stop at the first error. Driving `load_lane_file` directly until the
fixture loads shows it is missing **seven** required fields:

`schema_version`, `scope`, `enforcement`, `budget`, `allow_argv_append`, `env`,
`env_passthrough`

Consequences, in order of severity:

* **`test_config_accepts_a_matching_language_operator` is unsatisfiable.** It is
  the *control* — it calls `load_lane_file` and asserts a lane comes back. It
  will raise `LaneConfigError` against every implementation, correct or not.
  AUTHORING's NOT READY trigger verbatim.
* **The two negatives are false reds that will convert to false greens.** Both
  wrap `pytest.raises(C.LaneConfigError)`. Today the *right exception class* is
  raised for the *wrong reason* (missing `schema_version`), so only the message
  assertion fails. A-230c pinned the class precisely to stop "any exception with
  the right substrings" — but pinning the class is not enough when the fixture
  guarantees that class is raised regardless. If a future repair adds
  `schema_version` alone, `test_config_refuses_a_cross_language_operator` starts
  raising for `scope` instead, and the operator rule is still never exercised.
* **The implementer cannot fix it.** `nyxloom-trove/carve-assets/**` is in
  `scope.forbid`, and `escalate_if` names editing a locked asset explicitly.
  The only legal move is BLOCKED. A dispatch here buys a BLOCKED receipt.

**Carver repair — the exact fixture that loads clean** (verified: `load_lane_file`
returns a `LaneFile` with `operators=('compare-swap',)`):

```toml
schema_version = 1
[lanes.demo]
scope = "S1"
enforcement = "gate"
argv = ["python", "-m", "pytest"]
rigor = ["R0", "R2"]
budget = "5m"
allow_argv_append = false
env = {}
env_passthrough = ["PATH"]
[lanes.demo.judge]
language = "python"
source_roots = ["src"]
base = "0000000000000000000000000000000000000000"
mutation = { jobs = 1, max_mutants = 10, operators = ["compare-swap"] }
```

`_load_lane` must also `mkdir` a `src/` under `tmp_path` — `source_roots`
existence is validated at load (A-016).

**And add the missing guard**, because a fixture that fails early is exactly the
trap that survived two rounds: the two negatives must assert the refusal is
*about the operator*, not merely that `LaneConfigError` was raised. The
cross-language test already does (`"sql:drop-check" in str(exc.value)`); the
control needs its own positive assertion on the parsed lane
(`lane_file.lanes["demo"].judge.mutation.operators == ("python:compare-swap",)`),
not `assert lane_file is not None`.

### R5-B2 — `test_sweep_finds_every_known_consumer` asserts a category string the locked sweep does not emit. *(BLOCKING — two locked assets contradict each other)*

```
E  AssertionError: release_wheel receives its manifest path from the caller, not a literal
E  assert 'indirect-path-from-argv' == 'indirect-path-from-caller'
E    nyxloom-trove/carve-assets/P33/test_acceptance_v5.py:442
```

`sweep_v4_consumers.py:280` emits `"indirect-path-from-argv"`. The locked suite
demands `"indirect-path-from-caller"`. **Both files are locked carver-owned
assets in `scope.forbid`.** No implementation of P33 touches either. This test
can never pass.

The same dead name has propagated into the implementer-facing prose:

* handoff work item 8b, line 272: "reports `gate/distribution/release_wheel.py`
  as `indirect-path-from-caller`";
* `carve-assets/P33/README.md:87`: "Adds `indirect-path-from-caller` for
  `release_wheel.py`".

So an implementer told to re-run the sweep and check for
`indirect-path-from-caller` finds no such category anywhere in its output.

**Repair:** pick one spelling and apply it to the sweep, the locked suite,
handoff 8b, and both README tables. (`indirect-path-from-argv` is the more
accurate of the two — the mechanism is `sys.argv`/`argparse`, and there is a
sibling `indirect-path-from-environ`. Renaming the *test* is the smaller edit.)

### R5-B3 — `CANONICAL_COUNTS` is not the single source of truth; four documents still restate numbers, and three are wrong. *(BLOCKING — the headline round-4 fix, unfixed)*

Work item 9 states: *"no document restates a number independently any more."*
That is false as committed.

| location | states | `CANONICAL_COUNTS` says | verdict |
|---|---|---|---|
| handoff `:55` | "**92 files** migrate behind it" | `implementer_owned: 105` | **wrong** |
| `README.md:88` (Round-3 table) | "now **38 tests**, **30 failed / 8 passed**" | 43; `33 failed / 10 passed` | **wrong** |
| `README.md:87` (Round-3 table) | "consumers 11 → **40**", "schema **v3**" | `sweep_consumers: 28`; manifest `schema_version: 4` | **wrong** |
| `JIT-CARVE:127` | "**90 files** migrate" | 105 | **wrong** (a *third* figure) |
| `JIT-CARVE:228/:354` | "19 tests, 17 failed / 2"; "19 → 30 tests (**26 failed / 4 passed**)" | 43; 33/10 | historical narrative, but no round-4 successor line |
| handoff `:247` | "20 of P26's 24 tests" | `p26_retained: 20`, `p26_deselected: 4` | consistent, still a restatement |

The README is the sharpest instance: it now contains a corrected main table
("Counts: `migration-manifest.json` → `CANONICAL_COUNTS`") **and**, 70 lines
lower, an uncorrected "Round-3 additions" table asserting 38 tests / 30 failed /
8 passed / 40 consumers / schema v3 in the present tense. One file, two
contradictory answers. Round 4's finding was "four different count pairs across
four documents"; there are still four, and the count that governs implementer
routing (`92 files migrate` — the stated reason the package needs Opus) is off
by 13.

**Repair:** delete the numbers from the Round-3 README table (or mark the whole
section historical with a one-line banner), replace handoff `:55`'s "92 files"
and `JIT-CARVE:127`'s "90 files" with a pointer, and add one round-4 line to the
JIT-CARVE's growth narrative. A test asserting no live document contains a
literal count would close the class rather than the instance (A-141).

### R5-B4 — Work item 6b (`equivalence_artifact` refusal, A-230b) has no oracle, by the handoff's own argument. *(BLOCKING)*

Work item 6 argues the point correctly for `kill_signal_artifact`:

> `judge.mutation` is already a closed sub-table, so an unknown key is *already*
> rejected today — meaning "the declaration is refused" cannot distinguish a
> correct implementation of this work item from doing nothing at all.

Work item 6b then gives `equivalence_artifact` "the identical disposition" — and
supplies **no test at all**. `grep equivalence_artifact` over the locked suite
returns exactly one hit, at line 209, and it is an artifact-level pairing
manipulation, not a config-load refusal. There is no
`test_config_names_equivalence_artifact_as_reserved_for_p34`.

So by the handoff's own stated reasoning, **doing nothing satisfies work item
6b**. This is A-060's class ("an oracle that cannot fail is worse than no
oracle") in its absent form, introduced by the round-4 repair that was written to
remove exactly that class.

**Repair:** clone `test_config_names_kill_signal_artifact_as_reserved_for_p34`
for `equivalence_artifact` (on the repaired R5-B1 fixture), asserting
`LaneConfigError` and both `"equivalence_artifact"` and `"P34"` in the message.

---

## (2) False-PASS attacks

### R5-F1 — O5's gate-wiring oracle is blind to every suppression mechanism except `--deselect`

`test_gate_script_wiring_is_exactly_what_the_handoff_claims` extracts the
deselect set with `re.findall(r"--deselect[= ]\S*?::(\w+)", gate)` and asserts
`deselected == required`.

**The attack:** suppress a fifth test by any *other* mechanism. `-k "not
test_all_structural_and_aggregate_bounds_precede_every_git_call"`, `--ignore`,
`-m`, or an `addopts` entry are all invisible to that regex. `deselected` still
equals `required`, the explicit
`"test_all_structural_… not in deselected"` assertion still holds — and A-210's
aggregate-bounds-before-Git security oracle is silently gone. A-229 records that
oracle being dropped once already by a transcription slip; the test written to
prevent a recurrence does not cover the cheapest way to do it again.

**Secondary attacks on the same oracle:**

* It is **substring presence over an unparsed shell script**. Every one of
  `"carve-assets/P33/test_acceptance_v5.py" in gate`,
  `"ASSAY_GATE_PHASE=verdict-v5-accepted" in gate`, and
  `"carve-assets/P26/test_acceptance.py" in gate` is satisfied by a `#` comment.
  A gate whose P33 invocation is commented out passes the wiring oracle. (The
  real gate would then fail for other reasons — but the *wiring* claim O5 makes
  is not what caught it, which is precisely what the test was added to fix.)
* **The P25 half is weaker than its own comment.** The comment says "both P25
  consumers point at the v5 siblings"; the code reads `tools/tester-unified-gate.sh`
  and `gate/python/qualify_topos.py` **only**. `tests/test_python_qualification.py:259`
  — the second consumer named in work item 8b — is never opened. An implementer
  who repoints `qualify_topos.py` and forgets `test_python_qualification.py`
  passes. It also asserts each sibling name appears *once anywhere in the
  concatenation*, so one migrated site out of six satisfies it.
* **Fragility in the failing direction:** `\S*?` cannot cross a newline, so a
  `--deselect \` + continuation-line value fails the oracle for a reason
  unrelated to wiring correctness. The script's house style keeps flag and value
  together (`--no-index \`, `--require-hashes \`), so this is a live but
  secondary risk.

**Repair:** parse the actual P26 `python -m pytest` invocation (the command
substring between `-m pytest` and the next unescaped newline-terminated
statement) rather than the whole file; assert no `-k`/`--ignore`/`-m`/`addopts`
appears in it; and read `tests/test_python_qualification.py` explicitly.

### R5-F2 — A-230a (`helpers` omitted, never `[]`) has no enforcement at any layer

`helpers` in the locked v5 schema has **no `minItems`** and is not in the
top-level `required` list — so `helpers: []` is schema-valid. Verified by
loading the schema directly.

`test_helpers_is_omitted_when_no_helper_ran` iterates `V5_TEMPLATES`, skips
`sql-r2-v5-template.json`, and asserts `"helpers" not in doc` — over the
**carver's own locked templates**, which the implementer neither writes nor can
edit. It cannot fail unless the carver edits its own assets, and it exercises no
producer.

**Wrong implementation that passes everything:** serialise `helpers: []` on every
emitted artifact. Schema-valid, invariant-5-satisfied (an empty array trivially
has no entry needing a claim), locked templates untouched, all 43 tests green.
A-230a is specified and unenforced.

**Repair:** either add `"minItems": 1` to the schema's `helpers` (the locked
asset is the carver's to correct under A-197), or add a verifier clause refusing
an empty `helpers` array with a differential test. The schema route is stronger
and matches the layer-ownership rule in A-182 — an empty-array refusal is
locally expressible, so the schema should own it.

### R5-F3 — The sweep's `indirect-path-from-environ` category has no supplier guard, and it is masking a real detection failure

The classifier is asymmetric (`sweep_v4_consumers.py:279-282`):

```python
callers = _subprocess_callers(f, files)
if INDIRECT_ARGV.search(text) and callers:      # <- supplier required
    kind = "indirect-path-from-argv"
elif INDIRECT_ENV.search(text):                 # <- no supplier required
    kind = "indirect-path-from-environ"
```

`test_sweep_reports_no_zero_frozen_tree_noise_without_a_supplying_caller` guards
only the argv branch — its own docstring claims the rule covers environ ("or it
sources one itself (environ)") and nothing checks it.

**Demonstrated false positives** (run the sweep; both appear in the 28 with
`frozen_trees: []`, `v4_verdict_consumer: false`):

* `tests/test_cgroup_parent.py` — **zero** frozen-tree references anywhere in the
  file. Its only environ hit is `os.environ['PATH']` while building a fake-bin
  PATH. It consumes no frozen expectation of any kind.
* `tests/test_runner_plan_env.py` — its only "expected" occurrence is inside a
  docstring sentence; its comparisons are `child_env ==` and `called ==` over
  in-memory dicts.

**Demonstrated false negative, masked by the above** — the more serious half:
`nyxloom-trove/carve-assets/P20/test_acceptance.py` **is** a genuine consumer. It
does `expected = json.loads((ASSET_ROOT / "expected" / "post-dirty-v3.json").read_text())`
then `== expected`, where `ASSET_ROOT = Path(__file__).resolve().parent`. The
`reads_frozen_tree` predicate misses it entirely (`frozen_trees: []`), and it
lands in the inventory **only because it happens to mention `os.environ`**.
Delete that incidental mention and a real frozen-expectation consumer vanishes
from the closure the package's `escalate_if` depends on.

**Why this matters for dispatch:** `escalate_if` fires on *"sweep_v4_consumers.py
reports a consumer of a locked v4 expectation that no work item addresses."*
`tests/test_cgroup_parent.py` is reported and no work item addresses it. Whether
that trips the escape hatch depends on reading `v4_verdict_consumer: false` and
deciding it does not count — a judgement the trigger does not authorise. A
mechanical trigger firing on noise is the failure mode BLOCKED exists to avoid
(AUTHORING §5).

**Repair:** require a supplier for the environ branch too — a closure member that
sets the same environment variable *and* names a frozen tree
(`ASSAY_SELF_HOSTING_VERDICT` is set by `tools/tester-unified-gate.sh`, which
does name one, so `test_self_hosting.py` survives the tightening). Then extend
the noise test to both indirect kinds.

### R5-F4 — The two-adjacent-component rule misses the most natural way to spell a path

`_seg_variants` claims: *"a genuine reader either spells the path or joins its
parts in sequence."* Probed directly against `reads_frozen_tree` — **7 of 10
idioms miss**, including the head-of-chain join the docstring is describing:

| idiom | result |
|---|---|
| `"tests/fixtures/verdicts/r1.json"` | MATCHED |
| `os.path.join("tests", "fixtures", …)` | MATCHED |
| `root / "tests" / "fixtures"` | MATCHED |
| `Path("tests", "fixtures")` | MATCHED |
| **`Path("tests") / "fixtures"`** | **MISSED** |
| `Path(__file__).parent / "fixtures" / "verdicts"` | **MISSED** |
| `ROOT = Path(__file__).resolve().parents[2]` … `/ "fixtures"` | **MISSED** |
| `Path("tests").joinpath("fixtures", "verdicts")` | **MISSED** |
| `Path(f"{tree}/fixtures/verdicts")` | **MISSED** |
| `FIXTURES = "fixtures"; Path("tests") / FIXTURES` | **MISSED** |

The cause is that the pair variant is the literal string `"tests" / "fixtures"`,
so `Path("tests") / "fixtures"` fails on the intervening `)`. The rule only fires
mid-chain, never at the head. R5-F3's masked false negative
(`ASSET_ROOT = Path(__file__).resolve().parent`) is the live instance.

I searched the repo for a currently-missed *v4* consumer using these idioms and
found none beyond P20's — so this is a latent rule defect rather than a second
missed migration today. It is reported at that strength. But the closure claim
the package rests on is weaker than the docstring asserts, and the docstring
should not assert it.

### R5-F5 — The planted-decoy test writes into the repository under test

`test_sweep_finds_a_planted_decoy_consumer` writes
`tests/_p33_sweep_decoy_entry.py` and `src/assay/adapters/_p33_sweep_decoy.py`
into the live tree and unlinks them in a `finally`. This is a deliberate design
choice (the sweep scans `ROOT`, so `tmp_path` cannot work) and the test carries
its own leak guard — but three consequences are unstated:

* It contradicts the handoff's own copied constraint **B** ("Fresh `tmp_path`").
* A kill between write and unlink (gate timeout, `SIGKILL`, container teardown)
  leaves a module inside `src/assay/adapters/`. The next run's first assertion
  is the leak guard, so the failure is legible — but any *other* consumer of tree
  cleanliness in the same gate run (P20/A-175's post-command dirt guard, the
  self-hosted lane's own `DIRTY_TREE` check) sees genuine untracked dirt.
* The gate runs P33's suite and the self-hosted lane in the same container. The
  ordering that makes this safe (lane first, at gate line 187, before the P26/P33
  invocations) is real but incidental, and no oracle pins it.

Not blocking — the mechanism is sound and the guard is present. It should be
named in the handoff as a deliberate exception to constraint B, with the ordering
requirement stated.

---

## (3) Missing implementation-packet content

1. **A repaired `_load_lane` fixture** (R5-B1). The packet's config-layer proof
   material does not currently load.
2. **A config-refusal oracle for `equivalence_artifact`** (R5-B4).
3. **An enforcement layer for `helpers: []`** (R5-F2) — schema `minItems` or a
   verifier clause; today A-230a is prose only.
4. **The correct emitted category name** for `release_wheel.py` (R5-B2), in the
   sweep, the suite, the handoff and both README tables.
5. **The precedence rule in the handoff.** A-231 states *"the handoff now says so
   explicitly: where the locked schema or a named decision conflicts with
   design-doc prose, the schema and the decision win."* The handoff does **not**
   say so — `grep -i "precedence|supersede|wins|conflict"` finds nothing relevant,
   and line 77 still calls `SCHEMA-V5-DESIGN.md` "the specification" without
   qualification. The *design document* does carry the banner (lines 3–8) and
   both inline `SUPERSEDED` markers, so a cold reader who opens it gets the right
   answer first — which is why this is listed here rather than as blocking. But
   A-231's own record of itself is false (A-072's class: a ruling recorded but not
   propagated to the handoff).

---

## (4) Scope / dependency defects

1. **The two unsatisfiable locked tests are unfixable within scope.**
   `carve-assets/**` is in `scope.forbid`; `escalate_if` names editing a locked
   asset. R5-B1 and R5-B2 therefore route to BLOCKED, not to work. This is
   DOCTRINE §3's forbidden-needed-file failure with the forbidden file being the
   acceptance suite itself.
2. **`input_revision` is one commit behind the tree it describes.** It names
   `51668c0d`; `7bf86042` then changed `carve-assets/P33/README.md` (a locked
   asset), the handoff, `STATE.md` and the JIT-CARVE. An implementer checking out
   `51668c0d` gets a README whose hash table lists the *pre*-round-4
   `migration-manifest.json`, `sweep_v4_consumers.py` and `test_acceptance_v5.py`
   digests. (At HEAD all 13 hashes match — verified — so this is an anchor
   defect, not an asset defect.)
3. **`migration-manifest.json`'s `frozen_at_main` is `f9363543`** — the round-4
   *review* commit, i.e. **before** the re-carve that produced the manifest's own
   round-4 contents (`sweep_consumers: 28`). Under A-176/A-187/A-206/A-214 the
   anchor is load-bearing for READY; it should name the commit the scan actually
   ran against.
4. **The handoff's own readiness prose is stale.** "re-carved **three** times,
   after **three** NOT READY pre-dispatch reviews"; "**Three** review rounds are
   answered in … §§ 'Answering the pre-dispatch review', 'Answering round 2' and
   'Answering round 3'". There were four, round 4 *is* answered in the JIT-CARVE
   (§§ "What running the checks found…", "The sweep, third round on the same
   tool", "Contract gaps pinned…", "Documents that had drifted"), and the README
   was updated to "four" while the handoff was not.
5. **`Context to read first` item 2 omits A-230 and A-231** — the two newest
   decisions, cited by work items 6, 6b, 6c, 6d and by the design doc's own
   precedence banner. The list stops at A-229. This is the A-103/A-105/A-112
   citation-staleness class, third occurrence in this project.

---

## (5) Corrected oracle / fixture matrix

### Requirement → oracle traceability (defects only)

| Work item | Oracle | Status |
|---|---|---|
| 1 install schema | `test_shipped_schema_is_byte_identical…`, `--check` | ✓ sound |
| 2 model migration | template acceptance ×4 | ✓ sound |
| 3 verifier invariants | per-clause differentials | ✓ sound (per-clause discipline honoured) |
| 4 operator rename | O2 artifact half ✓ / **config half unsatisfiable (R5-B1)** | **BLOCKED** |
| 5 `go:*`/`sql:*` declarable-unreachable | refusal pinned for both languages | ✓ sound |
| 6 `kill_signal_artifact` refusal | message + `LaneConfigError` pinned ✓ / **fixture unsatisfiable (R5-B1)** | **BLOCKED** |
| 6b `equivalence_artifact` refusal | **none (R5-B4)** | **MISSING** |
| 6c `helpers` omitted | templates-only; **no producer/schema enforcement (R5-F2)** | **HOLLOW** |
| 6d narrowing | see below | ✓ sound, mis-propagated |
| 7 `ALL_MUTANTS_EQUIVALENT` | CA4 + CA9 pair, four-layer propagation enumerated | ✓ sound |
| 8 deselect exactly four | **regex blind to `-k`/`--ignore` (R5-F1)** | **WEAK** |
| 8b six qualify_topos sites | **all six verified accurate**; `test_python_qualification.py` unchecked | **PARTIAL** |
| 8c CA8 declared≠resolved base | scenario specified, machinery cited at `:404/:424/:836` | ✓ sound |
| 9 manifest counts | **`CANONICAL_COUNTS` contradicted by 4 documents (R5-B3)** | **BLOCKED** |
| 10 suite green | 43 tests confirmed; two can never go green | **BLOCKED** |

### Three combined-axis fixtures the suite still lacks

1. **`R0,R2` × cross-language operator × config load.** Every operator negative
   is artifact-level or (once R5-B1 is repaired) single-axis. Needed: a lane that
   loads cleanly, declares `language = "go"` **and** `operators = ["python:…"]`,
   and is refused — proving the check reads `judgment.resolved.language` rather
   than assuming Python. Today a hardcoded `"python:"` prefix check passes
   everything.
2. **`R0,R3` (no base) × `helpers` present × `uncovered-line`.** CA1 varies base
   absence alone and the helpers correspondence varies alone. Combined, an
   implementation that keys helper correspondence off `judgment.r1`'s *presence*
   rather than the claim's *payload* survives both single-axis tests and fails
   this one. A-150's shape: the one lane configuration nobody wrote.
3. **Migrated fixture × unmigrated sibling in one collection run.** The handoff
   names "a migration that passes because every fixture moved together" as the
   package's central risk, and then proves it only with v4-refused-by-version.
   Needed: one directory where a v5 document and a v4 document are verified in
   the same test, asserting the first passes and the second is refused *for its
   version specifically* — the differential form, applied to the migration itself.

---

## Explicit findings on each specifically-verified item

**`kill_attribution` narrowing (A-230d) — legitimate, but not propagated.**
The narrowing is the right call and matches settled precedent: A-142/A-144
deferred `MISSING_EXTERNAL_TOOL` to the package that made the state reachable,
and A-183 refused to ship "decorative code no test could discriminate". Building
a construction seam whose only purpose is to make a claim testable would have
been worse than dropping the claim. **It does not quietly drop real value** — the
rule is still schema-enforced for documents, both values are exercised as
artifact grammar by the locked templates, and P34 owns the producer proof.

But the narrowing stops at the body. **Frontmatter O4 still reads
`"kill_attribution is derived from judge.mutation.kill_signal_artifact and
self-consistent: …"`** — "is derived from" is exactly the half work item 6d
withdraws. An independent acceptance engineer reading the frontmatter (which the
daemon parses *without* the body — AUTHORING Level 2) will look for a derivation
witness that does not exist. O4's observable should be narrowed to the
self-consistency clauses alone, with the derivation stated in the body as
specified-not-witnessed. Note also the consequence worth stating in the handoff:
because config refuses `kill_signal_artifact`, the `declared` branch of the
derivation is **unreachable in any P33 producer** — a permanently dead branch on
day one. That is acceptable (assay's own gate asserts `tests-pass` only, so no
changed-line floor conflicts) but it should be named, not discovered.

**A-230a (`helpers` omitted, not `[]`)** — specified, **unenforced**. See R5-F2.
Schema has no `minItems`; the only test inspects the carver's own templates.

**A-230b (`equivalence_artifact` same disposition)** — **no oracle at all**. See
R5-B4. By work item 6's own argument, doing nothing satisfies it.

**A-230c (both refusals pinned to `LaneConfigError`)** — **honoured** for
`kill_signal_artifact` (`pytest.raises(C.LaneConfigError)` at suite lines 600 and
633, plus the `hasattr` pin at 660). Not applicable to `equivalence_artifact`,
which has no test. Caveat from R5-B1: pinning the class does not discriminate
while the fixture guarantees that class is raised for an unrelated reason.

**A-231 (design-doc precedence + inline staleness)** — **the design doc half is
done and correct.** `SCHEMA-V5-DESIGN.md` lines 3–8 carry the PRECEDENCE banner
at the very top; line 44 marks `resolved.base` `SUPERSEDED IN PART by A-223(a)`
with the conditional rule and an explicit "the `required` list in the fragment
below is stale"; line 107 marks "Go gets no operators" `SUPERSEDED by A-221/A-225`
with the three real operators enumerated and the struck text visibly struck. **A
cold reader no longer gets the wrong answer on either question.** The handoff
half of A-231 is not done (see §3 item 5).

**O5 gate-wiring oracle** — the carver's own flag was correct; see R5-F1. The
`-k`/`--ignore` blind spot is the strongest attack, and it lands on exactly the
oracle A-229 added to prevent a security oracle being dropped again.

**`qualify_topos.py`'s six v4-coupled sites** — **verified, all six accurate.**
Read directly at HEAD:

| line | site |
|---|---|
| `:51` | `_EXPECTED_ROOT = _PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P25" / "expected"` |
| `:715` | `normalized["judgment"]["r1"]["base"] = "@BASE_OID@"` |
| `:928` | `json.loads((_EXPECTED_ROOT / "missing-v4-template.json").read_text(…))` |
| `:962` | `template=_EXPECTED_ROOT / "missing-v4-template.json"` |
| `:1009` | `for result, template_name in ((primary, "pass-v4-template.json"), (missing, "missing-v4-template.json"))` |
| `:1018` | `template=_EXPECTED_ROOT / template_name` |

An independent `grep -nE "v4-template|_EXPECTED_ROOT"` returns exactly these five
lines plus `:715`'s `judgment.r1.base` — no seventh site. Round 3's partial
closure is genuinely closed. The residual gap is that no oracle checks the
*second* consumer, `tests/test_python_qualification.py` (R5-F1).

**`CANONICAL_COUNTS`** — the mechanism exists and is correct (43 tests,
`33 failed / 10 passed`, 28 consumers all match reality exactly). The *claim*
that every document points at it is false: see R5-B3.

**Sweep verification** — 40 → **28** confirmed. All five established consumers
present. `test_self_hosting.py` correctly classified `indirect-path-from-environ`
rather than accidentally matched. New FP/FN classes constructed against both new
mechanisms: R5-F3 (environ branch, two demonstrated false positives and one
masked real consumer) and R5-F4 (7-of-10 idiom miss). The supplier-tracking logic
itself is sound for the argv branch and its noise test does discriminate the
removal of the `and callers` guard — it was simply never extended to environ.

---

## (6) READY or NOT READY

**NOT READY.**

Blocking: R5-B1 (three config tests unsatisfiable by any implementation — seven
missing required fields in a locked fixture), R5-B2 (two locked assets assert
contradictory category names; unfixable inside scope), R5-B3 (`CANONICAL_COUNTS`
contradicted by four documents, including the routing justification), R5-B4
(work item 6b has no oracle and is satisfied by doing nothing).

Everything blocking is a **carver-side asset or prose repair**; none requires
reopening a product decision, and none touches the schema's semantics. The
substantive contract — V5-1 through V5-5, the five cross-object invariants, the
`ALL_MUTANTS_EQUIVALENT` terminal and its four-layer propagation, the operator
vocabulary, the six `qualify_topos.py` sites, CA8, and the A-230d narrowing — is
sound and, on this reading, converged. Rounds 1–4 closed real defects and the
design document's precedence repair is exemplary.

What has not converged is the *verification of the verification*. Rounds 4 and 5
each found the acceptance suite unable to go green against a correct
implementation, both times in the config-layer helper, both times invisible to
reading and visible in seconds of running. The carver's own conclusion after
round 4 was the right one and was applied to the symbol names but not to the
fixture body.

**Recommendation to the controller:** require, as a condition of the next
readiness claim, that the carve report records the verbatim tail of
`PYTHONPATH=src python3 -m pytest nyxloom-trove/carve-assets/P33/test_acceptance_v5.py`
**and** a demonstration that each still-red test fails for its intended reason —
not merely that it is red. `33 failed` is not evidence; *why* each of the 33 is
red is. Three of them were red for a reason no implementation could remove, and
the aggregate count concealed it.

---

## Appendix — the review prompt, verbatim

Copied from `nyxloom/reference/AUTHORING.md` § "Pre-dispatch adversarial handoff
review", blob `24460b57d015e44f0f1463e2a0393b09bdafaf40` at
`7bf860427804293bae774048f702b5b54b570bb4`:

> Review this handoff as a hostile implementer, a hostile environment, and an
> independent acceptance engineer. Do not propose code yet. Build a
> requirement-to-oracle traceability table and try to make every oracle pass
> while violating the stated product goal. Identify: undefined interfaces or
> data grammar; values the implementer must invent; shadowing or silent
> defaults; ambiguous ownership; missing terminal states; repo/project,
> host/container, source/artifact, or declared/effective namespace confusion;
> stale or producer-authored evidence; unbounded work; order, clock, ambient
> environment, and repeated-execution dependence; scope/dependency conflicts;
> and tests that share the implementation's assumption. Then construct a
> pairwise input matrix and name at least three combined-axis fixtures likely
> to break a convenient implementation. For each oracle, give one plausible
> wrong implementation that still passes the proposed test. Mark the handoff
> NOT READY if any externally visible decision, interface, example, bound,
> refusal, or proof source remains for the implementer to invent. Return only:
> (1) blocking ambiguities, (2) false-PASS attacks, (3) missing implementation-
> packet content, (4) scope/dependency defects, (5) a corrected oracle/fixture
> matrix, and (6) READY or NOT READY with reasons.
