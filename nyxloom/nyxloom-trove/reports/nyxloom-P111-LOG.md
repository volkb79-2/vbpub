# P111 CLI adoption audit log

Date: 2026-09-26

## Result

Audit artifacts and safe probes are complete. The original exact
`tester-unified` attempts were refused because the worktree was dirty. On
2026-09-27, the operator authorized commits from the assigned worktree and
removed the process blocker. The clean audit commit `ca0fe0751c5f27b4c88537986b50e99e1c5a5273`
then passed the complete `tester-unified` lane. No parser, runtime, test,
packaging, or cli-extended source was changed.

The original dirty-tree refusals remain in P12 and P14 as historical evidence.
They did not start a lane and are not a gate result. O6 is pending the final
committed-revision run; do not report it green until every lane completes.

## Revision and scope

- Worktree: `/workspaces/vbpub/.worktrees/nyxloom-cli-extended`
- Branch: `nyxloom-cli-extended`
- Audited CLI source snapshot from P111: `253125a696301c2f6df17561fc2af4f7179f6d80`
- Git HEAD when the audit resumed: `d3cca99dffba8ffa3f71d044f234c55e4106a490`
- Primary entrypoint: `src/nyxloom/cli.py`, installed as `nyxloom`
- In scope: README, canonical CLI reference, Design Guide, Consumers Guide,
  decisions, this log, and the report.
- Out of scope and unchanged: runtime/parser/dispatch, tests, pyproject,
  cmru.toml, project trove config, and `libraries/cli-extended` source.

## Probe ledger

All commands were local and offline. Temporary state/project fixtures were
used. No real project state, user transcript, credential, daemon, repository
remote, release, provider, or notification destination was touched.

| ID | Invocation / fixture | Result, streams, status, side effects | Evidence / limits |
|---|---|---|---|
| P1 | Parser declaration introspection of `_build_parser()` and `main()` dispatch at the pinned source snapshot. | 35 top-level choices, 55 parser nodes, 45 dispatchable leaf actions, one empty reserved `gate`; 37 positional declaration occurrences; 158 action-local option declarations / 168 spellings; 2 root global options. | `src/nyxloom/cli.py`; declarations and dispatch recorded in `docs/CLI-REFERENCE.md`. |
| P2 | Every dispatchable leaf path supplied valid synthetic positionals/options; each `cmd_*` handler replaced by an inert callback. | 45/45 leaves parsed and reached exactly their intended handler once, return 0 from the stub; `gate` separately observed as an empty reserved path. No state touched. | Proves grammar-to-dispatch routing, not handler effects. |
| P3 | Each of the 158 action-local option declarations tested alone with synthetically valid required positionals/options. | 158/158 parser-accepted. No state touched. | Parser construction/parse probe; combined behavior is covered separately. |
| P4 | Invalid-input matrix, 94 cases across missing required inputs, extra/missing arity, invalid choices/types, mutually exclusive groups, and global-option placement. | 94 expected parser refusals or handler validations; no unexpected acceptance/refusal recorded. Parser errors exit 2; the `--show-tool-call-intent` dependency is a handler refusal unless `--show-tool-calls` is also present. No state touched. | `cli.py`, `tests/test_cli.py`, `tests/test_cli_extract.py`; one handler validation intentionally occurs after parse. |
| P5 | Placement probes: `--debug version`; `version --debug`; `--project-id p status`; `status --project-id p`. | Root `--debug` before a verb parses; root `--debug` after a verb and action `--project-id` before its command are rejected; command-local `--project-id` after `status` parses. | Confirms current global/action placement; no state touched by parser probe. |
| P6 | 48 shell command examples in README and CONSUMERS checked against the primary parser, with actual top-level help interception honored. | 48/48 examples parse; 0 failures. Examples are parser-valid; examples that contact a provider or mutate state were not executed. | README, `docs/CONSUMERS.md`, `cli.py`. |
| P7 | Local Markdown links and anchors in all seven touched documents, using GitHub heading-anchor rules. | 32 local links/anchors checked; 0 missing targets after final edits. | README, CLI reference, Design Guide, Consumers, decisions, LOG, REPORT. |
| P8 | Each form in a fresh temporary `NYXLOOM_STATE`, `PYTHONPATH=src`: bare invocation; `--help`; `-h`; `--version`; `version`; `extract --help`; bare `extract`. | Bare command: exit 2, grouped help on stderr. Help aliases, version forms, nested help: exit 0 and output on stdout. Bare `extract`: exit 2 with nested usage on stderr. Every invocation created a zero-byte `logs/nyxloom.jsonl` under the fresh state root. Version outputs differ: `nyxloom 0.0.0+unknown` vs `0.0.0+unknown`. | `cli.py::main`, `_bootstrap_logging`, `log.configure`; filesystem delta observed per isolated state root. Side-effect defect recorded. |
| P9 | Minimal temporary project with valid backlog config and no entries/index; invoke `backlog list` from that project directory. | Exit 0, index text printed; creates `nyxloom-trove/backlog/INDEX.md` (175 bytes in fixture) where no index existed. | `cli.py::cmd_backlog_list`, `backlog_entries`; proves the display command writes. No source/user project touched. |
| P10 | Existing test assertion review for selected boundaries. | Inspected behavior assertions for resync dry-run, free-model refresh dry-run, capability-map dry-run/findings suppression, doctor rebuild/write, and tool-intent dependency. Tests were not run by this probe. | `tests/test_cli.py`, `tests/test_cli_extract.py`, `tests/test_resync.py`, `tests/test_free_models.py`, `tests/test_doctor.py`. |
| P11 | `./run-gate.py history tester-unified --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-extended --json` before gate attempt. | Exit 0; `tester-unified` history is empty and `latest` is null. No active run found. Read-only. | Run-gate rev 46; history store reported under the judged worktree. |
| P12 | Required gate: `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-extended tester-unified`. | Exit 2: `run-gate: refusing to judge a dirty tree: /workspaces/vbpub/.worktrees/nyxloom-cli-extended has 5 uncommitted change(s) (first: ' M nyxloom/README.md') — commit or pass --allow-dirty`. No lane/container started. | `run-gate.toml` marks `tester-unified` `clean_tree=true`; the worktree was dirty. The no-commit instruction was later explicitly removed by the operator. Historical refusal, not a gate result. |
| P13 | P111 handoff lint with `NYXLOOM_STATE` set to a temporary root containing a symlink to the authoritative existing `routes.toml`; command: `PYTHONPATH=src python -m nyxloom.cli lint nyxloom-trove/handoffs/nyxloom-P111-cli-extended-audit.md`. | Exit 0; stdout `clean`; no stderr. Logging stayed in the temporary state root. | Uses the existing route/tier source without writing to global state. |
| P14 | Repeated the required exact gate after all seven deliverables existed in the worktree. | Final tree still refused before lane startup for uncommitted changes; no lane/container started. | Historical dirty-tree refusal; the operator later authorized the required worktree commit. |

The 94-case matrix includes one intentionally handler-level refusal: argparse
accepts `extract --show-tool-call-intent`, then `cmd_extract` rejects it unless
`--show-tool-calls` is also present. This is not counted as a parser defect; it
is a grammar/help declaration mismatch proposed for registry-level validation.

## Source and test evidence reviewed

- Parser, dispatch, help/version bootstrap, and handlers: `src/nyxloom/cli.py`.
- Logging side effect/version identity: `src/nyxloom/log.py`,
  `src/nyxloom/__init__.py`.
- Session output/format behavior: `src/nyxloom/session_extract/` and its
  README; `tests/test_cli_extract.py` assertions inspected.
- State/effect assertions inspected in `tests/test_cli.py`,
  `tests/test_cli_help.py`, `tests/test_effects_dispatch.py`,
  `tests/test_resync.py`, `tests/test_free_models.py`, `tests/test_doctor.py`,
  `tests/test_resume_guard.py`, `tests/test_control_auth.py`, and
  `tests/test_migrate_store.py`.
- The handoff names `src/nyxloom/cli_support.py` and
  `src/nyxloom/output.py`; neither exists in this checkout. Their relevant
  responsibilities reside in `cli.py`, `log.py`, and session renderers.
- Existing assertions were reviewed, not executed separately. No successful
  real notification/provider/remote call or stateful mutation was performed.
  Such paths remain bounded by current tests/source evidence, not live probes.

## Decisions and backlog

At the audit snapshot, open decisions D-001 through D-006 covered `-h`,
read-only backlog listing, ignored/refused option combinations, unknown
explicit project selectors, the empty `gate` path, and `--debug` versus
verbosity/traceback semantics. The operator approved all six on 2026-09-27;
the runtime remains unchanged until the adoption implementation.

Backlog dispositions for NL-10 and NL-13 through NL-22 are in the canonical
CLI reference. No backlog item was implemented. NL-16 remains a long-term
wheel-boundary question; approved cli-extended bundling targets the current
Nyxloom wheel.

## Follow-up implementation file map (not authorized by P111)

- `src/nyxloom/cli.py`: replace parallel argparse/help/dispatch declarations
  with one cli-extended registry; move side-effecting logging bootstrap after
  help/version classification; map errors, cancellation, output, and current
  safeguards.
- `pyproject.toml`: include the `cli_extended` package in the existing Nyxloom
  wheel without introducing a separate release/runtime distribution.
- Nyxloom CLI tests: `tests/test_cli.py`, `tests/test_cli_help.py`,
  `tests/test_effects_dispatch.py`, affected command tests, and a new isolated
  installed-wheel test.
- User docs: `README.md`, `docs/CLI-REFERENCE.md`, `docs/DESIGN-GUIDE.md`,
  `docs/CONSUMERS.md`, `docs/USAGE.md`, and the appropriate `docs/SPEC.md`
  section. `docs/USAGE.md` and `docs/SPEC.md` were outside P111 write scope.
- Nyxloom declares no verb aliases; its existing option aliases map directly
  to `OptionSpec.flags`, so no alias API is needed. A separately scoped
  cli-extended prerequisite must decouple the `mutating` label from required
  confirmation/`--yes` (preserving current defaults for other consumers) and
  add the approved optional Questionary prompt API. Nyxloom will use its
  existing operation/state safeguards as consent and `nyxloom[interactive]`
  for interactive authoring. Do not work around the library boundary with a
  second parser or false declarations.

## Gate status

The initial history query found no active `tester-unified` run. The initial
gate attempts were refused before container startup because the worktree was
dirty. On 2026-09-27, the operator explicitly authorized feature-worktree
commits and approved changing `nyxloom-trove/STANDING.md` to remove the
no-commit restriction and blanket docs/packaging freeze. The historical
P12/P14 refusals are not green evidence.

## Operator review addendum - 2026-09-27

The operator resolved D-001 through D-006 with the recommendations recorded in
the decision entries. D-002 means `backlog list` must be read-only; the chosen
implementation is in-memory rendering for a missing index, with `backlog
index` as the explicit writer.

The operator approved three user-facing console scripts in the same Nyxloom
wheel: `nyxloom` for local trove authoring, `nyxloom-harness` for AI-harness
workflows, and `nyxloomctl` for all local host-control/admin/developer
diagnostics. All current host-control command families move to `nyxloomctl`
without legacy forwarding paths in `nyxloom`;
all `extract*` commands move to `nyxloom-harness`. `nyxloom` and
`nyxloom-harness` must work without daemon availability. The dashboard remains
the current HTTP/SSE client; remote `nyxloomctl` operations are deferred. The
operator also approved a directly executable same-wheel `nyxloomd` service
launcher, moving the local daemon command to `nyxloomctl daemon`, adding the
optional shared Questionary API, and exposing its Nyxloom use through
`nyxloom[interactive]`. D-013 records removal of old command paths and a
required old-to-new migration table. D-014 selects managed backlog entry
create/edit as the first consumer.
After documenting these boundaries in README/reference, a follow-up link scan
checked 33 local links/anchors with none missing; the scoped handoff lint
again printed `clean`.

The operator resolved D-015 and D-016 on 2026-09-27. `nyxloom lint` will lint
the current project locally, while `nyxloomctl lint` preserves the existing
no-argument all-registered-project scan. The interactive backlog grammar is
`nyxloom backlog new --interactive [TITLE]` and `nyxloom backlog edit
ENTRY_ID`; omitted interactive TITLE is prompted, and noninteractive `new
TITLE` remains available. Both flows use the metadata fields already supported
by `backlog new`; current field options seed interactive prompts, and empty
optional values clear the field. Creation keeps the current body-template or
`--body-from` path. Editing preserves other metadata and Markdown body text,
validates the whole schema candidate before writing, and leaves status/merge-
owned fields to transition helpers. README, Design Guide, and CLI reference
record these as approved target behavior, not shipped runtime behavior.

## P15 - required exact-revision gate

Immediately before starting, `./run-gate.py history tester-unified --worktree
/workspaces/vbpub/.worktrees/nyxloom-cli-extended --json` exited 0 and showed
no active run. It listed only the prior dirty-tree refusal. The gate command
was then run from the Nyxloom project root:

```text
./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-extended tester-unified
```

Result: **PASS, exit 0**, on clean commit
`ca0fe0751c5f27b4c88537986b50e99e1c5a5273`. The only declared lane,
`tester-unified`, completed in 145.366 seconds. Assay claims were R0 PASS and
R1 PASS. R1 reports 100.0% changed-line coverage with 0 changed production
lines considered; this is not a coverage claim about the documentation-only
diff. Pytest ran as
`/opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-branch --cov-report=json:coverage.json`.
The verdict is
`nyxloom/.assay/verdict-tester-unified.json`; history marks the tree clean and
records no OOM or limit-drift events. Peak memory was 938,237,952 bytes.

Assay emitted its advisory that it was imported from the selected worktree's
editable source rather than an installed distribution; this follows the
repository's in-repo Assay consumption policy and did not fail the lane. The
full lane completed; no partial result is reported as green.
