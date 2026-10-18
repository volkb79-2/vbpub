# CMRU coverage-canary fixture repair (Claude Opus, high) — 2026-10-18

Resolved (previously BLOCKED; the operator approved resolution A).
`tests/test_builtin_handlers.py::test_wheel_build_resolves_registered_builder_image_from_cmr_config`
reads the complete registered estate. The fixture cannot carry that, because
it would include `libraries/cli-extended/cmru.toml` (CX-D1). The test now has
the same complete-estate skip as its estate-reading siblings. It skips in the
disposable fixture and runs its unchanged real-estate assertion wherever all
registered configs are present. Every other observed control failure is
repaired by fixture inputs.

## Bindings

- Checkout `/workspaces/vbpub/.worktrees/cmru-candidate-integrated-20261017`,
  branch `cmru-candidate-integrated-20261017`; started at `3266b10fa`.
- Retained failed release transaction
  `.worktrees/cmru-release-20261018_020837-cmru-fvsibz`: read only, not touched.
- No push, no tag, no change to main, other worktrees, or other projects. R2 not run.

## Lane history

| run | HEAD | result |
|---|---|---|
| release `gate-provisional` canary (`/tmp/run-gate/run-gate-vbpub-canary-1601649-1792290420.log`) | `3266b10fa` | control exit 2: collection errors, `tester-unified/Dockerfile` and `nyxloom/pyproject.toml` missing |
| operator-run `canary` (`/tmp/run-gate/run-gate-vbpub-canary-1629392-1792292196.log`, 280.10 s) | `34cae01ca` | control exit 1: 4,906 passed, 435 skipped, 7 failed; transformed run never reached |
| fixture inputs + BLOCKED report | `81d3a0f1e` | not run by this agent (harness approval unavailable) |
| resolution A follow-up | see "Gate result" | not run by this agent; the controller runs it |

## Root cause

`copy_project_fixture` builds the disposable tree for the canary's known-good
control and for every mutation candidate. It carried only CMRU plus a
hand-listed subset of repository-root files. The suite has a deliberate
"isolated canary tree" mode: tests that judge tester-unified, nyxloom, the
bootstrap guide, Run-Gate tooling or the whole estate skip when their key files
are absent. Two things went wrong. First, some modules read repository-root
files **during collection**, before their skip applies. Second, several tests
read repository-root files with no skip at all. Either way the control fails,
and it fails loudly, as a control failure. It was never reported as a coverage
refusal.

## Derived closure (from the test sources, not from the first missing path)

| input | reader | disposition |
|---|---|---|
| `tester-unified/Dockerfile` | `test_tester_image_cli_extended_identity.py:27,33` module constants; `test_bootstrap_from_zero_sequence.py:338` | copied (`34cae01`) |
| `nyxloom/pyproject.toml` + setuptools search root `nyxloom/src` | `test_tester_unified_image.py:2772` `_nyxloom_wheel_inputs()` parametrize (`:2647`, `:2665`) | copied (`34cae01`) |
| `.gitignore` | `test_release_gate.py:1864` clean-source check (both params) | copied (this commit) |
| `run-gate-project/cmru.toml` | `test_release_gate.py:1395` provider prefix | copied (this commit) |
| `assay/src` | `test_release_gate.py:1067`: `run_release_gate._load_components()` must import from `REPOSITORY_ROOT/assay/src` | copied (this commit) |
| `docs/BOOTSTRAP-FROM-ZERO.md` | `test_docs_config_examples.py:321`: linked from CMRU README, DESIGN-GUIDE and CONSUMERS (the only outward link target the fixture lacked) | copied (this commit) |
| all 11 project configs of `cmru.orchestration.toml` | `test_builtin_handlers.py:112` → `handlers._configured_env_value` → `load_forge_config` parses every registered `cmru.toml` (`config.py:1283-1303`) | not copied; the test gets the complete-estate skip (resolution A, below) |

These skip keys must stay absent, because their presence starts full modes the
fixture cannot satisfy: `tester-unified/gen-requirements.py` (needs `git ls-tree
HEAD` and `libraries/cli-extended/src`), `tester-unified/build-image.sh`,
`libraries/cli-extended` (`test_installed_wheel_subprocess.py:371`
`_needs_build_tools`, `test_bootstrap_from_zero_sequence.py:38`),
`run-gate-project/tools/release_candidate.py` (`:530`), `tls-edge/cmru.toml`
(installer tests), and the complete set of project configs
(`test_dependency_levels_pyproject.py` `_estate_complete`). I checked that none
of the copied inputs is itself a skip key.

## Former BLOCKED finding and its resolution

The wheel-builder test calls `_configured_wheel_builder_image(repo_root /
"run-gate-project")`. That loads root `cmru.orchestration.toml`, which parses
`libraries/cli-extended/cmru.toml`, `cmru/cmru.toml`, `ciu/cmru.toml`,
`assay/cmru.toml`, `topos/cmru.toml`, `nyxloom/cmru.toml`,
`modern-debian-tools-python-debug/cmru.toml`, `pwmcp/cmru.toml`,
`tls-edge/cmru.toml`, `run-gate-project/cmru.toml` and
`scripts/cgroup-profiler/cmru.toml`. Any missing one fails loudly
(`config.py:723`). Copying them all has three effects:

1. It creates `libraries/cli-extended/`. That breaks the CX-D1 oracle in
   `tests/test_project_fixture.py` and this task's "cli-extended remains absent"
   rule.
2. It switches on `_needs_build_tools`, whose tests copy
   `libraries/cli-extended/src` (`test_installed_wheel_subprocess.py:392-400`),
   which is forbidden source.
3. It makes `_estate_complete()` true and makes tls-edge present. That starts
   the estate and tls-edge suites, which need further trees.

The test arrived in `1fa737782` (2026-10-14), without the isolated-tree guard
that its estate-reading siblings carry. Two resolutions were offered:

- **A (recommended):** guard that one test exactly as its siblings are guarded,
  i.e. skip unless the registered estate is complete (the
  `test_dependency_levels_pyproject._estate_complete` predicate).
- **B:** change CX-D1 so the fixture may carry `libraries/cli-extended/cmru.toml`,
  and re-key `_needs_build_tools` / `_needs_repo` on `libraries/cli-extended/src`.
  This changes a published fixture contract.

**The operator approved A, and it is implemented as follows.** The test
imports `_estate_complete` from `tests.test_dependency_levels_pyproject`, so
the predicate is the same function, not a copy. It now carries
`@pytest.mark.skipif(not _estate_complete(), reason="estate projects absent
(isolated canary tree)")`. The predicate is true only when root
`cmru.orchestration.toml` exists, registers at least one project, and every
registered `config` path is a file.

- **Full estate:** all 11 registered configs are tracked, so a full-checkout
  gate snapshot satisfies the predicate. The `coverage` lane (`pytest tests` in
  `{worktree}/cmru`) therefore executes the unchanged assertion: the configured
  `CMRU_WHEEL_BUILDER_IMAGE` equals the value declared in
  `cmru.orchestration.toml`.
- **Disposable fixture:** only `cmru/cmru.toml`, `topos/cmru.toml`,
  `nyxloom/cmru.toml` and `run-gate-project/cmru.toml` are present, so only
  this test is skipped, with that reason.
- **Unchanged:** the test body and the adjacent synthetic resolver test
  (`test_wheel_builder_image_from_config_follows_the_registered_project_containing_cwd`).
  The synthetic test stays unconditional, so the resolver is still exercised in
  the fixture. No product code and no fixture contents changed.

## Changes

- `cmru/tools/project_fixture.py`: adds `SUITE_COLLECTION_INPUTS`, the derived
  inputs above, each with its consumer named. They are spliced into
  `EXTERNAL_DOC_ARTIFACTS` because `mutation_campaign._test_manifest` and
  `_campaign_tree_status` fingerprint and dirty-check exactly the four fixture
  constants' pathspecs. The mutation manifest therefore binds every new input
  without editing `mutation_campaign.py`. The required check now accepts a tree
  pathspec when it selects at least one tracked file. The original properties
  are unchanged: only Git-tracked files, no `.git`, symlink link text kept, a
  missing declared input refuses with its name.
- `cmru/tests/test_project_fixture.py`: uses a realistic Git estate (the first
  version lacked `cmru/pyproject.toml`, which `_test_manifest` requires; that
  was my defect, and it caused the 7th operator-run failure). Its oracles:
  every support file is copied byte-for-byte; skip keys, sibling trees
  (`assay/tests`, `nyxloom/tests`, `run-gate-project/tools`), unregistered
  estate configs (`ciu`, `tls-edge`, cli-extended), untracked and ignored files
  stay out; a tracked symlink keeps its link text; each declared input refuses
  when untracked; the mutation manifest pins the inputs and detects drift.
  Canary contract, driven through `coverage_canary.run` with an offline
  stand-in test command:
  - the control passes, the uncovered candidate exits 1 with `Required test
    coverage of 100% not reached`, and the evidence records both results;
  - a missing support input raises "known-good canary control failed" and
    writes no evidence;
  - a candidate that fails for another reason is refused, and its evidence
    says so.
- `cmru/tests/test_builtin_handlers.py` (resolution A follow-up): adds the
  complete-estate skip to the real-estate wheel-builder test only, as described
  above.
- `coverage_canary.py` and `tools/test_mutation_campaign.py`: unchanged (the
  latter monkeypatches `EXTERNAL_DOC_ARTIFACTS` to `()`).

## Gate result

No cockpit tests or gates were run by this agent, for `81d3a0f1e` or for the
resolution A follow-up. The registered `canary` lane on the follow-up commit is
the controller's to run, and its result is not claimed here. The oracle it must
show is unchanged: the known-good control exits 0; the appended uncovered line
exits 1 with `Required test coverage of 100% not reached`; the evidence file
records both.

## Remaining limits

- `mutation_campaign.py` keeps its own copy of the fixture pathspec list. A
  shared `fixture_pathspecs()` would remove that coupling (the file was out of
  scope here).
- The `canary` log is truncated to a 2,000-character tail, so failures past the
  first are not visible in it. They were derived from the test sources.
- The comment at `test_tester_unified_image.py:243` ("isolated copy of `cmru/`
  alone, with no repository-root files") is stale. It is out of scope.
