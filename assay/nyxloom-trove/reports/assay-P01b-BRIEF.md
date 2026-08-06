# P01b — successor brief

You have `verdict.py` and `schemas/verdict.schema.json`. Four of you will edit
both; here is what you must not break.

## Adding your claim payload

The claim object is closed by `unevaluatedProperties: false`, and payloads are
admitted by **additive branches** in `$defs/claim/allOf`:

```json
{"if":   {"required": ["rigor"], "properties": {"rigor": {"const": "R2"}}},
 "then": {"properties": {"mutation": {"$ref": "#/$defs/mutation"}}}}
```

Add the branch, add a `$defs` entry closed by `additionalProperties: false`, add
the field to `Claim`. That is the whole edit. The payload is then legal in that
branch and **nowhere else** — `coverage` on an R0 claim is already rejected and
nobody wrote a rule against it. Do **not** widen the claim to
`additionalProperties: true` to make room; that deletes the guard, and
`test_an_unknown_claim_key_is_rejected` will say so.

## Two rules that are not conventions

**Absent means unknowable; empty means known-and-empty.** `coverage` is omitted
under `NO_MEASUREMENT` because a number that is not a measurement gets read as
one (A-025). `argv_declared = []` where no lane loaded asserts *"the lane
declared no argv"*, which is false — so the lane-resolved group
(`declared_rigor`, three `argv_*`, `argv_modified`, two `env_*`) is all-present
or all-absent. Ask the question before you emit a zero.

**Two invariants are the MODEL's, because draft 2020-12 cannot compare two
locations in one instance** (there is no `$data`): that `claims` covers
`declared_rigor` exactly, and that `argv_effective` is
`argv_declared + argv_appended`. Need a third? Put it in `__post_init__` and
assert it with `pytest.raises`. Do not fake it in the schema, and do not soften
it into "the model emits the right thing".

## Traps

* **The schema is HAND-WRITTEN and must stay so.** `test_verdict_reason_codes.py`
  asserts its enums equal `errors.REASON_CODES` — a real drift guard only
  because the two artifacts are independent. Generate one from the other and the
  module becomes self-agreement.
* **`Verdict` refuses `outcome != rollup(claims)`** when claims are non-empty.
  P07: compute the outcome with `rollup()`, never choose it alongside.
* **Hand-edited mutations have no `drop_key` safety net.** My `replace_all` hit
  one of two sites (different indentation) and I nearly logged **1 failed**
  where the truth was **5**. `grep -c` the mutant before trusting a count.
* The `standalone` fixture (wheel build + offline install) is now in
  `conftest.py`, session-scoped. Reuse it; do not copy it.
* `tests/fixtures/verdicts/` holds the independent oracle (A-041). Add a field
  and you update all six by hand. That is the cost, and it is the point.

## Spec ambiguities I had to interpret

All were ruled on by the controller before implementation.

* **DESIGN-GUIDE §6 is wrong about the `GateResult` superset.** The artifact
  carries assay's own names — no `gate_id`, `phase` or `environment`. assay has
  no source for nyxloom's phase vocabulary and no environment knowledge (§7);
  and nyxloom's `_Serde.from_dict` rejects unknown keys, so a literal superset
  could never have been fed to it. The guide is being corrected.
* **A-055 is superseded.** Coverage is defined here and claims carry their own
  `reason_code`. P09 owns attestation and adjudication payloads, not coverage.
* **Claim `status` is the six `Outcome` values.** `PENDING` (A-O08) is a schema
  edit for whoever ships the first async producer.
* **The handoff said to define the enums in `verdict.py`.** A-066 says
  `errors.py` owns them; decisions.md won.
