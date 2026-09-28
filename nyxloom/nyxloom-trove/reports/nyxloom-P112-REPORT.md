# P112 report: adopt cli-extended across Nyxloom's command surfaces

Date: 2026-09-28  
Result: **FOLLOW-UP VALIDATION IN PROGRESS** — the original P112 implementation
passed its declared gate on `75157505`. A post-gate code review found three
fixes, now implemented; the declared gate has not yet been rerun for them.
Implementation remains on `nyxloom-cli-adoption`; no merge to `main` was
performed.

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
| O6 — wheel and service entrypoints | PASS | The exact implementation wheel contains `nyxloom`, `nyxloom-harness`, `nyxloomctl`, and `nyxloomd` scripts with their intended targets and bundled `cli_extended`. Isolated help/version and representative nested help succeeded. `questionary` stayed unloaded; the service supervisor invokes `nyxloomd` directly. |
| O7 — docs and gate | PASS | README, DESIGN-GUIDE, CONSUMERS, SPEC, USAGE, ARCHITECTURE, runtime-process-model, backlog spec, and CLI reference were updated and reconciled. The CLI reference contains the migration table. The exact declared `tester-unified` lane completed with R0 and R1 PASS; the final input revision and evidence are recorded below. |

## Final gate

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
was built and installed from this same implementation revision before the
documentation-only report/log/input-revision commit. Its artifact and isolated
entrypoint/import checks are also recorded in the log.

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
wheel containing all seven `cli_extended/` package files. The review gate for
these corrections is still pending; do not treat the earlier `75157505` R0/R1
result below as evidence for this follow-up.

## Deviations and resolved gate findings

Six full-gate attempts were needed. Attempts one through five exposed real
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

## Reviewer suggestions

Review the command migration table against the three registry declarations,
with particular attention to the preserved safeguard boundaries, backlog
editor validation-before-write behavior, and the installed `nyxloomd` service
entrypoint. Treat the earlier gate failures as resolved history; the verdict
for acceptance is the sixth attempt on `75157505`.
