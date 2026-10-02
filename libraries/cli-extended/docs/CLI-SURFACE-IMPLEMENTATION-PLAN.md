# CLI surface and semantic review tooling plan

**Status:** Netcup consumer pilot and correctness follow-up in progress. In the CIU worktree `cli-extended-review`, commit `77d13912` passed R0/R1 with 100% statement and branch coverage (3,404 statements; 1,650 branches), Netcup's registered suite (228 tests), and the generated Netcup CLI surface check. Its resumed R2 campaign completed all 1,013 candidates on 2026-10-02 and failed with two survivors; R3 passed with the expected canary rejection. The survivors and current remediation are recorded below. Fresh R0/R1, R2, and R3 evidence is required for the corrected source.

**Scope:** CLI-EXT-01, CLI-EXT-03 disposition, and CLI-EXT-04
**Decision owner:** cli-extended maintainers and adopting product owners

## Objective

Give adopters a shared, repeatable way to inventory a registered CLI, discover
bounded option-combination cases, retain product-owned semantic decisions,
and prove that each reviewed case has a collected behavioral test. The library
must remove consumer-side parser introspection, Markdown parsing, region merge,
candidate enumeration, and test-reference checking. It must not claim to know
product effects or silently certify parser syntax the built parser does not
expose.

The resulting workflow should let an adopter regenerate and compare its
canonical CLI specification after a grammar change without deleting semantic
decisions, rationale, or test links. The generated Markdown is the readable
view in that specification; a versioned machine-readable record is the
comparison input.

## Decisions in this plan

1. **Keep Python registrations as the executable grammar source.**
   `CliRegistry` already builds argparse, help, and dispatch from the same
   `VerbSpec`, `ArgumentSpec`, and `OptionSpec` declarations. Export that live
   contract. Do not introduce a second full CLI definition in TOML or JSON.
   A full external definition would still need Python handlers and custom
   parser callbacks, would need a substantial migration of existing adopters,
   and would create another runtime boundary whose consistency must be proved.
2. **Make the TOML review catalog the editable source of semantic decisions.**
   It records accepted/refused outcomes, why they are correct, effect
   boundaries, and test references. It is not a second grammar declaration.
   The generated section embedded in the consumer's canonical CLI spec is the
   published, readable semantic specification; TOML is its decision source.
   Parse TOML with Python 3.11's `tomllib`; keep the package's zero-runtime-
   dependency contract.
3. **Generate the human view into a marked region of that canonical CLI spec.**
   The generated region contains the syntax inventory, bounded review
   checklist, semantic decisions, and test references. The library updates
   only that region. The TOML catalog and generated JSON manifest are
   checked-in machine inputs/outputs beside the product spec.
4. **Represent changes as diffs and pending review, never as automatic
   approval.** Stable surface/case IDs and per-case signatures show changes.
   New candidates have no decision and fail check mode. Changed signatures
   require re-review. Removed cases remain in the catalog and fail check mode
   until a human marks them retired with a reason. Generation must never
   delete or approve a semantic record.
5. **Start with bounded structural review dimensions.** Generate a minimum-
   syntax checklist, one case per option spelling, each required-exclusive
   alternative, each choice value, and combinations in explicitly named
   consumer interaction groups. Do not generate the power set of all options.
   These are review dimensions, not executable argv or predicted outcomes.
   Use a documented library candidate limit (initially 512) with an explicit
   per-call override; fail loudly if exceeded and never truncate silently.
   Complex/domain conditions remain product-owned and are named explicitly
   in the catalog.
6. **Keep CLI-EXT-02 open pending a second distinct adopter case.** CMRU has
   presence-based dry-run requirements and refresh conflicts. Netcup's
   monitor-task demonstrates verb scoping and behavior, not the same
   conditional-option need. Netcup also has a monitor/no-monitor conflict
   expressible with existing mutually-exclusive group metadata, so it is not
   evidence for a new conditional API. CLI-EXT-04 must work with current
   `choices`, `required`, and mutually-exclusive metadata; it can also include
   product-declared interaction groups from the review catalog. Do not add
   `OptionSpec.requires`/`conflicts_with` or change handler validation until
   another consumer demonstrates a distinct need and the API composition is
   reviewed.
7. **Keep service-entrypoint behavior documented, not API-backed.** The lazy
   handler pattern and its help/version/error side-effect tests already cover
   CLI-EXT-03. Close that investigation as docs-only; reopen only with a
   concrete adopter that cannot use the existing registry pattern.
8. **Stabilize the existing colour query.** `CliOutput.color_enabled(stream)`
   is already public and used by Netcup. Treat it as supported API, document
   its per-stream behavior and JSON boundary, and add direct contract tests.

## Independent review outcome

A separate reviewer found five implementation risks: semantic source and
published spec ownership were ambiguous; `RegisteredCli` had discarded source
metadata; the selected Netcup validator and other custom parsers could not be
called “complete” under the original plan; generated candidates had no safe
source for real positional values; and current consumer tests were not marked
for the proposed catalog linkage. The plan now names TOML as canonical
editable semantic input and the marked CLI-spec section as its generated
published rendering, retains registry declarations and inspects built parser
actions, separates syntax completeness from opaque validation, generates
symbolic review dimensions instead of invented argv, and labels CMRU/Netcup
test mapping as an adoption example rather than already-verified library
traceability.

The review also found that the original CLI-EXT-02 proposal did not satisfy
its own backlog evidence bar: CMRU is the demonstrated conditional-relation
adopter; Netcup's monitor flags can use existing exclusive-group metadata,
and its monitor-task example is about per-verb scoping. That public API is
deferred. CLI-EXT-04 proceeds independently on the metadata the registry
already exposes.

## Final grammar audit

A final code audit found that `VerbSpec.configure()` can change
`allow_abbrev` on one parser without changing the registry-wide setting. The
first checker draft used the registry value at every depth, which could accept
or refuse an abbreviated option incorrectly. The surface now exports parser
settings at each route depth, includes them in case signatures, and resolves
abbreviations using the owning parser. Since the checker does not model custom
`prefix_chars` or `fromfile_prefix_chars`, those settings make the surface
incomplete and fail check mode.

Reviewing the generated human-readable table against the canonical-spec
contract exposed fields that existed only in JSON: confirmation policy,
parser action/converter details, hidden status, const values, callback and
opaque-field status, and delegated metadata. The Markdown view now includes
those facts so reviewers can judge them from the product spec. A delegated
route records every wrapper in a nested delegation chain and the final
delegated command; wrapper-only parser declarations and inherited globals
absent from the delegate are marked incomplete. The surface schema was raised
to version `3` for route invocation and delegated metadata, and to version
`4` for parser integrity and cross-route interaction metadata. Delegated
behavior/confirmation changes participate in review signatures.
The final route audit also exposed empty-argv behavior: a single-command
registry can either show help before parsing or pass empty argv into its
parser. Required syntax can still make that parse fail. The route table now
shows this behavior for the executable entrypoint and single-command routes.
It describes route prefixes as selecting nested commands, delegated groups as
forwarding remaining tokens to the child CLI, and ordinary command routes as
parsing their remaining tokens. This avoids claiming that a multi-command
route shows help when its parser may dispatch or reject according to required
syntax. Candidate signatures include the actual single-command empty-argv
behavior so a change cannot leave an old semantic decision appearing current.
The inventory also records argparse's negative-number matcher and whether a
parser registers negative-number-like options. The semantic checker consumes
those values when deciding if a signed token is an option value or positional;
custom or uninspectable matchers make the syntax incomplete.
Review tests derive the default matcher from the active Python's argparse
parser and compare value-boundary decisions with `ArgumentParser._parse_optional`,
so a supported Python version's matcher is not assumed to equal another
version's pattern. The checker ignores a custom matcher rather than executing
consumer-supplied regex syntax; that surface remains incomplete and cannot pass
check mode.

The prior registered run against committed `9c14091c` reached
490 of 919 candidate records before Assay's 60-minute hard budget expired; its
`BUDGET_EXCEEDED` artifacts are preserved under
`libraries/cli-extended/.assay/archive/r2-stale-9c14091c-20261001/` and are not
a mutation verdict for the corrected source. At the observed rate, the full
campaign needs about 113 minutes before overhead. The Assay and run-gate
budgets are now 180 minutes each, with a 210-minute combined gate budget.

The pre-follow-up implementation passed registered R0/R1 on `f49fdc13` with
100% statement and branch coverage (3,236 statements and 1,548 branches). The
registered R3 canary also passed on that revision; its expected message is
`canary rejected: JSON redaction test fails when its guard is disabled`.
The first registered R2 run against that snapshot ended at 2026-10-01 03:45 UTC with
`BUDGET_EXCEEDED` (`LANE_TIMEOUT`, Assay exit 4): 909 killed, 48 survived, and
one budget-exceeded candidate out of 958. Its verified R0/R1 results remain
valid for `f49fdc13`, but the R2 outcome is not a pass and predates the current
correctness changes. An initial committed-tree R0/R1 run found that one new
parser case selected a root-level positional candidate instead of the tested
`values` argument; candidate selection now matches by argument ID. Clean
R0/R1 and R3 subsequently passed on `9f7099ef`; the later `4d303f7b` R2
campaign and its survivor follow-up are recorded below.
One R2 survivor changes the interaction occurrence check from `or` to `and`.
A focused regression now supplies every named conflicting option while
repeating one member, so a count mismatch remains visible even when the set of
present IDs is correct, and the regression passes in the current-worktree
R0/R1 gate. The earlier 48 survivors and budget-exceeded candidate remain
diagnostic evidence only; the fresh final-source campaign must be triaged on
its own results.

Correction to the timeout analysis: source inspection shows the caller checks
that the option token's index is in range, then passes `index + 1` to the value
boundary helper. A value-taking option at the end of argv therefore reaches
the `position == len(argv)` guard in `review.py`. The earlier claim that the
caller checks the same bound is incorrect. The optional-const behavior test
places `nargs="?"` at the end of argv and exercises this guard; it passed in
the current-worktree R0/R1 gate. The later registered R2 campaign completed
without timing out; its survivors and the follow-up changes are recorded below.

The prior `f49fdc13` R2 artifact is schema v13, native, unsharded, and has a
complete 958-candidate inventory. Inspection found that its 909 killed records
contain no execution-witness receipts, so `--reuse-from` cannot replay those
kills as work-saving hints; it would rerun the full campaign. The current
registered R2 lane therefore uses a fresh state directory with `--resume` and
a progress log, without `--reuse-from`. Its Assay budget is 180 minutes and
the combined gate budget is 210 minutes.

The implementation review found three additional completeness
gaps, now closed. Inherited delegate globals are compared by their built action
shape, not just spelling, because the wrapper splits leading tokens before the
child parses them. Catalog interactions can now reference an option on another
route; the foreign route and action shape participate in the signature, and
the checker requires every named local option, the foreign unknown option, and
the target route's required baseline syntax. The consumer catalog owns the
expected decision and status; the checker does not infer semantics from route
ownership. Undeclared unknown options fail check mode. It checks declared value
counts and choices for selected options.
Parser callbacks remain supported when they add inspectable argparse actions,
while replaced token-parsing methods,
uncaptured parser-level defaults, or inconsistent option-action maps mark the surface
incomplete. Incompleteness stays within the affected delegate subtree. These
interaction fields change the exported surface contract, so the JSON surface
schema is version `6`; the TOML decision catalog remains schema version `1`.

The implementation review found further false-certification cases beyond the
original plan review. Non-minimum candidates could omit required positional or
option syntax; a valid first occurrence could hide a malformed repeated value;
and delegated globals with identical flags but different mutex-group policy
were treated as equivalent. The generated v6 candidates now sign and check
their route's required baseline, validate arity and choices on every option
occurrence, and compare inherited exclusive-group membership and requiredness.
Custom converter/action objects are compared by runtime identity because equal
labels do not prove equal behavior.

A final review found that omitted optional-option `const` choice handling
cannot be inferred reliably from the Python minor version: available CPython
source and the installed interpreter did not agree. The exporter now probes a
disposable stock parser with representative `None`, `bool`, `float`, `int`,
and `str` constants, records the matching result on `nargs="?"` option actions,
and includes it in generated Markdown and candidate signatures. Positional
`nargs="?"` omission uses its default, not the option's `const`, so those
actions do not inherit the option-only check or incompleteness rule.

The review also found that a deleted route/option referenced by an interaction
made sync abort before it could regenerate the grammar or show the old semantic
row. Direct export remains strict; sync records the broken reference, emits the
current grammar, and keeps the catalog case visible for repair or explicit
retirement. It never rewrites the TOML catalog. Regression coverage was added
for these cases.

A later self-review found a typed-choice false certification: stringifying
choice values could accept `--mode 1` when argparse receives a string but the
registered choices are integers. It also found that option and positional
choice candidates compared their reviewed choice as display text, which could
reject valid built-in conversions. The correction models only exact built-in
`str`, `int`, `float`, and `bool` conversions, leaves consumer converters to
linked behavior tests, recognizes built-ins by exact runtime identity rather
than import label, recognizes stock choice actions by exact class identity,
and marks non-scalar choice objects incomplete rather
than flattening their equality semantics. The same review found that parser
type-registry overrides and custom action classes can change choice handling
while leaving action metadata unchanged. Any non-default type-registry
registration makes the surface incomplete because argparse uses dictionary
equality to resolve a type name, not object identity. Custom choice containers
remain opaque because their membership behavior may differ from their items.
Non-callable type references on value-taking actions also make the surface
incomplete because argparse cannot resolve them without a custom registry.
Custom actions remain opaque to static choice checks, and flag-only `choices`
declarations are incomplete because argparse ignores them.
For `nargs="?"`, an omitted option value resolves to `const`. Argparse applies
the action's converter only when the constant is a string. The exporter probes
choice handling for the action's exact built-in const type and signs the result
into the surface. The review checker
also applies modeled built-in converters to values when no choices are
declared, so known type conversion failures are not certified as valid
invocations. An omitted optional positional uses its default, not `const`.
An additional review found that a string-only probe cannot stand in for a
numeric or `None` constant on runtimes that treat those values differently.
The surface now probes each supported built-in const type separately and uses
the probe for the action's actual type. Rendering also refuses missing route
invocation flags instead of inferring `false` from absent data.
The checker now follows argparse's special choice rules for `PARSER` (first
converted value only) and `REMAINDER` (no choice check); declared remainder
choices make the surface incomplete. Regression cases compare these results
with the built CLI and ensure unsupported or missing const-probe metadata is
treated as opaque.
Regression cases compare review findings with the real CLI. The typed-choice
and surface-completeness corrections are now in the CIU-managed integration
worktree. Current-worktree R0/R1 passed with 100% statement and branch
coverage (3,371 statements and 1,634 branches) at 05:06 UTC. R3 passed on the
same clean revision at 05:06 UTC; the canary reported the expected rejection
after disabling the JSON redaction guard. The later `4d303f7b` R2 outcome and
the follow-up validation status are recorded below.

## R2 follow-up: first complete campaign and survivor remediation

The registered R2 campaign on commit `4d303f7b9654435bfdabd3c71af73b1e8169b5bb`
ran from 2026-10-01 05:08:43 UTC to 06:05:08 UTC and failed with
`MUTANTS_SURVIVED`: 1,005 killed, 11 survived, and zero crashed, timed out, or
were classified equivalent out of 1,016 candidates. The exact verdict,
progress stream, run-gate history, and mutation state were copied before
follow-up work to
`libraries/cli-extended/.assay/archive/r2-4d303f7b-before-survivor-fixes-20261001/`.
The surviving mutations were reviewed and grouped as follows:

| Survivor group | Review finding | Follow-up in the worktree |
| --- | --- | --- |
| `review.py:1069` | The explicit `end < len(argv)` check duplicated the EOF behavior of `is_option_boundary(end)`, so removing it preserved behavior. | Replaced the boundary-driven unbounded loop with a finite range; added end-of-argv cases for `nargs="*"` and `nargs="+"`. |
| `review.py:1488,1492` (four candidates) | Count and shortage diagnostics repeated overlapping predicates after parser positioning had already constrained the values. | Added `_value_count_problem()` to classify shortage, wrong count, or valid count once; tests assert each classification and the existing invocation diagnostics retain shortage-specific messages. |
| `review.py:1602` | The unknown-required-option check had a negative test but no valid required-option reference, so an inverted kind comparison survived. | Made the fixture option required and added a positive known-required-option case beside unknown references. |
| `surface.py:72` | `add_help=False` on a disposable parser was irrelevant to `_get_values()`. | Removed the no-op setting. |
| `surface.py:85` | The probe's `True` literal was only inspected by exact type, and either boolean value was rejected by the empty choice set. | Expressed the representative boolean probe as `bool(1)` and documented that only exact type matters. |
| `surface.py:194,218` (two candidates) | Combined short-circuit predicates obscured the two distinct normalization paths. | Split callable checks from label/action lookup and added coverage for callable values outside the `action` kwarg. |
| `surface.py:1257` | The fallback surface test checked global incompleteness but not that every route was incomplete without retained registrations. | Asserted incompleteness for every fallback route. |
| `surface.py` non-callable type path | No test showed that a non-callable type reference is preserved while refusing to certify syntax. | Added coverage for a numeric type reference; it remains visible in the surface and marks syntax incomplete. |

The first survivor follow-up passed registered R0/R1 and R3, but its fresh R2
campaign exposed a further termination issue, described below. Do not treat
either earlier R2 result as a pass.

### R2 campaign on `0b0dbecd` and bounded-scan correction

The registered R2 campaign on commit `0b0dbecde671d04884c3d74f8314eb42777f6d9f`
ran from 2026-10-01 13:08:51 UTC to 14:18:35 UTC. It inventoried all 1,007
candidates: 1,006 were killed and one was budget-exceeded; none survived,
crashed, or were classified equivalent. The verdict is
`BUDGET_EXCEEDED` with reason `LANE_TIMEOUT`; the run-gate container completed
the campaign and saved its receipt and progress stream under
`libraries/cli-extended/.assay/`.

The sole budget-exceeded candidate was `True->False` at `review.py:1043`
(`candidate_id` `8f577cd36549b95b56053a55c3d2d9e8f8861ae425908776ef7f98b58b2c9`).
It changed the EOF/terminator branch in `is_option_boundary()`. With the
boolean literal flipped, the `nargs="*"` scan kept incrementing after argv
ended, so the test process stayed CPU-active and exceeded Assay's derived
66.018798-second per-candidate budget. The process was not idle; this is why
Assay recorded `budget_exceeded`, not `hung`.

The current follow-up derives EOF-boundary truth from `position >= len(argv)`
and scans `nargs="*"`/`nargs="+"` with a finite `range` ending at `len(argv)`.
The delimiter lexer case also covers `--` inside a greedy option scan. These
changes prevent a false boundary result from making the checker run forever.
The bounded-scan source passed fresh registered R0/R1 with 100% statement and
branch coverage (3,378 statements and 1,642 branches). Fresh R2 and R3 gates
must pass before CLI-EXT-04 is complete.

The generator deliberately does not enumerate every optional value count or
repeat count. Consumers declare separate named interactions for token shapes
that have distinct product meaning, and their behavior tests assert exact
cardinality. This keeps candidate generation bounded and makes each added
semantic case explicit in the consumer's catalog and canonical spec.

### R2 campaign on `77d13912` and survivor triage

The resumed registered R2 campaign on commit `77d139123f31e3bbdcd4b3ade27f065a4d7fa927`
completed on 2026-10-02 with `MUTANTS_SURVIVED`: 1,011 killed, two survived,
and zero crashed, timed out, or exceeded budget out of 1,013 candidates. The
R3 canary then passed with the expected rejection after disabling the JSON
redaction guard. The verdict and progress stream are
`libraries/cli-extended/.assay/verdict-r2-choice-semantics.json` and
`libraries/cli-extended/.assay/progress-r2-choice-semantics.jsonl`; wrapper
logs are `/tmp/cli-extended-review-r2-resumed.log` and
`/tmp/cli-extended-review-r3-after-resumed-r2.log`.

| Survivor | Review finding | Follow-up |
| --- | --- | --- |
| `review.py:1045`, `True->False` | At EOF, both the original and mutated boundary check made the caller's cursor land beyond `len(argv)`, so the final findings did not change. The prior `try/except IndexError` obscured that the check is a sequence-length boundary. | Compare `position` with `len(argv)` before indexing; the existing missing-value case exercises EOF and now protects against an out-of-range access. |
| `surface_cli.py:36`, `strict=True->False` | No test distinguished a missing script from an existing directory. Strict resolution intentionally gives a missing path its `FileNotFoundError`; an existing directory instead reaches the explicit “is not a file” validation. | Add a focused missing-script regression test to preserve the distinct, accurate filesystem error. |

The remediation is in progress. Do not treat the `77d13912` R2 result as a
pass; archive its completed state before starting a fresh R2 run for the
corrected commit.

## Consumer workflows this should support

### CMRU: constraint-heavy command with mutation boundaries

`cmru tool-deps` currently has three important syntactic interactions:

| Candidate | Expected result | Current evidence |
| --- | --- | --- |
| `--dry-run` without `--refresh` | Refuse before configuration/network work | The refusal assertion in `test_tool_dependency_refresh_dry_run_does_not_write_pin_files` |
| `--dry-run --refresh PROVIDER` | Accept and preview the planned pin refresh without writes | That test covers the refresh function directly; a parser-level `cli.main` case for this exact invocation still needs to be added when CMRU adopts the semantic catalog |
| `--refresh PROVIDER --json` | Refuse; refresh is not a report mode | `test_tool_deps_refresh_rejects_output_and_freshness_flags` |
| `--refresh PROVIDER --allow-stale-tool-deps` | Refuse; the freshness override applies to verification, not refresh | `test_tool_deps_refresh_rejects_output_and_freshness_flags` |

Today the registrations describe the flags, but `_run_tool_deps` hand-checks
these combinations. The review catalog can name those interactions and the
generator can retain the cases, rationale, and test links in CMRU's canonical
`docs/SPEC.md` section S-CLI.9. The consumer continues to own and test the
current handler refusals and prove that dry-run performs the documented reads
and no writes. CLI-EXT-02 would move simple presence rules to the registry
only after a second, distinct adopter need is established.

CMRU also has commands such as `cleanup`, where several modes and `--yes` /
`--dry-run` define distinct effects. Those cases belong in explicit interaction
groups and retain consumer-authored action-plan tests; a syntax generator must
never infer that a command has a safe or complete dry-run.

### Netcup `monitor-task`: distinct verbs and output/effect behavior

`monitor-task show TASK_UUID [--json]` fetches once; `watch TASK_UUID
[--poll SECONDS]` polls to a terminal state. The existing contract test already
checks that `show --poll` is rejected. The review catalog can link that case to
the real executable contract test, while separate marked cases link `show
--json` to redaction assertions and `watch --poll` to validation-before-auth
and polling behavior. Its `_task_uuid` callable is reported as an opaque
validator attached to an otherwise visible positional shape. The syntax
inventory remains complete; accepted UUID values and refusal timing remain
semantic test obligations. This is the Netcup pilot. `scp-api.py` has many
custom parser callbacks, so it can use the exporter only after its generated
surface and opaque portions receive their own review; this plan does not claim
that every Netcup command surface is already reviewed.

These examples demonstrate the split of responsibility: cli-extended produces
syntax and a checklist; the product owner states what each invocation means,
which effects are allowed, and which behavior test proves it.

## Proposed library contract

### Surface export

Add a public, versioned surface representation and deterministic JSON renderer
in a focused module (proposed `cli_extended.surface`). It must be obtainable
from the `RegisteredCli` returned by the existing `build_cli()` pattern, so a
consumer does not need to retain a second hand-built list of registrations.
`RegisteredCli` must retain an immutable snapshot of originating registry
metadata, including the single-command case where there is no `HelpCatalog`.
The exporter traverses delegates recursively and inspects each built argparse
tree after parser callbacks have run. Retained declarations supply product
labels and IDs; parser actions supply installed syntax, including callback-
added argparse arguments. Emit:

- executable identity and invocation path, including root options accepted
  before and after a verb;
- parser settings at each path depth, including `allow_abbrev`,
  `prefix_chars`, and `fromfile_prefix_chars`;
- verb ID/name, description, semantic group, behavior/confirmation labels,
  and delegated-command path;
- positional name, display shape, requiredness, `nargs`, choices, default,
  and scope;
- option IDs, all spellings/aliases, canonical spelling, value/action shape,
  requiredness, choices/default, help group, exclusive group, and scope
  (global, common, or verb-local);
- declared constraints and whether custom parser configuration ran;
- syntax completeness separately from opaque validation/behavior markers.

JSON serialization is deterministic (stable key order, stable entry order,
normalized scalar values). Callable parser types/actions/default objects are
reported by a stable import label where possible and marked opaque; they are
never dropped or stringified with a memory-address-bearing `repr`. A custom
`configure` callback's ordinary argparse actions are visible in the built
parser and included in the inventory. An action or parser mutation that cannot
be inspected is explicitly incomplete; check mode refuses to certify that
syntax as inventoried. A callable type such as Netcup's `_task_uuid` is
recorded as an opaque validator, not omitted syntax. It remains an inventoried
surface whose semantic cases must link tests that establish accepted values
and the failure boundary.

IDs default to `entrypoint:<command-name>`, `verb:<parent-route>/<verb>` or
`arg|option:<route>/<canonical-name>`. Combination IDs use the route, case
kind, and sorted participating option IDs; aliases get distinct candidate IDs
so each spelling is considered. Add optional explicit `surface_id` fields to
verb/argument/option declarations for cases where identity must survive a
rename. Validate ID uniqueness. Descriptions and rendered Markdown are not
identifiers. A rename without an explicit continuity ID appears as one stale
entry and one new entry, which is safer than silently joining unrelated
decisions.

Case signatures hash the generator schema version plus only the relevant
surface fields: route and IDs, positional/option shape, aliases, action and
`nargs`, requiredness, defaults, choices, option scope/placement, exclusive
group, and catalog-declared interaction members. They exclude help wording so
copy edits do not require semantic re-approval. Include a custom type/action's
import label in the signature; implementation changes inside that callable
remain test and code-review concerns. An alias change changes the relevant
signature and requires review; a removed alias case remains stale until
disposition.

### Conditional option relationships

CLI-EXT-02 remains open during this implementation. The generator uses
relationships already represented in the registry (`choices`, `required`, and
mutually-exclusive groups) and combinations explicitly declared in the
product-owned review catalog. It does not add runtime `requires` or
`conflicts_with` fields and does not move CMRU handler validation into the
shared parser. The backlog's evidence bar is a second, distinct consumer need
for the same new API; a per-command mutually-exclusive group in Netcup is
already expressible by today's `OptionSpec` and does not meet that bar by
itself.

For CMRU `tool-deps`, the product catalog explicitly lists the relevant
combinations: `--dry-run` alone, `--dry-run` with `--refresh`, and `--refresh`
with each rejected output/freshness flag. The helper gives these cases stable
IDs, re-renders their rationale and tests, and reports changed grammar for
re-judgment. CMRU continues to enforce and test their runtime behavior until a
later approved CLI-EXT-02 change.

### Review catalog and generated artifacts

Use a TOML catalog with a schema version, declared option-interaction groups,
and semantic case records. Each active record has:

- stable `case_id` matching a generated review dimension;
- concrete, consumer-validated argv examples or a fixture reference when the
  case needs values the parser cannot derive (for example a provider name or
  UUID); the generator never invents these values;
- expected parser/CLI outcome (`accept` or `refuse`);
- product-owned reason, CLI status, output/diagnostic assertions, and
  filesystem/state/network/credential effect expectations;
- the reviewed candidate signature;
- one or more pytest node IDs, paired with a `pytest.mark.cli_case(case_id)`
  marker on the behavior test.

Each catalog also declares interaction sets by stable option ID; the
generator makes only the specified combinations from these sets. Validate the
schema version, unique IDs, field types, route/option references, legal
decision/status values, and required content. An active record requires a
rationale, current reviewed signature, expected outcome/effects, and at least
one test reference. A retired record remains present with a human-written
retirement reason. If a retired ID reappears, it stays retired until a human
reactivates it and records a fresh review.

The catalog is never synthesized by the grammar exporter and never
overwritten by sync. Provide a template renderer for new/missing candidate
records so adopters do not write serializer glue. TOML remains authored by the
product team; a helper reports exactly which records to add or re-review.

The argv sanity check recognizes options only at their declared parser depth,
skips their declared values, and respects `--` at each parser level. It counts
required options and their values in minimum cases only when the option token
is active, and requires exactly one alternative from each required exclusive
group. Parent positional lexing accounts for required values and remainder
positionals before nested commands. Positional choice and shape candidates
must receive their values in the registered action at that parser depth; a
sibling positional with the same value does not satisfy the row. A flag-only
option with an inline value is not a valid spelling. The referenced product
test remains the oracle for full parser acceptance and behavior.

The generated manifest stores current grammar. The consumer's spec uses a
pair of documented markers around the generated Markdown block. Sync writes
the manifest and replaces only that block. Check recomputes the same bytes and
reports stale manifest, stale generated block, missing candidate decisions,
signature mismatches, unresolved removals, missing test references, and
incomplete parser syntax as separate actionable findings. Check validates
that test IDs are present in the catalog; a separate pytest collection helper
proves those exact IDs exist and carry the case marker. Sync/check must not
normalize or rewrite text outside the owned block.

Expose simple library calls rather than forcing consumers to implement a
generator command. The initial public names are:

```python
app = build_cli()
surface = export_cli_surface(app)
sync_cli_surface(app, review_path=review_path, manifest_path=manifest_path, spec_path=spec_path)
check_cli_surface(app, review_path=review_path, manifest_path=manifest_path, spec_path=spec_path)
assert_cli_case_tests(request.session.items, review_catalog)
```

`export_cli_surface()` supplies deterministic manifest data, candidate IDs,
and signatures. `sync_cli_surface()` writes the manifest and the marked
Markdown block, but never the TOML catalog. `check_cli_surface()` performs the
same render in memory and returns actionable findings without writing.
`render_cli_review_template()` renders missing catalog entries for copy into
the TOML file without rewriting its comments or existing semantic records.
Keep the package free of mandatory runtime dependencies.

### Test traceability

Provide a small shared assertion that receives collected pytest items and the
loaded review catalog. It verifies that every active case names at least one
collected node ID, that the referenced node exists, and that each referenced
test carries the matching `cli_case` marker. It also rejects markers for
unknown cases and duplicate/empty case IDs. Parametrized cases require exact
collected node IDs in the catalog; dynamically skipped tests are not proof of
executed behavior. This check proves collection/linkage only, not test strength
or a passing result. The consumer test must call the registered parser or
actual executable and assert the expected result/effects, and the normal gate
must run it. Existing CMRU and Netcup tests are workflow evidence but are not
marked today; migrating their product specs/catalogs/tests is a separate
adopter change. Library tests exercise traceability with synthetic collected
items, and consumer docs show the annotations to add.

### Change lifecycle

1. The adopter changes `CliRegistry` declarations or a custom parser callback.
2. `sync_cli_surface()` updates the generated JSON manifest and the marked
   section in the canonical product spec. The catalog is read only. New
   review dimensions appear as unreviewed, changed signatures appear as
   `REVIEW REQUIRED`, and removed catalog entries remain visible as stale.
3. The product reviewer compares the grammar diff with the intended workflow,
   decides accepted/refused behavior and effect boundaries, fills a real argv
   or test fixture where needed, updates the reviewed signature, and either
   adds or revises the behavior test and case marker.
4. The adopter runs the spec check plus the shared pytest coverage assertion.
   These fail on unreviewed dimensions, stale decisions without retirement,
   missing exact test node IDs, or missing/mismatched case markers. The normal
   project test gate must then execute and pass those behavior tests.
5. Removed decisions stay in TOML and the rendered spec with their prior
   rationale/test evidence until a reviewer explicitly marks each retired and
   supplies a reason. Generation never removes them.

## Implementation sequence

1. **Resolve the independent review.** Correct unsafe assumptions in this
   plan before implementation; capture any deferred product choice as an open
   backlog item with the evidence needed, not as an implementation blocker.
2. **Add surface export and bounded review dimensions.** Preserve delegate
   routes, parser scopes/placement, defaults/choices/aliases, synopsis data,
   deterministic IDs and signatures, and inspect built parser actions added by
   callbacks. Mark nonportable parser behavior as opaque or incomplete.
3. **Add the semantic review catalog model.** Parse versioned TOML, validate
   cases/interaction groups/test references, compare signatures, preserve
   removed decisions, and render missing-record templates without writing the
   catalog.
4. **Add safe spec sync/check and test traceability helpers.** Test first
   generation, repeat generation, outside-region byte preservation, grammar
   change, new/changed/removed cases, explicit retirement, missing/stale test
   references, and opaque syntax.
5. **Resolve existing backlog items.** Document/test the color query (01),
   leave CLI-EXT-02 open with the identified second-consumer evidence gap,
   close the lazy service investigation as documentation-only (03), and mark
   the manifest/checklist feature complete only when its gates are present
   (04).
6. **Update user-facing library docs.** README states what helper capability
   exists; DESIGN-GUIDE records why registrations stay Python-owned and why
   the sidecar/Markdown boundary was chosen; CONSUMERS gives a pasteable sync,
   check, and pytest-traceability recipe with CMRU and Netcup examples. Include
   the public colour API and current parser-constraint limits. Keep this plan
   as the review/implementation record and link to the stable design-guide
   section.
7. **Run the library's registered gates.** From `libraries/cli-extended`, use
   `./run-gate.py gate`; its committed symlink invokes the shared
   `run-gate-project` runner and its declared `gate` lane serially runs
   `r0-r1`, `r2`, and `r3`. Keep all test, mutation, and canary work in
   `tester-unified`. The runner reads the lane resources and the
   orchestration-provided gates cgroup; the R2 command installs Assay from the
   selected worktree and stores resume/progress files under `.assay/`. Do not
   run a cockpit pytest command as gate evidence.

## Required behavioral oracles

- Surface output includes nested delegated commands and callback-added
  argparse actions, aliases, option scope/placement, positionals,
  choices/defaults, existing required/exclusive metadata, and actual common
  options. Its Markdown view exposes route behavior/confirmation and
  delegated metadata; option/action rows include descriptions, parser
  action/type/const, defaults, exclusive-group requiredness, hidden status,
  opaque fields, and invocation/empty-argv behavior; the latter is signature-
  sensitive. Parser-specific negative-number handling is shown and included in
  signatures. Single-command built-ins match the forms accepted by
  that parser. Delegated single-command signatures cover child behavior and
  confirmation metadata. It includes parser-scoped abbreviation policy in signatures and
  marks custom option prefixes or argument-file expansion incomplete. Output
  is byte-stable across repeat runs. Nonserializable values
  and custom action behavior are marked opaque or incomplete, never omitted.
- Candidate count is bounded by a documented default maximum of 512 (with an
  explicit per-call override) and fails without truncation when exceeded.
  Required/exclusive alternatives, individual
  option spellings, choice values, and catalog-declared interactions appear
  with stable IDs. Candidates do not become guessed executable argv and do not
  predict outcomes.
- Surface IDs and signatures follow the documented derivation. Changing a
  description leaves signatures unchanged; changing an alias, default,
  requiredness, choice, action/nargs, scope/placement, confirmation policy,
  delegated behavior/confirmation, synopsis override, or interaction
  signature forces review. A rename with no continuity ID yields
  a stale old ID plus a new ID.
- Sync preserves all bytes outside the generated region and never alters the
  review catalog. Sync/check reject aliased paths among the catalog, manifest,
  and spec, including symlink and hard-link aliases. Check passes on an unchanged tree and fails independently
  for changed grammar, missing decisions, changed signatures, unresolved stale
  cases, invalid marked tests, and uninspectable parser syntax. Opaque custom
  value validators require linked semantic cases but do not make syntax
  incomplete.
- Route sanity checks agree with argparse for scoped option values, `--`
  terminators, required parent positionals, and a nested route after a
  `REMAINDER` positional. Positional candidate values are assigned to the
  registered action at the matching parser depth, and flag-only options reject
  inline values. Long-option abbreviations follow the `allow_abbrev` value for
  each parser depth. Non-default `prefix_chars` and enabled
  `fromfile_prefix_chars` make the inventory incomplete. They also recognize
  options on a single-command entrypoint with an empty route path. They do not
  execute parser callbacks.
- Marker parsing fails if either boundary is absent, duplicated, reversed, or
  nested; repeated sync is idempotent. TOML comments and all catalog fields
  remain unchanged during sync.
- The test traceability helper detects missing collected node IDs, missing
  case markers, unknown markers, unsupported parametrized references, and
  statically skipped tests. It reports node IDs discovered for each case but
  claims only collection/linkage, not pass status.
- CMRU and Netcup documentation describes real cases/tests as adoption
  examples, without claiming the new catalog or markers already exist in
  those consumers.
- `CliOutput.color_enabled(stdout)` and `.color_enabled(stderr)` follow the
  explicit color switches, TTY detection, and `NO_COLOR`; JSON output remains
  ANSI-free.

## Files expected to change

- `libraries/cli-extended/src/cli_extended/parser.py`
- new `libraries/cli-extended/src/cli_extended/surface.py` and, if needed,
  `review.py` (keep parsing/rendering responsibilities small and distinct)
- `libraries/cli-extended/src/cli_extended/__init__.py`
- `libraries/cli-extended/SPEC.md`
- focused tests for surface export/reconciliation, test traceability, and
  color API
- `libraries/cli-extended/BACKLOG.md`
- `libraries/cli-extended/README.md`
- `libraries/cli-extended/docs/DESIGN-GUIDE.md`
- `libraries/cli-extended/docs/CONSUMERS.md`
- this implementation plan

Consumer product specs are examples and future adoption targets; migrating
CMRU or Netcup in this package change is out of scope. Their real cases/tests
must be used as evidence for the library contract and documented adoption path.

## Risks and limits

- The CLI surface has dynamic escape hatches. The exporter must be conservative:
  incomplete is a useful result; a false complete inventory is not.
- Signature scope is critical. Include only syntax and declared constraint
  fields that affect a candidate, not help wording, so prose edits do not force
  semantic re-approval. Conversely, changes to defaults, choices, aliases,
  requiredness, scope, and relationships must change the relevant signature.
- An existing test may cover several candidates. Marker IDs keep traceability
  many-to-many; avoid forcing one test function per option.
- Test-node identity can change during refactoring. Marker identity is the
  semantic key; TOML references should be updated with the test rename and
  check mode must name missing references clearly.
- The generator is a docs tool, not the product's semantic authority. It must
  never infer effect safety, expected status, or accepted combinations from
  option names or descriptions.
- This work does not migrate either consumer to a full external parser
  definition. Revisit that only if a concrete use case demonstrates that a
  runtime registry plus semantic catalog cannot provide one source of truth.
