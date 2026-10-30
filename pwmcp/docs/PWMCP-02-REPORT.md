# PWMCP-02 report: complete cli-extended adoption by pwmcp

Branch `pwmcp-clix` (worktree `/workspaces/vbpub/.worktrees/pwmcp-clix`), 2026-10-07.
Sources: `pwmcp/KNOWN_ISSUES_TODO_BACKLOG.md` PWMCP-02, decisions CX-D1..CX-D12 and
`ADOPTION-CHECKLIST.md` AC-01..AC-25 of `libraries/cli-extended/docs/`.

## What changed

| Area | Result |
| --- | --- |
| `pwmcp` client CLI | `client/src/pwmcp_client/cli.py` builds a `CliRegistry` (`build_cli()`, import-safe): verbs `contract`, shared `doctor` (checks `contract`, `playwright`, plus the automatic `skills` check), and `skills` via `register_skills_verbs`. Global `--contract URL_OR_FILE`. `unexpected_exceptions="report"`, domain failures as `CliFailure`, `main()` returns the status (exit 3 with one `[ERROR]` line when the version cannot be determined). `PwmcpArgumentParser` and `cli_headline` are gone; `diagnostics.py` now holds the doctor checks. |
| Version | `CliIdentity.resolve(distribution="pwmcp-client", version_file=.../VERSION)`. `client/src/pwmcp_client/VERSION` is also the wheel's dynamic version (`[tool.setuptools.dynamic]`), so metadata and file are one source and must agree; no literal fallback. `pwmcp_client.__version__` is a lazy attribute returning the identity version. |
| Dependency | `cli-extended>=0.3.0` in `client/pyproject.toml` `[project].dependencies` with the floor reason (first release whose `register_skills_verbs` forwards the global `--contract` to the `skills` group, CLI-EXT-26; same floor as cmru). No vendoring, no `package-dir`. Install docs in `docs/USAGE.md` and `README.md` (library first from its release, then the client; CX-D2). |
| Skills | `pwmcp/.claude/skills/pwmcp-fetch` moved (`git mv`) to `client/src/pwmcp_client/skills/pwmcp-fetch` as package data; `pwmcp skills install|uninstall|check|list`. |
| Surface | `[tool.cli-extended]` in `client/pyproject.toml`; `docs/CLI-SPEC.md` (product semantic table plus generated region), `docs/cli-review.toml` (59 active cases), `docs/cli-surface.json`, `docs/cli-review-findings.toml` (no findings). `pytest.ini` loads `cli_extended.pytest_plugin` (strict case linking). |
| Scripts | `build-push.py`, `scripts/build-bundle.py`, `scripts/publish-bundle.py`, `scripts/resolve-playwright-version.py` register a single-command CLI through `scripts/_vars.py` `script_cli` and import the installed `cli_extended` (CX-D3). Their `main()` keeps the historical contract (None on success, `SystemExit(n)` on failure) via `run_script`. |
| cmru source hack | The `sys.path.insert` toward `cmru/src` in `build-push.py`, `build-bundle.py`, `publish-bundle.py` is replaced by the installed `cmru` package. Verified basis: CMRU runs these steps (`cmru.toml` `[steps.build|push|prepare]`, argv `python3 ...`); cmru's own wheel requires `cli-extended`, and the scripts now need `cli_extended` in the same interpreter, so an interpreter that can run them under CMRU is a cmru-installed one. The tester image has cmru (`0.0.0+tester.unified`) and `cli-extended 0.3.0` (checked with `docker run ... pip list`), and the host `~/.venv` has both. No third approach was introduced. |
| Orchestration | `cmru.orchestration.toml`: `[orchestration.project.pwmcp] depends_on = ["cli-extended", "cmru"]`. |
| Backlog and program doc | PWMCP-02 marked FIXED with observed/fix/oracle; program doc W10 row 5 updated. PWMCP-01 untouched. |

## Acceptance checklist (AC-01 .. AC-25)

`cli-extended audit` run from `pwmcp/client/` (config discovery walks up to
`client/pyproject.toml`; `--config client/pyproject.toml` from `pwmcp/` is equivalent):

```
[PASS] AC-01 version-source: heuristic: CliIdentity.resolve( is used
[PASS] AC-04 synopsis-overrides: no synopsis overrides
[PASS] AC-05 shadowed-controls: no consumer option shadows a library control
[PASS] AC-07 configure-callbacks: no verb uses a configure callback
[PASS] AC-08 hidden-options: no hidden options
[PASS] AC-10 exception-policy: unexpected_exceptions='report' (library boundary)
[PASS] AC-12 mutation-safety: every mutating verb has confirmation or dry-run
[PASS] AC-16 surface-configured: review, manifest and spec are configured
[PASS] AC-17 surface-check: the surface check passes
[PASS] AC-18 surface-complete: the exported surface is syntax-complete
[PASS] AC-19 skills-packaged: 1 packaged skill(s) validate: pwmcp-fetch
[PASS] AC-20 doctor: a doctor verb is registered
[PASS] AC-22 pytest-plugin: heuristic: cli_extended.pytest_plugin is referenced
[PASS] AC-24 dependency-declared: heuristic: pyproject declares cli-extended>=
[PASS] AC-25 no-path-hacks: heuristic: no sys.path/PYTHONPATH vendoring found
audit: 15 pass, 0 warn, 0 fail, 0 manual
```

(The audit scans `client/` only; the scripts are covered by `tests/test_script_cli.py`.
Run from `pwmcp/` without `--config` it finds no config; run from the repo root it would
find none either, because the config lives with the wheel.) `cli-extended surface check`
prints `CLI surface check passed.` and is also asserted by
`client/tests/test_cli_adoption.py::test_committed_manifest_spec_and_catalog_are_current`.

| ID | Result | Evidence or justification |
| --- | --- | --- |
| AC-01 | pass | audit `version-source`; `test_identity_*`; plant 4 |
| AC-02 | pass | `CliIdentity(name="PWMCP", long_name="client utility", command="pwmcp")`; command equals the configured CLI id `pwmcp`; scripts use their file name as `command` |
| AC-03 | pass | every verb, argument and option is a `VerbSpec`/`OptionSpec`; no hand-built parsers remain (the four `argparse.ArgumentParser(...)` calls are gone) |
| AC-04 | pass | audit; no `synopsis=` override |
| AC-05 | pass | audit; `--contract`, `--build`, `--push`, `--check`, `--refresh`, `--age-window-days` are consumer options, none is a library control |
| AC-06 | n/a (client), pass (scripts) | the client has no option conflicts or requirements of its own (`skills` constraints are the library's); the scripts declare `--build`/`--push` (required one-of) and `--check`/`--refresh` (exclusive) with `mutually_exclusive_group` |
| AC-07 | pass | audit; no `configure` callbacks |
| AC-08 | pass | audit; no hidden options |
| AC-09 | n/a | no comma or space separated name lists; `doctor --check` is a repeatable option |
| AC-10 | pass | audit; `test_unexpected_handler_exception_is_one_error_line_unless_traceback_is_requested` |
| AC-11 | pass (client), recorded deviation (scripts) | client: `contract` raises `CliFailure` with a hint, doctor failures are check results with exit 1. Scripts keep their `fail()` helpers (`[ERROR] msg` on stderr then `SystemExit(1)`), which the shared runner maps to the same status; with `identity_banner="never"` the stderr is byte-identical to before, and 40+ golden tests assert `SystemExit`. Converting them is cosmetic and was not done |
| AC-12 | pass | audit. Client: no mutating verb of its own; `skills install/uninstall` carry the library's `dry_run=True`. Scripts: `mutating=True, dry_run=True, confirmation_required=False`: no `--yes` because CMRU runs them as non-interactive steps and choosing the step is the confirmation (recorded in `script_cli`'s docstring) |
| AC-13 | n/a | nothing asks a question: the client is read-only, the scripts are non-interactive release steps |
| AC-14 | pass (client), recorded deviation (scripts) | client results go through `runtime.output.primary`, `contract` and `doctor` offer `--json`. The scripts keep their existing `[INFO]` `print` lines unchanged (golden output of the release steps; not migrated to `runtime.output`) |
| AC-15 | n/a | no long operation of the tool's own; docker builds stream their own progress |
| AC-16 | pass | audit |
| AC-17 | pass | audit and `surface check`; plant 3 |
| AC-18 | pass | audit |
| AC-19 | pass | audit; `test_the_skill_is_packaged_data_and_valid_and_no_claude_skills_tree_remains`; `skills` verbs exercised in `test_skills_*` and 50 of the 59 reviewed cases |
| AC-20 | pass | audit; `test_doctor_*` |
| AC-21 | pass | `test_real_executable_obeys_cli_contract` (`assert_cli_contract` against `python -m pwmcp_client.cli`, with invalid invocations and `known_verb_errors`); scripts: `assert_cli_contract` does not apply (bare invocation is the operation, which the helper forbids), replaced by explicit subprocess tests of help, version, usage errors and `--dry-run` in `tests/test_script_cli.py` |
| AC-22 | pass | audit; `pytest.ini` plugin; plant 3 shows a stale surface fails |
| AC-23 | pass | `invoke_module`/`make_invoker`/`invoke_script` with `home=tmp_path/"home"`; in-process replay sets `HOME` and cwd to `tmp_path`; nothing writes to the real home (`test_skills_install_check_and_dry_run_use_only_the_given_destination` asserts it) |
| AC-24 | pass | audit; `test_cli_extended_is_a_declared_wheel_dependency_with_a_floor_and_reason`; plant 1a; wheel built and installed offline (below) |
| AC-25 | pass | audit; `test_client_sources_put_no_library_or_cmru_source_on_sys_path`, `test_scripts_import_the_installed_libraries_not_source_trees`; plant 1c. Lanes keep `PYTHONPATH = "client/src"` (the worktree's own source) and take `cli_extended` from the tester image |

## Golden behavior and intentional changes

Kept byte-for-byte: `pwmcp --version` (`pwmcp 0.1.0`, stdout only, exit 0), `pwmcp contract`
stdout and exit 0 (indent 2, sorted keys; the old tests still assert it), `--contract` accepted
before or after the verb, the headline `PWMCP <version> — client utility`, usage errors exit 2,
the scripts' success paths and their `main()` return/raise contract (all pre-existing
`tests/` pass unchanged except where listed).

Intentional changes (each is required by a checklist row or by the library's contract):

1. `pwmcp doctor` prints one `[OK]/[WARN]/[FAIL]/[SKIP]` line per named check plus a summary
   line instead of `ok release=... playwright=... ws=...` (AC-20). The same facts are in the
   `contract` check (`release=... ws=...`) and `playwright` check (`playwright=...`) summaries
   and in `--json`. New: `--check NAME`, `--json`, an automatic `skills` check (`[WARN]` when
   the skill is not installed, exit stays 0). An unreachable contract is now a `[FAIL]` line and
   exit 1 (was a traceback, exit 1).
2. `pwmcp contract` with an unreadable or malformed contract: one `[ERROR] cannot load the pwmcp
   contract from SOURCE (ExcType: message)` plus a hint, exit 1 (was a traceback). `--json` prints
   the same object on one line.
3. Usage-error layout (AC-03/AC-04): the `[ERROR] pwmcp: ...` diagnostic is the first block, then a
   blank line, then the identity headline and usage (was: headline first). The documented README
   sentence was updated. Exit code 2 is unchanged.
4. Bare `pwmcp` prints help and exits 0 (was: argparse "arguments are required", exit 2).
   `-h` is rejected (exit 2) as the library convention requires (was accepted by argparse);
   `--help` and `help [verb]` work.
5. New surface: verbs `skills install|uninstall|check|list`, `help`, `version`, and the shared
   controls `--json --quiet --debug --log-level --color --no-color --progress --traceback
   --debug-raw`. The `skills` group is the only mutating surface and carries `--dry-run`.
6. An unexpected exception is one `[ERROR] unexpected Type: msg` line, exit 1; `--traceback`
   restores the stack (client and scripts).
7. Library API: `pwmcp_client.diagnostics.PwmcpArgumentParser` and `cli_headline` are removed;
   `pwmcp_client.__version__` still works but raises `VersionLookupError` instead of falling back to
   `"0.1.0"` when neither metadata nor the VERSION file resolves (or when they disagree).
8. Install: the client wheel now requires `cli-extended>=0.3.0`; a plain `pip install ./client`
   without the library fails naming it (docs updated, see the offline install below).
9. Scripts: `--help`, `--version` and `--dry-run` are new; `-h` and argparse prefix
   abbreviations (`--ref`) are rejected; `build-bundle.py` and `publish-bundle.py` now reject
   operands they used to ignore; bare `build-push.py` still exits 2 but with the library's
   `[ERROR] build-push.py: one of the arguments --build --push is required` plus help. A bare
   `build-bundle.py`/`publish-bundle.py`/`resolve-playwright-version.py` still runs the operation
   (`no_args_action=True`; plant 6 shows the alternative would print help and exit 0 inside CMRU).
10. The cmru source-path hack is gone (see above).

## Plants (each reverted; tree clean afterwards)

| # | Plant | Result |
| --- | --- | --- |
| 1a | Re-vendor `cli_extended` in `client/pyproject.toml` (`packages.find where` plus `[tool.setuptools.package-dir] cli_extended = "../../libraries/cli-extended/src/cli_extended"`) | `client/tests/test_cli_adoption.py::test_adoption_audit_has_no_fail_and_nothing_left_to_judge` FAILED with `[FAIL] AC-24 dependency-declared: heuristic: package-dir vendors cli_extended` (audit `14 pass, 0 warn, 1 fail`) |
| 1c | `sys.path.insert(0, .../"libraries"/"cli-extended"/"src")` in `cli.py` | `test_client_sources_put_no_library_or_cmru_source_on_sys_path` FAILED. The library's own audit AC-25 still said pass: heuristic gap filed as CLI-EXT-32 |
| 2 | Break every verb's exit code (`cli.run(argv=argv); return 0` in `main`) | `test_real_executable_obeys_cli_contract` FAILED (`AssertionError: -h must be rejected by default`), plus at least 18 more, among them `test_contract_failure_*`, `test_doctor_fails_with_a_remedy_on_a_playwright_mismatch`, `test_doctor_unknown_check_is_a_usage_error`, `test_skills_install_check_and_dry_run_use_only_the_given_destination` and the exit-status assertions of `test_reviewed_case[skills-check*]` |
| 3 | Reword the `contract` verb description (grammar text drift) | `test_committed_manifest_spec_and_catalog_are_current` FAILED: `generated CLI manifest is stale`, `generated CLI spec block is stale`, `CLI surface check failed.` |
| 4 | Literal `0.1.0` fallback in `identity()` on `VersionLookupError` | `test_identity_has_no_literal_fallback_when_no_source_resolves` and `test_identity_refuses_metadata_that_disagrees_with_the_version_file` FAILED (`DID NOT RAISE VersionLookupError`) |
| 5 | Remove `cli-extended` from pwmcp's `depends_on` | `cmru dependencies` still printed `PREFLIGHT: PASS`: the preflight only reads `<project>/pyproject.toml`, not the nested `pwmcp/client/pyproject.toml`. So the edge is declared by hand; filed as cmru KI-70 [now KI-83] |
| 6 | `no_args_action=False` in `script_cli` | `tests/test_script_cli.py::test_build_push_requires_exactly_one_operation` FAILED (bare `build-push.py` printed help, exit 0) |

## Gate

Registered gate: `pwmcp/cmru.toml` `[steps.run-tests]` is `./run-gate.py r1`; `run-gate.toml` lane
`r1` is `kind = "assay"`, environment `tester-unified`, 100% line and branch on the targets.
The tester image `tester-unified:local` holds `cli-extended 0.3.0` (the library comes from the
image; the lane's `PYTHONPATH` is `client/src` only).

```
cd pwmcp && flock <gate.lock> nice -n 19 ionice -c 3 ./run-gate.py r1 --worktree /workspaces/vbpub/.worktrees/pwmcp-clix
r1: PASS (exit 0)    commit 7609d7078c262381c9cc63c160722c341d1c3691
verdict-r1.json: outcome PASS, exit_code 0, declared_rigor [R0, R1]
coverage claim: executable 634/634 covered, branches 172/172, pct 100.0, considered 7 targets,
                files_missing_coverage [], missing_lines {}, missing_branch_lines {}
```

The gate was run a second time on the tip that carries this report's text before the final
documentation-only commit (`194f01e1d`): `r1: PASS (exit 0)`, same 634/634 lines and 172/172
branches, 100.0%.

Gate log lines worth knowing (not caused by this package): `run-gate: WARNING profiling: cleanup
crashed unexpectedly: 'NoneType' object has no attribute 'get'`, and assay's `no judge_provenance
recorded` notice because the lane installs assay from the worktree source.

`./run-gate.py r2` (the 4 h mutation lane) was not run, as the brief asked for the registered
release gate (`r1`).

## Orchestration

From `pwmcp/` in the worktree:

```
cmru dependencies   ->   pwmcp <- cli-extended, cmru  (declared release order) ...  PREFLIGHT: PASS   (exit 0)
cmru status         ->   pwmcp   pwmcp-v1.63.0-r2   prepare   (derived by PWMCP_VERSION)              (exit 0)
```

## Packaging evidence (wheel from a scratch copy, installed outside the checkout)

* The built `pwmcp_client-0.1.0-py3-none-any.whl` contains `pwmcp_client/VERSION`,
  `pwmcp_client/cli.py`, `pwmcp_client/skills/pwmcp-fetch/SKILL.md`; METADATA has `Version: 0.1.0`
  and `Requires-Dist: cli-extended>=0.3.0`.
* `pip install --no-index <wheel>` in a fresh venv: `ERROR: Could not find a version that satisfies
  the requirement cli-extended>=0.3.0 (from pwmcp-client)` (dependency named).
* With the cli-extended wheel built from the library tree: `pip install --no-index --find-links
  <wheels> pwmcp-client` succeeds; from outside the checkout, with no `PYTHONPATH`, `pwmcp --version`
  prints `pwmcp 0.1.0`, `pwmcp contract --contract contract.json` prints the JSON, `pwmcp skills
  list --dest d` works, and `cli_extended` resolves to the venv's site-packages.
* Not done (controller's): a released pwmcp (client wheel plus bundle) deployed and the real
  release bundle's offline install exercised against a published `cli-extended-latest` pointer.
  `docs/USAGE.md` documents the steps.

## Follow-ups filed

* cmru `KNOWN_ISSUES_TODO_BACKLOG.md` KI-70 [now KI-83: renumbered in INT-01 because KI-70 is the released mutation-campaign issue]: dependency preflight ignores a nested wheel pyproject.
* `libraries/cli-extended/BACKLOG.md` CLI-EXT-32: audit AC-25 misses a library path built from pieces.
* Not filed (small): the scripts' `fail()` helpers still use `SystemExit` (AC-11 deviation above).
