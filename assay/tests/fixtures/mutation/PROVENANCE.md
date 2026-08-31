# Real mutation-report artifacts — carver-owned evidence, never hand-authored

This file is the mutation-side sibling of
`tests/fixtures/coverage/PROVENANCE.md`. It covers exactly the artifacts that
came out of a REAL external mutation-testing tool, produced OUTSIDE assay and
committed verbatim. The other subdirectories here (`go/`, `python/`, `sql/`)
hold hand-written *source* samples that assay's own NATIVE mutation engines
mutate; they are inputs, not evidence about a foreign tool, so they are not
described here.

**A-334 is why this file exists**: a test double is not evidence about an
external system. Every claim `mutation_parsers/mutation_report_json.py` and
its tests make about what Stryker actually emits is checked against the
committed real document below, not against a synthetic one written to match
the design. Synthetic reports still exist in the suite — they are the only
way to reach statuses this project cannot make a real tool produce on demand
(`Pending`, `CompileError`, an over-ceiling mutant count) — and every one of
them is labelled as synthetic in its own test.

---

# `mutation-report-json.probe-js-stryker.json` — real StrykerJS output (B046)

The mutation-testing-report-schema document produced by a real StrykerJS run
over the **same sources** the `coverage-istanbul-json.vitest-*` fixtures were
produced from (`tests/fixtures/coverage/probe-js/src/`). One program, two
kinds of evidence about it: coverage in the coverage fixtures, mutation here.

## Versions — measured, not assumed

Read back from the artifact itself and from the committed lockfile:

| what | version | where it is recorded |
|---|---|---|
| `framework.name` | `StrykerJS` | the artifact's own `framework` object |
| `framework.version` | `10.0.0` | the artifact's own `framework` object |
| `schemaVersion` | `1.0` | the artifact's own top-level key |
| `@stryker-mutator/core` | `10.0.0` | `probe-js-stryker/package-lock.json` |
| `@stryker-mutator/vitest-runner` | `10.0.0` | `probe-js-stryker/package-lock.json` |
| `mutation-testing-report-schema` | `3.8.4` | `probe-js-stryker/package-lock.json` (Stryker's own transitive dependency — the package that OWNS the JSON Schema this format is defined by) |
| `vitest` | `3.2.4` | inherited from `probe-js/package.json` |
| `node` | `v26.5.1` | this devcontainer, 2026-08-31 |
| `npm` | `11.17.0` | this devcontainer, 2026-08-31 |

## How it was produced

```sh
# 1. a scratch copy of the probe-js project, sources untouched
cp -r assay/tests/fixtures/coverage/probe-js/. /tmp/stryker-probe/
cd /tmp/stryker-probe

# 2. the two Stryker packages, on top of probe-js's own devDependencies.
#    The resulting package.json + package-lock.json are committed as
#    `probe-js-stryker/package.json` and `probe-js-stryker/package-lock.json`.
npm install --no-audit --no-fund --save-dev \
    @stryker-mutator/core@10.0.0 @stryker-mutator/vitest-runner@10.0.0

# 3. `probe-js-stryker/stryker.config.json`, committed verbatim
npx stryker run                      # exit 0

# 4. the report Stryker's own `json` reporter wrote
cp reports/mutation/mutation.json \
   assay/tests/fixtures/mutation/mutation-report-json.probe-js-stryker.json
```

To reproduce: copy `coverage/probe-js/`'s `src/` and `vite.config.ts` beside
`probe-js-stryker/`'s three committed files, `npm ci`, then `npx stryker run`.
Mutant ids and the exact `Survived`/`NoCoverage` split are Stryker's, not
assay's, and a newer Stryker may legitimately produce a different one — that
is what a regenerated fixture is for, not a reason to edit this one.

## The config, and why `thresholds.break` is `null`

`probe-js-stryker/stryker.config.json` declares `"thresholds": {"break": null}`
deliberately, and B046's own non-repudiation rule (ii) mandates it for every
ingested lane. Stryker exits NON-ZERO when its mutation score falls under its
OWN `break` threshold. If a lane let Stryker break, a score assay is supposed
to judge would instead reach assay as R0's `COMMAND_FAILED`, and the exit
status — the thing CIU proposal §1.10 already refuses to treat as proof —
would silently become the judgment. With `break: null` the exit status carries
only crash information; the score is assay's to judge. The committed run
exited `0` with a mutation score far below any conventional threshold, which
is exactly the point.

`"coverageAnalysis": "perTest"` and `"concurrency": 2` are performance
choices with no effect on the report's shape. `mutate` excludes the spec/test
files and `src/types.d.ts`; `timeoutMS` is Stryker's per-mutant bound, the
thing B046 maps onto `budget_exceeded`.

## What it proves — the facts B046's design depends on

Every number below is read back from the committed document, not asserted
from the design.

1. **The status vocabulary is the schema's, not Stryker's invention.**
   `mutation-testing-report-schema@3.8.4`'s
   `src/mutation-testing-report-schema.json` defines `MutantStatus` as
   exactly eight values: `Killed`, `Survived`, `NoCoverage`, `CompileError`,
   `RuntimeError`, `Timeout`, `Ignored`, `Pending`. B046's bucket map is
   total over that enum; `mutation_report_json.py`'s own map is checked
   against it by a test, so a ninth status added upstream fails loudly rather
   than being silently dropped.
2. **Three of those eight really occur here**: `NoCoverage` 69, `Killed` 21,
   `Survived` 19 — 109 mutants over 6 files. `NoCoverage` being the LARGEST
   bucket is the substantive reason B046 refuses to fold it into `survived`:
   on a real project it is not a rounding error, it is the majority finding,
   and it is the worst kind of survival.
3. **The mutator names are DATA, not a closed assay vocabulary**: this one
   run alone emits nine distinct `mutatorName` values (`ArithmeticOperator`,
   `ArrayDeclaration`, `BlockStatement`, `BooleanLiteral`,
   `ConditionalExpression`, `EqualityOperator`, `LogicalOperator`,
   `ObjectLiteral`, `StringLiteral`), none of which is one of assay's own
   native operator names. This is the measured basis for the
   `stryker:<mutatorName>` namespace rather than an attempt to map foreign
   mutators onto assay's closed catalogue.
4. **`location.start.line` is 1-based and always present** (the schema marks
   `location` required on every `MutantResult`, and `Position.line` has
   `minimum: 1`), which is what makes the changed-line scope intersection
   well defined. `location.end` is EXCLUSIVE per the schema's own
   `Location` description — assay uses `start.line` only, so the
   exclusive-end subtlety cannot silently shift a mutant into or out of
   scope.
5. **`files` keys are project-relative forward-slash paths**
   (`src/Badge.tsx`, `src/branchy.ts`, `src/format.ts`, `src/hinted.ts`,
   `src/orphan.ts`, `src/roles.ts`) — never absolute, which is the opposite
   of what istanbul does one format over (A-341), and the reason this parser
   needs no absolute-path reconciliation branch at all.
6. **`projectRoot` is present and absolute**, and in this committed artifact
   it names the SCRATCH DIRECTORY the run happened in. That is not a defect
   in the fixture: a `projectRoot` is by construction the absolute path of
   whatever machine produced the report, so no committed artifact can carry
   a value that equals a future snapshot root. The parser therefore takes
   the expected root as a PARAMETER and the real-artifact tests pass this
   document's own recorded value; the mismatch refusal is proved separately,
   over a synthetic document, because a real one cannot exhibit both.
   `projectRoot` is OPTIONAL in the upstream schema (only `schemaVersion`,
   `thresholds` and `files` are required) — assay refuses a report that
   omits it, because an unbindable report is not evidence (A-351 family).
7. **`framework.version` is present** and is what
   `judgment.r2.producer_tool` copies. The schema requires only
   `framework.name`; assay refuses a report whose `framework` cannot
   identify the producer, for the same reason.

## What the implementer must not do

**Do not edit this artifact.** A parser that needs it changed is a parser
that does not read the real format — the same rule the coverage PROVENANCE
already states for its eight documents. If Stryker's output shape genuinely
changes, regenerate the whole file by the recipe above and update the table.
