# P112 report: adopt cli-extended across Nyxloom's command surfaces

Date: 2026-09-28 (controller follow-up 2026-09-29)
Result: **DONE** — all seven handoff oracles pass after the review corrections.
The P112 implementation commit is now an ancestor of local `main` at
`f26f93cc`. No push to `origin/main` or release-tag change was made. The
separately requested session-extract R2 follow-up failed; its result and
triage are recorded below and do not replace the declared P112 gate evidence.

## Summary

Nyxloom now uses cli-extended as the source of truth for parser grammar,
generated help, usage, common options, and dispatch on its three human command
surfaces: `nyxloom`, `nyxloom-harness`, and `nyxloomctl`. Host-local operator
commands and AI-harness session extraction have their approved separate
entrypoints. The `nyxloomd` service launcher is installed in the same wheel and
is invoked directly by the container supervisor.

The adoption bundles `cli_extended` from the maintained sibling library source
into the Nyxloom wheel, adds the optional `nyxloom[interactive]` extra, and
uses the library prompt driver for managed backlog create/edit. The CLI
reference and user documentation record the new boundaries, grammar, migration
paths, and usage. Necessary Docker, compose, service, test, and documentation
additions were authorized and recorded in the P112 log. The user also directed
removal of the standing blanket out-of-scope edit prohibition; `STANDING.md`
now treats `scope.touch` as the planned inventory and permits directly needed
additions when their reason is recorded.

## Oracle traceability

| Oracle | Result | Evidence |
|---|---|---|
| O1 — library prerequisite | PASS | Nyxloom imports the shared prompt and confirmation API from cli-extended. The final isolated wheel contains `cli_extended`; installed imports resolve from the wheel target, not the repository checkout. No second maintained copy or runtime library distribution dependency was added. |
| O2 — registry contract | PASS | `nyxloom`, `nyxloom-harness`, and `nyxloomctl` each declare grammar and dispatch through `CliRegistry`/`VerbSpec`/`ArgumentSpec`/`OptionSpec`. The full R0 suite passes; CLI parser/adoption tests cover declared parsing, generated help, dispatch, conflicts, and malformed inputs. |
| O3 — migration and safety | PASS | The CLI reference records the old-to-new map. Old extraction and host-control paths are removed from the primary CLI; `gate` and `daemon --foreground` are removed. Existing safeguards remain the consent boundary; parser conflicts are rejected before dispatch. R0 passed. |
| O4 — boundaries and effects | PASS | Local authoring, extraction, and local operator workflows do not require a daemon or host registry. Project-local `nyxloom lint` and registered-project `nyxloomctl lint` are distinct. Help/version and usage paths are covered as side-effect-free; service liveness now invokes `nyxloomctl`. R0 passed. |
| O5 — backlog wizard | PASS | Interactive create/edit use cli-extended's optional prompt API and the approved D-016 field set. Candidate frontmatter is validated before writing; edit preserves unrelated metadata and body bytes. Cancellation, invalid input, missing entries, prompt failures, and schema failures are covered by behavioral tests. R0 and R1 passed. |
| O6 — wheel and service entrypoints | PASS | The isolated wheel proof on `75157505` verified all four scripts, bundled `cli_extended`, and help/version paths. The review-corrected Docker wheelbuild from the exact e3e5d5a9 source tree contains all four script declarations and all seven `cli_extended` files. `questionary` stayed unloaded; the service supervisor invokes `nyxloomd` directly. |
| O7 — docs and gate | PASS | README, DESIGN-GUIDE, CONSUMERS, SPEC, USAGE, ARCHITECTURE, runtime-process-model, backlog spec, and CLI reference were updated and reconciled. The CLI reference contains the migration table. The exact declared `tester-unified` lane passed R0 and R1 on corrected implementation commit `e3e5d5a9`; evidence is recorded below. |

## Final gate after review corrections

The declared gate ran against clean implementation commit
`e3e5d5a98ac01f718cc42bff3cdaa484d8e6ef47`:

```text
command: ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-adoption tester-unified
tester-unified: PASS (exit 0)
commit: e3e5d5a98ac01f718cc42bff3cdaa484d8e6ef47
R0: PASS
R1: PASS — 984/984 measured executable lines and 136/136 branches; 100%; no missing, excluded, or unclassified lines
pytest: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-branch --cov-report=json:coverage.json
lane duration: 127.266 seconds; run-gate total: 142.6 seconds
Assay verdict: nyxloom/.assay/verdict-tester-unified.json
```

The wrapper returned exit 0 and run-gate history records this exact commit as
PASS. The progress JSONL shows baseline pytest PASS at 123.427 seconds and the
R0/R1 verdict written at 127.266 seconds. Peak memory was 993 MiB, p90 986
MiB, average CPU 2.37 cores, and memory-full stalls 6.7 seconds.

## Initial implementation gate (before review corrections)

The required gate ran against clean implementation commit
`75157505452b017110328b1a7b809378b88abed3`:

```text
command: ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-adoption tester-unified
tester-unified: PASS (exit 0)
commit: 75157505452b017110328b1a7b809378b88abed3
R0: PASS
R1: PASS — 949/949 executable lines and 134/134 branches; 100%; no missing, excluded, or unclassified lines
pytest: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-branch --cov-report=json:coverage.json
lane duration: 268 seconds; run-gate total: 274.298 seconds
Assay verdict: nyxloom/.assay/verdict-tester-unified.json
```

The exact run-gate output tail, including the lane's exit line, is recorded in
[`nyxloom-P112-LOG.md`](nyxloom-P112-LOG.md). The final implementation wheel
was built and installed from this original implementation revision. The
review-corrected Docker wheelbuild also completed on the exact source tree
represented by `e3e5d5a9`; its artifact contains all seven `cli_extended`
modules and all four console-script declarations.

## Installed-wheel evidence

- Artifact: `/tmp/nyxloom-p112-wheel-75157505/nyxloom-0.8.1.dev501+g75157505-py3-none-any.whl`.
- The wheel was installed with `--no-deps` into `/tmp/nyxloom-p112-installed-75157505`; from `/tmp`, `nyxloom` and `cli_extended` imported from this target.
- Metadata declared exactly `nyxloom = nyxloom.cli:main`, `nyxloom-harness = nyxloom.cli_harness:main`, `nyxloomctl = nyxloom.cli_ctl:main`, and `nyxloomd = nyxloom.daemon_entrypoint:main`.
- `--help` and `--version` passed for all three human CLIs; `nyxloom-harness extract --help` and `nyxloomctl doctor --help` passed. Help paths did not import `questionary` or create the isolated Nyxloom state directory.

## Files touched

The implementation diff is against base `fb9d8f5b1131c7a12c20f457f36c74e0510c1c8e`.

The post-gate review follow-up additionally changes repository-root `.dockerignore`
and `nyxloom/src/nyxloom/{backlog_wizard.py,cli.py}` plus
`nyxloom/tests/test_backlog_entries.py`.

- Library: `libraries/cli-extended/{README.md,SPEC.md,assay.toml,docs/CONSUMERS.md,docs/DESIGN-GUIDE.md,pyproject.toml,src/cli_extended/__init__.py,src/cli_extended/output.py,src/cli_extended/parser.py,src/cli_extended/prompts.py,tests/test_output.py,tests/test_parser_specs.py,tests/test_prompts.py}`.
- Nyxloom CLI and domain code: `nyxloom/src/nyxloom/{backlog_entries.py,backlog_wizard.py,capability_map.py,cli.py,cli_ctl.py,cli_harness.py,cli_registry.py,commands.py,control_auth.py,daemon.py,daemon_entrypoint.py,doctor.py,exception_census.py,free_models.py,gate_scaffold.py,intake_bridge.py,migrate_store.py,notify.py,reconcile.py,render.py,resync.py,transport_check.py,types.py,wrapper.py}` and `nyxloom/src/nyxloom/session_extract/README.md`.
- Nyxloom tests: `nyxloom/tests/{test_backlog_entries.py,test_backlog_items.py,test_cli.py,test_cli_adoption.py,test_cli_extract.py,test_cli_help.py,test_commands.py,test_control_auth.py,test_daemon.py,test_doctor.py,test_events_cmd.py,test_free_models.py,test_gate_scaffold.py,test_intake_bridge.py,test_intake_chat.py,test_lint.py,test_liveness.py,test_liveness_units.py,test_migrate_store.py,test_notify.py,test_reconcile.py,test_resume_guard.py,test_resync.py,test_resync_apply.py,test_route_doctor.py,test_session_extract_edge_contracts.py,test_session_extract_reasonix.py,test_wrapper.py,legacy_planner.py,test_core_characterization.py}`.
- Nyxloom documentation and project contract: `nyxloom/README.md`, `nyxloom/docs/{ARCHITECTURE.md,CLI-REFERENCE.md,CONSUMERS.md,DESIGN-GUIDE.md,SPEC.md,USAGE.md,backlog-entries-spec.md,design-choices.md,logging.md,plan-logging.md,plan-state-integrity.md,runtime-process-model.md}`, `nyxloom/reference/{STANDARD.md,TESTING-METHODOLOGY.md}`, `nyxloom/nyxloom-trove/{STANDING.md,decisions.md,nyxloom.toml,handoffs/nyxloom-P111-cli-extended-audit.md,handoffs/nyxloom-P112-cli-extended-adoption.md,reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md,reports/cli-extended-nyxloom-prerequisite-prompt.md,reports/nyxloom-P111-LOG.md,reports/nyxloom-P111-REPORT.md,reports/nyxloom-P112-LOG.md,reports/nyxloom-P112-REPORT.md}`.
- Packaging and service: `nyxloom/{pyproject.toml,assay.toml,docker-bake.hcl}`, `nyxloom/nyxloomd/{Dockerfile,ciu.compose.yml.j2,ciu.defaults.toml.j2,docker-compose.yml,supervise.sh,systemd/README.md,systemd/nyxloom-liveness.service}`.

The report, execution log, and handoff `input_revision` were updated after the
implementation gate as evidence/refreeze metadata; they do not change the
gated implementation. The handoff's `scope.touch` records the planned file
inventory, and the log records authorized necessary additions and reasons.

## Post-gate code review follow-up

The requested review found and this worktree now fixes three issues: the
repository-root `.dockerignore` omitted `libraries/cli-extended` from the
Nyxloom Docker build context; `backlog list` treated a project without
`[backlog_entries]` as an adopted empty backlog; and invalid `backlog edit`
input suggested rerunning the create command. The first issue also blocked the
image wheel build. The root `.dockerignore` is outside Nyxloom's project tree
and is not listed in `scope.touch`; it is a necessary O6 fix and is recorded in
the P112 log under the standing rule permitting directly needed additions.

Two focused regression tests for the backlog behavior passed. Buildx's static
check passed, and the real Docker `wheelbuild` target completed and exported a
wheel containing all seven `cli_extended/` package files and the four expected
entrypoints. The corrected R0/R1 gate passed on `e3e5d5a9`; see the final gate
section above.

## Deviations and resolved gate findings

Six full-gate attempts were needed for the original adoption. Attempts one through five exposed real
import/contract/coverage issues and two test or runtime defects; each cause and
repair is documented in the execution log. The final uncovered branch was the
`cmd_discuss --traceback` re-raise path. A focused regression was added, then
the sixth exact-commit run passed R0 and R1. No coverage threshold or exclusion
was relaxed. Earlier failed attempts are disclosed and are not represented as
final evidence.

During scope review, the wheel build context, Ciu compose template, host
liveness unit, affected existing CLI tests, and session-extraction guide were
found to be necessary adoption surfaces. The user authorized these additions;
the P112 log records each reason. No unrelated project behavior was changed.

## Review disposition

The requested Codex review found three actionable issues; all three were fixed
and the corrected implementation passed the declared gate. No open findings
remain from that review. P112 declares `tester-unified` (R0/R1) as its required
gate; the additional user-requested session-extract R2/R3 result is recorded
below.

## User-requested session-extract R2 follow-up — 2026-09-29

This additional campaign ran against exact commit
`d3d5a5d024af2586bb257be87d4af23257f354e5`:

```text
command: ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-adoption session-extract
start: 2026-09-28T21:04:47Z
end: 2026-09-29T00:19:45Z
container: run-gate-vbpub-session-extract-3351574-1790629480
run-gate: FAIL/UNCOVERED_LINES, exit 1
R1: FAIL — 2,510/2,511 lines and 921/922 branches; missing line 416 and branch at line 415 in src/nyxloom/session_extract/follow.py
R2: 599 candidates; 522 killed, 77 survived, 0 equivalent, 0 crashed
R3: INCONCLUSIVE/CANARY_INCONCLUSIVE because R1 did not pass
```

The wrapper log is `/tmp/nyxloom-session-extract-r2-final-20260928.log`; the
raw gate log is
`/tmp/run-gate/run-gate-vbpub-session-extract-3351574-1790629480.log`; the
machine verdict is `.assay/verdict-session-extract.json`. The test container
was removed after the run. This result is **not green**.

Two focused coverage cases were added after inspecting the result. The follow
test appends non-conversation Codex metadata after the initial cursor, covering
the missed incremental-filter path. A Claude Code case verifies that a
sidechain-only session preserves a structured question and its answer in the
expected `INTERVIEW`/`OPERATOR` prose. Both passed locally with the devcontainer
venv; no test container was started for that diagnostic run.

The R2 survivor at `claude_code.py:786` (`And->Or`, candidate
`b089d16650fba3f2539d24fcc27b2635b1c55abbe94195b51d841a15677807fb`) was
probed with the exact mutation in a disposable worktree. The focused
sidechain-question test still passed under that mutation, so it does not kill
the candidate. Inspection shows `parse()` later walks the complete record
stream through `parse_record()`, which registers each question before its
answer; the earlier pre-pass duplicates state registration for normally
ordered sessions. A malformed out-of-order result could differ, so this is
recorded as a likely redundant candidate, **not** as an assay-adjudicated
equivalent. The probe worktree was removed. No production-code change was made
from this R2 triage.
