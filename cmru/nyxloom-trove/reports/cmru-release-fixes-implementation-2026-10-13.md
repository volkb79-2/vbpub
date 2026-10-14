# CMRU release-fix wave: implementation report (2026-10-13)

- Branch: `cmru-release-fixes-20261013` (worktree `.worktrees/cmru-release-fixes-20261013`), started from `91a4ab26a`.
- Implementer: Claude Sonnet 5.5 (this report is the implementer's own account).
- **Not independently reviewed.** The stored Opus review
  (`cmru-release-readiness-claude-opus-5-high-2026-10-13.md`) reviewed the STARTING candidate, not the commits below.
- Nothing was merged, tagged, published, pushed, installed, deployed or restarted.

## Status in one paragraph

**Implementation is complete and committed; the aggregate release gate has NOT passed on the final
candidate.** Operator hold (2026-10-13, Assay pilots running): all CMRU gates are stopped and must not
be restarted until the operator authorises it. The last code commit (KI-78, `07871df27`) added
code and tests that no registered lane has executed yet, so the 100% branch-coverage result below
predates it. Nothing below is a "ready to release" claim.

## KI-78: `[Unreleased]` carried into the body, consumed after publish (operator requirement, added after the first report draft)

Operator requirement: preserve each project's `CHANGES.md` `[Unreleased]` entries, migration notes
included, append them below the generic install text, clear/consume them only after a successful
publish, keep them on dry-run or failure. **KI-75 did not cover this** (its oracle forbade reading
`[Unreleased]`, and KI-30 refused a non-empty one), so a dedicated issue was filed:
`KNOWN_ISSUES_TODO_BACKLOG.md` **KI-78**, with six acceptance oracles. Implemented in `07871df27`:

- Body = generated block, exact version section, then the `[Unreleased]` text verbatim
  (`release_notes.compose_release_body`, through the one builder the publisher and `--dry-run` share).
- KI-30's refusal is removed for every consumer: `version.detect_changed_projects` now excludes the project's
  *configured* changelog path (not just the default names), so the post-publish commit cannot schedule a
  release (follow-up commit, operator follow-up of 2026-10-14: "work for all CMRU consumers").
- "Until the complete transaction succeeds": the consume commit reaches `main` only via the promotion push, so
  a failed gate/build/publish/promotion leaves `main`'s `[Unreleased]` intact (backlog KI-78, oracle 7).
- `cli._consume_published_unreleased_notes` commits the move (appended to the released section,
  `[Unreleased]` emptied) after the publish and before promotion; unreachable from dry-run, failed
  gate/build/publish and `--no-build`.
- README, DESIGN-GUIDE, CONSUMERS, RELEASE-TRANSACTIONS updated; `test_docs_config_examples` pins the contract.
- Tests: `tests/test_unreleased_notes.py` (new), edits in `test_changelog.py`, `test_release_notes.py`, `conftest.py`.
- **Not executed**: only `pyflakes` ran on them. They must pass on the registered `coverage` lane.

## Per-finding disposition

| Finding | Disposition | Where |
|---|---|---|
| C-1 gate helper not bound to HEAD | **Fixed.** Before any lane, preflight or import, the host runner inventories every `sys.path` root (`cmru/src`, `cmru/tools`, `assay/src`, `assay/analysis/src`, `assay/tools`, `libraries/worktree/src`) against `git ls-tree HEAD` by blob hash and refuses an untracked or ignored executable module (incl. a top-level shadow such as `json.py`), any symlink, and any tracked file edited or deleted since HEAD. The earlier check used `ls-files --others --ignored`, which cannot see a plain untracked file. | `cmru/tools/run_release_gate.py` (`_assert_no_untracked_gate_code`), `tests/test_release_gate.py` |
| C-2 ref digest cried wolf | **Fixed.** The digest covers only gate-owned refs: snapshot branch, source branch, `refs/cmru-gate/<id>/`. An unrelated commit, branch, tag or fetch in another worktree, mid-lane or between resumed runs, does not fail the gate. New `ref-probe` lane: from inside the real tester container it writes a ref in the gate namespace and the gate must detect it (silence fails the gate). Stated limit: a tester writing an UNRELATED shared ref is indistinguishable from another agent; Run-Gate's mount hardening owns that. | `gate_snapshot.py`, `run-gate.toml [lanes.ref-probe]`, `run_release_gate.py` |
| C-3 coverage lane unreachable | **Fixed.** The dead cleanup was replaced by the tested `cmru gate-snapshots` verb; dead/duplicate branches in `gate_snapshot.py` were removed instead of excluded. Registered `coverage` lane: 100.00% branch (see Evidence). No omit/exclusion added. | `gate_snapshot.py`, tests |
| C-4 lease liveness / namespace | **Fixed.** `gate_lease_state`: `dead` only on positive evidence (no lease; expired non-CMRU holder; same PID-namespace+boot holder whose process is gone or recycled). A foreign-namespace, legacy or malformed holder is `live` until its 18 h TTL and `indeterminate` after; neither is reaped. A source path that cannot be statted is not a gone source: absence counts only with Git's evidence (deregistered, or registered+unlocked+prunable). `worktree.remove_orphan_workspace` now refuses a source Git still registers as live/locked. | `gate_snapshot.py`, `libraries/worktree/src/worktree/core.py` (+2 tests) |
| C-5 installed-wheel direct vs live | **Fixed.** `installed-wheel` is a plain component lane again (docs agree). New gate-only `installed-wheel-live` requires a manifest that is `running`, names THIS snapshot checkout, and was refreshed within 30 min (rewritten before every lane); a `running` manifest from a killed gate fails it. | `run-gate.toml`, `tools/installed_wheel_smoke.py`, `run_release_gate.py` |
| C-6 no cleanup verb | **Fixed.** `cmru gate-snapshots [--remove] [--include-current] [--dry-run] [--json]`: lists `removable`/`retained` with reasons, re-judges each snapshot from fresh facts, repeatable, never touches a live/unprovable lease or an unobservable source. CLI surface manifest, SPEC region/semantic row, review catalog (8 cases) and packaged skill updated. | `cli.py`, `docs/cli-*`, `docs/SPEC.md` |
| C-7 canary ignore | **Kept and tested.** `/.cmru-gate-canary-*.txt` is already ignored; new tests assert the source `status --untracked-files=all` is empty after a passing AND a SIGTERM-interrupted gate. | `tests/test_release_gate.py` |
| C-8 stale KI-52 text | Already corrected in the checkpoint (title/(b) cite B147 shipped in assay-v8.0.0); verified, no change. KI-74/58/59/68/75 statuses updated; KI-77 filed. | backlog |
| D-1 simplification | Not done (design decision recorded in DESIGN-GUIDE: host runner kept, bound to HEAD instead of moving execution into the snapshot). | DESIGN-GUIDE |
| D-2 Run-Gate floor, D-3 container events | **Not done; filed as KI-77** (needs a Run-Gate-side revision API). | backlog |
| D-4 host capacity record | Not recorded: `dev-gates.slice`/`dev.slice` `memory.max/current` are not visible from the devcontainer (read attempted, slice dirs absent). Remains a release prerequisite before the 7h R2 lane. | n/a |
| D-5 / KI-75 | **Implemented** (below). | |
| D-6 branch reconciliation | KI-59 and KI-68 ported selectively from `cmru-bugs62` (below); `cmru-bugs62` NOT merged; its masker removal, floor and KI-69 scope bump were not taken (superseded or already present). `cmru-ki52-focused` / `cmru-inst62` untouched. | |

## KI-75 (release bodies)

- `src/cmru/release_notes.py`: exact `## [X.Y.Z] - YYYY-MM-DD` section extraction (verbatim, no `[Unreleased]`, no other version); refusal for missing, undated, duplicated, empty or wrong-version sections; reads from the tagged commit (`CMRU_RELEASE_COMMIT` or a retained build's source commit) via git.
- ONE body builder (`release.build_release_body` / `build_variants_release_body`) used by `publish_versioned*`; `cmru release --dry-run` prints the complete body (between `----- begin release body -----` markers) through the same function. Only the artifact file name and SHA256 (unknown before a build) are marked placeholders; the test substitutes real values and asserts byte equality with the publisher's body.
- Protected declaration `CMRU_RELEASE_NOTES_SOURCE=changelog:<path>|none` is set by `run_project_step` for every step, reserved against project `[env]`; a handler without it refuses (no silent fallback). `--notes-env` replaces only the opening line.
- Prepare phase refuses before the gate/tag/upload if the pending section is unusable.
- Product decision taken within the brief: a project WITH a changelog and no usable section REFUSES; `changelog = false` publishes the generated body alone. Image-only pushes (no handler) have no GitHub body.
- Docs: README (what), DESIGN-GUIDE (why), CONSUMERS (how, incl. the KI-58 recipe), SPEC rows, backlog.

## KI-58 / KI-59 / KI-68

- KI-58: the saved notes (`run-gate-project/nyxloom-trove/reports/CMRU-KI58-unreleased-notes-carry-forward-2026-10-13.md`) were NOT edited, nor the `cmru-bugs62` draft (an older subset). No downstream `CHANGES.md` was touched. The exact safe carry-forward is in `KNOWN_ISSUES_TODO_BACKLOG.md` KI-58 and `docs/CONSUMERS.md` "Release bodies and curated notes": one commit on `main` per project authoring the pending `## [X.Y.Z]` section with the generated marker and `source-end` = full SHA of the final project commit, `[Unreleased]` left empty, `--set-version` pinned, then `cmru release <name> --dry-run` must print a body ending with exactly those entries. cgroup-profiler's image push has no GitHub body, so its notes stay in its changelog.
- KI-59: `_is_own_bump_commit` (exact subject + diff limited to the version file and generated outputs). KI-68: stale generated section with non-generated lines now REFUSES (exit 4, lists up to 20 lines); the `cmru-bugs62` override flag `--allow-changelog-regeneration` was deliberately not ported (new CLI surface and a silent-drop escape hatch). Tests: `tests/test_changelog_hand_edits.py`.

## Pre-existing failures found by the first registered coverage run and fixed

(not caused by this wave; present at `91a4ab26a`, all in CMRU tests)

- `test_project_fixture`: the fixture helper resolves a real Git worktree since `7bb29e359`; the test used a non-repository.
- `test_tester_unified_image::test_dockerfile_copies_every_cmru_wheel_input_before_build`: pinned the old `1003:994` identity block that `da06e52de` replaced; updated to the Dockerfile's current text (tester-unified itself untouched).
- `test_w2_pkg1_transaction_exceptions::test_remove_workspace_translates_and_propagates`: fake workspace lacked `path`, now read by the gate-snapshot cleanup hook.
- `test_docs_config_examples` still asserted the removed masker and stale doc phrases; rewritten for the new boundary.

## Files changed

`git diff --stat 91a4ab26a..HEAD`: 36+ files. By area:

- `cmru/src/cmru/`: `gate_snapshot.py`, `release_notes.py` (new), `release.py`, `handlers.py`, `changelog.py`, `cli.py`, `config.py`, `skills/cmru-cli/SKILL.md`
- `cmru/tools/`: `run_release_gate.py`, `installed_wheel_smoke.py`
- `cmru/run-gate.toml` (lanes `installed-wheel-live`, `ref-probe`), `cmru/assay.toml` (R1 base `cmru-v6.1.0`)
- `cmru/docs/`: `SPEC.md`, `cli-review.toml`, `cli-surface.json`, `CONSUMERS.md`, `DESIGN-GUIDE.md`, `RELEASE-TRANSACTIONS.md`; `cmru/README.md`; `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` (KI-58/59/62/68/74/75/77/78)
- `cmru/tests/`: new `test_gate_snapshot_internals.py`, `test_gate_snapshots_cli.py`, `test_release_notes.py`, `test_changelog_hand_edits.py`, `test_unreleased_notes.py`; edited `test_release_gate.py`, `conftest.py`, `test_changelog.py`, `test_docs_config_examples.py`, `test_project_fixture.py`, `test_tester_unified_image.py`, `test_w2_integ.py`, `test_w2_pkg1_transaction_exceptions.py`, `test_run_step_replacements.py`
- `libraries/worktree/`: `src/worktree/core.py` (orphan cleanup needs Git-registry evidence), `tests/test_core.py`
- Not touched: `tester-unified/`, downstream `CHANGES.md`, the KI-58 saved notes, `cmru-bugs62`.

## Evidence

Every run is a registered lane (`./run-gate.py ...`); exit statuses are the job's own (`EXIT=$?` appended
inside the background wrapper), never a pipe's. Logs: `$CLAUDE_JOB_DIR/tmp/*.log` (session-local).

| Lane / command | Commit | Outcome |
|---|---|---|
| `cd cmru && ./run-gate.py coverage` (run 1) | `3cf2e8f37` | EXIT=1: 10 failed (4 pre-existing at the start commit, 6 mine), 99.96% |
| same (run 2) | `0fa13df87` | EXIT=1: 100.00%, 1 failed (`test_tarball_publish_reads_version_and_tarball_from_real_retained_inventory`) |
| same (run 3) | `48f566146` | **EXIT=0**: 4556 passed, 6 skipped, 100.00% line+branch, no exclusions |
| `cd libraries/worktree && ./run-gate.py worktree` | `b42b93dd6` to `e4c01ba20` (passing run) | run 1 FAIL (an existing test assumed `git show-ref` exits 1), run 2 FAIL (98.45% coverage), run 3 and 4 FAIL (`MUTANTS_SURVIVED`, 5 then 2), **run 6 PASS** (R0–R3, EXIT=0) |
| `cd cmru && PATH=/home/vscode/.venv/bin:$PATH ./run-gate.py gate` (attempt 1) | `e4c01ba20`… | EXIT=1: the cockpit `python3` lacks `cli_extended`; the gate must be started with the estate venv first on `PATH` |
| same (attempt 2) | `ea9ee1b93` parent | EXIT=1: the snapshot validator rejected Run-Gate rev 68's `cmru/.run-gate/` store (fixed in `ea9ee1b93`) |
| same (attempt 3) | `ea9ee1b93` | `ref-probe` PASS, `installed-wheel-live` PASS, `assay` PASS, `coverage` PASS, then `mutation` refused (EXIT=1): R1 base `cmru-v5.5.0` was behind the latest published `cmru-v6.1.0`; fixed in `670f99d46`. **This is the only run that exercised `ref-probe` and `installed-wheel-live` live.** |
| same (attempt 4) | `670f99d46` | started cleanly, `ref-probe` and `installed-wheel-live` PASS; **stopped by me (SIGTERM, then `docker stop` of my own `run-gate-vbpub-assay-…` container by exact name) at the operator's hold**, EXIT=2. No verdict. A resumable retained snapshot `cmru-gate-ad61a1146ae971f80547` remains (`cmru gate-snapshots` lists it as retained; `--include-current` removes it). |
| `cmru gate-snapshots` / `--remove` | `670f99d46` | live use removed two finished snapshots correctly |

**Not run (and why):** the coverage lane on `07871df27` (KI-78 code), the aggregate `gate`, the 7h mutation lane,
the canary: held by the operator. `pyflakes` is the only check the KI-78 code and tests have had.

## Unresolved / blocked

- **Operator hold on all CMRU gates** (Assay pilots). Not a technical blocker; the gate is simply not run.
- **No gate verdict on the final candidate.** The "complete registered CMRU gate bound to the final commit" oracle is OPEN.
- **KI-77** (Run-Gate revision floor D-2, container-level event evidence D-3): needs a Run-Gate-side revision API. Not done.
- **D-4 host capacity record:** `dev-gates.slice`/`dev.slice` `memory.max` are not visible from the devcontainer (Run-Gate's own admission warning says the same: cgroupns=private). The operator or the host must supply the figure before the 7h R2 lane.
- **No independent review.** The stored Opus review covered the starting candidate only.
- **Process notes:** (1) a Python heredoc `write_text` edit of `gate_snapshot.py` early in the wave, and a small Python string-replace on `tests/test_release_notes.py` during KI-78, both against the "edits via Edit/apply_patch" rule, plus (during the 2026-10-14 follow-up) one `sed -i` for a one-word typing import in `version.py` and a shell heredoc append to `tests/test_unreleased_notes.py`; the content was reviewed afterwards but the rule was broken four times. (2) The mid-gate hold meant KI-78 could not be validated; treat its tests as unproven.
- **Known KI-78 limit:** if the post-publish consume commit fails, the release is already public; the tag/artifacts/candidate are retained and the operator moves the text by hand.

## Safe next release steps

1. Operator authorises gates (Assay pilots finished).
2. Optionally remove the retained snapshot: `cd cmru && cmru gate-snapshots --remove --include-current` (or let the next gate resume it).
3. `cd cmru && ./run-gate.py coverage` on a clean tree: must be 100% with the KI-78 tests green. Fix forward (tests were never run).
4. `cd cmru && PATH=/home/vscode/.venv/bin:$PATH ./run-gate.py gate` on the committed final candidate, in the background with an `EXIT=$?` marker. If the 7h mutation campaign starts, read progress ~90 s later, then poll no more often than every 25 minutes. Do NOT use `--postpone-mutation`.
5. Only after a green gate and an independent review: merge serially with `--no-ff`, then the supported `cmru release` flow. Before releasing downstream projects, apply the KI-58 carry-forward, or simply leave the pending entries under `## [Unreleased]` in each project's `CHANGES.md` (KI-78); verify with `cmru release <name> --dry-run` that the body ends with them.
6. Do not merge `cmru-bugs62` wholesale; its KI-59/KI-68 content is already ported.
