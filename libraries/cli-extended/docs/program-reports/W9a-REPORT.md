STATUS: CHECKPOINT (the original BLOCK was resolved by W6b; see "Resumption" at the end). The BLOCK text below is kept as history.

# W9a report: Netcup adopts cli-extended (checkpoint, BLOCKED)

Branch `cli-ext-w9a-netcup`, worktree `/workspaces/vbpub/.worktrees/cli-ext-w9a-netcup`.

## The block

`cli_extended.pytest_plugin.pytest_collection_finish` loops over every configured CLI that has a `review` catalog and calls `assert_cli_case_tests(session.items, catalog)` for each, passing ALL collected items. `assert_cli_case_tests` reports any `cli_case` marker whose case id is not in that catalog as `references unknown CLI case`. With two or more reviewed CLIs, every marked test is therefore "unknown" in all catalogs but its own, so the run always fails at collection.

Reproduction (this branch, with `cli-extended.toml` declaring three CLIs; the monitor-task catalog is the only populated one):

```
ERROR: cli-extended: CLI case test coverage failed:
- test 'tests/test_monitor_task.py::test_real_executable_obeys_help_version_and_parse_contract' references unknown CLI case 'case:route:entrypoint:monitor-task/watch/argument-shape/...'
```

(raised against the install-host catalog, which is empty). Needed library change: in the plugin (or in `assert_cli_case_tests`), partition markers by the `cli_id` encoded in the case id (`case:route:entrypoint:<cli_id>/...`) or by catalog `cli_id`, and check each catalog only against its own markers (and each marker against exactly the catalog that owns its CLI; a marker matching no catalog is the "unknown" error). Add a multi-CLI test to the library suite. The library's own docs (CONSUMERS "Linking review cases with the pytest plugin") promise "for every CLI in the project config that has a `review` catalog", so this is a bug, not a design choice.

Keeping Netcup's old hand-wired `pytest_collection_finish` (one call per catalog with the right items) would be the workaround the brief forbids, so I stopped.

## Done and verified

| Brief item | State |
| --- | --- |
| 1 config | `scripts/netcup/cli-extended.toml` declares install-host, monitor-task, scp-api. `sync` supports exactly one marked region per spec file, so there is one `CLI-SPEC-<id>.md` per CLI and `CLI-SPEC.md` is an index. monitor-task files `git mv`d (`cli-review.toml` -> `cli-review-monitor-task.toml`, `cli-surface.json` -> `cli-surface-monitor-task.json`, `CLI-SPEC.md` -> `CLI-SPEC-monitor-task.md`). Review/findings files for install-host and scp-api exist as empty schema stubs (catalog rows not yet written). |
| 2 identity | The three VERSION-regex blocks are replaced by `CliIdentity.resolve(..., version_file=Path(__file__).resolve().with_name("VERSION"))`. |
| 3 policy | `unexpected_exceptions="report"` plus the domain `expected_exceptions` tuples moved from `run()` into `CliRegistry(...)` on all three. |
| 4 dry-run | install-host's hand-added `--dry-run` option removed; wizard/configure/install register `VerbSpec(dry_run=True)`; `_prepare_runtime_arguments` sets `cli_args.dry_run = runtime.dry_run` once, and the ten `getattr(args, "dry_run", False)` sites read `args.dry_run`. Plan output unchanged. scp-api and monitor-task have no `--dry-run`. |
| 5 constraints | Only structural check found was `--custom-script-file is only valid with wizard or configure` (dead: the option is not declared on other verbs); removed. All other handler checks are config/env-derived or depend on a positional `action` value (see friction). |
| 6 tests | Both `_invoke` helpers replaced by `make_invoker`/`invoke_script` (HOME under tmp_path, `scrub_prefixes=("NETCUP_SCP_API_",)`); conftest uses `pytest_plugins = ["cli_extended.pytest_plugin"]`; test identities use `CliIdentity.resolve`; the `libraries/cli-extended/src` `syspath_prepend` hacks removed. |
| 7 surface | `surface sync` run for all three at schema 7 (manifests + spec regions regenerated). Case counts still to be written: install-host 78, monitor-task 13 (re-review, signatures changed with schema 7), scp-api 151. NOT done: catalogs, pack/review, findings. |
| 8 audit | Run, no catalog yet so AC-17 fails; AC-01/05/07/08/10/12/16/18/22/25 pass for install-host and monitor-task. Remaining judgement items noted below. |
| 9 docs | README/DESIGN-GUIDE/CLI-SPEC links updated for the per-CLI spec files; the test/config workflow paragraph is still to be extended after the catalogs land. |

Evidence run: `pytest tests/ -q` with the plugin pointed (via `-o cli_extended_config=`) at a scratch config that has no `review` entries: 227 passed, 1 failed. The one failure is `test_cli_guides_have_resolving_local_markdown_links`, caused by the library doc link below.

Not run: the Netcup `suite` gate lane (it cannot pass with the plugin bug and the doc-link failure). O1/O2/O3/O5 unproven. O4: `grep` shows no `getattr(args, "dry_run"`, no VERSION regex, no `_invoke` helper and no hand-added `--dry-run` left in `scripts/netcup` (the only `"--dry-run"` string is a test passing the library flag to `main`).

## Second library-tree issue (needs controller)

`libraries/cli-extended/docs/CONSUMERS.md` (section "Current generator coverage") links to `scripts/netcup/cli-review.toml` and `scripts/netcup/CLI-SPEC.md`; the renamed files break `test_cli_guides_have_resolving_local_markdown_links` (it checks that doc). The doc must be updated to `cli-review-monitor-task.toml` / the new index (and its text about a "monitor-task pilot" and "pytest collection hook" is now stale). Library files are out of my scope.

## Library friction (for the controller)

1. **Plugin multi-CLI bug** (the BLOCK above).
2. **Running the library CLI from the worktree source fails**: `python -m cli_extended.cli ...` exits `distribution 'cli-extended' is not installed` because the shared venv resolves the library through an editable finder with no dist-info, and the CLI's own identity uses `distribution="cli-extended"`. The brief's recipe does not work as written; I used a scratch `*.dist-info/METADATA` directory on `PYTHONPATH`. A source-run fallback (or a documented recipe) is needed.
3. **Constraints cannot reference positionals.** scp-api's real rules are "`--name`/`--consistency-check`/`--active` only with action X", "`set` requires `--active` or `--inactive`", "`put` requires policy_id". They depend on the value of a positional `action` and cannot be declared with `Requires`/`Conflicts`/`RequiresChoice`, which only take `--option` flags. Netcup keeps them in handlers; they remain invisible to the surface review.
4. **Positional "action" verbs force `configure` callbacks.** scp-api uses ten `configure` callbacks to add an optional positional `action` with choices after other optional positionals; `ArgumentSpec` can express that, but argparse's greedy optional-positional behaviour needs the hand-written `mac`/`action` swap in the handler. The audit marks all ten `manual`; I have not yet decided which can move.
5. **`synopsis=` audit noise**: derived synopsis is `[options]`-suffixed while Netcup's hand synopses deliberately omit it; the audit demands deleting four-plus redundant overrides, fine, but the warning list mixes "equal" and "informatively different" overrides.
6. **Decision-record scale**: scp-api yields 151 generated cases (about 12 per verb, mostly the same `--json`/`--debug-raw`/`--yes` option-spelling rows). There is no way to share one rationale/effects block across the repetitive library-control cases; every one needs its own row and linked test.
7. Docs: CONSUMERS says the Netcup pilot is a one-CLI config; nothing says what a multi-CLI project's spec layout should be (one region per spec file had to be found by reading code).

## Judgements for the `adoption` findings (to record when unblocked)

AC-04 synopsis overrides (keep the informative ones); AC-07 configure callbacks (see friction 4); AC-19 skills (no agent-facing skills ship with Netcup: not needed, record as note); AC-20 doctor (candidate: check `.env` token, `netcup.toml`, API reachability; product decision, list for the controller); AC-24 (standalone scripts use the installed library, CX-D3).

## Remaining plan (for the successor, after the library fix)

1. Library: fix plugin multi-CLI linking and the CONSUMERS links; make the source-run recipe work.
2. Write the three review catalogs (`surface template` prints the row skeletons) and the linked behavioural tests; `surface pack` per CLI and review per the skill; findings files; fix blockers/majors.
3. `surface check` and `audit --cli <id>` for all three; Netcup `suite` lane; README/DESIGN-GUIDE workflow paragraph.

Gate verdict: none (not run).

Co-Authored-By: Claude Sonnet <noreply@anthropic.com>

## Resumption (checkpoint 2, after the coordinator unblocked W9a)

State at this checkpoint (HEAD of `cli-ext-w9a-netcup`, integration merged in):

- `pytest tests/ -q` in `scripts/netcup` (real plugin, real `cli-extended.toml`, nice/ionice, serial): **228 passed**. The plugin now works with the three CLIs in one config.
- **monitor-task is finished for step 7**: its 13 existing catalog rows were re-signed to schema 7 (only `reviewed_signature` changed; every decision, rationale, effect and test link is untouched, so no test assertion changed). `cli-extended surface check --cli monitor-task` prints `CLI surface check passed.`
- **install-host (78 pending cases) and scp-api (151 pending cases) are NOT done.** Their catalogs are still empty schema stubs; their manifests and spec regions are synced. `surface template --cli <id>` prints the skeleton rows (one saved copy for install-host was at the scratchpad `tpl-ih.txt`, regenerate rather than trust it).
- Not started: `surface pack` + LLM review, findings (including the AC-07 `wontfix` findings pointing at CLI-EXT-17 and the AC-19/AC-20 adoption notes), the audit transcripts, synopsis-override cleanup, README/DESIGN-GUIDE workflow paragraph, the `suite` gate lane.
- Tooling note: I kept running the library from the worktree source with a scratch `cli_extended-0.2.0.dev0.dist-info/METADATA` on `PYTHONPATH` (scratchpad `w9a-meta`, helper `w9a-env.sh` defines `cx`) instead of the editable scratch venv, because `pip install -e` needs build-time downloads that are not available here. Behaviour is identical for `sync`/`check`/`audit`/`pack`.

Facts the successor needs (verified in `review.py` `check_cli_surface`, ~line 1489 onward): an `active` case must carry a concrete `invocation` that names the route's command path, uses only declared options, supplies every required positional and a valid value for each option under review, and (for constraint/exclusive candidates) triggers the rule; `expected_exit_status`, `effects`, `rationale`, `decision`, `test_ids` are required. The invocation is documentation: the linked test is what actually runs, and the plugin requires each `test_ids` node to exist, carry `@pytest.mark.cli_case("<id>")` and be listed. Per the coordinator, keep tests DRY with one parametrized test per library control (`--json`, `--debug-raw`, `--yes`, `--dry-run`) across routes using `pytest.param(..., marks=pytest.mark.cli_case(...))`, and list each param node id in `test_ids`.

Remaining work, in order:
1. install-host catalog (78 rows) + linked tests (in-process `_invoke_app`-style with `FakeClient` for dry-run/confirm paths; `make_invoker` only for parse refusals).
2. scp-api catalog (151 rows) + linked tests.
3. `surface pack` per CLI, review per `cli-extended-review/SKILL.md`, findings files (fix blockers/majors; wontfix with rationale for product decisions; AC-07 items `wontfix` -> CLI-EXT-17).
4. `surface sync`/`check` and `audit --cli <id>` x3 transcripts; delete redundant `synopsis=` overrides the audit warns about.
5. README/DESIGN-GUIDE: new test/config workflow (cli-extended.toml, per-CLI spec/catalog/findings files, plugin, `invoke_script`).
6. `cd scripts/netcup && flock <scratchpad>/gate.lock ./run-gate.py --worktree <wt> suite > <scratchpad>/w9a-suite.log 2>&1`, then `grep verdict` separately; fill in the oracle evidence (O1-O5) and the deviations table.

(Items 1 of this list is now DONE; see "Successor session" below, which is the current continuation brief and supersedes this list.)

## Successor session (checkpoint 3)

State at this checkpoint: branch `cli-ext-w9a-netcup`, HEAD `b30ca374e`. Ran: `pytest tests -q` in `scripts/netcup` (nice/ionice, serial): 306 passed. Netcup `suite` lane through the flock: `run-gate: lane 'suite' verdict PASS; exit_code 0` (log `/tmp/run-gate/lanes/suite/b9b6f0b43d91f03ca54403d206b8d019.log`). `cx surface check` passes for install-host (78 active cases) and monitor-task (13). scp-api: catalog still an empty stub (151 pending cases), manifest re-synced after the fix below.

### Done

1. **install-host catalog: 78 active rows, linked tests.**
   - Rows are in `scripts/netcup/cli-review-install-host.toml`.
   - Tests are in `scripts/netcup/tests/test_cli_cases_install_host.py` and use the in-process harness `scripts/netcup/tests/case_harness.py` (`run_install_host`).
   - Design:
     - The test module reads the catalog row (invocation, exit status, stdout/stderr substrings) and replays that exact invocation through the real `main()`.
     - Replay: fake client, HOME and cwd under `tmp_path`, `load_env_file` patched out, `os.environ` replaced by a private copy (nothing leaks), SSH follower and `time.sleep` stubbed so `attach` ends with the simulated Ctrl-C (exit 130).
     - `test_replayed_case_matches_catalog` is one parametrised test for all non-control options. It also asserts the parsed argument values and that the install POST happens only for a live, accepted run.
     - `test_dry_run_plans_without_mutating`, `test_yes_is_the_only_consent_in_a_non_interactive_run` and `test_debug_raw_warns_and_is_off_by_default` are the three control tests. Each is parametrised across routes with its own `pytest.mark.cli_case` per param, and each proves a contrast (the run without the control behaves differently).
     - Node ids: `tests/test_cli_cases_install_host.py::<test>[<route>-<option>]`.
2. **Real defect found by replaying scp-api and fixed (blocker class: crash in the primary path).**
   - Library-built `OptionSpec`s default to `argparse.SUPPRESS`, so an omitted option left no attribute on the namespace and handlers using `args.x` raised `AttributeError`.
   - Observed before the fix: `metrics 42 cpu` (no `--hours`), `firewall-policies` (list, create, put; no `--query/--limit/--offset`) and `user-iso upload FILE` (no `--name/--multipart`) all exited 1 with `unexpected AttributeError`.
   - Fix: explicit `default=None` (and `default=False` for `--multipart`) in `scp-api.py`; commit `b30ca374e`. Existing unit tests had missed it because they build `SimpleNamespace` args.
   - Record as findings fixed in `cli-review-findings-scp-api.toml`, with the replay tests as regression evidence. Do not drop this when writing the findings file.
3. **scp-api in-process harness**: `run_scp_api` + `RoutedClient` in `case_harness.py`.
   - Replays against a fixed endpoint table (`SCP_ROUTES`). An unplanned GET fails the test.
   - Records every call as `(method, endpoint, body-or-params)`.
   - Stubs `build_client`, `run_device_code_login`, the SSH probe and reverse-DNS helpers.
   - Writes `custom.iso` and `policy.json` fixtures into the cwd.
4. The full list of 100 candidate scp-api invocations I probed, with their observed outputs, is saved outside the repo at `<scratchpad>/w9a/test_zz_probe_scp.py` and `<scratchpad>/w9a/probe-scp2.txt` (scratchpad = `/tmp/claude-1003/-workspaces-vbpub/384f276e-fadc-4611-bf1d-973b249d83c0/scratchpad`). Copy the invocation list into the new test table rather than re-deriving it. Observed behaviours worth knowing:
   - `snapshots 42 dryrun` prints nothing (empty dict through `print_kv`).
   - `login` with no tty prints "Login succeeded. Protected-server selection was skipped" and makes GET `/api/v1/servers`, `/api/v1/servers/42`.
   - Non-tty without `--yes` always refuses with exit 2 "confirmation is required, but stdin is not interactive".
   - Positional rules (`firewall 42 get`) work; the argparse "mac/action" swap is in the handler.
   - Multipart upload needs the part endpoint `/api/v1/users/1/isos/custom.iso/up-1/parts/1`; it is already in `SCP_ROUTES`.

### Remaining, in order

1. **scp-api catalog (151 rows) + `tests/test_cli_cases_scp_api.py`.**
   - Rows:
     - Print the signatures with this scratch one-liner: `python3 -c` over `cli-surface-scp-api.json` (`candidates[].id`/`.signature`), or `cx surface template --config cli-extended.toml --cli scp-api`.
     - After writing rows, run `cx surface sync` once (the generated spec region embeds catalog state), then `cx surface check`.
   - Test design (same as install-host, per controller decision):
     - Catalog-driven replay test for every non-control case (parse refusals, arguments, choices, exclusive groups, route options).
     - One parametrised test per control across routes: `--json` (17 routes, all but login), `--debug-raw` (18), `--yes` (9 mutating routes: attach-iso, firewall-policies, firewall, iso-attached, power, rescuesystem, snapshots, tasks, user-iso).
     - Use `pytest.param(..., marks=pytest.mark.cli_case(id), id=...)` and list each param node id in `test_ids`.
     - Control contrast checks:
       - `--json`: `json.loads(stdout)` equals the canned payload; the same call without `--json` prints a table. `metrics` prints JSON either way, so special-case it.
       - `--debug-raw`: stderr warning present vs absent.
       - `--yes`: with it, the mutation call is recorded; without it, exit 2 and no mutating call.
     - Shared invariant (as in install-host): a mutating call (`post/put/patch/delete/upload_file`) appears only when the run exit status is 0 and the verb is not a pure read.
   - Cases per route (kind counts from the manifest):
     - attach-iso 11, disks 6, firewall-policies 17, firewall 17, guest-agent-status 4, imageflavours 5, iso-attached 7, iso-bootable 5, login 2, metrics 10, power 10, rescuesystem 7, server-details 4, servers 3, snapshots 9, status 5, tasks 19, user-iso 10.
     - Argument-choice / option-choice case ids embed a 10-hex hash of the choice value (for example `5498a731a1` is `create`).
   - Controller decisions that stand:
     - scp-api positional-value rules and the ten `configure` callbacks stay hand-written; record the AC-07 manual items as `wontfix` adoption findings pointing at cli-extended backlog CLI-EXT-17.
     - Repetitive library-control rows (CLI-EXT-18) each get a real row and a linked param, with shared rationale wording that must stay true per route.
2. **Review, per `cli-extended-review/SKILL.md`**:
   - `cx surface pack` for the three CLIs; perform the LLM review myself; write one findings file per CLI (`cli-review-findings-<id>.toml`).
   - Fix blockers/majors in Netcup code. Candidate review observations already noted:
     - `snapshots dryrun` prints nothing on success (minor/major UX).
     - install-host `--attach-custom-script` is a no-op default.
     - The `--ssh-*` options print nothing in the plan.
     - The no-`--yes` non-tty refusal comes from the library, not Netcup code.
3. **Gates**: `cx surface check` and `cx audit --cli <id>` for all three, transcripts into this report. Record AC-04/AC-07/AC-19/AC-20/AC-24 judgements as `adoption` findings (see "Judgements" above). Delete redundant `synopsis=` overrides only where the audit says they equal the derived synopsis (the hand synopses omit `[options]` on purpose; keep the informative ones).
4. **Docs**: README / DESIGN-GUIDE paragraph for the new workflow (cli-extended.toml, per-CLI spec/catalog/findings files, plugin, replay-test harness `case_harness.py`, `invoke_script`). CLI-SPEC.md index already links the per-CLI specs.
5. **Final gate**: rerun the `suite` lane through the flock, `grep verdict` in a separate step; fill O1-O5 evidence and the deviations table; include one hand-planted mutation killed by a replay test (not yet done; plant it in scp-api, e.g. flip a `default=None`, and show the kill).

### Tooling notes for the successor

- Environment: `. <scratchpad>/w9a-env.sh` defines `cx` (worktree library on PYTHONPATH + the scratch dist-info). Run pytest with `nice -n 19 ionice -c 3 /home/vscode/.venv/bin/python -m pytest tests -q -p no:cacheprovider`.
- Running one test file alone fails the plugin's cross-catalog coverage check. Run the whole `tests` directory, or pass `-o cli_extended_config=<scratch toml without review entries>` (examples: `<scratchpad>/w9a/noreview.toml`, `noreview-scp.toml`).
- Stdin must be non-tty (`</dev/null`) when running probes by hand, or interactive prompts hang.
- Test oracle changes: none of the previously existing tests' assertions was changed; the only edits to existing test-support files are the new `case_harness.py` and the new case test modules.

Co-Authored-By: Claude Sonnet <noreply@anthropic.com>
