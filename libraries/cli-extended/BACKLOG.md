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

**Status:** Implemented (W2) — see [the design guide](docs/DESIGN-GUIDE.md#declare-option-constraints-structurally)
and [SPEC declared option constraints](SPEC.md#declared-option-constraints).
Reopened 2026-10-05 as program package W2 (operator
decision CX-D10, [unified-adoption program](docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md#w2-declarative-constraints-and-selector-type-cli-ext-02-cli-ext-12)).
The evidence gate below is superseded: every vbpub CLI now adopts the
library, and CMRU and nyxloom `validate=` callbacks already supply the shapes.

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

**Status:** Library implementation and Netcup pilot validated; broader consumer adoption remains follow-up work. Commit `827f67d7` passed registered R0/R1 with 100% statement and branch coverage (3,408 statements; 1,654 branches). Commit `75c007e3` passed R2 with all 1,015 mutations killed and no survivors, crashes, hangs, or budget overruns; R3 passed with its expected canary rejection. The Netcup registered suite passed all 228 tests, and the generated `monitor-task` surface check passed. The CMRU migration is not part of this library pilot and remains a separate adoption task. The R2 survivor analysis is in [`CLI-SURFACE-IMPLEMENTATION-PLAN.md`](docs/CLI-SURFACE-IMPLEMENTATION-PLAN.md#r2-campaign-on-e0ec2c9e-and-explicit-eof-bounds).

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
branches). That gate evidence was historical and did not cover the current
consumer-pilot changes; the final gates for this worktree are recorded below.

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

Final pilot gate evidence in `cli-extended-review` (2026-10-02): registered
library R0/R1 passed with 100% statement and branch coverage (3,408 statements,
1,654 branches); R2 killed all 1,015 candidates without survivors or budget
failures; R3 passed with the expected canary rejection; the Netcup registered
`suite` lane passed all 228 tests; and `surface_cli check` reported no drift in
the generated manifest or canonical spec. CMRU and other consumer migrations
remain outside this library pilot.

**Related:** CLI-EXT-02 — declarative conditional option constraints.
## CLI-EXT-05 — a shared `skills` verb group: install a tool's packaged agent skills into each harness

**Status:** Implemented in program package W4 (CX-D9), pending integration and
release; design pinned in
[the program](docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md#w4-packaged-agent-skills-cli-ext-05-new-skillspy);
normative rules in [SPEC §14](SPEC.md#14-packaged-agent-skills).
The stamp also goes into the SKILL.md frontmatter `metadata` and a visible
banner (operator choice), with an integrity sidecar for modified-state
detection; there is no global cross-tool sync. Controller decision during W4:
overwriting a locally modified skill uses its own `--overwrite-modified` flag,
not `--yes`, because the library's `--yes` is generic consent and must not
also carry a destructive-overwrite meaning (the oracle text below that says
`--yes` is superseded).  
**Type:** Feature  
**Area:** Command registration (shared verbs)

Each vbpub tool (ciu, cmru, assay, run-gate, nyxloom, pwmcp, cgprofile) is to
ship its agent skills (`<skill>/SKILL.md` trees) inside its own wheel as
package data, so the skill version always matches the installed tool version
and a consumer-only checkout (e.g. dstdns alone) needs neither a vbpub checkout
nor symlinks (dstdns D-647 #6). Wheels cannot run post-install hooks, so an
explicit install step is unavoidable. Every tool would otherwise hand-roll the
same verb, and seven copies of the same file-copy and drift logic would
diverge.

Proposed contract (one registration call per consumer, e.g.
`register_skills_verbs(app, package="ciu", resource_dir="skills")`):

- `<tool> skills install [--harness claude|agents|all] [--dest DIR] [--dry-run]`
  copies the packaged skills, located via `importlib.resources`, into
  `~/.claude/skills/` (Claude Code) and/or `~/.agents/skills/` (Codex and
  opencode). Neither harness reads the other's directory (verified
  2026-10-03). The copy is idempotent. Each copied skill carries a stamp
  (tool name + version + content hash), and a skill whose stamp names a
  different tool is refused, never overwritten.
- `<tool> skills list` shows each packaged skill with its installed state:
  current, stale (older stamp), modified locally (hash mismatch), or absent.
- `<tool> skills check` is the drift check. It exits non-zero when a skill
  is stale or absent, for use by `<tool> doctor` and by mdt's devcontainer
  finalize step, which runs `install` for every installed tool.
- `<tool> skills uninstall` removes only the skills stamped as this tool's.

Oracles:
- Installing twice changes nothing on the second run.
- An upgraded wheel makes `check` report stale and `install` refresh the skill.
- A locally edited skill is reported as modified and not overwritten without
  `--overwrite-modified` (was `--yes`; see Status).
- A skill stamped by another tool is refused.
- A controlled wrong implementation that copies without stamping must fail
  the foreign-tool refusal oracle.

**Provenance:** dstdns, 2026-10-03: D-647 #6, and the D-651 v8 interview,
where the operator chose `<tool> skills install` provided through
cli-extended for DRY. Per-tool adoption entries follow once this exists.

## CLI-EXT-06 … CLI-EXT-22 — unified-adoption program (2026-10-05)

**Status:** Scheduled. Operator interview 2026-10-04/05 decided that every
vbpub Python CLI adopts cli-extended as a real wheel dependency. The library
first absorbs the boilerplate consumers hand-roll today. Each item's full
design and oracles are in
[`docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md`](docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md).
This list is the index:

| ID | Item | Package | Replaces (evidence) |
|---|---|---|---|
| CLI-EXT-06 | `CliIdentity.resolve` version resolver | W1 | Netcup VERSION regex ×3; cmru/nyxloom metadata lookups with invented fallbacks |
| CLI-EXT-07 | `unexpected_exceptions="report"` + `--traceback`; registry-level `expected_exceptions` | W1 | nyxloom `_invoke`; per-`main` exception tuples |
| CLI-EXT-08 | `VerbSpec.dry_run` + dry-run-safe `confirm()` | W1 | Netcup hand-added `--dry-run` and ~10 `getattr(args, "dry_run")` sites |
| CLI-EXT-09 | Library `contract_version`; consumer signatures exclude library-owned controls | W3a | A library upgrade would otherwise re-sign every consumer surface (CX-D5) |
| CLI-EXT-10 | `[tool.cli-extended]` / `cli-extended.toml` config + `cli-extended` console script | W3b | Four repeated `surface_cli` flags; no CLI to install the library's own skills |
| CLI-EXT-11 | `surface pack`/`report`, findings file, `cli-extended-review` skill | W3b | No supported LLM-review step or actionable-items output |
| CLI-EXT-12 | `SelectorList` value type | W2 | cmru `parse_target_names` |
| CLI-EXT-13 | Shared `doctor` verb | W5 | nyxloom, cgprofile, planned ciu doctors |
| CLI-EXT-14 | `invoke_script`/`invoke_module` + opt-in pytest plugin | W6 | Netcup's two `_invoke` helpers; conftest marker wiring |
| CLI-EXT-15 | `ADOPTION-CHECKLIST.md`, `cli-extended audit`, `cli-extended-adoption` skill | W7 | No way for a consumer to verify complete, correct adoption |
| CLI-EXT-16 | Wheel release via cmru, GitHub Releases + `--no-index` resolution | W8 | Four incompatible import mechanisms; PyPI dependency-confusion risk (CX-D2) |
| CLI-EXT-17 | Constraints conditioned on a positional's value (e.g. `When("action", equals="set", then=RequiresChoice(...))`) plus declarable optional "action" positionals, so scp-api's ten `configure` callbacks and its handler-side `mac`/`action` swap can go (status: planned) | W9a Netcup adoption | scp-api hand-rolled conditional checks and `configure` callbacks |
| CLI-EXT-18 | Shared review decision for the library-reviewed common controls (`--json`, `--yes`, `--debug-raw`, `--dry-run`) across routes: one rationale/effects block plus one parametrized linked test covering N route cases (status: planned) | W9a Netcup adoption | scp-api yields 151 cases, mostly these controls; W9b: every `--config`/`--config-json` verb adds five identical cases (62 rows for 8 verbs) — share one case across identical option declarations too |
| CLI-EXT-19 | `invoke_script`/`invoke_module` take `python_args` (e.g. `-S`, `-I`) or an `isolated=True` switch that also drops the inherited `PYTHONPATH` for an explicit interpreter (status: planned) | W9b debian-install-v2 adoption | Wrapper scripts and `env={"PYTHONPATH": None}` to prove "library not installed" |
| (CLI-EXT-19 status) | **Fixed `7cfe227f3`** (CX-BACKLOG 2026-10-06): `python_args=` and `isolated=True` (adds `-I`, drops inherited `PYTHONPATH`; refuses `pythonpath`/`library_path`) on `invoke_script`/`invoke_module` | | |
| CLI-EXT-20 | `Requires`/`Conflicts` on an option that has a default ("differs from its default"), so a defaulted option need not drop its argparse default (status: planned) | W9b debian-install-v2 adoption | `--repo-url` lost its default to be constrainable (F-005) |
| (CLI-EXT-20 status) | **Open, decision ask** (CX-BACKLOG 2026-10-06): not implemented, see the design note below | | |
| CLI-EXT-21 | Per-verb `--dry-run` help sentence on `VerbSpec(dry_run=...)` (status: planned) | W9b debian-install-v2 adoption | One generic sentence for every verb (F-007) |
| (CLI-EXT-21 status) | **Fixed `7cfe227f3`**: `VerbSpec(dry_run_help="...")`; default sentence unchanged | | |
| CLI-EXT-22 | Audit heuristic precision: AC-01 `version-source` ignores test files that build a pinned `CliIdentity(...)`; AC-25 `no-path-hacks` ignores comments (status: planned) | W9b debian-install-v2 adoption | Two false positives fixed by rewording tests, not code |
| (CLI-EXT-22 status) | **Fixed `7cfe227f3`**: AC-25 ignores comments (tokenizer-based for `.py`); AC-01 ignores test files | | |
| (CLI-EXT-17, 18 status) | **Open, size L, decision ask**: design notes below; not implemented | | |

**Provenance:** controller survey 2026-10-04 of cmru, nyxloom, Netcup,
debian-install-v2 and the five non-adopting CLIs (ciu, assay, run-gate,
pwmcp, cgprofile).

## Triage — CX-BACKLOG, 2026-10-06 (branch `cx-backlog-2026-10`)

Code commits: `f6ad8ce0c` (23, 26, 27, 28, 29) and `7cfe227f3` (19, 21, 22, 24).
Per-fix evidence and plants: `docs/CX-BACKLOG-2026-10-REPORT.md`.

| ID | Status | Size | Note |
|---|---|---|---|
| 01, 03, 04, 05 | done (closed or implemented, see entries) | – | |
| 02 | done (implemented in W2) | – | |
| 06 – 16 | done (released in 0.2.0, see CHANGES.md) | – | |
| 17 | open, decision ask | L | design note below |
| 18 | open, decision ask | L | design note below |
| 19 | done `7cfe227f3` | S | |
| 20 | open, decision ask | M (policy) | design note below; reverses a documented, tested refusal |
| 21 | done `7cfe227f3` | S | |
| 22 | done `7cfe227f3` | S | |
| 23 | done `f6ad8ce0c` | M | opt-ins, defaults unchanged |
| 24 | done `7cfe227f3` | S | |
| 25 | open, decision ask | L | design note below |
| 26 | done `f6ad8ce0c` | S | |
| 27 | done `f6ad8ce0c` | S | |
| 28 | done `f6ad8ce0c` | S | |
| 29 | done `f6ad8ce0c` | S | |

### Design notes for the open items (decision asks)

**17 and 25 share one mechanism; decide them together.** Today a constraint
(`constraints.py`) relates option flags only: `resolve_constraints` binds each
flag through `parser._option_string_actions` and reads presence from the
parsed value. Both items need a constraint whose subject is a *positional*
(17: its value, `When("action", equals="set", then=RequiresChoice(...))`; 25:
its cardinality, `When("target", count=1, then=Requires/Forbids(...))`, with
`SelectorList.ALL` and an omitted target as distinct cases after conversion).
That is not a local change:
1. `constraints.py`: a new constraint kind, positional-destination binding and
   a post-conversion evaluator (for 17 also optional "action" positionals
   with `nargs="?"` and choices).
2. `surface.py`: the exported route record, the constraint-to-candidate
   mapping (`_CONSTRAINT_CANDIDATE_KINDS`) and member ids, which today are option ids
   only; a manifest schema bump (currently 7) and a one-time `surface sync`
   for every adopter that declares one.
3. `review.py`: the "case must exercise its constraint" check, which looks for
   the trigger *option* in argv, must learn positional trigger values and counts.
4. Help epilog and Markdown rule text for the new kinds.
Ruling needed: (a) accept the schema bump (schema 8, contract note); (b) the
spelling (`When(subject, equals=… | count=…, then=…)`); (c) whether `count`
covers only `SelectorList` positionals. Proposed: implement both in one
package after cmru 6.0 has adopted the current surface, with oracles
from the 17 and 25 entries (wrong implementation treating `ALL` as one name
must fail).

**18 (shared review decision across routes).** Review rows are per route and
per option; the four reviewed common controls (`--json`, `--yes`,
`--debug-raw`, `--dry-run`) and identical `--config` declarations repeat one
decision N times (scp-api 151 rows, cmru 346 cases for 32 routes). Collapsing them
changes the review catalog format (a shared case listing its routes, one
parametrized linked test), the signature rule (a shared case must be re-signed when any
member route changes its declaration) and the findings file addressing, i.e. a
catalog schema bump and a migration for every adopter's catalog. Ruling
needed on whether the saved review volume justifies a catalog schema bump
now, or after the remaining adopters (ciu, assay, run-gate, pwmcp,
cgprofile) are in, so migration is done once.

**20 (constraints on a defaulted option).** The refusal of non-None / non-False
defaults is deliberate and documented (DESIGN-GUIDE "Presence is the parsed
value differs from the default"; tested in `test_w2_constraints.py`): with
`--retries 3`, typing `--retries 3` is indistinguishable from omitting it, so
a `Requires("--retries", …)` would silently never fire. The code change is
small (compare against the default, converted through the action's `type`
for a string default) but it makes that blind spot the contract. Options:
(1) keep refusing and let consumers drop the argparse default (what
debian-install-v2 did, F-005); (2) allow it with the documented blind spot;
(3) allow it only as an explicit opt-in on the constraint
(`present="differs-from-default"`), keeping the loud default. Proposed: (3).
No code was changed for this item.

## CLI-EXT-23 — error output: identity banner after every error, full help on parser errors

**Status:** Fixed in `f6ad8ce0c` (CX-BACKLOG 2026-10-06). Opt-ins, defaults unchanged.

**Resolution:** `CliRegistry(identity_banner="once"|"never", error_help="full"|"usage")` (also `RegisteredCli` fields,
`run_cli` and `CliOutput` kwargs). The defaults (`once`, `full`) keep every current consumer's output byte for byte;
`never` drops the headline before an error, `usage` prints the usage line and `Run '<prog> --help' for full help.` at every
error-with-help site (parser errors, unknown help topic, missing verb, `CliFailure(show_help=True)`). The entry argued for the
"least noisy default that contract tests allow", but changing the default would re-break every adopter's stderr assertions,
so the noisy default is kept and consumers opt in. Exit codes unchanged. The `on-usage-error` banner variant was not built.

**Problem:** two library behaviours make every error noisy, and a consumer cannot opt out:
1. `CliOutput.error()` (`src/cli_extended/output.py`, around line 262) prints the tool's identity headline after the
   first error of a run, so the error is no longer the last line on stderr.
2. A parser-level usage error, such as a bad positional value (`scp-api.py power bogus`) or a non-integer id, dumps the
   whole help block instead of the usage line plus a one-line hint like `scp-api.py help <verb>`.

Netcup LT-NC1 (2026-10-06) worked around this at verb level only, for verb-raised usage errors. Parser-level errors and
the banner still behave as above, because the fix belongs here.

**Acceptance:**
- An identity-banner policy (`never` / `once-per-run` / `on-usage-error`) settable per `CliIdentity` or `CliOutput`,
  defaulting to the least noisy choice that existing contract tests allow.
- Parser errors print `usage: …` plus `error: …` plus a single `help <verb>` hint; full help only with `--help`.
- Exit codes unchanged (2 for usage).
- A contract test per behaviour, and migration notes for consumers that assert on the old output.

**Provenance:** netcup live test Phase 0 (2026-10-06), finding F4; LT-NC1 REPORT and review B9.

## CLI-EXT-24 — a version probe for `CliIdentity.resolve`

**Status:** Fixed in `7cfe227f3` (CX-BACKLOG 2026-10-06).
**Type:** Feature
**Area:** Identity

**Resolution:** `CliIdentity.resolve(version_probe=callable)`. `None` is unresolved; a malformed value or a
raising probe is a `VersionLookupError`; the probe must agree with installed metadata and a version file
(disagreement names every source). The CONSUMERS.md note on delegate/parent shared `unexpected_exceptions` was added.
The UNVERIFIED route-metadata question (delegate wrapper with `mutating=True` and no `dry_run`) was not investigated
and stays open for the first cmru sync.

`CliIdentity.resolve` accepted only installed distribution metadata and an
absolute VERSION file. A tool whose version is SCM-derived (cmru: no VERSION
file) that also runs from a source tree (gate lanes use `PYTHONPATH=src`; an
editable venv carries stale metadata, observed as 720 versus 1111 commits)
could not get a checkout-accurate version without building `CliIdentity(...)`
directly, which fails audit AC-01.

**Related (N2, delegate/parent coupling):** a parent and its delegates must
share `unexpected_exceptions` (a hard `ValueError` at `build()`), and
`any_dry_run` is registry-wide. That is not a defect, but a tool with many
registries (cmru: twelve at survey time) must flip the policy for all of them
at once; cmru uses one shared factory (`cmru.cli_support.cmru_registry`).

**Provenance:** cmru KI-51 survey (gaps N1 and N2), 2026-10-06, program package W2-PKG0.

## CLI-EXT-25 — cardinality constraints on a selector

**Status:** Open, size L, decision ask (CX-BACKLOG 2026-10-06). See the shared 17/25 design note in the triage section.
**Type:** Feature
**Area:** Constraints

Rules such as "`--output` only with exactly one target", "`--build-output`
requires one project", "`--delete-build-output` and
`--delete-unmanaged-release-tag` require exactly one target" and
"`--discard-build-worktree` forbids a target" (about seven cmru sites)
cannot be declared. CLI-EXT-17 conditions on a positional's value, not on its
count or on the `SelectorList.ALL` sentinel. They therefore stay handler-side
and invisible to the exported surface and the review.

Wanted: a constraint over a selector positional, for example
`When("target", count=1, then=Requires("--output"))` / `Forbids`, evaluated
after the selector converter so `ALL` and an omitted target are distinct
cases.

Oracles:
- One name satisfies `count=1`; two names, `ALL` and an omitted target each
  refuse with the declared message.
- The constraint appears in the exported surface and in the review rows.
- A controlled wrong implementation that treats `ALL` as one name must fail
  the `ALL` oracle.

**Provenance:** cmru KI-51 survey (gap N3), 2026-10-06, program package W2-PKG0.

## CLI-EXT-26 — `register_skills_verbs` drops the consumer's global options

**Status:** Fixed in `f6ad8ce0c` (CX-BACKLOG 2026-10-06).
**Type:** Defect
**Area:** Skills, surface export

**Resolution:** the child registry now receives `registry.global_options` (plus `identity_banner` and `error_help`).
The review-volume note below is unchanged and is CLI-EXT-18.

`register_skills_verbs(registry, package=...)` built its child `CliRegistry`
without `global_options=registry.global_options`
(`cli_extended/skills.py`). A consumer that declares a global option on its
registry (cmru: `--log-prefix-time-short`) therefore got `cmru skills
install|uninstall|check|list` WITHOUT it, and `cli-extended surface check`
failed with `incomplete parser syntax: cmru skills: delegated parser does not
register inherited global option(s): --log-prefix-time-short`, so such a
consumer could not pass AC-17 after adopting the shared `skills` verbs
(AC-19). Observed adopting cmru 2026-10-06 (W2-PKG5).

**Related (review volume):** the same adoption produced 346 review cases for
32 routes, 60 of them identical `--debug-raw` and consumer-global cases
repeated per route; CLI-EXT-18 would remove that repetition.

**Provenance:** cmru W2-PKG5 surface adoption, 2026-10-06.

## CLI-EXT-27 — `cli_extended.testing` prepends its own install directory to `PYTHONPATH`

**Status:** Fixed in `f6ad8ce0c` (CX-BACKLOG 2026-10-06). Behaviour change, see CHANGES.md.
**Type:** Defect
**Area:** Testing helpers

**Resolution:** no implicit `PYTHONPATH` edit; the child gets `pythonpath` followed by the caller's inherited
value. Explicit opt-in `library_path=True` prepends the imported library directory.

The subprocess helpers put cli-extended's own install directory at the front
of the child's `PYTHONPATH` unless the caller passed `python=`. A consumer
test that ran its CLI in a subprocess therefore imported the library from the
test environment's site-packages ahead of the consumer's own pinned or
wheel-installed copy, and hid a missing or mis-floored `cli-extended`
dependency. Observed adopting cmru (W2-PKG5, 2026-10-06).

**Provenance:** cmru W2-PKG5 review round 1 (library backlog ask), 2026-10-06.

## CLI-EXT-28 — a test cannot assert the absence of a path hack (audit AC-25)

**Status:** Fixed in `f6ad8ce0c` (CX-BACKLOG 2026-10-06).
**Type:** Feature
**Area:** Audit, testing

**Resolution:** the marker `# cli-extended: allow-path-assertion` exempts its own line, or the line after a
marker-only line. Only marked lines are exempt; a real `sys.path.insert(...)` in the same file still fails AC-25.
The `assert_no_path_hack` helper alternative was not built.

The AC-25 `no-path-hacks` heuristic scans test and source text for
`sys.path`/`PYTHONPATH` manipulations that name the library. A test that
asserts the cli-extended source path is ABSENT had to spell the needle, which
the heuristic reported as a hack; cmru worked around it with
`tests/_cx_paths.py`.

**Provenance:** cmru W2-PKG5 review round 1 (library backlog ask), 2026-10-06.

## CLI-EXT-29 — `expected_exceptions` cannot carry an exit code or hint

**Status:** Fixed in `f6ad8ce0c` (CX-BACKLOG 2026-10-06).
**Type:** Feature
**Area:** Runner, error rendering

**Resolution:** a matched expected exception exits with its integer `exit_code` attribute (non-bool; default 1) and
prints its non-empty string `hint` after `[ERROR] <msg>`, with no "unexpected" label. Exceptions without those
attributes, and plain tuples, behave as before. The `{type: exit_code}` mapping form was not built.

`CliRegistry(expected_exceptions=(...))` rendered a matching exception as
`[ERROR] <str(exc)>` and always exited 1 (`parser.py`); a consumer with an
exit-code taxonomy (cmru: 2 usage, 3 prerequisite missing, 4 refused by
policy) had to subclass `CliFailure`. Observed in cmru W2-PKG5 review fix
round 1 (B1).

**Provenance:** cmru W2-PKG5 review fix round 1, 2026-10-06.
