---
schema_version: 1
id: nyxloom-P112-cli-extended-adoption
project: nyxloom
title: "Adopt cli-extended across Nyxloom's installed command surfaces"
tier: frontier-review
input_revision: "c4ba7d921e5ba8f85f85cbac86f6af6a62a5a775"
depends_on: []
session: fresh
source: {kind: user, ref: null}
scope:
  touch:
    - "pyproject.toml"
    - "src/nyxloom/cli.py"
    - "src/nyxloom/cli_harness.py"
    - "src/nyxloom/cli_ctl.py"
    - "src/nyxloom/cli_registry.py"
    - "src/nyxloom/backlog_entries.py"
    - "src/nyxloom/backlog_wizard.py"
    - "src/nyxloom/daemon_entrypoint.py"
    - "tests/test_cli.py"
    - "tests/test_cli_help.py"
    - "tests/test_lint.py"
    - "tests/test_cli_extract.py"
    - "tests/test_effects_dispatch.py"
    - "tests/test_backlog_entries.py"
    - "tests/test_daemon.py"
    - "tests/test_cli_adoption.py"
    - "tests/test_installed_wheel.py"
    - "tests/test_free_models.py"
    - "tests/test_session_extract_edge_contracts.py"
    - "tests/test_events_cmd.py"
    - "tests/test_session_extract_reasonix.py"
    - "tests/test_resync.py"
    - "tests/test_resync_apply.py"
    - "tests/test_migrate_store.py"
    - "tests/test_doctor.py"
    - "tests/test_resume_guard.py"
    - "tests/test_backlog_items.py"
    - "tests/test_intake_bridge.py"
    - "tests/test_route_doctor.py"
    - "tests/test_liveness.py"
    - "tests/test_intake_chat.py"
    - "tests/test_control_auth.py"
    - "nyxloomd/supervise.sh"
    - "nyxloomd/Dockerfile"
    - "docker-bake.hcl"
    - "nyxloomd/ciu.compose.yml.j2"
    - "nyxloomd/docker-compose.yml"
    - "README.md"
    - "docs/CLI-REFERENCE.md"
    - "docs/DESIGN-GUIDE.md"
    - "docs/CONSUMERS.md"
    - "docs/SPEC.md"
    - "docs/USAGE.md"
    - "docs/ARCHITECTURE.md"
    - "docs/runtime-process-model.md"
    - "docs/backlog-entries-spec.md"
    - "src/nyxloom/session_extract/README.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/reports/nyxloom-P112-LOG.md"
    - "nyxloom-trove/reports/nyxloom-P112-REPORT.md"
    - "nyxloom-trove/STANDING.md"
  forbid:
    - "run-gate.toml"
oracles:
  - id: O1-library-prerequisite
    observable: "The implementation uses the merged cli-extended prompt API and explicit confirmation opt-out from its shipped SPEC/API; cli_extended imports from the built Nyxloom wheel, not a development path or a second parser."
    negative: "If the prerequisite is absent or the required API is missing, the task records BLOCKED and stops before adding a local substitute, parser, dependency, or vendored duplicate."
    gate: tester-unified
  - id: O2-registry-contract
    observable: "Each user-facing command (`nyxloom`, `nyxloom-harness`, `nyxloomctl`) gets its parser, help, usage, common options, and dispatch from a cli-extended CliRegistry with VerbSpec, ArgumentSpec, and OptionSpec declarations."
    negative: "A second parse, handwritten command inventory/help, stale usage(), an option accepted outside its declared path, or a valid documented invocation reaching the wrong handler fails."
    gate: tester-unified
  - id: O3-migration-and-safety
    observable: "The full old-to-new command map below is implemented and documented. Moved host/session commands have no forwarding paths in `nyxloom`; `gate` and `daemon --foreground` are removed. Existing explicit safeguards and exit/error behavior remain, with only D-001..D-018 changes."
    negative: "Any unapproved grammar/effect change, old alias that still dispatches, generic confirmation added on top of an existing safeguard, or mutation hidden behind a read-only command fails."
    gate: tester-unified
  - id: O4-boundaries-and-effects
    observable: "Local authoring and harness commands run without daemon or host-registry availability; `nyxloom-harness` is a stable shell boundary for AI-harness skills, which need not import Nyxloom internals. Local `nyxloomctl` operations remain local and do not silently become HTTP calls. Help/version are side-effect-free, and `nyxloom lint` and `nyxloomctl lint` perform their distinct approved scans."
    negative: "A help/version path writes Nyxloom state, project authoring/session extraction requires host registration or a daemon, host-wide lint runs from `nyxloom lint`, or local admin needs a live daemon fails."
    gate: tester-unified
  - id: O5-backlog-wizard
    observable: "`nyxloom backlog new --interactive [TITLE]` and `nyxloom backlog edit ENTRY_ID` use the shared prompt API, the D-016 metadata field set, and the optional Nyxloom interactive extra. Full candidate frontmatter validates against the shipped schema before any entry or index write; edit preserves non-form metadata and the body verbatim."
    negative: "Non-TTY, cancellation, invalid values, unknown entry IDs, schema failure, or a failed prompt leaves both entry and index unchanged; status/merge-owned fields cannot be edited by the wizard."
    gate: tester-unified
  - id: O6-wheel-and-service-entrypoints
    observable: "A wheel built from this revision contains `cli_extended`, all four console scripts, and the optional `interactive` extra. An isolated install with the checkout absent runs representative help/version paths and resolves cli_extended from the installed Nyxloom distribution. The service supervisor invokes the installed `nyxloomd` executable directly."
    negative: "A repository import path, separate cli-extended runtime distribution dependency, Questionary import on base help/version/noninteractive paths, `python -m nyxloom.cli daemon` in the supervisor, or a missing script entry fails."
    gate: tester-unified
  - id: O7-docs-and-gate
    observable: "README, DESIGN-GUIDE, CONSUMERS, SPEC, USAGE, ARCHITECTURE, runtime-process-model, backlog-entries-spec, and CLI-REFERENCE agree on shipped grammar; consumer examples parse; CLI-REFERENCE includes the migration table. The exact tester-unified gate completes and every declared lane is recorded."
    negative: "An example using a removed path, missing command/option in the reference, broken cross-document anchor, partial gate, or dirty-tree refusal reported as green fails."
    gate: tester-unified
gates: [tester-unified]
review_focus:
  - "Reconcile each registry declaration against actual parse, help, handler, and side effects for all three user-facing CLIs."
  - "Attack command ownership, daemon independence, old-path removal, option placement, ignored combinations, and explicit consent boundaries."
  - "Try cancellation and schema-invalid backlog candidates at every write boundary; compare both entry and INDEX.md bytes."
  - "Inspect the built wheel and run it from an isolated environment with the source checkout absent."
  - "Check container supervisor and healthcheck commands against the installed console scripts."
escalate_if:
  - "The cli-extended prerequisite has not landed on the implementation base, or its shipped prompt/confirmation API does not satisfy this contract."
  - "The built Nyxloom wheel cannot include cli_extended from the repository's library source without relying on the checkout at runtime or creating an unmaintained second source copy."
  - "An oracle cannot be met without editing another project, the shared gate, or the library package, and the user has not authorized that cross-boundary change."
  - "The exact declared gate cannot start because of a deterministic environment/governance defect outside Nyxloom's scope."
---

# nyxloom-P112 - Adopt cli-extended across Nyxloom's command surfaces

Contract class: **2b, complex solution-bearing execution**. D-001 through
D-018 fix user-visible behavior and the command boundaries. The implementer
owns the registry integration, wheel inclusion, backlog wizard, and direct
service entrypoint, but must not reopen those decisions silently.

## Start condition and worktree

Do not start implementation until the separate
`cli-extended-nyxloom-api` prerequisite has completed its declared library
gate and its commit is present on the implementation base. That prerequisite
adds the optional prompt API and separates the `mutating` label from required
confirmation. If either contract is absent, apply the BLOCKED rule below.

Implement in `/workspaces/vbpub/.worktrees/nyxloom-cli-adoption` on branch
`nyxloom-cli-adoption`. The implementation base must contain current local
`main`, the approved P111/P112 audit and handoff, and the completed gated
`cli-extended-nyxloom-api` prerequisite. Those commits are already present in
this worktree. Keep implementation commits isolated here; do not merge them
into the shared `main` checkout as part of this task. The audit authoring
worktree is not the implementation worktree.

### User-authorized scope amendment — 2026-09-28

The user authorized expanding `scope.touch` after the implementation audit
found that the installed wheel and service-entrypoint oracles require the
Nyxloom image build and Ciu compose inputs. The added paths are
`nyxloomd/Dockerfile`, `docker-bake.hcl`, and
`nyxloomd/ciu.compose.yml.j2`. Update all three image-build descriptions and
entrypoint/healthcheck commands consistently with the existing Nyxloom compose
file; keep their build contexts aligned.

The user also authorized the CLI ownership changes, including updating
existing command-level tests that still invoke moved paths through
`nyxloom.cli:main`. Those tests are now included in `scope.touch`; command
coverage remains attached to its current test module while invocations move to
`nyxloomctl` or `nyxloom-harness`.

No separate `cli-extended` runtime distribution is to be installed by Nyxloom.
Bundle its package source into the existing Nyxloom wheel. The library remains
maintained from `libraries/cli-extended`; do not make an unmanaged copy or
depend on the repository checkout at runtime.

### User-authorized standing-rule change — 2026-09-28

The user directed removal of Nyxloom's hard out-of-scope edit prohibition so
implementation can make all changes needed to complete its accepted task.
`scope.touch` remains the planned inventory, not an exclusive allowlist. The
standing rule now permits directly necessary additional files when the reason
is recorded in the task log or report. `nyxloom-trove/STANDING.md` is added to
this handoff's scope so the rule can be updated. Protected files, ownership by
another active package, explicit forbids, and unrelated work remain guarded;
the user's authorization to override a protected file or explicit forbid must
be recorded as a bounded contract amendment.

## Approved decisions

Treat these decisions in `nyxloom-trove/decisions.md` as fixed inputs:

- **D-001..D-006:** remove `-h`; make backlog listing read-only; reject
  ignored/invalid option combinations; reject unknown explicit project IDs
  on status/doctor; remove the empty `gate`; use library verbosity semantics
  for `--debug`, add `--log-level`, and retain tracebacks as `--traceback`.
- **D-007..D-009:** install `nyxloom`, `nyxloom-harness`, `nyxloomctl`, and
  service-only `nyxloomd` in one Nyxloom wheel. Keep the dashboard as the
  current HTTP/SSE client and do not add remote `nyxloomctl` operations here.
- **D-010/D-012:** use the optional shared Questionary prompt API through
  `nyxloom[interactive]`; ordinary installs and commands remain usable
  without Questionary.
- **D-011:** move the local daemon command to `nyxloomctl daemon`; remove the
  ineffective `--foreground` option; the container supervisor uses
  `nyxloomd`.
- **D-013:** remove old host-control paths from `nyxloom` immediately. Do not
  add compatibility forwarding aliases; document the migration table.
- **D-014/D-016:** add interactive managed-backlog create/edit with the
  approved command forms, field set, body behavior, validation-before-write,
  and transition-field ownership below.
- **D-015:** keep project-local lint as `nyxloom lint`; move the existing
  no-argument all-registered-project scan to `nyxloomctl lint`.

Nyxloom's current explicit safeguards count as consent. Do not add generic
confirmation prompts or `--yes` where an existing state, role, `--apply`,
confidence, or explicit-command safeguard already authorizes the operation.
Use cli-extended's explicit confirmation opt-out while keeping the accurate
`mutating` catalog label.

## Context to read first

Paths below are relative to the Nyxloom project root unless they begin with
`libraries/` or `scripts/`, which are relative to the vbpub repository root.

1. `reference/AUTHORING.md`: contract class 2b, implementation packet,
   behavioral oracle, exact gate, BLOCKED rule, and two-step input revision.
2. `nyxloom-trove/STANDING.md` and `nyxloom-trove/DOCTRINE.md`: owned files,
   worktree/commit rules, gate policy, and Nyxloom-specific implementation
   traps. `nyxloom-trove/decisions.md` D-001..D-018 and
   `nyxloom-trove/reports/nyxloom-P111-REPORT.md` are the adopted decisions and
   audit evidence; `docs/CLI-REFERENCE.md` is the complete current grammar and
   approved target/migration inventory.
3. `libraries/cli-extended/SPEC.md` §§1-9, 11-12, its confirmation opt-out
   addition, and its new prompt API section; `libraries/cli-extended/README.md`
   “Adopt it” and “Generated documentation and contract tests”; and
   `libraries/cli-extended/docs/CONSUMERS.md` installation, registry, consumer
   responsibilities, prompts, and adoption-test sections. The newly added
   API sections must be present in the prerequisite commit before work starts.
4. `libraries/cli-extended/src/cli_extended/parser.py`, `output.py`,
   `identity.py`, `testing.py`, and the prompt implementation. Verify the
   shipped signatures and behavior from source; do not assume a checkout import
   proves wheel inclusion.
5. `src/nyxloom/cli.py`: parser creation, top-level help/version/bootstrap,
   dispatch, `cmd_lint`, all `extract*` handlers, `cmd_daemon`, backlog command
   handlers, and every handler moved to `nyxloomctl`. Follow affected handler
   calls into their owner modules to establish state/effect guards.
6. `src/nyxloom/backlog_entries.py`, the frontmatter/schema-loading helpers,
   `src/nyxloom/schemas/backlog-entry.schema.json`, and
   `docs/backlog-entries-spec.md` §§73-171, 192-230, 269-299. Read
   `tests/test_backlog_entries.py` assertions, not just test names.
7. `nyxloom/pyproject.toml` (scripts, extras, package discovery, version),
   `nyxloom/nyxloomd/supervise.sh`, and
   `nyxloom/nyxloomd/docker-compose.yml` healthcheck. Preserve the existing
   container boundary.
8. `tests/test_cli.py`, `tests/test_cli_help.py`,
   `tests/test_cli_extract.py`, `tests/test_effects_dispatch.py`,
   `tests/test_daemon.py`; inspect assertions and fixtures for side effects,
   safeguard behavior, and process lifecycle.
9. `docs/SPEC.md` §§1-14, `docs/USAGE.md` §4, `docs/ARCHITECTURE.md` CLI and
   package boundaries, `docs/runtime-process-model.md` §§1-2,
   `README.md`, `docs/DESIGN-GUIDE.md`, and `docs/CONSUMERS.md`.
10. `scripts/debian-install-v2/wizard-requirements.txt` and
    `scripts/debian-install-v2/debian_install_v2/wizard.py` for the approved
    Questionary version and existing repository usage. Do not choose a newer
    version by guesswork.

## Implementation packet (normative)

### Installed commands and ownership

Keep one Nyxloom distribution and install these four entrypoints:

| Executable | Owner and scope |
|---|---|
| `nyxloom` | Local project authoring: `init`, `onboard`, project-local `lint`, and `backlog *`. It works from a project checkout without registry registration or daemon availability. |
| `nyxloom-harness` | Every session/extraction path: `extract`, `extract-lossless`, `extract-debug`, `extract-report`, and `extract-sessions`. It is the stable shell boundary for AI-harness skills, reads explicit harness session files/stores, and does not initialize Nyxloom host state or require project registration. |
| `nyxloomctl` | All local host-control, administration, operator, and developer diagnostics, including project registry, status/doctor, workflow/intake/finding actions, routes/models, auth, host-wide lint, migration, and the local `daemon` command. It performs local operations; remote/API transport is out of scope. |
| `nyxloomd` | Direct service-manager process launcher for the existing daemon container. It is not a fourth human CLI and does not enter through a user-command parser. |

Use one cli-extended `CliRegistry` for each user-facing executable. The
registry declarations own that executable's grammar, generated help/usage,
common-option placement, and handler dispatch. Use `VerbSpec`,
`ArgumentSpec`, and `OptionSpec`; use the library's nested-command mechanism
where needed. Parse each invocation once. Remove the handwritten usage path,
parallel command lists, and old argparse dispatch rather than running two
parsers or manually forwarding old paths.

The `pyproject.toml` script targets must resolve to callable entrypoints for
all four names. Suggested targets are `nyxloom.cli:main`,
`nyxloom.cli_harness:main`, `nyxloom.cli_ctl:main`, and
`nyxloom.daemon_entrypoint:main`; equivalent private module placement is fine
if the installed scripts and registry ownership remain exact. `nyxloomd`
calls the existing daemon lifecycle directly and preserves its foreground,
signal, and logging behavior.

### Command migration table

Implement and publish this table in `docs/CLI-REFERENCE.md`. The target
commands are direct commands under the named executable, preserving their
nested shape and current handlers unless an approved decision says otherwise.

| Current path | Target path / disposition |
|---|---|
| `nyxloom extract`, `extract-lossless`, `extract-debug`, `extract-report`, `extract-sessions` | Same verb paths under `nyxloom-harness`. |
| `nyxloom project add/list` | `nyxloomctl project add/list`. |
| `nyxloom doctor`, `status`, `resync`, `render`, `migrate-store`, `tick`, `decide`, `discuss`, `intake`, `intake-bridge poll`, `reject`, `merge`, `pause`, `resume`, `leases`, `digest`, `events` | Same verb paths under `nyxloomctl`. |
| `nyxloom auth show/bootstrap/rotate` | Same nested paths under `nyxloomctl`. |
| `nyxloom free-models list/refresh`, `capability-map refresh`, `route doctor`, `finding record/list` | Same nested paths under `nyxloomctl`. |
| `nyxloom daemon` | `nyxloomctl daemon`; remove `--foreground`. The service manager invokes `nyxloomd`. |
| `nyxloom lint` with no paths (current host-wide scan) | `nyxloomctl lint` preserves the all-registered-project scan. New no-path `nyxloom lint` discovers the current project and checks its configured handoffs. Explicit handoff paths stay local under `nyxloom lint`. |
| `nyxloom init`, `onboard`, `backlog *` | Remain under `nyxloom`, with D-014/D-016 adding the interactive backlog paths below. |
| `nyxloom gate` | Remove. Project verification remains each project's `run-gate.py`/Assay lane. |
| `nyxloom --help`, `help <verb>`, `--version`, `version` | Generated by cli-extended for each user-facing CLI. Remove `-h`; version identity comes from Nyxloom distribution metadata. |

No old host-control or extraction command remains as a hidden alias in
`nyxloom`. Unsupported old paths fail as unknown commands. Do not route those
paths to a second registry.

### Common CLI behavior

- Follow the shipped cli-extended contract for identity/version, `--help`,
  `help <verb>`, usage errors, output formatting, log levels, colors, JSON,
  progress, and cancellation. Advertise common options only on commands where
  they have a defined effect; no read-only command accepts a meaningless write
  control.
- `-h` is rejected. `--debug` means verbosity; `--log-level` is explicit and
  `NYXLOOM_LOG_LEVEL` remains its environment default; `--traceback` retains
  the approved traceback behavior.
- Help, version, and usage-error paths must not create Nyxloom state/log files.
  Perform only side-effect-free identity and argument classification before
  the logging/bootstrap path required by a command.
- Keep the real handler effects, exit codes, and explicit guards from the
  current CLI reference. Reject invalid/ignored combinations from D-003 rather
  than accepting no-op flags. Unknown explicit project IDs on status/doctor
  fail in every mode (D-004). `backlog list` is read-only, even if its index
  does not exist (D-002).
- Use cli-extended's mutating catalog declaration without generic confirmation
  where the current action has an approved safeguard. Do not add an extra
  confirmation prompt or `--yes` on top of explicit operation/state/role/
  `--apply`/confidence safeguards. Never mislabel an operation to hide its
  effect.
- Declare closed values and parser types from their authoritative schema or
  registry; malformed values fail before dispatch with the generated command
  help (D-018).
- `nyxloom`, `nyxloom-harness`, and local `nyxloomctl` commands do not become
  HTTP clients. The dashboard remains the existing HTTP/SSE client. New remote
  control/API behavior is out of scope.

### Backlog interactive create/edit

Register these exact local command forms:

```text
nyxloom backlog new --interactive [TITLE]
nyxloom backlog edit ENTRY_ID
```

`new TITLE [existing options]` without `--interactive` remains noninteractive
and scriptable. `TITLE` is optional only with `--interactive`; if omitted,
ask for it, and if supplied, use it as the prompt's initial value. Existing
metadata options seed the matching prompts. Keep the existing body template
and `--body-from` behavior. Do not write a generic final confirmation prompt.

Both interactive forms use the shared `runtime.prompts` API and injected
`PromptDriver` seam shipped by cli-extended. Add `nyxloom[interactive]` as an
optional extra using the existing repository-approved Questionary pin. The
base Nyxloom wheel, help/version, lint, noninteractive backlog, harness, and
host-control paths must not import Questionary. A prompt request without the
extra reports the exact `pip install 'nyxloom[interactive]'` remedy; non-TTY
or cancellation is handled using the library's stable refusal/cancellation
contract. No locally hand-rolled `input()`/Questionary wrapper or fallback is
allowed.

The editable field set is fixed by D-016 and mirrors `backlog new`:
`title`, `type`, `severity`, `priority`, `component`, `context_estimate`,
`folds_into`, `provenance`, `filed_by`, and `spec_owner`. `type` choices are
`feature|bugfix`, `severity` is `low|medium|high`, and `context_estimate` is
`small|medium|large`. Parse `priority` as an integer. An empty optional value
clears that field; invalid values must produce a field-specific correction
path and can never be written. Existing CLI values are prompt defaults.

For creation, retain ID allocation, `status: open`, `filed_date`, and body
creation from the existing helpers. For editing, preserve `id`, `kind`,
`schema_version`, `status`, `filed_date`, `decisions`, `promoted_from`,
`carved_handoff`, `merge_commit`, `closed_date`, `closed_reason`, and any other
frontmatter field outside the form. Preserve the Markdown body byte-for-byte.
Only the existing status/merge transition functions may change their fields.

Collect input and construct the full candidate first. Validate the complete
candidate frontmatter against `src/nyxloom/schemas/backlog-entry.schema.json`
with the shipped loader before writing the entry or regenerating `INDEX.md`.
Cancellation, invalid values, a missing entry, and schema failure leave both
files unchanged. Update the generated index only after a valid entry write,
using the existing index renderer/writer. Do not weaken entry-ID allocation,
body preservation, lint, or transition invariants.

### Wheel and service process

- Package the source in `libraries/cli-extended/src/cli_extended` into the
  existing `nyxloom` wheel through declared build configuration. Do not add a
  `cli-extended` install/runtime dependency, development `PYTHONPATH`, or a
  second unmanaged source copy. The built artifact, not the checkout import,
  is the evidence.
- Add the optional `interactive` extra to Nyxloom for Questionary, pinned from
  `scripts/debian-install-v2/wizard-requirements.txt`; the base dependency set
  remains free of Questionary.
- Add a directly executable `nyxloomd` script and point the existing
  `nyxloomd/supervise.sh` default command at the installed script. Change the
  container healthcheck from `python -m nyxloom.cli doctor --liveness` to the
  installed `nyxloomctl doctor --liveness`. Do not add a container, image, or
  remote API.
- Build the Nyxloom wheel, install it into an isolated environment outside the
  repository, and run it with the checkout absent from `sys.path` and
  `PYTHONPATH` unset. Verify the four entrypoint declarations and that
  `cli_extended.__file__` is under the installed Nyxloom distribution. Run
  representative top-level/nested help, both version forms, and base-install
  noninteractive commands without Questionary installed. Exercise a prompt
  through the injected driver; the library prerequisite owns its real TTY /
  Questionary integration tests.

### Documentation

Update all in this task:

- `README.md` states the shipped four entrypoints and links to the canonical
  reference.
- `docs/DESIGN-GUIDE.md` explains the data/user boundaries, immediate removal
  of old paths, local versus host-wide lint, optional prompt API boundary, and
  why the daemon launcher is separate.
- `docs/CONSUMERS.md` contains pasteable wheel installation, local lint,
  backlog wizard, extraction, and host-control examples, including
  `nyxloom[interactive]`.
- `docs/CLI-REFERENCE.md` is regenerated/updated as the canonical grammar,
  effects, options, and old-to-new migration table. Keep current behavior and
  target behavior distinct only where the command is not yet shipped; after
  implementation, it must describe the shipped grammar.
- `docs/SPEC.md` adds the normative installed CLI, error/help/version, safety,
  and entrypoint contract. Update `docs/USAGE.md` §4, `docs/ARCHITECTURE.md`,
  `docs/runtime-process-model.md`, and `docs/backlog-entries-spec.md` so old
  paths and examples do not contradict the new behavior.

README answers what Nyxloom does, DESIGN-GUIDE explains why, and CONSUMERS
provides examples an adopter can paste. Examples in all three must parse
against the installed wheel; all cross-document anchors must resolve.

## Work

1. Verify the library prerequisite and its merged API first. If it is absent or
   the source, SPEC, and installed package disagree, do not start the Nyxloom
   migration; apply the BLOCKED rule.
2. Build an exact old-command → new-command inventory from
   `docs/CLI-REFERENCE.md` and `src/nyxloom/cli.py`. Implement only the
   approved ownership map above. Keep all current command safeguards and
   handler outcomes unless D-001..D-018 says otherwise.
3. Replace the independent argparse/help/dispatch path with the three
   cli-extended registries. Run actual parse-to-handler probes for each
   advertised path, option, alias, choice, arity, required/optional input,
   placement, and prohibited combination. Verify every moved old path fails
   on `nyxloom` and reaches only its new executable. Verify generated help and
   the canonical reference match the parsed grammar.
4. Implement the local lint split and backlog wizard exactly as above. Use
   temporary projects and an injected prompt driver for mutation-path tests.
   Compare entry and index bytes on cancellation, invalid input, and schema
   refusal; prove the valid path preserves unmanaged fields and body text.
5. Add and verify the four wheel entrypoints, optional extra, bundled library,
   supervisor command, and container healthcheck. Prove the isolated installed
   wheel can import and run without the repository checkout.
6. Update every required document in the same change, including the migration
   table and parser-checked Consumer examples. Do not leave the reference,
   README, SPEC, or process docs describing removed paths as current behavior.
7. Produce `nyxloom-trove/reports/nyxloom-P112-LOG.md` with probe argv,
   fixtures, stdout/stderr, exit status, filesystem effects, wheel origin,
   and gate evidence. Produce `nyxloom-trove/reports/nyxloom-P112-REPORT.md`
   with oracle traceability and actual result for every lane.
8. Run `nyxloom lint` on the P112 handoff and all affected temporary fixtures.
   Commit the implementation in the assigned worktree, then run the exact
   declared gate from `nyxloom/`:

   ```bash
   ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-adoption tester-unified
   ```

   Inspect gate history first; attach to an active run instead of duplicating
   it. Record the exact judged commit and every lane's actual result. A partial
   lane is not green.

## Oracles

1. For every command in the migration table, exercise at least one valid
   invocation with its handler replaced by a fixture-safe callback and prove
   exactly the intended registered handler runs once. Exercise missing and
   extra positionals, invalid choice/type, omitted values, options before and
   after the command boundary, repeated options, and each declared conflict or
   required alternative. Test parser behavior, not help-string presence.
2. Compare top-level help, `help <verb>`, nested help, usage errors, and
   accepted parser syntax for all three CLI programs. `-h` and removed
   `nyxloom` host/session paths fail. `nyxloom version` and `nyxloom
   --version` use the same Nyxloom distribution identity; each new user CLI has
   its own truthful program name. These paths create no Nyxloom state/log
   files.
3. In a fixture with no Nyxloom daemon, run `nyxloom init`, project-local
   `nyxloom lint`, a harmless harness discovery/help path, and representative
   local `nyxloomctl` read commands. Confirm each uses only its owned data
   boundary. Test `nyxloomctl lint` against two registered fixture projects
   and `nyxloom lint` against one unregistered cwd project; the scopes must not
   collapse.
4. For the backlog wizard, test each editable field, enum choice, explicit
   CLI seed, empty optional field, invalid integer/choice, missing entry,
   schema refusal, non-TTY, and cancellation at each prompt boundary. A failed
   or canceled flow leaves both entry and `INDEX.md` byte-identical. A valid
   edit changes only the approved metadata, retains other frontmatter and the
   body bytes, validates the complete candidate before writing, and updates
   the generated index after the entry write. Status, merge, and closed fields
   remain controlled by existing transitions. Verify creation still supports
   the old scriptable syntax and `--body-from`.
5. Build the wheel from the implementation commit, install it in a fresh
   isolated environment, remove checkout and `PYTHONPATH` access, and verify
   installed metadata lists all four scripts and `cli_extended` files.
   `nyxloom`, `nyxloom-harness`, and `nyxloomctl` help/version plus
   representative nested help work from the installed wheel. Base paths work
   without Questionary; the interactive extra supplies the prompt driver.
6. Verify `nyxloomd` is an installed executable target that invokes the current
   daemon lifecycle; verify supervisor default and compose healthcheck use the
   new binary names, with no `python -m nyxloom.cli daemon` left in their live
   command strings. Preserve signal, logging, and healthcheck behavior.
7. Parse every command example in README, DESIGN-GUIDE, and CONSUMERS against
   the built CLI; check closed public choices are documented and all
   cross-document anchors resolve.
8. Run the complete `tester-unified` project gate on a clean committed
   revision. Report every declared lane's status and exact commit. The gate
   container is the judge; a devcontainer pytest run is not ship evidence.

## BLOCKED rule

Write `BLOCKED: <specific unmet contract>` to `nyxloom-trove/reports/nyxloom-P112-LOG.md`,
commit the log in the implementation worktree, and stop dependent work if:

- the library prerequisite/API is absent from the implementation base;
- the required registry or prompt API would need a consumer-side second parser
  or a local hand-rolled substitute;
- the Nyxloom wheel cannot contain the library source without a runtime
  checkout path, a separate `cli-extended` runtime distribution, or an
  unmaintained duplicate;
- the declared gate has a deterministic environment/governance failure outside
  Nyxloom's control.

Do not use BLOCKED for a product decision. If implementation evidence reveals
an externally visible choice not covered by D-001..D-018, record a proposed
`D-019` (or next available number), show current behavior, alternatives,
compatibility impact, and recommendation, and stop only work dependent on that
answer while continuing independent audit work.
