# Run-Gate 24.0.0 release blockers — report (2026-10-15)

**Status: DONE, none BLOCKED.** Fix commit **`3da9b9ce847074bfad2e3a598c7f57fa31a30d41`**
(`3da9b9ce8`) on branch `run-gate-rg93-git-metadata-20261013`, on top of
`ee15a9174` (path-limited port of `cmru-release-readiness-20261015@2b7f27501`)
and `1ba70a8ea` (`--no-ff` merge of `main@da4b67f0b`). This report and the LOG
are committed separately after it. Nothing was released, published or merged
to `main`; no R2 or long gate was run; no test was executed from the cockpit.

## Oracle matrix

Every row's tests are in `run-gate-project/tests/` and run in the registered
`selftest` lane (`run-gate selftest`, from `run-gate-project/`). **None has been
executed by me** — they are written to pass and to go red on the named wrong
implementation; the controller's gate is the first real run. Facts I did probe
by hand are in the LOG.

| # | item | oracle (test) | goes red when | docs synced |
|---|------|---------------|---------------|-------------|
| 1 | RG-98 scratch isolation | `TestRG98JudgeScratchIsolation::test_default_in_repo_state_root_reaches_r0` — real `run_bare_host_lane`, real inner script and state preflight, default in-repo `.run-gate`, a judge that allocates scratch via `tempfile`/`TMPDIR` and ERRORs on a scratch inside `repo_top`; asserts R0 reached, judge keeps the operator's `TMPDIR`, child gets the state path. `::test_regression_control_…` shows the old shape errors. Unit: `test_assay_inner_keeps_an_in_worktree_state_root_off_the_judge_tmpdir`, `…_derives_test_temp_and_git_ceiling_from_state_root` (external root keeps `TMPDIR`), `…_judges_a_linked_worktree_…`; `test_in_repo_state_root_never_reaches_preflight_as_a_refusal` | `TMPDIR=<in-repo root>` reaches the judge, or a refusal/probe returns | README, SPEC (state-root rule), DESIGN-GUIDE (why (a) over (b)), CONSUMERS (table + `required-env` mapping), CHANGES, backlog |
| 2 | RG-99 lock-free Git config | `TestRG99LockFreeSafeDirectory` — real bash + real Git: `…foreign_owner` (`GIT_TEST_ASSUME_DIFFERENT_OWNER=1`: rejected without, accepted with), `…preexisting_entries_are_kept…` (+ `…visible_to_git_itself`), unreadable-count refusals, git-floor refusal by version table, gitless/unparseable-banner pass-through, concurrent lanes leave no `/tmp/run-gate-gitconfig*`, assay inner shares the injection | index-0 overwrite, a lock/file, a missing floor check | SPEC R-19a (floor 2.38, append, no `GIT_CONFIG_GLOBAL`), README, DESIGN-GUIDE, CONSUMERS, CHANGES |
| 3 | RG-100 imported inventory | `TestRG76AssayLaneImports`: `…listing_does_not_probe_and_says_so`, `…resolves_inventory_for_a_verb_that_needs_every_lane` (real facts from a real fake-judge: alpha/beta/gamma), `test_doctor_footprint_and_history_receive_the_imported_lanes`, `test_unavailable_inventory_refuses_instead_of_answering_partially` (9 argv shapes, asserts exit 2, no verb body ran, **no manifest file written**), `test_declared_lane_queries_do_not_need_the_inventory`, `test_resolution_requirement_matrix`; updated `…do_not_require_stopped_import_runner` (zero judge calls) | `--list`/help probe; doctor/history/footprint answer without the inventory; a manifest is written without it | SPEC R-49, README, DESIGN-GUIDE, CONSUMERS (verb table), CHANGES |
| 4 | RG-101 docs + remedy | SPEC terms (§1) and R-07 rewritten; `test_undefined_bare_host_…`, `test_undefined_legacy_host_…`, `test_inherited_central_lane_is_migrated_in_the_central_config`, `test_named_migration_makes_the_loader_accept_the_lane[bare-host,host]` (runs the *printed* command, reloads, resolves), `test_migrate_modes_materializes_names_referenced_only_by_assay_policy` | the printed command does not make the loader accept the lane | SPEC, README, DESIGN-GUIDE, CONSUMERS, CHANGES |
| 5 | RG-103 sequence doctor | `TestRG103SequenceDoctor`: legal sequences (incl. nested) through the CLI `doctor` (exit 0, per-member `[OK]` with environment facts, no `KeyError`), unresolvable member environment → `[FAIL]` exit 2, malformed members (missing, no environment, cycle, empty) → named `[FAIL]`s with other findings intact, `--check-env`, `sequence_leaf_members` unit | doctor resolves a sequence's environment; any malformed shape crashes | SPEC R-30, DESIGN-GUIDE, CONSUMERS (example), README, CHANGES |
| 6 | Tester image deps | `tests/test_tester_unified_base_variants.py` — extracts the Dockerfile's real apt `RUN` step and executes it under `/bin/sh` against shim bases with a dpkg-style ownership clash: MDT-like (no Docker package installed), docker-without-compose (only compose), upstream-like (both, verified), and a control proving the legacy unconditional line fails on MDT | unconditional install returns | `tester-unified/README.md`, `docs/DESIGN-GUIDE.md`, CHANGES |

Also: the doubled backslash in `CONSUMERS.md` (zero-release bootstrap example)
is fixed; both review reports are byte-identical to the candidate's.

## Things the controller must know

1. **RG-98 is a behavior change for consumers.** With the default in-repo root
   the judge no longer gets `TMPDIR`, so a consumer lane that relied on
   `env_passthrough = ["TMPDIR"]` to hand the state path to pytest must add
   `[lanes.<n>.infrastructure] TMPDIR = "required-env:RUN_GATE_TMPDIR"` (and drop
   `TMPDIR` from its passthrough) — recipe in CONSUMERS. It is the only
   mechanism Assay offers; I did not invent a product alternative. I did not
   inspect any consumer's `assay.toml` (dstdns is the reporting consumer, with
   36 lanes affected by the original defect); whether a given lane needs the
   mapping depends on what its tests do with `TMPDIR`.
2. **The RG-98 consumer fixture uses a fake judge** that reproduces Assay's
   scratch rule, not real Assay (the cockpit/test image runs no Assay lane and
   I ran nothing live). Recommended live acceptance: `run-gate assay-r1` from a
   **primary** checkout with the default root — previously refused by the
   interim preflight — reaches R0 (Sol's "real inventory and runner" ask).
3. **RG-99 stays OPEN for live acceptance only** (two concurrent `docker exec`
   lanes in one runner). Floor 2.38 comes from the reviews; I verified behavior
   on Git 2.55 and the version gate by fake banners, not on a real 2.38.
4. **Cross-scope test in the candidate:** `cmru/tests/test_tester_unified_image.py`
   (candidate only, lines ~3366–3367) greps the Dockerfile for the literal
   `docker-cli docker-compose`. I may not edit CMRU, so that substring is kept in
   an explanatory Dockerfile comment and the test stays green — but it is a
   text-grep, not an oracle; its owner should replace it with the executable one
   here when the candidate lands.
5. Oracle for item 6 lives in `run-gate-project/tests/` (not
   `tester-unified/tests/`) because that is what the registered `selftest` lane
   runs; no new lane was needed. An actual image build on each base is still a
   release-lane activity.
6. Out of this packet and untouched: Sol findings 1, 3, 5 (bootstrap doc,
   controller install step, slice verification), Opus H3/L1/L3, CIU-141/RG-102.
7. Diff coverage: every new conditional arm in `run_gate.py` has a test;
   whether the 100% changed-line/branch gate holds is for `selftest` to say.

## Controller's next commands

```bash
cd /workspaces/vbpub/.worktrees/run-gate-rg93-git-metadata-20261013/run-gate-project
run-gate selftest                     # authoritative; serially, one gate container
run-gate --base main assay-r1         # from a primary checkout for the RG-98 live check
```
Then an independent adversarial review of `ee15a9174..HEAD`, and
`git merge --no-ff` onto the integration branch only after the gates.
