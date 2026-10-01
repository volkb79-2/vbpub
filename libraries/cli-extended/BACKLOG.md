# cli-extended backlog

Follow-up decisions for the shared CLI contract layer. Open items need more
evidence; closed items retain their resolution for future maintainers.

## CLI-EXT-01 — expose the shared colour policy to consumer renderers

**Status:** Closed — supported API
**Type:** Feature  
**Area:** Output and terminal policy

`CliOutput.color_enabled(stream)` already resolves explicit `--color` /
`--no-color`, TTY state, and `NO_COLOR`. The documented contract currently
focuses on colour applied by `app.run()` to help, diagnostics, hints, and
progress. A CLI that owns a styled primary result (for example, a Rich or
Pygments renderer on stdout) has no documented contract for asking the shared
layer whether that destination should receive ANSI, so consumers can duplicate
the policy or rely on a method that is not named in the consumer guidance.

`CliOutput.color_enabled(stream)` is the supported public API. The consumer
guide shows how to pass it to a primary renderer, distinguishes stdout from
stderr, and requires consumers to keep JSON plain even when color is forced.
Direct contract coverage exercises stream-specific TTY state, `NO_COLOR`, and
explicit switches.

**Resolution:** No new wrapper API was needed; the implementation already
existed and only needed a clear contract and direct tests.

**Provenance:** nyxloom's session extractor uses Rich and Pygments for its own
primary stdout rendering and currently implements terminal detection in its
CLI; this surfaced the adoption gap while reviewing whether it could reuse
cli-extended's existing TTY/`NO_COLOR` policy.

## CLI-EXT-02 — evaluate declarative conditional option constraints

**Status:** Open — evidence gap

**Type:** Feature investigation

**Area:** Grammar metadata and validation

The registry can express choices, required values, and required or optional
mutually exclusive groups. A consumer may still need rules such as “`--dry-run`
requires `--update`/`--write`/`--refresh`” or “this option applies only when a
particular mode is selected.” CMRU's adoption review found several such
relationships implemented as post-parse handler checks. The semantic review
catalog can inventory and test these combinations, but it does not declare
parser-time dependencies or conflicts.

Evaluate a structured way to declare simple option dependencies and
forbidden combinations so the registry can reject them consistently and
reflect them in generated help/reference metadata. Keep rules that depend on
loaded product configuration, runtime state, or domain data in the consumer.
Current evidence is insufficient for a general conditional-constraint API.
CMRU needs selector-gated dry-run/refresh and output-mode refusals. Netcup's
monitor/no-monitor pair is already expressible as a mutually exclusive group;
`show --poll` is naturally scoped to `watch`, so neither supplies a second
adopter for the same new relationship. Reopen when another consumer
demonstrates a distinct condition that cannot be expressed with current
metadata and handler validation. Any proposal must define composition with
delegates, globals, callbacks, and synopsis generation. The consumer owns the
reason and user-facing remedy.

**Provenance:** CMRU's semantic audit found selector-gated dry-run options and
mode-dependent helper inputs; see
[`CMRU S-CLI.9`](../../cmru/docs/SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit).

## CLI-EXT-03 — assess a lazy service-entrypoint pattern

**Status:** Closed — documentation pattern

**Type:** Feature investigation

**Area:** Entrypoint lifecycle

A daemon CLI may intentionally start its service on a valid no-argument
invocation, while `--help`, `--version`, and malformed arguments must remain
side-effect free. The current registry accepts callable handlers, so consumers
can keep service imports inside a lightweight handler and use
`single_command=True, no_args_action=True`. Nyxloom's `nyxloomd --help` was
initially handled after daemon startup and needed a pre-runtime argument path.

The existing lazy-handler pattern is documented: construct the registry
without starting the service and import/start the service inside the selected
handler. `single_command=True, no_args_action=True` supports a deliberate
no-argument action while preserving side-effect-free help/version/errors. No
shared lazy-handler API is needed unless a real adopter cannot use this pattern.

**Resolution:** Keep daemon lifecycle, credentials, and state ownership in the
consumer; test the installed entrypoint with a startup sentinel.

**Provenance:** Nyxloom P114 found and fixed service startup before argument
handling; see
[Nyxloom command ownership guidance](../../nyxloom/docs/CLI-REFERENCE.md#command-ownership).

## CLI-EXT-04 — export a stable CLI surface and semantic-review checklist

**Status:** Consumer pilot and correctness follow-up in progress. On the reviewed snapshot `bf8a0e43` (2026-10-01), registered R0/R1 passed, R3 passed with its expected canary rejection, and R2 failed with 1,004 killed plus one surviving `GtE->Gt` candidate out of 1,005. Inspection showed that at `position == len(argv)` the mutation moves the internal cursor past EOF, but the consumed slice and all review findings remain identical; this was an observationally equivalent comparison. The current work replaces that redundant length comparison with sequence indexing and an `IndexError` EOF boundary. Fresh gates for this follow-up are required before closure.

Self-review found false certifications in typed-choice checking and choice
surface export. The correction models exact built-in conversions, marks
non-default type registries and non-callable type references incomplete, and
refuses to flatten custom choice containers, scalar subclasses, custom
actions, flag-only choices, and non-scalar optional-value constants. For
omitted `nargs="?"` option values, it probes the stock argparse runtime's
choices behavior for representative `None`, `bool`, `float`, `int`, and
`str` constants and records the matching result in the generated surface and
signatures. It keeps positional omission on the
separate default-value path. The checker reports failures from modeled
built-in conversions even when choices are absent. An earlier R2 run on
`f49fdc13` ended with 909 killed, 48 survived, and one budget-exceeded
candidate; those results are not for the current source. The `4d303f7b`
campaign killed 1,005 mutants and left 11 survivors. Its follow-up commit
`0b0dbecd` passed R0/R1 and R3, then ran all 1,007 R2 candidates: 1,006 were
killed and one budget-exceeded after a greedy scan continued past EOF under a
mutated predicate. That code now uses a finite range. Details and the next
required gates are in `docs/CLI-SURFACE-IMPLEMENTATION-PLAN.md`.
The same review found that a string-only `const` probe could not safely model
numeric or `None` constants on runtimes that treat those values differently.
The surface now probes each supported built-in const type, and Markdown
rendering refuses missing route invocation flags instead of defaulting them to
`false`. The checker also models `argparse.PARSER` choice validation on its
first converted token, and it converts `argparse.REMAINDER` values without
claiming ignored `choices` are enforced; remainder choices mark the surface
incomplete.
The earlier corrections are in the CIU-managed integration worktree. Before the
survivor follow-up, clean R0/R1 passed on `4d303f7b` at 05:08 UTC with 100%
statement and branch coverage (3,371 statements and 1,634 branches). R3 passed
on that revision with its expected canary rejection. An earlier committed
R0/R1 attempt revealed a parser-case test that selected the wrong argument; it
now selects the target argument by ID. The first survivor follow-up passed
registered R0/R1 with 100% statement and branch coverage (3,378 statements and
1,640 branches), and R3 passed. The bounded-scan follow-up passed registered
R0/R1 with 100% statement and branch coverage (3,378 statements and 1,642
branches). That gate evidence is historical and does not cover the current
consumer-pilot changes. Fresh gates for the current review worktree are still
required.

**Type:** Feature

**Area:** Registry introspection and consumer contract testing

`CliRegistry` remains the executable grammar source. The implementation adds a
versioned JSON surface exporter, bounded candidate generation, and consumer
helpers for a TOML semantic catalog, a marked-region Markdown renderer/sync,
read-only drift checking, review templates, and pytest node/marker linkage.
It includes delegated registries and callback-added argparse actions. Nested
routes carry parent-parser actions forward and identify parser-depth placement;
required-subcommand prefixes do not produce false executable candidates.
Inherited globals on delegated CLIs are compared by built action shape,
including arity, value rules, and mutex-group policy, so matching spelling
cannot hide a wrapper and child parser mismatch. A delegated mismatch marks that subtree incomplete
without making unrelated sibling routes incomplete. Ordinary callback-added
argparse actions are inventoried; parser-method overrides, uncaptured
parser-level defaults, or inconsistent option lookup maps make the surface
incomplete rather than claiming the inventory is complete. Parser defaults
already represented by built action defaults remain part of the manifest and
do not make the surface incomplete.
Parser-scoped `allow_abbrev` is exported, included in candidate signatures, and
used when checking the invocation at each parser depth. Entrypoint
empty-argv behavior and single-command route behavior are visible and
signature-sensitive. Route prefixes and delegated groups have distinct
invocation descriptions; ordinary command routes are described as parsing the
remaining tokens, without assuming they display help when that remainder is
empty. Required syntax may still reject the parse.
Argparse's runtime negative-number matcher and negative-number-like options are
exported per parser and used to distinguish signed values from options; the
review lexer tests compare this boundary with argparse on the running Python
version. Custom or uninspectable matchers make the inventory incomplete.
Unsupported custom `prefix_chars` and `fromfile_prefix_chars` do too.

The generated checklist includes minimum valid syntax, positional shapes and
enumerable values, option spellings and choices, exclusive alternatives and
conflicts, nested aliases, and each explicitly declared option interaction,
including options registered on another route. Cross-route signatures include
the foreign route and action shape, and the invocation check requires the
foreign option with its declared value shape and valid required syntax for the
target route. Local options named by a combination must also be present. It is
symbolic:
adopters supply real invocation argv, outcomes, effects,
rationale, and test node IDs. Generation never rewrites the TOML catalog or
deletes stale decisions. Sync owns only a marked region in the canonical CLI
spec and a JSON manifest; check detects changed signatures, stale files,
pending decisions, stale records, and syntax that cannot be inventoried. The
shared pytest helper checks exact collection and marker linkage; the consumer's
gate still proves test behavior. Sync/check reject paths that alias the review
catalog, manifest, or spec so a destination typo cannot overwrite the semantic
source.

Optional-value counts and option repetition are not automatically enumerated.
Consumers declare separate named interactions for such forms when they have
distinct meaning, and their behavioral tests assert exact values and counts.
Check mode validates every declared occurrence's arity and choices and requires
the route's required syntax baseline for every candidate. When an interaction
reference becomes stale, sync still regenerates the grammar and displays the
old semantic row for repair or retirement without editing the decision catalog.
This keeps the candidate list bounded and the product decision visible.

No second grammar DSL or conditional-option runtime API was added. CLI-EXT-02
remains open because that is a separate evidence-gated question.

**Provenance:** CMRU's S-CLI.9 was a manually maintained grammar inventory and
semantic result table. Independent review of the plan clarified that Python
registrations should stay authoritative and generated Markdown should be a
view over retained semantic decisions, not a replacement for them.

### Independent review disposition — 2026-10-01

The Sol xhigh review found that no consumer had adopted the CLI-EXT-04 catalog
lifecycle yet, even though CMRU and Netcup already use the registry/parser
layer. This follow-up pilots the complete lifecycle on Netcup's real,
hyphenated `monitor-task.py`: its canonical `CLI-SPEC.md`, editable
`cli-review.toml`, generated `cli-surface.json`, collection-time case linkage,
and behavioral tests are checked together. The pilot explicitly records
cross-verb refusals, opaque UUID validation, JSON redaction and its raw opt-out,
poll value constraints, and repeated-option behavior. The shared marker helper
only proves exact collection/linkage; the Netcup gate still runs the tests that
assert effects and output.

The pilot exposed a consumer-side adoption seam: a hyphenated script cannot be
named as `python.module:callable`. `surface_cli --factory` now also accepts
`path/to/file.py:callable`, loads sibling imports as direct script execution
does, registers the module before execution for dataclass introspection, and
uses a stable module name so checkout-root paths do not churn manifest
signatures. No consumer adapter or second grammar DSL was introduced.

The review also found and this follow-up corrects a contradictory design-guide
sentence about hidden options: operator help omits them, while the generated
semantic surface inventories and labels them. Netcup's `install-host.py`
`--monitor`/`--no-monitor` pair now uses existing `OptionSpec` mutex metadata,
so argparse refuses the combination before `.env` or runtime configuration is
loaded; the later duplicate handler check was removed.

Remaining evidence gap: CMRU's `S-CLI.9` and the other consumers have not yet
migrated to the catalog lifecycle. A broad migration should follow the Netcup
pilot. Decide a published-wheel and version-pinning contract only if CLI-EXT-04
is intended for consumers outside this monorepo; current first-party adoption
uses the source-backed same-repository boundary.

Current pilot gate evidence in `cli-extended-review` (2026-10-01): registered
library R0/R1 passes with 100% statement and branch coverage (3,404 statements,
1,650 branches); the Netcup registered `suite` lane passes all 228 tests; and
`surface_cli check` reports no drift for the generated manifest or canonical
spec. A fresh R2 campaign is running against this follow-up with resume state
and progress artifacts under `.assay/`; R3 is pending its final verdict.

**Related:** CLI-EXT-02 — declarative conditional option constraints.
