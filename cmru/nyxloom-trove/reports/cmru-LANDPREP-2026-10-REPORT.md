# CMRU-LANDPREP report (2026-10-06)

Base `d243dd770`, branch `cmru-wave-2026-10`. Everything below was run in this worktree unless marked "not run".
Nothing was reinstalled, pushed, released, built or retagged.

## Commits (in order)

1. `208c60ba5` feat: `--postpone-mutation <ID>` in `cmru/tools/run_release_gate.py`, lane `gate-provisional` (`cmru/run-gate.toml`), KI-62 in `cmru/KNOWN_ISSUES_TODO_BACKLOG.md`, `cmru.toml` `steps.run-tests` -> `./run-gate.py gate-provisional` (`TODO(cmru-6.0 post-release)`), tests.
2. `07d44b8a1` chore: shims. `default_projects` re-added to `cmru.orchestration.toml`; `extensions` line removed from `ciu/cmru.toml` (both `TODO(cmru-6.0 post-release): remove shim`).
3. `58247078b` docs: release notes (CHANGES.md, UPGRADING-6.0.md pointer text).
4. `959bd4019` test: skip of `test_ciu_inlines_its_own_enroll_fragment_and_matches_the_committed_file` while the extensions shim is active (needed; see below).
5. `6bc3f860c` docs: controller addition C1 (floor text 0.2.0 -> 0.3.0).
6. (this report commit)
7. LAST: "requires retag" image/lane revert commit.

## 1. Provisional gate

- Option skips ONLY the mutation lane and also its remote-facts preparation (`baseline.build_facts`, consumed only by mutation). Lane order otherwise unchanged: installed-wheel, assay, coverage, canary. The gate never ran an `enroll` lane at HEAD (it moved to `ciu/run-gate.toml`; the existing docs test asserts this), so nothing was dropped.
- Marker: `cmru/.assay/mutation-postponed-cmru.json` (`.assay` is already in `cmru.toml` `evidence_paths`, so it is retained with the release evidence). Fields: schema_version, lane, status, tracking_id, timestamp (UTC), reason. One stderr line `cmru-release-gate: WARN: mutation lane POSTPONED (KI-62); ...`.
- Written only after installed-wheel/assay/coverage pass. A full gate (no option) deletes any stale marker.
- Tracking id: stripped; empty/whitespace refused (argparse error exit 2, and `ArgumentTypeError` for direct calls); a missing value is an argparse error.
- Tests (`cmru/tests/test_release_gate.py`, `test_docs_config_examples.py`): skip + marker + WARN + order; failing later/earlier lane; no marker and stale-marker removal without the option; blank ids; missing value; main passthrough; lane declaration equals `gate` + `--postpone-mutation KI-62`. `pytest tests/test_release_gate.py tests/test_docs_config_examples.py --cli-case-partial`: 32 passed. Full-suite coverage lane: 100.00%.
- `./run-gate.py --dry-run gate-provisional`: lane resolves, command line contains `--postpone-mutation KI-62`. The real `gate-provisional` lane was NOT run (it would run all container lanes); only stubbed unit tests exercise `run_release_gate`.
- KI-62 text filed as asked (next free id; no KI-62 on any local branch).

## 2. Shims: verification

- OLD cmru (the devcontainer's installed editable 5.5, i.e. main-checkout code) run with cwd = this worktree: `cmru status all` exit 0, table printed. Old behaviour BEFORE the shim (failure on missing `default_projects`) was taken from the controller state, not re-measured.
- 6.0 source from this worktree (`sys.path` insert of `cmru/src`): `cmru status all` exit 0; `cmru dependencies` PREFLIGHT PASS. 6.0 does NOT reject `default_projects`: it prints one stderr line `[WARN] orchestration.default_projects is ignored and will be removed ...` (config.py:1217). No unknown-key refusal. No solution needed.
- CONSEQUENCE of removing `extensions` (not forced-away): the cmru test `test_ciu_inlines_its_own_enroll_fragment_and_matches_the_committed_file` renders ciu's get.py from `ciu/cmru.toml` and failed in the first coverage run (1 failed, 4035 passed). Fixed by a marked skip (commit 4), not by weakening the shim. Separately: a `cmru get-py ciu` / ciu release rendered inside the window would DROP the enroll fragment from `ciu/get.py`; the committed `ciu/get.py` is unchanged and ciu's own enroll tests run against the committed file, so they are unaffected. Do not release ciu inside the window (stated in the ciu/cmru.toml comment). Restore the line and delete the skip together post-release.

## 3. Editable-install analysis (devcontainer)

Facts inspected (read-only): dist-info `cmru-5.5.1.dev720+g06e2142d8`, finder `__editable___cmru_5_5_1_dev720_g06e2142d8_finder.py`.

- Finder MAPPING: `cmru` -> `/workspaces/vbpub/cmru/src/cmru`, `worktree` -> `libraries/worktree/src/worktree`, `cli_extended` -> `libraries/cli-extended/src/cli_extended`; NAMESPACES `cmru.templates`. It is a `MetaPathFinder` placed AFTER `PathFinder` in `sys.meta_path`.
- Simulation (scratchpad `sim_editable_6_0.py`; the real finder module with its `cmru` mapping repointed at this worktree, in one process, nothing installed): all 33 `cmru.*` modules import with no failures; `cmru.cli.main(["--help"])` works; `importlib.resources` finds `cmru/templates` and `cmru/skills` through the package path (new package data works because editable = source tree). New subpackages are discovered through `cmru.__path__`, so the finder needs no change for them. The 6.0 `package-dir`/`where` change (dropping the vendored `cli_extended` mapping) is irrelevant to the old finder: top-level `cli_extended` resolves from site-packages (`cli-extended 0.3.0` wheel, via `PathFinder`) and the stale source mapping is shadowed; `worktree` also resolves from site-packages and `diff -rq` against `libraries/worktree/src/worktree` shows no difference today.
- Verdict: the 6.0 tree imports correctly through the old editable finder, provided cli-extended >= 0.3.0 stays installed in the venv (it is).
- Stale metadata effects (what the old dist-info cannot know):
  - Entry points: old `entry_points.txt` lists `cmru`, `cmru-agent`, `cmru-controller`. `cmru` (`cmru.cli:main`) is unchanged and works. `cmru-agent`/`cmru-controller` (and their `venv/bin` scripts) point to modules RETIRED in 6.0: import of `cmru.agent.cli` / `cmru.controller.cli` fails with ModuleNotFoundError (measured in the simulation). Nothing in the repo (py/toml/sh/yml) references them; the leftover untracked `__pycache__` dirs make `cmru.agent` an empty namespace package. They become dead commands until reinstall.
  - `Requires-Dist` has no `cli-extended`, so `pip check` will not flag it, and `cmru doctor`'s cli-extended floor check reads the installed metadata: expect a doctor WARN (documented as finding `adoption-doctor-cli-extended-floor-warn`; I did NOT run `cmru doctor`).
  - Version string: `cmru version`/help banner report `5.5.1.dev720+g06e2142d8` (verified in the help banner) until a reinstall, even though the code is 6.0.
  - `[tool.cli-extended]` / `cli-extended surface` read `pyproject.toml` by path, not metadata; `surface check` passed here.
- What the merge changes immediately for every session: new `cmru` processes run 6.0 code, with the BREAKING grammar (`run-step`, bare `publish`, mode-less `cleanup`, `run --run-tests`, `--discard-*-on-release`, `--target` on `handler oci-image-*` all refused; see UPGRADING-6.0). Already-running processes keep the old modules in memory. Gate/script callers of the old spellings in other worktrees will break at once.
- Not covered / not run: the real console script from main after merge, `cmru doctor`, `cmru release`, a non-editable 5.5 install. Recommendation: after merge, reinstall the editable (`pip install -e` with `--no-deps`, cli-extended first, per UPGRADING) so metadata, version and entry points match; that is the controller's step.

## 4. Release notes

Checked `changelog.py`: a version release is refused when `## [Unreleased]` has a non-comment body (KI-30) or when a `## [X] - UNRELEASED` heading for the same version exists (KI-23); a hand-written `## [6.0.0] - <date>` section without the `cmru: generated` marker is also refused, and one WITH the marker is regenerated if project commits follow its cursor. Hence no hand-written 6.0.0 text can live in CHANGES.md; the real guards report `_unreleased_body == ''` and no UNRELEASED headings on the committed file. The 6.0.0 section is generated from commit subjects since `cmru-v5.5.0`.
- The pre-wave text (`68a03b4fe^`): its `[Unreleased]` bullets duplicate 5.5.0 or are covered by the commit subjects; its untagged generated `[5.6.0]` section was dropped by the wave. Both are recorded in the CHANGES.md comment (comment only, so non-blocking).
- The R2 postponement and KI-62 are carried into the generated notes by commit `58247078b`'s subject (shows under Documentation), plus UPGRADING-6.0.md "Release status: PROVISIONAL" paragraph and a note about the two shims. UPGRADING step 1 floor corrected to 0.3.0.
- Post-release pointer: none required in CHANGES.md; after KI-62 closes, the TODO in the CHANGES comment says to add a one-line pointer to the 6.0.x notes.
- Not run: `cmru release --dry-run` (would need the real release transaction).

## 5. Lane verdicts (run before the image-revert commit; flock + nice + ionice, one container at a time)

- `coverage`, first run (after commits 1-3): FAIL, 1 failed / 4035 passed / 6 skipped; coverage 100.00%; the failing test is the ciu render test explained above.
- `coverage`, after commits 4-5: PASS exit 0, 4035 passed, 7 skipped, total coverage 100.00% (log `scratchpad/landprep-coverage2.log`).
- `canary`: PASS exit 0 (log `scratchpad/landprep-canary.log`). Both ran on the `cmru-tester` environment (`tester-unified:cmru6-integ`), i.e. BEFORE the revert commit.
- The commit following those runs (this report, docs only, and the revert commit) is untested by lanes; the controller re-runs `coverage`/`canary` after the retag. `docs/test_docs_config_examples.py` (11 passed) was run after the revert edit.

## 6. Image/lane revert (last commit, "requires retag")

`coverage` and `canary` -> `environment = "tester-unified"`; `[environments.cmru-tester]` deleted; `cmru-mutation` image -> `tester-unified:local`; all `TODO(cmru-6.0 landing)` markers removed (grep over toml/py/sh finds none left). Hazard: until the controller retags `tester-unified:cmru6-integ` to `tester-unified:local`, these lanes run on the OLD image (without the baked 6.0 wheel/cli-extended) and will likely fail. Backup `:pre-cmru6` is the controller's step.

## Other notes

- C1 (controller addition): UPGRADING-6.0.md (both lines), `tester-unified/README.md:35` (Dockerfile pin verified as 0.3.0), findings remedy and SPEC.md:1080 changed to `>=0.3.0`; `cli-extended surface sync` then `surface check` passed.
- No checkpoint continuation file was needed (well under budget).
