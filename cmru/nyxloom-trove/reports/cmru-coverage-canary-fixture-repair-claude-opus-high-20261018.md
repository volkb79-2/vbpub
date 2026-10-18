# CMRU coverage-canary fixture repair (Claude Opus, high) — 2026-10-18

BLOCKED: `tests/test_builtin_handlers.py::test_wheel_build_resolves_registered_builder_image_from_cmr_config`
reads the complete registered estate. Every project config in root
`cmru.orchestration.toml` would have to be in the fixture, including
`libraries/cli-extended/cmru.toml`. That directory is forbidden by the fixture
contract (CX-D1: vendored cli-extended stays absent). Its presence would also
switch other modules out of their isolated-tree skip and into modes that copy
cli-extended **source**. The test is outside this task's edit scope, and it has
no isolated-tree guard. Every other observed control failure is repaired here.

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
| this commit | see below | recorded in "Gate result" |

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
| all 11 project configs of `cmru.orchestration.toml` | `test_builtin_handlers.py:112` → `handlers._configured_env_value` → `load_forge_config` parses every registered `cmru.toml` (`config.py:1283-1303`) | **BLOCKED** (see below) |

These skip keys must stay absent, because their presence starts full modes the
fixture cannot satisfy: `tester-unified/gen-requirements.py` (needs `git ls-tree
HEAD` and `libraries/cli-extended/src`), `tester-unified/build-image.sh`,
`libraries/cli-extended` (`test_installed_wheel_subprocess.py:371`
`_needs_build_tools`, `test_bootstrap_from_zero_sequence.py:38`),
`run-gate-project/tools/release_candidate.py` (`:530`), `tls-edge/cmru.toml`
(installer tests), and the complete set of project configs
(`test_dependency_levels_pyproject.py` `_estate_complete`). I checked that none
of the copied inputs is itself a skip key.

## BLOCKED detail and evidence needed to resolve it

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
that its estate-reading siblings carry. Possible resolutions; each is an
operator decision outside this scope:

- **A (recommended):** guard that one test exactly as its siblings are guarded,
  i.e. skip unless the registered estate is complete (the
  `test_dependency_levels_pyproject._estate_complete` predicate). The adjacent
  `test_wheel_builder_image_from_config_follows_the_registered_project_containing_cwd`
  already proves the resolver against a synthetic estate. The real-estate
  assertion would still run in the `coverage` lane.
- **B:** change CX-D1 so the fixture may carry `libraries/cli-extended/cmru.toml`,
  and re-key `_needs_build_tools` / `_needs_repo` on `libraries/cli-extended/src`.
  This changes a published fixture contract.

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
- `coverage_canary.py` and `tools/test_mutation_campaign.py`: unchanged (the
  latter monkeypatches `EXTERNAL_DOC_ARTIFACTS` to `()`).

## Gate result

No cockpit tests were run. The registered `canary` lane result for this commit
is recorded in the controller hand-back. The expected residual is exactly the
one BLOCKED test, so the control should still exit non-zero until resolution
A or B lands.

## Remaining limits

- `mutation_campaign.py` keeps its own copy of the fixture pathspec list. A
  shared `fixture_pathspecs()` would remove that coupling (the file was out of
  scope here).
- The `canary` log is truncated to a 2,000-character tail, so failures past the
  first are not visible in it. They were derived from the test sources.
- The comment at `test_tester_unified_image.py:243` ("isolated copy of `cmru/`
  alone, with no repository-root files") is stale. It is out of scope.
