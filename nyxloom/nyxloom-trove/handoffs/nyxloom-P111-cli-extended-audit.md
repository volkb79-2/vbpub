---
schema_version: 1
id: nyxloom-P111-cli-extended-audit
project: nyxloom
title: "Audit and canonically document the primary Nyxloom CLI before cli-extended adoption"
tier: frontier-review
input_revision: "dfee5e5878c853cb1d7322c1582ee877be520ed2"
depends_on: []
session: fresh
source: {kind: user, ref: null}
scope:
  touch:
    - "README.md"
    - "docs/CLI-REFERENCE.md"
    - "docs/DESIGN-GUIDE.md"
    - "docs/CONSUMERS.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/reports/nyxloom-P111-LOG.md"
    - "nyxloom-trove/reports/nyxloom-P111-REPORT.md"
  forbid:
    - "src/"
    - "tests/"
    - "pyproject.toml"
    - "cmru.toml"
    - "nyxloom-trove/nyxloom.toml"
    - "nyxloom-trove/STANDING.md"
oracles:
  - id: O1-cli-reference
    observable: "docs/CLI-REFERENCE.md has a source-backed row for every primary CLI verb/action, positional input, option spelling and alias, including nested commands; each option row records syntax, placement, default source, omission behavior, interactions, side effects, and documentation accuracy."
    negative: "An option accepted by the installed parser but absent from the matrix, or a documented option that a black-box probe rejects, fails this oracle. No default or side effect may be inferred from its name."
    gate: tester-unified
  - id: O2-behavioral-audit
    observable: "The LOG records valid, invalid, omitted, conflicting, before-verb, after-verb, and delegated-path probes for every command surface, with intended handler or refusal and exit/output behavior tied to code and existing test assertions."
    negative: "A probe that can mutate real project, repository, release, credential, or daemon state is forbidden; an untested write path is reported as unverified rather than certified."
    gate: tester-unified
  - id: O3-design-review
    observable: "Every keep/remove/move/rename/default-change proposal is tied to a concrete Nyxloom user journey, current implementation or documentation evidence, migration cost, and compatibility impact. Open CLI backlog items are assessed without being implemented."
    negative: "Any grammar or behavior change made without an explicit operator decision, or any backlog feature added opportunistically, fails this oracle."
    gate: tester-unified
  - id: O4-docs-and-package-boundary
    observable: "README links to the canonical CLI reference; DESIGN-GUIDE explains only approved rationale; CONSUMERS contains pasteable examples for the currently shipped grammar; the report states how the chosen bundled cli-extended package will be proved inside an installed Nyxloom wheel."
    negative: "An example that does not parse, a link to a missing anchor, or a claim that cli-extended is already present in the Nyxloom wheel fails this oracle."
    gate: tester-unified
  - id: O5-no-runtime-migration
    observable: "The audit deliverables and probes are complete while parser, dispatch, runtime, packaging, and library source remain unchanged; unresolved choices are captured for the follow-up implementation handoff."
    negative: "Any changed file under src/, tests/, pyproject.toml, cmru.toml, or the sibling libraries/cli-extended package is a scope violation."
    gate: tester-unified
  - id: O6-complete-gate-evidence
    observable: "The handoff lints, and the exact tester-unified gate for this committed worktree revision has a complete recorded result for every lane."
    negative: "A partial, stale, cockpit-only, or still-running gate is not green and must not be reported as complete."
    gate: tester-unified
gates: [tester-unified]
review_focus:
  - "Verify table completeness from parser declarations and dispatch, not help text alone."
  - "Attack side-effect claims for help, version, dry-run, read-only verbs, and explicit safeguards."
  - "Check bundled-wheel findings against actual build configuration and isolated-install evidence."
escalate_if:
  - "A grammar or side-effect claim cannot be established from implementation plus observable probes."
  - "A required file falls outside scope.touch or a named contract requires a forbidden runtime, test, or package-owned file."
  - "The declared tester-unified gate cannot run because of a deterministic configuration or environment defect outside this package's scope."
---

# nyxloom-P111 - Audit the primary Nyxloom CLI before cli-extended adoption

Contract class: 2a, design-bearing audit. This package produces the complete,
evidence-backed grammar reference and the decision packet for a separate
implementation handoff. It does not change runtime behavior or perform the
cli-extended migration.

The package is explicitly allowed to update README.md,
docs/CLI-REFERENCE.md, docs/DESIGN-GUIDE.md, docs/CONSUMERS.md, and the
decisions/report files named in scope.touch. This is a package-scoped amendment
to the frozen-file rule in nyxloom-trove/STANDING.md; no other normally frozen
file is authorized.

## Approved operator decisions

Treat these as fixed inputs; do not ask the operator to decide them again.

1. Scope is the installed primary nyxloom command and all nested actions
   reachable through it. nyxloomd and separate agent/controller commands are
   out of scope.
2. The follow-up implementation must align with the cli-extended contract and
   use its features where they apply to Nyxloom.
3. Nyxloom's existing explicit safeguards count as consent. Do not add generic
   confirmation prompts indiscriminately or require a second confirmation on
   top of an existing explicit safeguard.
4. cli-extended is to be bundled into the existing Nyxloom wheel. Do not add a
   separate cli-extended release artifact or runtime distribution dependency.
5. This audit and canonical reference come first. A separate implementation
   handoff follows after the operator reviews the audit and resolves any new
   public grammar decisions.

## Context to read first

Paths under libraries/ and the root README are relative to the vbpub
worktree root. Other paths are relative to the Nyxloom project root.

1. reference/AUTHORING.md, especially the 2a-2e contract ladder,
   implementation packet, section 3b test-oracle rules, real-gate rule,
   mechanical BLOCKED rule, product-decision rule, and two-step
   input_revision rule.
2. nyxloom-trove/STANDING.md, especially Frozen files and Core-redesign
   wave exception, and nyxloom-trove/DOCTRINE.md, especially structlog
   reserved-key rules. Apply only the explicit package-scoped ownership grant
   listed above.
3. libraries/cli-extended/SPEC.md sections 1-9, 11-12;
   libraries/cli-extended/README.md sections Adopt it and Generated
   documentation and contract tests; and
   libraries/cli-extended/docs/CONSUMERS.md sections Install and choose a
   version source, Turn an interface inventory into registrations, Consumer
   responsibilities, and Tests required for an adoption.
4. libraries/cli-extended/BACKLOG.md item CLI-EXT-01. The current library
   has no first-class verb-alias field; mutating=True enables --yes and
   describes confirmation as required; include_json and include_progress
   default to true. CliOutput.color_enabled(stream) exists, but CLI-EXT-01
   records that the consumer-facing colour-policy contract is not documented.
   Verify these facts in the read-only implementation declarations:
   libraries/cli-extended/src/cli_extended/parser.py, output.py,
   identity.py, and testing.py. These files are read-only in this package.
5. src/nyxloom/cli.py: _VERB_GROUPS, top-level invocation
   classification, _print_top_level_help, _build_parser, and main's dispatch
   branches. Read the whole parser-registration and dispatch regions.
6. src/nyxloom/cli_support.py, src/nyxloom/output.py, and
   src/nyxloom/__init__.py for parser, output, bootstrap, and version
   behavior. Follow each registered action into its actual handler and read
   only those owner modules needed to establish its effects.
7. tests/test_cli.py, tests/test_cli_help.py, tests/test_cli_extract.py, and
   tests/test_effects_dispatch.py. Inspect assertions and their fixtures; test names
   alone are not evidence. Read other tests only when a parser action's behavior
   is owned there.
8. pyproject.toml, cmru.toml, libraries/cli-extended/pyproject.toml,
   the repository-root README.md cli-extended inventory row, and
   Nyxloom README.md's CLI and
   Documents sections. Establish the actual wheel and release boundaries; do
   not assume a sibling package is included merely because the checkout can
   import it.
9. docs/SPEC.md, docs/DESIGN-GUIDE.md, docs/CONSUMERS.md, and
   docs/ARCHITECTURE.md sections describing the installed operator CLI,
   versioning, project lifecycle, manual-controller workflow, and package
   boundaries.
10. nyxloom-trove/4-backlog-inbox.md, its generated
    nyxloom-trove/backlog/INDEX.md, and the open entries NL-16, NL-18, NL-19,
    NL-20, and NL-22:
    - NL-16: Nyxloom CLI/tooling versus full daemon wheel boundary.
    - NL-18: task backfill for manual-controller-dispatched work.
    - NL-19: whether/how to integrate tools/pack.py into the CLI.
    - NL-20: possible session-context/process-registry CLI.
    - NL-22: session-extract Bash/Task tool descriptions.
    Scan the rest of the open CLI-related backlog as well; the named entries
    are not an exhaustive allowlist.

## Implementation packet (normative)

### Owned artifact and interface

The canonical artifact is docs/CLI-REFERENCE.md. It describes the
installed primary nyxloom command, not nyxloomd or the other installed CLI
programs. The reference has:

- one row for every top-level verb and every nested action;
- one row for every positional argument, including requiredness and
  multiplicity;
- one row for every distinct option declaration, listing all aliases and
  every command path where it is accepted;
- a separate environment/input table for variables and ambient context that
  change parsing, output, configuration, or side effects;
- evidence references and an adoption-disposition column.

The verb/action rows record purpose, actual handler, valid invocation forms,
positionals, default behavior, state and filesystem effects, read/write/
mutate/publish/confirm/resume/display classification, and help/example
accuracy.

The option rows record user task and actual behavior; valid paths and option
placement (before/after a verb and through delegated paths); type, choices,
arity, multiplicity, default source, and omitted-value behavior; interactions
with other options and positionals; side effects and existing safeguards; and
whether help, README, Design Guide, Consumer Guide, and SPEC describe those
semantics accurately. Include hidden, deprecated, compatibility, and alias
spellings if the parser accepts them.

For each questionable option or command, recommend exactly one disposition:
keep, remove, move, rename, or change the default. Include concrete evidence,
migration cost, compatibility impact, and the user journey that supports the
recommendation. Mark current behavior separately from a proposed target; the
reference must not imply a proposal has shipped.

README.md links readers to the CLI reference and states the current user-facing
surface. DESIGN-GUIDE.md explains the approved rationale without presenting
unapproved grammar as fact. CONSUMERS.md has pasteable examples that parse
against the current CLI. Check docs/SPEC.md for consistency; in this audit,
record any required normative SPEC change as a follow-up proposal rather than
editing the frozen specification.

### Required evidence flow

1. Derive the reachable grammar from the installed console entrypoint,
   registry/parser registration, and dispatch code. Include all nested
   subparsers and any command path that delegates elsewhere. Do not derive the
   grammar from help output alone.
2. For each syntax row, compare parser acceptance, destination values, actual
   dispatch branch, tests, README, docs, and delegated command behavior.
   Derive every default from a parser declaration, configuration value,
   environment source, or documented policy. If no authoritative source exists,
   say so; never invent one.
3. Exercise valid examples, invalid arity, missing required input, invalid
   choice, omitted option, conflicting option pairs, and placement before and
   after each command boundary. Cover every declared mutually exclusive or
   required group and every behavior-changing interaction found in code.
   Use a systematic matrix or pairwise combinations for independent flags; do
   not claim exhaustive Cartesian coverage where the state space is larger.
4. For read-only commands, prove that write controls are rejected or have a
   real documented meaning. For dry-run paths, compare filesystem/state
   artifacts before and after. For mutating actions, prove which existing
   explicit safeguard authorizes the operation. Use temporary fixtures,
   fakes, or current behavioral tests; never touch real project state,
   credentials, remotes, releases, or user data.
5. Probe help, bare invocation, explicit version, version command, malformed
   top-level and nested invocations, stdout/stderr, exit statuses, and
   filesystem/state changes. Determine whether logging bootstrap or other
   initialization runs before help/version and whether it has side effects.
6. Compare actual help, nested help, generated/handwritten usage, and parser
   behavior. Treat every usage view as a rendered view of the accepted
   grammar. Record where options are accepted before/after a verb and where
   option forwarding stops.
7. Review the command set through these real Nyxloom journeys:
   - a project author registering a project, onboarding it, linting handoffs,
     and maintaining backlog/spec inputs;
   - an operator diagnosing status, repairing or resyncing state, controlling
     pause/resume/tick, and recovering after an interrupted attempt;
   - a manual controller creating, reviewing, rejecting, merging, and
     backfilling work without daemon dispatch;
   - an automation consumer using machine-readable output, stable exit codes,
     explicit paths, and non-interactive execution;
   - an operator inspecting routes, free models, capability maps, findings,
     events, leases, and session extracts.
   For each proposed command/group change, cite the journey, current
   implementation/docs/backlog evidence, migration cost, and compatibility
   effect. Identify overlap, missing common use cases, obsolete or redundant
   routes, unclear boundaries, and separate CLI users that must remain distinct.
8. Review adjacent contracts: error vocabulary and exit codes, JSON shape and
   stream placement, non-interactive behavior, global-option placement,
   safe defaults, confirmation boundaries, discoverability, and behavior with
   missing or ambiguous project/config/repository context.
9. Assess the cli-extended boundary against the approved Nyxloom contract:
   - CliRegistry and the VerbSpec, ArgumentSpec, and OptionSpec declarations
     must eventually own parser, help, usage, and dispatch.
   - Check aliases, nested parser configuration, and per-action option scoping;
     do not hide gaps with a second parser or parallel handwritten command
     list.
   - Verify the meanings of mutating=True, --yes, and confirmation-required
     help. Nyxloom's already approved safeguards count as consent; identify
     every mismatch and propose a library API change if needed rather than
     misdeclaring commands.
   - Map cli-extended common options to commands where each has actual
     meaning. Read-only or unrelated commands must not inherit meaningless
     write, JSON, progress, or confirmation controls.
   - Assess help, --help, help <verb>, bare invocation, -h compatibility,
     version identity, debug/log controls, JSON, color, progress, error help,
     cancellation, and output/exception handling against SPEC sections 1-9.
10. Confirm the package boundary. Nyxloom currently releases a Nyxloom wheel
    via cmru.toml; cli-extended has its own pyproject but is not a
    separate Nyxloom runtime dependency or CMRU release artifact. The approved
    target bundles the cli_extended import package into the existing Nyxloom
    wheel. Document the source/package-discovery/build constraints and the
    later implementation oracle: build the wheel, install it in an isolated
    environment that cannot import from the repository checkout, then run
    nyxloom --help, nyxloom version, representative nested help, and import
    the bundled cli_extended package. Its identity must use Nyxloom's
    authoritative distribution version; do not add a separately guessed
    cli-extended version or use a checkout-only import path. This package does
    not change packaging.
11. Review all relevant open backlog entries. For each candidate addition,
    state proposed behavior, overlap with existing commands, risk, scope,
    concrete journey, migration cost, and compatibility impact. Specifically
    assess NL-16 and NL-18 through NL-20 and NL-22. Do not pull a backlog item
    into implementation automatically.
12. Capture every new public grammar or behavior proposal as an open
    D-<NNN> entry in nyxloom-trove/decisions.md, using the project's existing
    decision format and the next id allocated by the actual decision workflow.
    Include current behavior, alternatives, compatibility impact, and
    recommendation. Do not turn unanswered product calls into BLOCKED.
13. Finish with a reviewable follow-up implementation packet: exact source,
    test, package, and document paths; registry/dispatch migration boundaries;
    options that will be retained/removed/moved/renamed/default-changed;
    bundled-wheel build plan; library API gaps; test/fixture matrix; and
    behavior still awaiting an operator decision. Do not write that
    implementation handoff yet; give the operator the audit, canonical table,
    and decisions to review first.

### Degrees of freedom

Formatting within the named tables and concise wording are flexible. Grammar
facts, defaults, safety semantics, recommendation categories, evidence, and
approved decisions are not. Runtime code, test code, packaging config, and
library code are out of scope for this package.

## Work

1. Complete the semantic inventory and behavioral probes in the packet.
   Preserve a probe ledger in the LOG with argv, environment, fixture, result,
   exit status, output streams, side effects, and source/test evidence.
2. Write docs/CLI-REFERENCE.md as the complete canonical current-state table
   and evidence-backed adoption proposal. Include exact current forms and
   visibly distinguish any not-yet-approved target behavior.
3. Review and update README.md, DESIGN-GUIDE.md, and CONSUMERS.md within the
   limits in the packet. Every example must be checked against the currently
   shipped parser; every cross-document anchor must resolve.
4. Add open D-<NNN> decisions for public behavior choices discovered by the
   audit. Keep already approved operator decisions fixed and do not ask them
   again. Do not edit docs/SPEC.md; record proposed SPEC changes in the
   reference and LOG.
5. Produce the LOG and REPORT with the decision summary, command/option
   counts, probe ledger, backlog dispositions, requirement-to-evidence
   traceability, proposed implementation file map, unresolved decisions, and
   gate evidence.
6. Stop after the audit package is complete. Do not alter the parser,
   dispatch, output runtime, tests, packaging, cli-extended source, or other
   backlog features. Wait for operator review before a separate implementation
   handoff is authored.
7. Validate the handoff and project documentation from this checkout:

       cd <worktree>/nyxloom
       PYTHONPATH=src python -m nyxloom.cli lint nyxloom-trove/handoffs/nyxloom-P111-cli-extended-audit.md

   This targets the package handoff; unscoped lint also reads other registered
   project inputs. Check document anchors and examples directly, and attribute
   any unscoped lint findings to their actual files.
8. Before finalizing the report, inspect the gate system for an active run
   against this exact HEAD and worktree. If one is active, inspect or attach
   to that run instead of starting a duplicate. Otherwise run:

       cd <worktree>/nyxloom
       ./run-gate.py --worktree <worktree> tester-unified

   Report every lane's actual result. An incomplete lane is not green. The
   devcontainer is a cockpit, never the gate.

## Oracles

The frontmatter oracles are binding. In addition:

- The CLI reference is mechanically checked against parser declarations and
  dispatch paths, not merely against help text.
- Each advertised valid syntax reaches its intended handler in a safe fixture;
  each malformed arity, invalid choice, missing required input, or prohibited
  combination is rejected with the recorded help and exit behavior.
- Help/version are side-effect free, or any measured violation is recorded as
  an adoption defect. Existing safeguards and dry-run paths are proven by
  their observable effect on isolated state.
- Every proposed redesign choice has current behavior, alternative(s),
  compatibility impact, and a user journey. No unapproved proposal changes
  runtime behavior.
- The Nyxloom CLI reference, README, Design Guide, Consumer Guide, and SPEC
  are compared. Current shipped facts are not written as future target facts.
- A future installed-wheel test must prove that cli_extended imports from the
  Nyxloom wheel itself with the repository checkout unavailable. The report
  records the exact acceptance recipe and current packaging gap.
- Run the gate for this exact committed revision using the active-run rule in
   Work item 8; record all lane results verbatim in the REPORT.

## BLOCKED rule

If a named contract cannot be established from source and safe behavioral
evidence, or if satisfying it requires an unlisted or forbidden file, write
BLOCKED: <reason> to the LOG, commit that result, and stop the dependent work.
Do not invent behavior, silently widen the package, or work around a
cli-extended API mismatch. A product choice is an open D-<NNN> decision, not
BLOCKED. The independent audit and documentation may continue while only a
decision-dependent recommendation is held.

## Test-oracle rules copied from reference/AUTHORING.md section 3b

### 3b. What an oracle must NOT contain -- paste this into any handoff that asks for tests

Every rule below is the residue of a real incident; the `L`/`PL` refs are the
write-ups in `reference/LESSONS.md`. **If a handoff asks an agent to write
tests, copy this list into it** -- an implementation agent has no access to our
incident history and will otherwise reproduce these by default.

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- FORBIDDEN: `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- FORBIDDEN: `time.sleep(N)` to "let the thread get there", then assert.
- FORBIDDEN: Asserting on elapsed time, or on how many iterations something completed.
- REQUIRED: Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- REQUIRED: **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- REQUIRED: A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous -- 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red -- a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- FORBIDDEN: Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 section 5)
- FORBIDDEN: `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` facades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- FORBIDDEN: Teardown that destroys shared state rather than restoring the prior value.
- REQUIRED: Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" -- pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (section 3 above, and DOCTRINE's review checklist)
- FORBIDDEN: A test body that is `pass`, or asserts only that nothing raised.
- FORBIDDEN: Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- FORBIDDEN: Weakening or deleting an assertion to get past a failure.
- REQUIRED: Assert the **contract**: given this input/state, this observable outcome.
- REQUIRED: Where a check guards a real crash, add a test proving the crash is real --
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- FORBIDDEN: A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line -- including in
  a comment that merely *describes* the rule.
- FORBIDDEN: Excluding an `except` body and assuming the `except` clause is covered too --
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- REQUIRED: If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs -- control them.**
- FORBIDDEN: Real network calls, real registries, real model endpoints in a unit test.
- FORBIDDEN: `datetime.now()` / `time.time()` where the assertion depends on the value.
- REQUIRED: Inject or mock the boundary; make offline the default path.

**F. No predicted measurements.** (distilled 2026-09-17 from an incident in a
consuming project's own decision ledger -- the specific entry isn't cited here
since a canonical doc shouldn't hard-reference a consumer's private,
renumberable ledger; see that project's own decisions.md around the same
date for the full incident writeup if useful.)
- FORBIDDEN: A carve or oracle asserting a specific coverage/mutation number, a "missing
  lines" list, or a "this branch is permanently uncoverable" claim computed by
  reasoning about a tool's rendered report instead of running the tool.
- FORBIDDEN: Trusting `coverage.py`'s rendered "Missing" column as a complete branch-arc
  list -- it silently suppresses an arc whose destination line is already
  reported missing elsewhere, so a hand-derived read of the report undercounts
  by exactly that arc. This exact mistake recurred three times independently
  in one wave before being traced to this display artifact.
- REQUIRED: Assert the POLICY requirement instead -- the project's coverage target, its
  R0-R3 (or equivalent) testing tier, the design decision -- as the oracle.
  Never a predicted number; the number does not exist until the implementer's
  own gate run produces it.
- REQUIRED: If a carve must justify "this is achievable" or "this line is
  unreachable" before dispatch, PROVE it by executing the tool
  (`coverage.py`/`runpy.run_module(mod, run_name="__main__")`, or the
  project's own judge) against real or synthetic stand-in code -- never by
  reading a report and reasoning about what it would show.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.
