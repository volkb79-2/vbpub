# assay-P01b — the verdict model and its schema — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P01b-verdict-model-and-schema`
**Commit:** `d0ff79ce`. **Base:** `main` at `659e02d6`.

## Gate

`[gates.tester-unified]` from `nyxloom-trove/nyxloom.toml`, run in the
FOREGROUND against HEAD with `{worktree}` substituted:

```
$ docker run --rm --cgroup-parent=nyxloom-gates.slice \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'cd /workspaces/vbpub/.worktrees/assay-P01b-verdict-model-and-schema/assay \
             && export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q'
........................................................................ [ 11%]
........................................................................ [ 22%]
........................................................................ [ 33%]
........................................................................ [ 45%]
........................................................................ [ 56%]
........................................................................ [ 67%]
........................................................................ [ 78%]
........................................................................ [ 90%]
...............................................................          [100%]
639 passed in 7.48s
GATE_EXIT=0
```

Baseline before this package: 207 passed. This package adds 432 tests.

Coverage, measured in the same image (not asserted by the gate, which declares
`asserts = ["tests-pass"]` only):

```
Name                    Stmts   Miss Branch BrPart  Cover
src/assay/__init__.py      10      0      0      0   100%
src/assay/cli.py           40      0      4      0   100%
src/assay/config.py       289      0    142      0   100%
src/assay/errors.py        53      0      4      0   100%
src/assay/verdict.py      193      0     96      0   100%
TOTAL                     585      0    246      0   100%
```

No image rebuild. Nothing but ignored caches left in the worktree.

## Delivered

| Work item | File | Notes |
|---|---|---|
| 1 | `src/assay/verdict.py` | 193 executable statements. `Verdict` / `Claim` / `Coverage`, `rollup()` (A-023), `iso_utc()`. Imports `Outcome`/`ReasonCode`/`EXIT_CODES`/`REASON_CODES` from `errors.py` and re-exports (A-066) |
| 2 | `src/assay/schemas/verdict.schema.json` | hand-written draft 2020-12, 8 verdict branches + 9 claim branches |
| 2 | `pyproject.toml` | `[tool.setuptools.package-data] assay = ["schemas/*.json"]` |
| — | `src/assay/__init__.py` | re-exports `Verdict`, `Claim`, `Coverage`, `rollup`, `load_schema`, `iso_utc`, `VERDICT_SCHEMA_VERSION` (controller ruling) |
| 3 | 6 test modules + 6 hand-written fixture artifacts | 432 tests |
| — | `tests/conftest.py` | schema/validator fixtures, `verdict_fixture()`, `why_invalid()`; the `standalone` venv fixture hoisted from `test_dependency_purity.py` (controller ruling) |

## Controller rulings implemented

All eight, as given. Recorded here because they are not yet in `decisions.md`:

1. Enums imported from `errors.py`, never redefined — the handoff's Work item 1
   ("verdict.py — the outcome enum, the closed reason_code enum") is superseded
   by A-066.
2. The artifact carries **assay's own names**. No `gate_id`, no `phase`, no
   `environment`: assay has no source for nyxloom's phase vocabulary and no
   environment knowledge at all (§7, permanently). Documented in
   `verdict.py`'s module docstring, including the `_Serde.from_dict` finding
   that settles it. **DESIGN-GUIDE §6 is wrong on this point** and the
   controller is fixing it.
3. `coverage` lives **inside the R1 claim**, not at top level.
4. P01b defines the envelope and the coverage payload, plus the **additive
   branch pattern** for P04/P05/P08/P10. The pattern is stated in the schema's
   own `description` and `$comment`s, not only in this LOG.
5. Claim `status` is the six `Outcome` values; no `PENDING`.
6. Claims carry their own `reason_code`, same closed enum, same rule.
7. **Absent means unknowable; empty means known-and-empty.** A verdict for a
   lane that never loaded omits the whole lane-resolved group.
8. O4 is split: schema enforces the envelope and the attested implication; the
   model enforces rigor coverage. Both as rejections.

## Per-oracle evidence

Each oracle is followed by the **mutation actually applied** to prove its tests
bite (A-067). Every mutation below was run against the real gate and reverted;
the counts are real.

### O1 — one Verdict per outcome, validated against the FILE

* `tests/test_verdict_serialises.py`. All six verdicts are built as constructor
  calls, serialised, and validated against `SCHEMA_PATH` read with
  `json.loads` — never a dict built in Python.
* The anti-hollow half: `test_serialisation_equals_the_hand_written_artifact`
  compares `json.loads(verdict.to_json())` against
  `tests/fixtures/verdicts/<outcome>.json`, six artifacts written by hand as
  JSON text that nothing in assay generated. This is A-041's doctrine arriving
  a package early, and it is the analogue of P01a's `tomllib` round-trip.
* Vacuity guards: `{}`, `[]`, a bare string, an unknown outcome, an unknown
  top-level key, a string `schema_version` and a naive timestamp are all
  asserted to FAIL, so an emptied schema fails this module rather than passing
  it. `test_the_shipped_schema_is_a_valid_draft_2020_12_schema` runs
  `check_schema`, so "accepts everything" cannot hide behind "is not a schema".
* `test_there_is_one_verdict_per_outcome_and_no_outcome_is_unproven` asserts the
  builder set and the fixture set both equal `{o.value for o in Outcome}` — an
  outcome with no fixture is a failure, not a silent gap.
* **Mutation 6** — emit the lane-resolved group as empties instead of omitting
  it (ruling 7's bug): **3 failed**, including
  `test_every_outcome_serialises_to_json_and_validates_against_the_file` and
  `test_serialisation_equals_the_hand_written_artifact`.

### O2 — the schema REJECTS, as validation failures

* `tests/test_verdict_schema_rejects.py`. Every reject test asserts the
  untouched document VALIDATES **in the same body**, so a reject-everything
  schema fails here too. The rejection instances are raw dicts, never routed
  through `Verdict` — the model refuses most of them, so routing them through it
  would mean the validator was never reached.
* `test_the_same_coverage_block_is_fine_on_a_measured_claim` exists so the
  A-025 rejection cannot pass for the wrong reason: it proves the planted
  payload is well-formed and that only `NO_MEASUREMENT` makes it illegal.
* **Mutation 2** — delete the claim's `NO_MEASUREMENT → no coverage` branch,
  i.e. reintroduce `pct: 100.0` beside `NO_MEASUREMENT`: **2 failed**
  (`test_a_no_measurement_verdict_carrying_a_coverage_block_is_rejected` and,
  independently, the installed-wheel copy of the same check).
* **Mutation 3** — remove `not: {required: [reason_code]}` from the PASS
  branch: **17 failed**.
* **Mutation 1** — relax `unevaluatedProperties` to `true` at both levels:
  **5 failed**, including all three `coverage-on-a-non-R1-claim` cases, which
  is the additive-branch pattern's teeth being measured.

### O3 — the CLOSED reason_code enumeration

* `tests/test_verdict_reason_codes.py`, 16 valid pairs asserted to validate and
  **64 cross pairs** (a real code, wrong outcome) asserted to be rejected — at
  verdict level and again at claim level. Each cross-pair test first asserts the
  same document with a CORRECT code validates.
* `test_the_shipped_schema_enumerates_exactly_the_codes_errors_py_declares` is
  the drift guard. It is legitimate **only because the schema is hand-written**:
  if it were ever generated from `errors.py`, this whole module would collapse
  into self-agreement. That is stated in the module docstring as the one thing a
  successor must not "simplify".
* `test_an_exit_code_that_disagrees_with_the_outcome_is_rejected` walks all six
  outcomes against all six exit codes.
* **Mutation 9** — widen the FAIL branch's `reason_code` from
  `$defs/reason_codes/FAIL` to the full enum: **11 failed**, exactly the eleven
  cross pairs for FAIL.

### O4 — the claim envelope, and rigor coverage

* `tests/test_verdict_claims.py`. Schema half: all three `source` values
  validate; `attested` + `verified_by_assay: true` is rejected; a `computed`
  claim MAY be verified (so the rule is about `attested`, not about the flag);
  unknown source, unknown rigor, unknown claim key and a missing envelope field
  are each rejected.
* Model half: rigor coverage. **The schema cannot express this** — it compares
  `declared_rigor` against `claims[].rigor`, two locations in one instance, and
  draft 2020-12 has no `$data` and no cross-instance reference. Per the
  handoff's own `escalate_if` this is reported rather than weakened: it is a
  `pytest.raises` rejection, never an acceptance test asserting the model emits
  one claim per level.
* Rollup: precedence order asserted against the declared list, every adjacent
  pair resolved in both orders, and an empty rollup asserted to RAISE.
* **Mutation 4** — remove the attested check from `Claim.__post_init__`:
  **1 failed**.
* **Mutation 5** — make `_check_claims_cover_declared_rigor` return
  immediately: **5 failed**.
* **Mutation 7** — make `rollup([])` return PASS: **1 failed**.

### O5 — the schema is inside the installed wheel

* `tests/test_verdict_schema_is_packaged.py`. The wheel's zip namelist is read
  directly; the schema is resolved from **inside the scratch venv** in a
  subprocess with a clean environment; the resolved path is asserted to be under
  the venv; and the text that comes back is compared with the source file, so "a
  file with the right name is present" cannot stand in for "the schema is".
* The vacuity shape this defends against is A-067's own: resolving through
  `PROJECT_ROOT`, or leaving `PYTHONPATH=src` (which the gate exports) in the
  child's environment, would find the source-tree copy and pass against an empty
  wheel.
* `test_the_installed_schema_still_rejects_a_malformed_verdict` validates HERE
  rather than in the venv, because the venv contains only assay — no
  `jsonschema` — which is A-005 working.
* **Mutation 8** — set `package-data` to `assay = []`: **5 failed**, i.e. every
  test in the module.

## Self-review

### Would each oracle's test fail if the behaviour were removed?

Yes for all five, demonstrated by nine mutations rather than asserted. The
weakest link before mutation testing was O1, whose oracle is an *acceptance*
statement; it is carried by the hand-written fixtures and the vacuity block, and
mutation 6 confirms it bites.

### What I found wrong in my own work

**Mutation 1 was silently a no-op on half its target, and I nearly recorded the
wrong number.** `replace_all` on `"unevaluatedProperties": false` matched only
the top-level occurrence, because the claim-level one has different indentation
and no trailing comma. The first run reported **1 failed** and I would have
logged that as "the closed-object guard is worth one test". Flipping the second
site as well gave **5 failed**, including all three coverage-on-a-non-R1-claim
cases. The lesson is P01a's `drop_key`/`set_key` discipline — *a mutation helper
must assert it mutated something* — applied to hand-edited mutations, where
nothing asserts it for you. I verified the second flip with `grep -c` before
trusting the count.

**Two test-harness bugs found by the first full run**, both mine, neither in the
implementation: `document_with()` read `claim["rigor"]` after the parametrised
test had deleted it, and `test_a_non_pass_claim_missing_reason_code_is_rejected`
pulled an adverse claim out of the ERROR fixture, which has none.

### What is MISSING from the diff that the handoff asked for

Nothing in `## Work`. Three items in the handoff's own text are deliberately not
implemented as written, each on an explicit controller ruling recorded above:
the enums are not defined in `verdict.py` (A-066), the artifact is not a literal
`GateResult` superset, and O4's rigor-coverage clause is not a schema
constraint.

### What I implemented that the handoff did not ask for

* **`exit_code` pinned to `outcome` in the schema** (6 branches). Not asked for.
  Justification: §6 says the exit code IS the verdict and a consumer checking
  only it must never be wrong; an artifact allowed to disagree with the process
  status defeats that.
* **`argv_modified` is derived, not stored.** Removes the class of artifact in
  which it and `argv_appended` disagree. The schema still carries the
  consistency rule, because a hand-built artifact from another producer can
  still get it wrong.
* **`argv_effective == argv_declared + argv_appended`**, refused at
  construction. §6 records all three but does not say they must agree.
* **`outcome == rollup(claims)`**, refused when claims are non-empty. This is
  A-023 read strictly. **It is the one thing here most likely to constrain
  P07** — if a runner needs to emit an outcome that is not the rollup of its
  claims, this check is what it will hit. Flagging it rather than burying it.
* **`Coverage` refuses `covered > changed_executable`** and a `pct` outside
  0–100. Arithmetic consistency is P04's subject, not mine; these are only the
  impossible cases.
* **`iso_utc()`** — ~6 lines, so P07 does not re-invent the timestamp spelling
  and so the naive-datetime refusal lives in one place.

### Decision ids I could not honour as written

* **A-055** — "P01 defines the claim envelope only (`source`, `status`,
  `verified_by_assay`); kind-specific payloads are P09's". Superseded by
  controller ruling: the coverage payload is defined here, and claims carry a
  `reason_code` beyond the listed three fields. The carving defect behind this
  (P04/P05/P08/P10 needed schema scope and did not have it) is being fixed in
  their handoffs.
* **A-029 / DESIGN-GUIDE §6** — the "superset of nyxloom's `GateResult`" claim
  is not implemented literally, per ruling 2. The guide is being corrected.
* Everything else cited by the handoff (A-021 through A-028, A-050, A-051,
  A-066, A-067, A-070) is implemented as written.

### Known-weak spots, stated plainly

* **The timestamp pattern exists twice** — as `$defs/timestamp` in the schema
  and `_TIMESTAMP_RE` in `verdict.py`. Deduplicating would mean generating one
  from the other, which is the self-agreement O3's docstring warns against. The
  duplication is deliberate; no test proves the two are equal, and one could
  drift from the other in a direction where the model is *stricter* than the
  schema without anything failing. The reverse direction is caught, because
  every constructed verdict is validated against the file.
* **A-069 is unchanged**: `setuptools_scm` is still absent from the image, so
  built wheels version as `0.0.0` and `fallback_version` remains unexercised.

## Left undone / for the next package

Written up separately in `assay-P01b-BRIEF.md`.
