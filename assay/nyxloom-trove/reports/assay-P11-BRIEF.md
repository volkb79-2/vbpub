# P11 successor brief — for P12 (bounded mutation execution)

P11 shipped construction only; P12 executes. Here is the exact shape you
consume.

## The call

`adapter.generate_mutants(text: str, lines: set[int]) -> tuple[Mutant, ...] | Literal["UNSUPPORTED"]`
is the 7th and final method on `LanguageAdapter` (`src/assay/adapters/base.py`).
`text` is a source file's full content; `lines` is the same "changed
line numbers" concept `assay.diff.AddedLines`/`assay.measurability` already
use. `"UNSUPPORTED"` means treat this file as `INCONCLUSIVE_NO_MUTANTS`
(never green) — it fires only when `text` itself fails to parse
(`PythonAdapter`) or unconditionally (`GoAdapter`, no Go toolchain exists,
A-042). It never fires for an individual unsupported construct inside an
otherwise-parseable file — that construct just contributes zero mutants,
so an empty `()` tuple is a completely normal, legal result and is NOT the
same thing as `"UNSUPPORTED"` (`result == ()` vs `result == "UNSUPPORTED"`
are both real, distinguishable outcomes you must handle separately).

## `Mutant` (`src/assay/mutation.py`)

Frozen `kw_only` dataclass: `lineno: int`, `operator: str` (one of the
closed `MUTATION_OPERATORS` — `compare-swap`/`boolop-swap`/
`bool-const-flip`/`falsy-swap`), `description: str` (human-readable,
e.g. `"Lt->LtE"`, never parsed back apart), `mutated_text: str` (the FULL
file text with exactly one construct changed — not a diff, not a patch,
the complete replacement content, ready to write to a scratch path
verbatim). `Mutant.identity` is a derived `@property` returning
`(lineno, operator, description, mutated_text)` — stable, and unique per
site even across a 3+-operand boolean chain (two sites on the SAME line
with the SAME operator/description still differ in `mutated_text`, since
the splice landed at different byte offsets). There is no `original_text`
field and no byte-offset field on `Mutant` — if you need to write the
mutant to disk you write `mutated_text` wholesale over the original file's
path; if you need to identify a mutant across a run, use `.identity` (or
just object identity within one `generate_mutants` call, since mutants
from one call are never merged with another's without also carrying which
file/adapter they came from — that pairing is your job, not `Mutant`'s).

## What one call gives you

Every mutant returned by ONE `generate_mutants(text, lines)` call is an
INDEPENDENT single-site experiment against the SAME original `text` —
never cumulative, never composed. Running mutant #3 does not need mutant
#1 or #2 applied first; each is spliced from the pristine original. This
is exactly what "bounded mutation execution" needs: a job list of
`(file_path, Mutant)` pairs you can fan out over a thread pool with zero
ordering dependency between them (nyxloom's own `mutation_gate.evaluate`'s
`ThreadPoolExecutor` fan-out, A-113, is the directly relevant prior art
for the ORCHESTRATION half — reuse its `jobs` list / position-aligned
result aggregation shape, never its `_run_is_killed` file-write mechanism
verbatim, since nyxloom writes `mutant.mutated_source` and yours is
`mutant.mutated_text`, same idea, different field name).

Ordering within one call is deterministic (`lineno`, `operator`,
`description`, then byte offset) and stable across repeated calls with
identical input — useful for reproducible job numbering, but do not build
a CORRECTNESS dependency on that specific tie-break order; only on
"deterministic," which is tested and will not silently change under you.

## Fixtures and precedent to reuse

`tests/fixtures/mutation/python/sample.py` is a literal, committed fixture
exercising all 27 real eligible sites (every catalogue member, both
boolean-chain lengths, every ineligible construct the catalogue
deliberately skips) — a good source of realistic mutants to drive a
scratch-execution test without inventing new fixtures from scratch, though
you may well want your own (P12 needs mutants whose kills/survivals you
can control, which this fixture's content does not stage for). The
`tests/test_adapters_python_generate_mutants.py::EXPECTED_MUTATIONS`
dict shows exactly how each site's `mutated_text` looks if you need worked
examples while writing your own oracle fixtures.

## Scope boundary, restated

P11 never runs a test, never shells out, never touches a filesystem path —
`generate_mutants` is pure `(text, lines) -> mutants`. P12 owns: writing a
mutant to a scratch location, running the real test command against it,
interpreting exit code as killed/survived, aggregating, and rendering the
`MUTANTS_SURVIVED`/`NO_MUTANTS` reason codes already reserved in
`errors.py` (untouched by P11, available to you) into a `Claim` attached
at `rigor == "R2"` (mirroring `Claim.coverage`/`R1` and
`Claim.canary`/`R3`'s own established gating pattern — no such R2 payload
type exists yet; you will need to add one, following `Coverage`/
`CanaryResult`'s exact frozen-`kw_only`-with-`__post_init__`-validation
shape, in `verdict.py`, which is IN your scope, not P11's).
