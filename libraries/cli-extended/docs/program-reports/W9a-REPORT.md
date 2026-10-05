STATUS: COMPLETE, awaiting reviewer verification (see "Successor session 4" at the end). The BLOCK and checkpoint text below is kept as history.

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

## Successor session 3 (checkpoint 4: scp-api catalog written, tests NOT yet written)

State: branch `cli-ext-w9a-netcup`. Integration (verb options keep real defaults, W8c) was merged before this session.

### Done this session
1. Ran `surface sync` for all three CLIs after the library default change. install-host: 26 rows reported `semantic review signature changed` (the `--monitor/--no-monitor`, `--config/--payload`, `--completion-marker`, `--custom-script-file` option rows plus the wizard/configure/install `minimum` rows, which now list the previously absent `--monitor`/`--no-monitor` as defaulted options). The behaviour decision of each still holds: the install-host replay tests (which assert the parsed values) passed unchanged (`pytest tests -q`: all passed before any scp-api change), the handlers coerce with `bool(getattr(...))`, and `None` is falsy like the absent attribute. Only `reviewed_signature` was re-signed (by Edit); no decision, rationale or test link changed. Committed. monitor-task needed no re-sign. `cx surface check` passes for install-host (78) and monitor-task (13).
2. **scp-api catalog: all 151 rows written** in `scripts/netcup/cli-review-scp-api.toml`, every row `state = "active"` with `test_ids` naming node ids in `tests/test_cli_cases_scp_api.py`, which DOES NOT EXIST YET. `cx surface sync` + `cx surface check --cli scp-api` print `CLI surface check passed.` (check does not look at tests). The pytest plugin WILL fail collection until the test module exists, so a full `pytest tests` run is red at this commit by design.
   - Fixes made while checking: `attach-iso/minimum` invocation is `attach-iso 42 --iso-id 1234` (exit 2, "confirmation is required, but stdin is not interactive"); the three `firewall` rows that name the `action` positional (choice get, choice set, shape action) put the MAC before the action (`firewall 42 aa:bb:cc:dd:ee:ff get|set ...`) because `check` requires positional order server_id, mac, action.

### Remaining (in order)
1. **Write `tests/test_cli_cases_scp_api.py`** (the node ids below are already in the catalog; match them exactly).
   - Model it on `tests/test_cli_cases_install_host.py`: load `ROWS` from the catalog, replay `row["invocation"]` through `run_scp_api` (`tests/case_harness.py`; the module fixture is `explore_mod`, see the probe file), assert `run.status == row["expected_exit_status"]`, `expected_stdout_contains in run.out`, `expected_stderr_contains in run.err`, plus the exact recorded `run.calls` (strip `/api/v1/`) per case. Expected calls come from the probe output `<scratchpad>/w9a/probe-scp2.txt`. Shared invariant: a call whose method is in `MUTATING` appears only when status is 0.
   - Param ids for `test_replayed_case_matches_catalog` (`pytest.param(case_id, ..., marks=pytest.mark.cli_case(case_id), id=...)`), with the mutating call to assert:
     - attach-iso: shape-server-id, conflict-iso, member-iso-id, member-user-iso-name, minimum, change-boot, iso-id, user-iso-name (post `servers/42/iso` with `{isoId:1234}` / `{userIsoName:"custom.iso"}` / `+changeBootDeviceToCdrom:True`; conflict and minimum: no calls)
     - disks: choice-supported-drivers, shape-action, shape-server-id, minimum
     - firewall-policies: choice-create, choice-put, shape-action, shape-policy-id, conflict-policy, member-policy-file, member-policy-json, minimum, limit, offset, policy-file, policy-json, query, filter (post `users/1/firewall-policies`; put `.../12` with `{name, description, rules: []}` from `policy.json`; list GET params `{limit:5}`, `{offset:2}`, `{q:ssh}`; every call preceded by `get_user_info`)
     - firewall: choice-get, choice-set, shape-action, shape-mac, shape-server-id, conflict-active, member-active, member-inactive, minimum, active, consistency-check, copied-policy-id, inactive, user-policy-id (put `servers/42/interfaces/aa:bb:cc:dd:ee:ff/firewall` body `{copiedPolicies, userPolicies, active}`; with an explicit MAC the server GET is skipped; confirm the exact read calls by running)
     - guest-agent-status: shape-server-id, minimum; imageflavours: shape-server-id, minimum, filter; iso-attached: choice-detach, shape-action, shape-server-id, minimum; iso-bootable: shape-server-id, minimum, filter; login: minimum
     - metrics: choice-network, choice-disk, choice-cpu, choice-network-packet, shape-metric, shape-server-id, minimum, hours (GET `servers/42/metrics/<m>`; network-packet is `.../network/packet`; hours param `{hours:24}`)
     - power: choice-reset, choice-on, choice-off, choice-cycle, shape-action, shape-server-id, minimum (patch `servers/42` with `({state:..}, {stateOption:..})` as in the probe; minimum exits 2, no calls)
     - rescuesystem: choice-deactivate, shape-action, shape-server-id, minimum; server-details: shape-server-id, minimum; servers: minimum
     - snapshots: choice-dryrun, choice-create, shape-action, shape-server-id, minimum, name (dryrun posts `servers/42/snapshots:dryrun` `{}`; create body name is `vbpub-<UTC timestamp>` unless `--name`; pin the clock or match the prefix)
     - status: shape-server-id, minimum, ssh-timeout; tasks: choice-cancel, shape-action, shape-uuid, minimum, state-waiting-for-cancel, state-running, state-finished, state-canceled, state-pending, state-error, limit, offset, query, filter, server-id, state; user-iso: choice-upload, shape-action, shape-file, minimum, multipart, name, part-size-mib
   - `test_invalid_argument_is_refused[<pid>]` (each carries the same `cli_case` marker as its row; expect exit 2, no calls, a distinguishing stderr substring): attach-iso-server-id (`attach-iso not-an-id --iso-id 1234`, "must be an integer"), disks-action (`disks 42 bogus`, "invalid choice"), disks-server-id, firewall-policies-action (`firewall-policies bogus`), firewall-policies-policy-id (`put abc --policy-file policy.json --yes`), firewall-action (`firewall 42 get bogus`), firewall-mac (`firewall 42 not-a-mac`, "must be a MAC address"), firewall-server-id, guest-agent-status-server-id, imageflavours-server-id, iso-attached-action, iso-attached-server-id, iso-bootable-server-id, metrics-metric (`metrics 42 bogus`), metrics-server-id (`metrics abc cpu`), metrics-hours (`metrics 42 cpu --hours 0`, "must be between 1 and 1440 hours"), power-action (`power bogus 42`), power-server-id (`power on abc`), rescuesystem-action, rescuesystem-server-id, server-details-server-id, snapshots-action, snapshots-server-id, status-server-id, tasks-action (`tasks <uuid> bogus`), tasks-state (`tasks --state BOGUS`), user-iso-action (`user-iso bogus`), user-iso-file (`user-iso upload missing.iso --yes`, "is not a regular file"; handler check, exit 2).
   - Control tests (one parametrised test each, `pytest.param(route, marks=pytest.mark.cli_case(<option-spelling id of that control on that route>), id=route)`; contrast against the same argv without the control):
     - `test_json_output_is_machine_readable[<route>]` for attach-iso, disks, firewall-policies, firewall, guest-agent-status, imageflavours, iso-attached, iso-bootable, metrics, power, rescuesystem, server-details, servers, snapshots, status, tasks, user-iso (17): stdout parses with `json.loads`; plain run differs (except `metrics`, whose output is JSON either way and must be identical).
     - `test_debug_raw_warns_and_is_off_by_default[<route>]` for those 17 plus login (18): stderr contains `--debug-raw is active`, the plain run does not, and the recorded calls are equal.
     - `test_yes_is_the_only_consent_in_a_non_interactive_run[<route>]` for attach-iso, firewall-policies, firewall, iso-attached, power, rescuesystem, snapshots, tasks, user-iso (9): with it one mutation recorded; without it exit 2, `confirmation is required, but stdin is not interactive`, no mutating call.
   - Dev command: `cd scripts/netcup; . <scratchpad>/w9a-env.sh; nice -n 19 ionice -c 3 /home/vscode/.venv/bin/python -m pytest tests -q -p no:cacheprovider </dev/null` (whole directory: the plugin's catalog check needs every module). Fix catalog text where observed behaviour differs (for example exact stdout words for `user-iso --multipart`, the `snapshots` outputs, `json` stdout of `snapshots --json`); catalog text edits need no re-sign (the signature depends on the surface only), but re-run `surface check`.
2. `surface pack` x3 + the LLM review per the skill; findings files: scp-api must contain the **fixed** finding for the `AttributeError` crash (commit `b30ca374e`; now redundant with the library default, keep or drop the explicit defaults; the five crashing invocations' tests must stay) and the AC-07 `wontfix` findings pointing at CLI-EXT-17. Observed review notes: `snapshots 42 dryrun` and `snapshots create` print nothing when the (fake) API returns an empty body (real responses are non-empty task objects: a minor note, not a defect); install-host `--attach-custom-script` no-op default; `--ssh-*` options print nothing in the plan.
3. `audit --cli <id>` x3 transcripts; delete redundant `synopsis=` overrides only where the audit says they equal the derived one.
4. README/DESIGN-GUIDE workflow paragraph.
5. `suite` lane through the flock (verdict read in a separate step); plant one mutation per CLI by hand in a scratch copy and record each kill in this REPORT; fill O1-O5 and the deviations table.

Controller decisions that stand: scp-api positional-value rules and `configure` callbacks stay hand-written (AC-07 manual items recorded as `wontfix` findings -> CLI-EXT-17); repetitive library-control rows keep real rows with one parametrised test per control (CLI-EXT-18 is the future library fix).

Tooling note: pytest needs `. <scratchpad>/w9a-env.sh` sourced in the same shell (worktree library + scratch dist-info on PYTHONPATH); without it the plugin import fails.

## Successor session 4 (final for W9a; nothing remains except reviewer verification)

Branch `cli-ext-w9a-netcup`, HEAD `d1d95294f` at the time of writing (this report is committed after it). Everything below was run; scratchpad = `/tmp/claude-1003/-workspaces-vbpub/384f276e-fadc-4611-bf1d-973b249d83c0/scratchpad`.

### Done

1. **scp-api behaviour tests: suite green.** `scripts/netcup/tests/test_cli_cases_scp_api.py` (commit `0ecc09425`).
   - Parameters are generated from the catalog's own `test_ids`, so node ids cannot drift from rows; each param carries its row's `cli_case` marker.
   - `test_replayed_case_matches_catalog`: replays the row's invocation through `main()` with `RoutedClient`; asserts exit status, stdout/stderr substrings and the exact API call list pinned per invocation in `CALLS` (a missing key is a KeyError). A mutating call may appear only on exit 0; exit 2 means no call at all.
   - `test_invalid_argument_is_refused`: 28 bad-value invocations (`INVALID`), exit 2, distinguishing stderr text, empty stdout, no API call.
   - Controls, one parametrised test each with a contrast run: `test_json_output_is_machine_readable` (17 routes), `test_debug_raw_warns_and_is_off_by_default` (18), `test_yes_is_the_only_consent_in_a_non_interactive_run` (9).
   - `test_part_size_changes_how_a_large_iso_is_split` (linked to the `--part-size-mib` row, node id added to the catalog): with an 11 MiB sparse file, `--part-size-mib 5` gives uploads of 5/5/1 MiB, the default gives one 11 MiB part. `case_harness.run_scp_api` gained `iso_size` for it.
   - `pytest tests -q` (nice/ionice, serial, plugin active): **487 passed**.
   - Note: metrics prints JSON with and without `--json`; the test asserts the same document, compact form with `--json`.
2. **surface pack + LLM review** done by me for all three CLIs per `cli-extended-review/SKILL.md` and the rubric; findings files written (`cli-review-findings-{install-host,monitor-task,scp-api}.toml`, commit `21be49f04`). `cx surface check` passes for all three (open minor findings print as NOTE).
3. **audit**: `cx audit --cli <id>`: install-host `11 pass, 0 warn, 0 fail, 4 manual`; monitor-task `11 pass, 0 warn, 0 fail, 4 manual`; scp-api `10 pass, 0 warn, 0 fail, 5 manual`. Every manual item is recorded as an `adoption` finding (AC-04 keep, AC-07 per verb, AC-19, AC-20, AC-24).
4. **Synopsis overrides**: removed the five redundant ones the audit flagged (monitor-task `show`; scp-api `login`, `servers`, `server-details`, `guest-agent-status`), commit `9703e5430`. This changed the surface signature of 19 rows (6 monitor-task, 13 scp-api); only `reviewed_signature` was re-signed by hand (Edit), no decision/rationale/test link touched, the replay tests passed unchanged.
5. **Docs**: README (workflow paragraph after the CLI-SPEC index paragraph) and DESIGN-GUIDE (new section "Why the case tests replay the catalog"), commit `d1d95294f`.
6. **Gate**: `flock gate.lock ./run-gate.py --worktree <wt> suite > w9a-suite.log`, then in a separate step `grep -i verdict`: `run-gate: lane 'suite' verdict PASS; exit_code 0; log /tmp/run-gate/lanes/suite/c0bbfc1ae662e724da1785594f736a43.log`. No mutation lane was run.

### Findings (state after the controller rulings; the first two are FIXED, not wontfix)

- MT-001 (major): FIXED. `monitor-task watch` now exits 1 when the task ends in ERROR/CANCELED/ROLLBACK and 0 only for FINISHED.
- SA-002 (major): FIXED as documentation. Each `--filter` help line states its semantics (server-side API query on `tasks`/`firewall-policies`, client-side case-insensitive match on `imageflavours`/`iso-bootable`).
- SA-003: `power` takes the action first, all other scp-api verbs the server first.
- SA-004: optional MAC positional on `firewall` (handler swap).
- SA-007: no scp-api verb has `--dry-run` (confirmation-only; AC-12 passes).
- SA-008 / SA-013: FIXED. The `attach-iso` and `power` configure callbacks are now declarative. The other eight (SA-009..SA-012 and SA-014..SA-017) wait on CLI-EXT-17.
- AC-20 doctor is a `wontfix` note on all three (product decision).
- Open minor findings (not fixed, no blocker/major open): IH-001..IH-006, MT-003, MT-004, SA-005.
- SA-001 is the `fixed` blocker for the `AttributeError` crash (commit `b30ca374e`) with replay tests as regression evidence.

### Controller rulings applied after this section (MT-001, SA-002)

MT-001 FIXED: `watch` returns 0 only for FINISHED, 1 for ERROR/CANCELED/ROLLBACK (final-state line unchanged); exit codes in verb help and README; `test_watch_accepts_explicit_poll_and_debug_raw` now expects 1, new tests `test_watch_exits_1_for_every_unsuccessful_terminal_state` (4 params) and `test_watch_exits_0_only_for_finished_even_in_lower_case`; the two catalog rows that replay an ERROR run (`--poll`, `--debug-raw`) now say `expected_exit_status = 1`. SA-002 FIXED as documentation only: per-verb `--filter` help states server-side query vs client-side casefolded substring over every field. Neither change altered a surface signature (sync reported no re-sign). Both are FIXED (see the Findings list above); no section of this report treats them as `wontfix` any more.

### Oracles

- O1: suite lane PASS (above). No previously existing test assertion was changed in this session; the earlier sessions changed only how tests invoke the CLIs (invoke_script, plugin).
- O2: `surface check` passes for install-host (78 active), monitor-task (13), scp-api (151).
- O3: audit transcripts above, no `fail`.
- O4: `grep` in `scripts/netcup`: no `getattr(args, "dry_run"`, no VERSION regex (`VERSION_RE`/`re.search(...version`), no `def _invoke(` helper, and no hand-added `"--dry-run"` option; the only `"--dry-run"` strings are in the generated `run-gate.py` wrapper (its own flag).
- O5: findings files exist; the open ones are minor only; major/blocker items are `fixed` or `wontfix` with rationale.

### Hand-planted mutations (scratch copy `<scratchpad>/mut/scripts/netcup`, planted with Edit, not in the repo)

The scratch copy has 3 unrelated failures in `tests/test_cli_contract.py` (doc-link/verb-in-guide checks that resolve `../../libraries/...` relative paths, which do not exist at the scratch location). They also fail on the unmutated scratch copy (baseline run: 3 failed, 484 passed), so they are not kills.

| CLI | Mutation | Killed by |
| --- | --- | --- |
| scp-api | `"cycle": ("ON", "POWERCYCLE", ...)` -> `"RESET"` | `test_cli_cases_scp_api.py::test_replayed_case_matches_catalog[power-choice-cycle]` and the existing `test_cmd_power_actions_are_confirmed_and_use_server_patch[cycle-...]` (5 failed incl. the 3 baseline) |
| install-host | `if args.dry_run:` -> `if False and args.dry_run:` before the "NOT calling POST" plan | 16 tests beyond the baseline, among them `test_cli_cases_install_host.py::test_dry_run_plans_without_mutating[install]`, `::test_replayed_case_matches_catalog[install-poll-interval]` and `test_install_host.py::test_install_from_payload_dry_run_never_posts` |
| monitor-task | `interval <= 0` -> `interval < 0` in `_watch` | `test_monitor_task.py::test_watch_rejects_nonpositive_or_nonfinite_poll_before_authentication` (4 failed incl. the 3 baseline) |

### Reviewer-rejection round (controller rulings 1-7 plus nits)

Done in this round (all in `scripts/netcup`):

- **Blocker 1, hollow `--filter` rows.** `case_harness.SCP_ROUTES` imageflavours and isoimages now have a second non-matching row; `test_client_side_filter_keeps_only_matching_rows[imageflavours|iso-bootable]` (linked to the two `--filter` rows) asserts the reviewed value, its upper-case and capitalised forms keep one row and drop the other, with identical API calls.
- **Blocker 2, effects not observed.** The install-host dry-run plan now prints the SSH target (`user@host:22`), identity choice, monitoring, customScript-log attachment, poll interval, completion marker/wait and local-key retention (`_print_dry_run_ssh_plan`), so the replay asserts those plan lines per row (`PLAN_LINES`). New: `test_local_controller_key_flag_decides_whether_the_key_is_removed` (6 params, linked to the six retention rows; a live run with a recorder for `monitor_task`: `remove` deletes the key and sets `CONTROLLER_LOCAL_KEY_RETENTION`, `retain` keeps it), `test_monitoring_receives_every_resolved_value` (host, user, identity, poll, completion wait/marker, attach flag reach `monitor_task`), `test_relative_completion_marker_is_refused`, `test_duplicate_account_key_ids_are_refused`, `test_missing_identity_file_is_refused_not_generated`, `test_install_defaults_to_monitoring_with_customscript_log_attachment`, `test_dry_run_plan_shows_the_ssh_target_a_live_run_would_use`, `test_active_task_lookup_treats_terminal_states_case_insensitively`. The replay also asserts, per row: dry-run runs only read, start no follower or task polling and write no config file; attach makes no API call and writes no file; refusals make no call and leave no file; named identity files are unmodified with no `.pub`; `--ssh-key-id` skips the key-list GET; wizard/configure `--yes` saves the config.
- **Effects audit.** Reworded because a replay cannot prove them: the four `--ssh-identity-file` rows (now "requires the file to exist, refuses otherwise, unmodified"), the `--debug-raw` rows for wizard/configure/attach (the redaction switch is only observable on install), and the two parse-conflict rows ("reads no credentials" dropped).
- **Blocker 3, stale text.** DESIGN-GUIDE and this report say MT-001 and SA-002 were FIXED.
- **Ruling 4.** `attach-iso` (required exclusive group `iso-source` plus options) and `power` (two ArgumentSpecs) lost their configure callbacks; SA-008 and SA-013 `fixed`. The three attach-iso exclusive-group rows were renamed from `parser-exclusive-...` to `iso-source` and 8 signatures re-confirmed; the replay tests passed unchanged. Audit AC-07 now lists 8 verbs (all CLI-EXT-17).
- **Ruling 5.** SA-007 wontfix now says "controller-confirmed".
- **Ruling 6.** IH-003 raised to major and `fixed` (plan lines above, with tests).
- **Ruling 7.** `--simulate-disconnect-seconds` is `hidden=True` with the AC-08 reason in a code comment and in IH-005 (`fixed`); audit AC-08 for install-host is a manual item judged by IH-005.
- **Nits.** I6/I7: defaults tested (the redundant second `monitor = True` in `_prepare_runtime_arguments` was removed because it made either assignment an equivalent mutant); I9 duplicate key refusal; S8 default part size pinned (65 MiB file splits 64+1); M8 `show` text and `watch` redaction tests; I11 dead `install --server-id` check removed (note: it was reachable when `NETCUP_SCP_API_SERVER_ID` is set in the environment, so `install` no longer refuses in that case; the config file still names the target); `install-host.py` active-task terminal states compare `.upper()`; monitor-task `watch` kept no synopsis override (MT-006 `fixed`, 7 signatures re-confirmed); IH-008 now says the override is cosmetic, preserving the pre-adoption layout.

Reviewer survivors planted again by hand in the scratch copy (`<scratchpad>/w9a-mut/netcup`, harness `<scratchpad>/w9a-probe/final*.py`); every one is now KILLED:

| Mutant | Killed by |
| --- | --- |
| S1 `_filter_rows` returns all rows | `test_client_side_filter_keeps_only_matching_rows[imageflavours]` |
| S17 `casefold()` dropped | `test_client_side_filter_keeps_only_matching_rows[imageflavours]` |
| S20 iso-bootable filter ignored | `test_client_side_filter_keeps_only_matching_rows[iso-bootable]` |
| S8 part-size default 64 -> 65 | `test_part_size_changes_how_a_large_iso_is_split` |
| I6 install monitor default off (first copy was redundant, removed; second copy mutated as I6b) | `test_install_defaults_to_monitoring_with_customscript_log_attachment` |
| I7 attach_custom_script default False | same test |
| I8 wizard monitor ssh_user fixed | `test_monitoring_receives_every_resolved_value[wizard]` |
| I9 duplicate key ids allowed | `test_duplicate_account_key_ids_are_refused` |
| I13 retention always `retain` | `test_local_controller_key_flag_decides_whether_the_key_is_removed[wizard-remove]` |
| I14a / I14b completion wait fixed (install / wizard path) | `test_monitoring_receives_every_resolved_value[install]` / `[wizard]` |
| I16 completion-marker validation off | `test_relative_completion_marker_is_refused[wizard]` |
| M8a / M8b `responseError` unredacted (show / watch) | `test_show_text_output_redacts_response_error_unless_debug_raw` / `test_watch_redacts_response_error_unless_debug_raw` |
| T1 install-host terminal state case-sensitive | `test_active_task_lookup_treats_terminal_states_case_insensitively` |
| P1 / P2 plan host / poll interval wrong | `test_replayed_case_matches_catalog[wizard-ssh-host]` / `[wizard-poll-interval]` |
| S2, S4, S11 (hours+1, limit+1, power cycle option) | `metrics-hours`, `tasks-limit`, `power-choice-cycle` replay cases |
| M1 watch always exits 0 | `test_watch_accepts_explicit_poll_and_debug_raw` |

Not killed on purpose: S13/S14/S15 (dropping an explicit `default=None`/`False` on an option): the library now defaults verb options to None/False, so those are equivalent mutants. I11 and the first I6 copy no longer exist (code deleted as redundant).

### Deviations from the brief

- Brief item 7 asked to FIX every blocker/major in Netcup code or mark wontfix for product decisions: the only blocker (SA-001) was fixed earlier; the two majors (MT-001, SA-002) were then FIXED on controller ruling (watch exit codes; per-verb `--filter` documentation).
- `CLI-SPEC.md` is an index with one `CLI-SPEC-<id>.md` per CLI (sync supports one region per spec file).
- Library-tree files other than this report were not touched.

Co-Authored-By: Claude Sonnet <noreply@anthropic.com>

Co-Authored-By: Claude Sonnet <noreply@anthropic.com>
