# CLI surface and semantic review tooling plan

**Status:** Implemented; final registered R2/R3 gate evidence pending

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

## Consumer workflows this should support

### CMRU: constraint-heavy command with mutation boundaries

`cmru tool-deps` currently has three important syntactic interactions:

| Candidate | Expected result | Current evidence |
| --- | --- | --- |
| `--dry-run` without `--refresh` | Refuse before configuration/network work | `test_tool_deps_dry_run_requires_refresh_and_renders_usage` |
| `--dry-run --refresh PROVIDER` | Accept and preview the planned pin refresh without writes | Tool-dependency dry-run behavior tests |
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
7. **Run the library's registered gates.** Use `./run-gate.py gate`; the gate
   includes 100% statement/branch coverage, the assay R2 campaign, and R3
   canary. Preserve assay progress/resume artifacts under `.assay/`. Do not
   treat a local venv result as gate evidence.

## Required behavioral oracles

- Surface output includes nested delegated commands and callback-added
  argparse actions, aliases, option scope/placement, positionals,
  choices/defaults, existing required/exclusive metadata, and actual common
  options. Output is byte-stable across repeat runs. Nonserializable values
  and custom action behavior are marked opaque or incomplete, never omitted.
- Candidate count is bounded by a documented default maximum of 512 (with an
  explicit per-call override) and fails without truncation when exceeded.
  Required/exclusive alternatives, individual
  option spellings, choice values, and catalog-declared interactions appear
  with stable IDs. Candidates do not become guessed executable argv and do not
  predict outcomes.
- Surface IDs and signatures follow the documented derivation. Changing a
  description leaves signatures unchanged; changing an alias, default,
  requiredness, choice, action/nargs, scope/placement, synopsis override, or
  interaction signature forces review. A rename with no continuity ID yields
  a stale old ID plus a new ID.
- Sync preserves all bytes outside the generated region and never alters the
  review catalog. Check passes on an unchanged tree and fails independently
  for changed grammar, missing decisions, changed signatures, unresolved stale
  cases, invalid marked tests, and uninspectable parser syntax. Opaque custom
  value validators require linked semantic cases but do not make syntax
  incomplete.
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
