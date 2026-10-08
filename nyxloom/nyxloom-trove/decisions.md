# nyxloom dev decisions inbox — product calls awaiting the user (D-<NNN>).

## 2026-09-02 — retire the coverage/mutation/canary toolkit and GA1/GA4 gate-verify (nyxloom-P98)

nyxloom-P98 deletes `src/nyxloom/coverage_gate.py`, the gate-judgment half of
`src/nyxloom/mutation_gate.py` (its pure mutant-generation engine survives as
`src/nyxloom/mutants.py`, the only piece `tools/remote_mutation_audit.py`
needs), and `src/nyxloom/gate_canary.py`, and retires GA1
(`nyxloom gate verify`, the CLI "gate for the gate") and GA4 (the daemon's
periodic gate-verify cadence) end to end. This reverses the 2026-07-27
operator directive that enabled `mutation_gate` in
`nyxloom-trove/nyxloom.toml`, on the premise that the toolkit modules earn
their keep as something nyxloom's own gate — or another project's — actually
runs. That premise no longer holds: nyxloom's own `[gates.tester-unified]`
has run entirely through `run-gate.py` (nyxloom-P48) since that package
landed, and never declared a `phase='mutation'` gate that would have
exercised the toolkit; no project, including nyxloom itself, ever imported
these modules as a library. GA1/GA4's external gate-trustworthiness
verification (proving a declared gate genuinely rejects a known-bad canary)
is superseded by Assay's own R2/R3 mechanisms once a project declares
assay/run-gate lanes, as nyxloom itself now does. This executes the prior
analysis in
`nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md`'s
"Deletion inventory and Assay transfer check" section, including that
report's endorsement of deleting `gate_canary.py` because Assay's own R3
canary mechanism is present and stronger.

One deliberate exception, not a missed cleanup: `Policy.
gate_verify_interval_days` (`config.py`) and `ReconcileInput.
days_since_gate_verify` (`reconcile.py`) stay declared, permanently unread by
any live code path once this package's scheduling removal lands.
`tests/legacy_planner.py` — a mechanically self-verified, byte-identical
snapshot of `reconcile.py` at commit `052857ae`, forbidden to edit — reads
both fields unconditionally off the same production `Policy`/
`ReconcileInput` instances the live planner consumes, so deleting either
field would break that file's own byte-identity self-check. They are kept
declared for that reason alone, not because anything still uses them.

## D-001 · 2026-09-26 · nyxloom-P111 · DECIDED 2026-09-27
**Question:** Should the CLI remove the currently accepted short -h help alias as part of cli-extended adoption, or should the library gain an explicit compatibility alias?
**Why it matters:** The current parser accepts -h at top-level and nested help paths. cli-extended specifies --help. Keeping -h requires an explicit library contract so a parallel argparse alias is not retained outside the registry.
**Options:**
- Remove -h and retain --help, help, and help <verb> under the registry.
- Extend cli-extended's generated help contract to accept both -h and --help.
**Recommendation:** Remove -h. Nyxloom's user documentation does not promise it, and removing it keeps the adopted grammar owned by the library. This changes conventional shorthand from help success to an option error; scripts can replace it with --help.
**Decision:** Approved: remove `-h` and follow cli-extended's documented help grammar.
**Context pointers:** nyxloom/docs/CLI-REFERENCE.md, Invocation and output contract; cli-extended SPEC sections 1-3; tests/test_cli_help.py.
**Resume prompt:** "Resolve D-001 for the Nyxloom CLI adoption. Record the selected help alias contract and compatibility rationale."

## D-002 · 2026-09-26 · nyxloom-P111 · DECIDED 2026-09-27
**Question:** Should backlog list remain read-only when INDEX.md is missing, with index generation reserved for backlog index?
**Why it matters:** cmd_backlog_list currently writes INDEX.md if absent, although list is a read verb and index is the explicit generator. A fresh listing therefore changes the checkout without an obvious write action.
**Options:**
- Keep the current lazy write for backward compatibility.
- Render the current index in memory for list and leave filesystem writes to backlog index.
- Refuse list when INDEX.md is missing and instruct the user to run backlog index.
**Recommendation:** Render in memory for list. backlog_entries.render_index(load_entries(cfg)) already supplies the deterministic content; backlog index remains the explicit committed-file generator. Existing callers keep useful output, while a read no longer mutates files.
**Decision:** Approved: `backlog list` is read-only and renders the listing in memory when `INDEX.md` is missing; `backlog index` remains the explicit writer. Current implementation still creates a missing index until the adoption change lands.
**Context pointers:** nyxloom/src/nyxloom/cli.py cmd_backlog_list; nyxloom/src/nyxloom/backlog_entries.py render_index/write_index; nyxloom/docs/CLI-REFERENCE.md; tests/test_backlog_entries.py TestO2Index.
**Resume prompt:** "Resolve D-002 for backlog list/index semantics. Record whether list renders in memory, retains its current lazy write, or refuses until index is run."

## D-003 · 2026-09-26 · nyxloom-P111 · DECIDED 2026-09-27
**Question:** Should parser-accepted options that are ignored or always refused be rejected or removed in the declared CLI grammar?
**Why it matters:** doctor --write without --rebuild, and doctor --rebuild/--write combined with --liveness, are accepted but the rebuild/write controls are ignored on that path. resync --apply-content-merges without --apply and capability-map refresh --dry-run --emit-findings are also accepted but suppress the requested effect. extract-lossless --redact-pattern is accepted but the handler always rejects it. cli-extended requires parser/help/dispatch agreement and unsupported combinations to fail clearly.
**Options:**
- Reject each ignored option combination with a clear command-local error; make --liveness exclusive with --rebuild/--write; remove --redact-pattern from extract-lossless.
- Preserve current no-op combinations and rejection timing as compatibility behavior.
- Give secondary options an implicit effect (for example, make --write imply --rebuild or let --emit-findings write during dry-run).
**Recommendation:** Reject the ignored combinations, make liveness mutually exclusive with rebuild/write, and remove the always-refused lossless option. This preserves dry-run and explicit safeguards, makes accepted syntax truthful, and does not change any combination that currently completes the requested action.
**Decision:** Approved: reject ineffective option combinations, make `doctor --liveness` exclusive with `--rebuild` and `--write`, and remove `extract-lossless --redact-pattern` from the accepted grammar.
**Context pointers:** nyxloom/src/nyxloom/cli.py cmd_doctor and liveness branch, cmd_resync, cmd_capability_map_refresh, extract-lossless registration/handler; nyxloom/docs/CLI-REFERENCE.md; tests/test_cli.py; tests/test_cli_extract.py.
**Resume prompt:** "Resolve D-003 for ineffective CLI option combinations. Record the selected refusal/removal behavior before writing the cli-extended migration."

## D-004 · 2026-09-26 · nyxloom-P111 · DECIDED 2026-09-27
**Question:** Should status and doctor reject an unknown explicit --project-id instead of returning an empty or host-only result?
**Why it matters:** Both help texts require a registered project ID. status silently selects no projects and exits 0; doctor silently skips project checks, and its normal path still runs host checks while --liveness returns an empty successful result. events deliberately permits unknown IDs as an empty read-only debug query, and finding list explicitly reads the selected finding store without registry validation; this proposal does not change those separate contracts.
**Options:**
- Keep empty/host-only output for compatibility.
- Refuse an unknown explicit selector with a clear nonzero error for status and doctor only.
**Recommendation:** Refuse in status and doctor. A typo should not certify an empty project view or imply the named project's health was checked. Keep events' documented empty-query behavior and finding list's current targeted-store behavior.
**Decision:** Approved: status and doctor refuse unknown explicit project IDs in every mode; events and finding list retain their distinct current contracts.
**Context pointers:** nyxloom/src/nyxloom/cli.py cmd_status/cmd_doctor/cmd_events/cmd_finding_list; nyxloom/docs/CLI-REFERENCE.md; tests/test_cli.py.
**Resume prompt:** "Resolve D-004 for unknown project filters in status and doctor. Preserve the intentionally different events/finding-list contracts unless separately decided."

## D-005 · 2026-09-26 · nyxloom-P111 · DECIDED 2026-09-27
**Question:** Should the empty reserved gate command path remain in the public Nyxloom CLI?
**Why it matters:** gate has no registered child action and exits 2 after printing root help. Project gates run through each project's run-gate.py/Assay lane, and the former nyxloom gate verify path was retired.
**Options:**
- Remove gate from top-level help/parser.
- Keep the reserved path to signal a future Nyxloom-owned gate command.
**Recommendation:** Remove the empty path unless a concrete Nyxloom-owned action is approved. Existing gate invocations become unknown-command errors; project run-gate workflows do not change.
**Decision:** Approved: remove the empty reserved `gate` path from the primary CLI.
**Context pointers:** nyxloom/src/nyxloom/cli.py gate registration/dispatch; nyxloom/docs/USAGE.md; NL-13 and NL-14; nyxloom/docs/CLI-REFERENCE.md.
**Resume prompt:** "Resolve D-005 for the empty reserved gate path before finalizing the cli-extended adoption grammar."

## D-006 · 2026-09-26 · nyxloom-P111 · DECIDED 2026-09-27
**Question:** How should Nyxloom preserve its current traceback switch while adopting cli-extended's verbosity meaning for --debug?
**Why it matters:** Nyxloom currently treats --debug as “re-raise exceptions with traceback” and reads NYXLOOM_LOG_LEVEL only from the environment. cli-extended defines --debug as an alias for --log-level=debug; these are different behaviors. Silently keeping Nyxloom's meaning would misstate the library contract, while silently changing it would alter script/operator diagnostics.
**Options:**
- Adopt cli-extended semantics: --debug controls verbosity, add --log-level, and move traceback behavior to a separate --traceback flag.
- Keep --debug as traceback and extend cli-extended with a Nyxloom-specific override.
- Drop the traceback switch and use the library's verbosity contract only.
**Recommendation:** Adopt the library meaning and add --traceback for the existing explicit diagnostic behavior. Keep NYXLOOM_LOG_LEVEL as the environment default, with an explicit --log-level taking precedence. This preserves both capabilities with a small command-line compatibility change for callers that used --debug only to request traceback output.
**Decision:** Approved: `--debug` means verbosity per cli-extended; add `--log-level`, preserve `NYXLOOM_LOG_LEVEL` as its environment default, and move traceback behavior to `--traceback`.
**Context pointers:** nyxloom/src/nyxloom/cli.py main/_bootstrap_logging; nyxloom/docs/CLI-REFERENCE.md; cli-extended SPEC sections 5 and 8.
**Resume prompt:** "Resolve D-006 for Nyxloom's --debug/--log-level/traceback mapping. Update the follow-up CLI registry contract to match."

## D-007 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** How should Nyxloom divide its user-facing command surfaces and wheel boundary?
**Why it matters:** `extract*` reads local AI-harness session stores; `init`, `onboard`, `lint`, and `backlog` operate on project trove files; workflow/admin commands operate on host-registered projects and daemon state. One installed command currently spans these audiences and data boundaries, and every invocation initializes the same host logging state.
**Options:**
- Keep one executable and organize its registry/help into explicit command groups.
- Add a separate session-extraction executable in the same Nyxloom wheel; keep trove and operator commands together.
- Split session, project-authoring, and operator tools into separate executables in one wheel.
- Reopen the approved single-Nyxloom-wheel boundary and publish separate distribution(s) for one or more tool families; this requires revisiting that earlier packaging decision.
**Recommendation:** Split by user and data boundary while keeping one wheel. Keep local authoring and operator workflows in `nyxloom`; give AI-harness workflows their own `nyxloom-harness` command; put daemon interaction, administration, and developer diagnostics under `nyxloomctl`. Keep the three tools independently invocable and avoid making local authoring or harness workflows depend on daemon availability.
**Decision:** Approved: provide `nyxloom`, `nyxloom-harness`, and `nyxloomctl` as three console commands in the existing Nyxloom wheel. `nyxloom` owns local trove/project authoring (`init`, `onboard`, `lint`, and `backlog`); `nyxloom-harness` owns every session extraction/discovery command; `nyxloomctl` owns all other host-control commands, including project registry, workflow/task state, credentials, routes/catalogs, diagnostics, and daemon operation. The split is by command surface, not distribution. Keep `nyxloom` and `nyxloom-harness` usable without a running daemon. The implementation handoff must list the complete command-to-executable mapping and old-to-new paths.
**Operator constraints (2026-09-27):** CLI workflows must remain usable when the daemon is unavailable. Trove authoring should include an interactive editing path that validates structured files before writing. The operator approved three user-facing executables in one Nyxloom wheel: local authoring/operator work (`nyxloom`), harness workflows (`nyxloom-harness`), and daemon administration/debug (`nyxloomctl`). Split local admin now; remote/API work is a later design. The service also gets a separate `nyxloomd` launcher. The detailed command-to-executable map is a required handoff artifact and must follow each command's data target, effects, and user journey.
**Context pointers:** nyxloom/docs/CLI-REFERENCE.md, "CLI ownership boundaries"; nyxloom/pyproject.toml `[project.scripts]`; nyxloom/src/nyxloom/cli.py `main()` and `_bootstrap_logging()`; nyxloom/src/nyxloom/daemon.py HTTP surface; nyxloom/docs/ARCHITECTURE.md §§3, 10; nyxloom/docs/SPEC.md §12.
**Resume prompt:** "Resolve whether Nyxloom should expose one CLI or separate executables for session extraction, trove authoring, and operator control. Decide executable count separately from wheel/distribution count."

## D-008 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** Which CLI surface owns daemon interaction, and must local consumer workflows depend on it?
**Why it matters:** The current `nyxloom` command dispatches local modules directly; the dashboard is the existing HTTP/SSE client. GET APIs expose registered host/project views to the trusted network, while POST mutations require the operator credential. The project workflow is currently driven from registered repositories, trove files, and dispatched work; there is no project-scoped consumer credential/client contract.
**Options:**
- Keep all workflows local and leave the dashboard as the only daemon HTTP/SSE client.
- Split a daemon-facing `nyxloomctl` from local authoring/operator workflows; design its transport and operations separately.
- Make the primary `nyxloom` CLI an authenticated daemon HTTP client.
**Recommendation:** Keep local authoring/operator workflows independent of the daemon. Give daemon interaction a distinct owner, and do not invent its network/API contract during parser adoption.
**Decision:** Approved: `nyxloomctl` owns daemon interaction/admin/debug workflows. `nyxloom` and `nyxloom-harness` must remain usable when the daemon is unavailable. The current dashboard remains the daemon HTTP/SSE client; the `nyxloomctl` transport, endpoint set, credentials, and remote operations are a separate follow-up design, consistent with the operator's direction to keep current workflows local for now.
**Context pointers:** nyxloom/src/nyxloom/cli.py `main()`/dispatch; nyxloom/src/nyxloom/daemon.py `_CONFIG_POST_PATHS`, `_handle_get`, `_handle_post`; nyxloom/src/nyxloom/control_auth.py; nyxloom/docs/ARCHITECTURE.md §§3, 10; nyxloom/docs/SPEC.md §12; nyxloom/docs/CLI-REFERENCE.md, "CLI ownership boundaries".
**Resume prompt:** "Resolve the Nyxloom daemon client/API boundary: local operator CLI, authenticated remote operator CLI, and/or a project-scoped consumer API. Name the actor, identity, operations, and trust boundary for each."

## D-009 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** Should the resident daemon continue to start through `nyxloom daemon`, or should the wheel expose a service-specific `nyxloomd` console entrypoint?
**Why it matters:** The service is a long-running headless process but currently enters through the human CLI parser and `main()` bootstrap. A dedicated script could give service managers a stable process contract independent of interactive help, global CLI options, and command dispatch.
**Options:**
- Keep `nyxloom daemon [--foreground]` as the only supported launch surface.
- Add `nyxloomd` in the same wheel and make it the service-manager entrypoint; retain or deprecate the CLI subcommand.
- Keep both indefinitely as equivalent supported launch surfaces.
**Recommendation:** Prefer a dedicated `nyxloomd` entrypoint for service managers if the code can give it a clear signal, logging, and shutdown contract; keep a short migration bridge for `nyxloom daemon` only if existing operators rely on it. This is an executable boundary decision, not a separate wheel requirement.
**Decision:** Approved: install a directly executable `nyxloomd` console script from the Nyxloom wheel and make the container supervisor invoke it instead of `python -m nyxloom.cli daemon`. This is the daemon process launcher, separate from the three user-facing CLIs; it does not change the existing container/image boundary. The service command must retain the current foreground/signal/logging behavior required by the supervisor.
**Context pointers:** nyxloom/pyproject.toml `[project.scripts]`; nyxloom/src/nyxloom/cli.py `cmd_daemon`/`main`; nyxloom/src/nyxloom/daemon.py lifecycle; nyxloom/docs/runtime-process-model.md; nyxloom/docs/CLI-REFERENCE.md, "CLI ownership boundaries".
**Resume prompt:** "Implement the approved direct `nyxloomd` executable and container invocation using the current foreground/signal/logging behavior. Resolve the legacy `nyxloom daemon` spelling under D-011."

## D-010 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** Should `cli-extended` grow a reusable interactive prompt/wizard API backed by the already-adopted Questionary package?
**Why it matters:** Debian install v2 uses Questionary for typed/secret/select/checkbox prompts and validates wizard output with its own shipped loader; Netcup uses `cli-extended` but still hand-rolls several `input()` prompt helpers. `cli-extended` currently exposes interactive labels, TTY detection, and confirmation, but has no reusable multi-prompt API and no runtime dependencies.
**Options:**
- Add an optional Questionary-backed prompt API to `cli-extended`; keep field meanings, domain validation, summaries, and persistence in each consumer.
- Use Questionary directly in Nyxloom and leave `cli-extended` stdlib-only until another consumer proves the shared API.
- Keep interactive authoring out of this adoption and defer it to a separate Nyxloom feature.
**Recommendation:** Add a small optional prompt/wizard layer, not a generic configuration editor. It should centralize TTY refusal, cancellation, typed text/secret/boolean/select/multi-select questions, and test injection; consumers keep schema validation and file-write policy. Preserve a dependency-free base install and expose the Questionary dependency as an explicit optional extra.
**Decision:** Approved: add a reusable Questionary-backed prompt API to `cli-extended` behind an explicit optional dependency extra. The shared API owns prompt mechanics, TTY refusal, cancellation, and an injectable driver for tests; consumers own field meaning, domain/schema validation, summaries, diffs, and persistence. Keep the base `cli-extended` install dependency-free. Nyxloom exposes its use through its own optional `interactive` extra (see D-012).
**Context pointers:** libraries/cli-extended/SPEC.md §§3, 5, 6, 9, 10; libraries/cli-extended/README.md "Adopt it"; scripts/debian-install-v2/debian_install_v2/wizard.py; scripts/debian-install-v2/wizard-requirements.txt; scripts/netcup/install-host.py `_prompt_choice`/`_prompt_text`/`_prompt_yes_no`.
**Resume prompt:** "Implement the approved optional Questionary-backed prompt API in cli-extended. Keep domain validation and writes in each consumer."

## D-011 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** Should the existing `nyxloom daemon` verb remain as a compatibility wrapper after the container moves to the `nyxloomd` executable?
**Why it matters:** The service entrypoint is now a dedicated same-wheel executable. Keeping the old verb preserves manual launch compatibility but retains a second public spelling; removing it changes the CLI grammar. Its current `--foreground` flag is accepted but ignored; D-003's approved rule for ineffective syntax applies.
**Options:**
- Keep `nyxloom daemon` as a thin wrapper to the same daemon runner for a documented transition period.
- Remove `nyxloom daemon`; service managers and operators use `nyxloomd` directly.
**Recommendation:** Keep the old verb as a thin compatibility wrapper for this adoption and make `nyxloomd` the documented service-manager path. Remove it only in a separately reviewed compatibility change.
**Decision:** Approved: move the local daemon command from `nyxloom daemon` to `nyxloomctl daemon`. It remains a local foreground operator/debug command using the existing daemon lifecycle and signal behavior. Remove the ineffective `--foreground` option. The container supervisor uses the dedicated `nyxloomd` service entrypoint from D-009.
**Context pointers:** nyxloom/src/nyxloom/cli.py `cmd_daemon`/`main`; nyxloom/nyxloomd/supervise.sh; nyxloom/pyproject.toml `[project.scripts]`.

## D-012 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** Should the Nyxloom interactive trove wizard require Questionary in every Nyxloom install or expose it as an optional extra?
**Why it matters:** The bundled `cli-extended` prompt API remains optional for its other consumers. Nyxloom must choose whether a standard install can run the interactive editor or whether users request that dependency explicitly.
**Options:**
- Declare the prompt dependency in Nyxloom's base installation.
- Add a Nyxloom `[interactive]` extra and keep the base installation free of the prompt dependency.
**Recommendation:** Use `[interactive]`. The CLI's ordinary help, authoring, harness, and host-control paths should not import Questionary unless a user starts an interactive workflow; the missing-extra error must name the exact install command.
**Decision:** Approved: expose the wizard through `nyxloom[interactive]`; keep Questionary optional in the Nyxloom distribution and import it only when an interactive path is requested.
**Context pointers:** nyxloom/pyproject.toml `[project.optional-dependencies]`; libraries/cli-extended/pyproject.toml; scripts/debian-install-v2/wizard-requirements.txt.

## D-013 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** Should legacy `nyxloom <host-control-command>` paths remain as deprecated forwarding aliases after all host-control commands move to `nyxloomctl`?
**Why it matters:** Removing paths makes the three CLI ownership boundary clear but breaks existing scripts; forwarding aliases preserve callers but keep host-control grammar and dispatch in the authoring CLI.
**Options:**
- Remove the old paths and publish an explicit old-to-new command migration table.
- Keep deprecated forwarding aliases in `nyxloom`, with a warning and a removal policy.
**Recommendation:** Remove old paths and provide a migration table. It preserves a real boundary, makes `nyxloom` usable without host-control initialization, and avoids maintaining two public command registries. The break is visible and documented.
**Decision:** Approved: remove the old host-control command paths from `nyxloom`; expose their commands only through `nyxloomctl` and publish an old-to-new command migration table. Do not retain compatibility forwarders or a second registry in `nyxloom`.
**Context pointers:** nyxloom/docs/CLI-REFERENCE.md, `CLI ownership boundaries`; D-007; libraries/cli-extended/SPEC.md §§2–3, 11.

## D-014 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** Which first Nyxloom authoring workflow should use the shared interactive prompt API?
**Why it matters:** A prompt API reduces hand-written terminal code, but the first consumer still needs a bounded set of fields, validation rules, and write behavior.
**Options:**
- Add guided create/edit for managed backlog entries.
- Add a project `nyxloom.toml` editor.
- Apply the shared prompt API only to the existing onboarding wizard.
**Recommendation:** Start with managed backlog entry creation/editing: the data has an explicit schema, existing read/write helpers, and a clear project-local authoring journey. Validate the full candidate before any write and preserve existing entry body content when editing.
**Decision:** Approved: the first Nyxloom consumer of the shared prompt API is a managed backlog entry create/edit flow. It must validate the complete frontmatter candidate against the shipped backlog schema before any file write, preserve the existing Markdown body when editing, and keep status/merge-owned fields under their existing transition functions. D-016 fixes the command spelling and title-input behavior.
**Context pointers:** nyxloom/src/nyxloom/backlog_entries.py; nyxloom/src/nyxloom/schemas/backlog-entry.schema.json; nyxloom/tests/test_backlog_entries.py; nyxloom/src/nyxloom/onboarding.py.

## D-015 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** Where should the current no-argument, all-registered-project lint scan live after the CLI split?
**Why it matters:** `lint` is both a project-authoring check and a host-wide inventory operation today. Moving the whole command would make local authoring depend on registry state; keeping only one spelling would hide the existing all-project workflow.
**Options:**
- Keep the no-argument scan on `nyxloom lint` and treat it as local authoring.
- Make `nyxloom lint` project-local and expose the all-registered-project scan as `nyxloomctl lint`.
- Remove the all-project scan.
**Recommendation:** Keep two explicit scopes. `nyxloom lint` with no paths discovers the project from the current directory and checks that project's configured handoffs. `nyxloomctl lint` preserves the existing all-registered-project scan. Explicit file paths remain available on the local command.
**Decision:** Approved: `nyxloom lint` is project-local; `nyxloomctl lint` owns the existing no-argument scan across registered projects. Do not remove the host-wide behavior.
**Context pointers:** nyxloom/src/nyxloom/cli.py `cmd_lint`; nyxloom/docs/CLI-REFERENCE.md, command inventory and ownership; D-007 and D-013.
**Resume prompt:** "Implement the approved lint boundary: project-local `nyxloom lint`, host-wide `nyxloomctl lint`, and no legacy host-wide behavior hidden in the local command."

## D-016 · 2026-09-27 · nyxloom-P111-review · DECIDED 2026-09-27
**Question:** What command spellings should expose managed backlog entry creation and editing through the shared interactive prompt API?
**Why it matters:** The new interactive path must be discoverable without replacing the existing scriptable create command or blurring create versus edit semantics.
**Options:**
- Add `nyxloom backlog new --interactive [TITLE]` and `nyxloom backlog edit ENTRY_ID`.
- Add a single `nyxloom backlog wizard [ENTRY_ID]` command for both operations.
- Leave exact spelling to the implementation handoff.
**Recommendation:** Make interactive creation explicit while keeping current noninteractive creation. Make editing an explicitly interactive operation keyed by the managed entry ID.
**Decision:** Approved: add `nyxloom backlog new --interactive [TITLE]` and `nyxloom backlog edit ENTRY_ID`. With interactive creation, TITLE may be omitted and is then requested by the prompt flow; if supplied it seeds the title prompt. Without `--interactive`, the existing required TITLE and noninteractive behavior remain. The wizard's editable metadata is the same set already supported by `backlog new`: title, type, severity, priority, component, context_estimate, folds_into, provenance, filed_by, and spec_owner. Existing CLI field options seed interactive prompts; an empty optional value clears that field. The existing body template or `--body-from` behavior remains in force for creation. `edit ENTRY_ID` loads the managed entry and prompts for that same metadata set. It preserves fields outside the form, including generated/transition-owned metadata, and preserves the Markdown body verbatim. The complete candidate must validate before writing; status and merge-owned fields stay under their transition functions.
**Context pointers:** nyxloom/src/nyxloom/backlog_entries.py; nyxloom/src/nyxloom/schemas/backlog-entry.schema.json; nyxloom/docs/CLI-REFERENCE.md; D-010 and D-014.
**Resume prompt:** "Implement the approved backlog wizard grammar and field ownership from D-014/D-016; retain scriptable `new TITLE` and validate before any write."

## D-017 · 2026-09-28 · nyxloom-P112 · DECIDED 2026-09-28
**Question:** What should an empty invocation of a CLI or delegated command group do after registry adoption?
**Why it matters:** The old `nyxloom backlog` group printed help but returned exit 2. cli-extended's contract treats an empty invocation as a help request that exits 0, so keeping the old status would make Nyxloom's delegated grammar disagree with the shared boundary.
**Options:**
- Preserve exit 2 for empty command groups as a malformed invocation.
- Follow cli-extended: print generated help and exit 0 for empty invocation, with no state/log side effects.
**Recommendation:** Follow the shared cli-extended contract across root and delegated CLIs. A user who types a command group without its subcommand receives its choices; scripts that treated exit 2 as an error should pass a subcommand or `--help` explicitly.
**Decision:** Approved under the user's direction to align fully with cli-extended: empty root or delegated command invocation prints the applicable generated help and exits 0 without side effects.
**Context pointers:** `libraries/cli-extended/SPEC.md` §9; `nyxloom/docs/CLI-REFERENCE.md`; `tests/test_cli_help.py`; `tests/test_backlog_entries.py`.

## D-018 · 2026-09-28 · nyxloom-P112 · DECIDED 2026-09-28
**Question:** Should the installed CLI reject malformed values against their authoritative domain vocabularies and types before dispatch?
**Why it matters:** Backlog statuses and finding kinds/severities are closed values in their schema or registry, while event cursors are integers and finding fields have a `KEY=VALUE` form. Accepting arbitrary strings lets typos look like empty reads or pushes malformed input into handlers, where it can produce a different exit status and omit command help.
**Options:**
- Keep these declarations as free strings and rely on each handler to reject invalid values.
- Declare source-derived choices and parser types so invalid values fail as cli-extended usage errors before dispatch.
**Recommendation:** Use the owning sources for backlog statuses, finding kinds/severities, and integer sequence cursors. Validate finding field shape at parse time. Do not duplicate these vocabularies in the registry.
**Decision:** Approved under the user's direction to audit every option and align fully with cli-extended: declare backlog list status choices from `backlog_entries.STATUSES`, finding record/list kinds and record severities from `findings.FINDING_KINDS` and `findings.SEVERITIES`, parse `digest/events --since` as integers, and reject malformed `finding record --field` values before dispatch. Existing valid values remain valid; malformed values and declared option conflicts fail in the parser boundary with exit 2 and relevant generated help. Runtime, data, and filesystem failures continue to use their domain status (normally 1).
**Context pointers:** `nyxloom/src/nyxloom/cli_registry.py`; `nyxloom/src/nyxloom/backlog_entries.py`; `nyxloom/src/nyxloom/findings.py`; `nyxloom/src/nyxloom/cli.py`; `nyxloom/docs/CLI-REFERENCE.md`; P112's parser-to-handler and documentation oracles.

## D-019 · 2026-09-28 · nyxloom-P113 · DECIDED 2026-09-28
**Question:** Which parts of a Claude Code `AskUserQuestion` request belong in a prose extract?
**Why it matters:** The assistant tool call stores the question, a displayed header, choice labels and descriptions, and whether multiple choices are allowed. Omitting those fields can remove information the user saw when making a choice; rendering only the later answer also hides unanswered prompts and prompts without assistant prose copies.
**Options:**
- Render only the question and choice labels, retaining the existing answer format.
- Render the complete displayed question context at the call record, and repeat that context with a labeled operator answer when a recognized result arrives.
**Recommendation:** Preserve the question, header, choice labels and descriptions, and multi-select behavior at the prompt position; use the same marked question block with `OPERATOR:` for a recognized answer. Keep unrecognized result text intact instead of guessing its structure.
**Decision:** Approved by the user's direction that every question shown to the user and every answer must appear as prose. Claude Code extracts now render the complete displayed question context at its call record, even without an answer or tool ID, and repeat it with a labeled answer when the result is recognized. Explicit no-answer rows remain labeled; unsupported result shapes remain raw source text.
**Context pointers:** `nyxloom/src/nyxloom/session_extract/adapters/claude_code.py`; `nyxloom/tests/test_session_extract_claude_code.py`; `nyxloom/tests/test_session_extract_cli_acceptance.py`; `nyxloom/docs/DESIGN-GUIDE.md`.

## D-020 · 2026-10-08 · nyxloom-search-opt · DECIDED 2026-10-08
**Question:** How should session search reduce Python-side counting cost without an index?
**Why it matters:** On the same Codex session root, the four-term query `debian iso cloud qcow` took 147.0 seconds in Nyxloom versus 10.0 seconds for `rg`; the two-term query `cloud qcow` took 10.8 seconds versus 6.0 seconds for `rg`. A warm rerun of the archived implementation took 154.8 seconds and 11.1 seconds, respectively. Python's repeated token counting dominates the multi-term gap, while adding a process pool to every short query could make the two-term case slower.
**Options:**
- Keep exact unbounded term frequencies and sequential Python candidate counting.
- Start workers immediately and parallelize every search, with exact unbounded frequencies.
- Keep ripgrep as the candidate scanner; start a bounded process pool only after a candidate threshold, and saturate each query-term frequency per session.
**Recommendation:** Use the bounded pool only for larger candidate sets and cap per-session query-term frequencies at a documented fixed value.
**Decision:** Follow the user's direction to use bounded process workers and cap term frequencies. Ripgrep continues to scan candidate records; searches crossing 512 candidates use at most four worker processes, with bounded record batches and pending work. One pool serves all path batches. Smaller searches stay in the caller process. Process workers run only from the guarded `nyxloom-harness` entrypoints; embedded calls from arbitrary Python scripts stay inline to avoid Python 3.14 re-importing a caller that lacks a main guard. Each query term's frequency saturates at 64 per session. This preserves exact match presence and matched-word reporting; repeats above the cap no longer increase relevance, limiting the impact of very common words while avoiding repeated counting work.
**Evidence:** On the same warm store, the four-term search fell from 154.8 seconds to 59.9 seconds (2.6x faster), while the two-term search fell from 11.1 seconds to 9.0 seconds (1.2x faster). A warm `rg` scan took 3.9 seconds and 2.2 seconds, so the multi-term gap remains open. Eight workers took 63.2 seconds, no faster than four; a cap of 16 took 57.5 seconds, only a small improvement over 64, so the more score-preserving cap remains.
**Context pointers:** `nyxloom/src/nyxloom/harness_search.py`; `nyxloom/tests/test_harness_search.py`; `nyxloom/README.md`; `nyxloom/docs/DESIGN-GUIDE.md`; `nyxloom/docs/CONSUMERS.md`; `nyxloom/docs/CLI-REFERENCE.md`.
