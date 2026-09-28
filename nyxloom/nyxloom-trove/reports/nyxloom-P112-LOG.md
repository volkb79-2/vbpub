# nyxloom-P112 execution log

## Initial status: BLOCKED before implementation (resolved 2026-09-28)

BLOCKED: The approved wheel and service-entrypoint oracles cannot be met using
the declared `scope.touch`. Nyxloom's Docker wheel build receives only the
`nyxloom/` build context and copies only `pyproject.toml` and `src/`; the
required authoritative `libraries/cli-extended/src/cli_extended` package is a
sibling outside that context. Bundling it without a second maintained copy
requires changing the build context and Dockerfile. The release build uses
`nyxloom/docker-bake.hcl`, and the local compose build uses both
`nyxloom/nyxloomd/docker-compose.yml` and
`nyxloom/nyxloomd/ciu.compose.yml.j2`; all currently resolve the context to the
Nyxloom project root. The supervisor's default command and both compose
healthchecks also use `python -m nyxloom.cli ...`, which becomes invalid when
host commands move to `nyxloomctl` and service launch moves to `nyxloomd`.

The handoff initially omitted `nyxloom/nyxloomd/Dockerfile`,
`nyxloom/docker-bake.hcl`, and `nyxloom/nyxloomd/ciu.compose.yml.j2`.
`nyxloom/tests/test_daemon.py` explicitly checks both compose files, so the Ciu
template cannot remain on the old healthcheck path while the pre-rendered
compose file changes. I paused before implementation and recorded the mismatch.

## Resolution: user-authorized scope amendment — 2026-09-28

The user directed me to remove the out-of-scope restriction as a blocker and
authorized the necessary changes. P112 `scope.touch` now includes the Docker
image/build and Ciu compose inputs listed above, and the worktree procedure
records the isolated branch base. The amendment and required two-step
`input_revision` refreeze were committed before implementation resumed
(`918d2c6d`, then `e00423ee`).

## Evidence inspected

- `nyxloom/pyproject.toml`: only `nyxloom = "nyxloom.cli:main"` is installed;
  package discovery reads only `src`.
- `nyxloom/nyxloomd/Dockerfile`: the wheel build copies only `pyproject.toml`
  and `src` from its build context.
- `nyxloom/docker-bake.hcl`: the release image context is `.` (Nyxloom root).
- `nyxloom/nyxloomd/docker-compose.yml` and
  `nyxloom/nyxloomd/ciu.compose.yml.j2`: compose build context is `..`
  (Nyxloom root); both healthchecks invoke
  `/opt/nyxloom-venv/bin/python -m nyxloom.cli doctor --liveness`.
- `nyxloom/tests/test_daemon.py`: liveness healthcheck contract iterates over
  both compose files and requires each to contain the probe.
- P112 O6 requires the built Nyxloom wheel to contain `cli_extended` and the
  installed service supervisor to invoke `nyxloomd` directly; O4 requires the
  host healthcheck command to remain valid after ownership moves.

## Worktree and gate state

- Implementation branch: `nyxloom-cli-adoption`.
- The worktree includes current local `main` and the completed
  `cli-extended-nyxloom-api` prerequisite commit
  `6e5c6ef49b557151717fff82950308b2ece33b70`.
- The library prerequisite's declared `./run-gate.py gate` completed on that
  exact commit: R0, R1, R2, R3, and top-level `gate` all exited 0. The R3
  canary printed its expected rejection message for the redaction-guard
  mutation and the lane exited 0.
- At the initial block, no Nyxloom implementation files were changed and the
  Nyxloom gate had not started. Implementation resumes after the amendment
  commits and handoff refreeze.

## Scope added by the amendment

`nyxloom/nyxloomd/Dockerfile`, `nyxloom/docker-bake.hcl`, and
`nyxloom/nyxloomd/ciu.compose.yml.j2` are now in P112 `scope.touch`. The
existing scoped `tests/test_daemon.py` will verify both compose files; the
existing scoped installed-wheel/adoption tests will verify the wheel contents
and executable paths.

## Additional authorized test-scope amendment — 2026-09-28

During implementation, a repository-wide call-site search found that the old
single-entrypoint tests for operator, extraction, and diagnostic commands live
in additional project test modules. Leaving them unchanged would make the
project gate exercise removed command paths. The user authorized making the
necessary changes, so P112 `scope.touch` now also includes the test modules
that exercise moved commands:

`tests/test_free_models.py`, `tests/test_session_extract_edge_contracts.py`,
`tests/test_events_cmd.py`, `tests/test_session_extract_reasonix.py`,
`tests/test_resync.py`, `tests/test_resync_apply.py`,
`tests/test_migrate_store.py`, `tests/test_doctor.py`,
`tests/test_resume_guard.py`, `tests/test_backlog_items.py`,
`tests/test_intake_bridge.py`, `tests/test_route_doctor.py`,
`tests/test_liveness.py`, `tests/test_intake_chat.py`, and
`tests/test_control_auth.py`. The P112 input revision is being refrozen in the
required follow-up commit before implementation continues.

The same search also found `tests/test_lint.py` exercising the old no-argument
host-wide lint path, so that existing test surface is included in the same
scope amendment. Its explicit-path tests remain on `nyxloom lint`; only the
registered-project scan moves to `nyxloomctl lint`.

## Additional authorized documentation-scope amendment — 2026-09-28

Reviewing the user-facing adoption docs found that the detailed session
extraction guide under `src/nyxloom/session_extract/README.md` uses the old
`nyxloom extract*` command names throughout. Those examples are part of the
primary handoff/skill workflow and would be wrong after extraction moves to
`nyxloom-harness`. The user has authorized the required adoption changes, so
this exact guide is added to P112 `scope.touch`; the handoff input revision is
refrozen in the separate follow-up commit as required.

## Removal of the standing out-of-scope edit ban — 2026-09-28

The user explicitly directed removal of Nyxloom's standing prohibition on
necessary out-of-scope edits. Updated `nyxloom-trove/STANDING.md` so
`scope.touch` is the planned inventory rather than an exclusive allowlist,
including the deliverables and Never sections. Protected files, other active
package ownership, explicit forbids, and unrelated edits remain guarded. The
STANDING update is commit `03e7942e`; the handoff input revision was refrozen in
`8c17cda2` after the approved CLI grammar decision commit `c4ba7d92`.

## Parser-audit decision and current evidence — 2026-09-28

D-018 records source-derived grammar refinements: backlog list status choices
come from `backlog_entries.STATUSES`; finding record/list kind and finding
record severity choices come from the finding registry; event sequence
cursors are integers; and malformed finding `--field KEY=VALUE` syntax is
rejected before dispatch. The reference matrix now has a row for every current
command-local option, with placement and effect boundaries. A parser-vs-table
test rejects missing, duplicate, or retired option rows and checks these
source-derived values.

Focused CLI evidence after these refinements:

- `PYTHONPATH=src:../libraries/cli-extended/src /home/vscode/.venv/bin/python -m pytest -q tests/test_cli_adoption.py tests/test_cli_help.py tests/test_cli.py tests/test_cli_extract.py`: exit 0; every test in these four modules passed.
- `PYTHONPATH=src:../libraries/cli-extended/src /home/vscode/.venv/bin/python -m nyxloom.cli lint nyxloom-trove/handoffs/nyxloom-P112-cli-extended-adoption.md`: exit 0; stdout `clean`; stderr empty.
- Initial installed-wheel check built from implementation commit `f73af03b14ae42fa7d7f7dcbe362a581bf21ff68`: `/tmp/nyxloom-p112-wheel-final/nyxloom-0.8.1.dev494+gf73af03b-py3-none-any.whl`. Installed with the estate venv's pip into `/tmp/nyxloom-p112-final-installed`; from `/tmp`, with only that target on `PYTHONPATH`, imports for `nyxloom` and `cli_extended` resolved inside the install target. The installed `nyxloom --help`, `nyxloom version`, `nyxloom-harness extract --help`, and `nyxloomctl doctor --help` all succeeded. The wheel metadata exposes `nyxloom`, `nyxloom-harness`, `nyxloomctl`, and `nyxloomd`; `cli_extended` is included, `questionary` was not imported, and the isolated `NYXLOOM_STATE` path was not created. Rebuild and repeat this check after the gate fixes below.

Before starting the gate, `./run-gate.py history tester-unified --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-adoption --json` returned an empty history (`latest: null`), and no Nyxloom adoption gate process/container was active. The gate will be run against the committed implementation and its exact judged commit and every lane result will be recorded below.

## First tester-unified attempt — 2026-09-28

Command: `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-adoption tester-unified` from `nyxloom/`.

- Judged commit: `e1094a6eb44683a29b1e7218d50ea98e88d165c5`.
- `tester-unified`: `FAIL/COMMAND_FAILED`, exit 1 after 147.1 seconds; the declared pytest command ran but could not import `cli_extended` because the lane set `PYTHONPATH=src` and omitted the sibling `../libraries/cli-extended/src`. This produced repeated `ModuleNotFoundError` failures and collection errors in `test_backlog_entries.py`, `test_cli_adoption.py`, and `test_cli_help.py`.
- Two independent repository ratchets also failed: `cli_registry.py` had one unclassified broad handler; `cli.py` had one fewer unclassified handler than its recorded legacy budget (actual 5, budget 6).
- R1 reported `EXCLUDED_LINES` for `cli.py`'s OSError fallback and the `__main__` guards in `cli_ctl.py`, `cli_harness.py`, and `daemon_entrypoint.py`. These branches had been explicitly excluded rather than exercised.
- The lane reported peak memory 900 MiB, p90 879 MiB, and 13.9 seconds stalled on memory. The recorded R0 cause is the source import setup and the two ratchets above; R1 also had no usable suite coverage while imports failed.
- The fix adds the sibling library source to both CLI-related Assay lane environments, classifies the boundary translation, lowers the retired CLI-handler budget, and tests the previously excluded branches. No coverage threshold or exclusion policy is relaxed.
- Focused regression command after the fixes: `PYTHONPATH=src:../libraries/cli-extended/src /home/vscode/.venv/bin/python -m pytest -q tests/test_exception_census.py tests/test_cli_adoption.py tests/test_cli_help.py tests/test_cli.py`; exit 0. The updated P112 handoff lint also returned exit 0 with stdout `clean` and empty stderr.

## Rebuilt installed-wheel evidence — 2026-09-28

- Implementation commit: `01e50efacff4f6c6f9cd462fb8529e1a90cfc5b0`.
- Build command: `/home/vscode/.venv/bin/python -m build --wheel --outdir /tmp/nyxloom-p112-wheel-01e50efa`; exit 0 using the isolated PEP 517 environment and the exact pinned build dependencies.
- Artifact: `/tmp/nyxloom-p112-wheel-01e50efa/nyxloom-0.8.1.dev496+g01e50efa-py3-none-any.whl`.
- Installed with `/home/vscode/.venv/bin/python -m pip install --target /tmp/nyxloom-p112-installed-01e50efa --no-deps <wheel>`; from `/tmp`, both `nyxloom` and `cli_extended` imported from that target. Metadata listed exactly `nyxloom`, `nyxloom-harness`, `nyxloomctl`, and `nyxloomd` with their intended callable targets.
- The installed `nyxloom`, `nyxloom-harness`, and `nyxloomctl` each passed `--help` and `--version`; `nyxloom-harness extract --help` and `nyxloomctl doctor --help` passed. An in-process installed-wheel `nyxloom --help` check confirmed `questionary` stayed unloaded and the isolated `NYXLOOM_STATE=/tmp/nyxloom-p112-wheel-state-01e50efa` path was not created.

## Second tester-unified attempt — 2026-09-28

Command: `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-adoption tester-unified` from `nyxloom/`.

- Judged commit: `d0f3d771a7a55430d4f68c1203588c001e1fda24`.
- `tester-unified`: `FAIL/COMMAND_FAILED`, exit 1 after 327 seconds. Imports now succeeded. R0 had ten stale assertions/contracts: four empty delegated groups still expected exit 2 despite approved D-017; three extraction conflict tests expected runtime exit 1 despite parser-owned exit 2 under D-018; the liveness-unit assertion still targeted the retired `nyxloom doctor` entrypoint; the core ownership inventory had stale line counts; and the full dispatch integration test ended in `QUEUED` under xdist load. The latter passed when rerun alone with the gate's `PYTHONPATH`; no product change was based on that single loaded-run failure.
- R1: `FAIL/UNCOVERED_LINES`, 888/940 executable lines and 114/142 branches covered. Remaining uncovered code included backlog field editing and prompt error paths, project-local lint discovery, unknown doctor/status selectors, missing-decision reporting, interactive `--body-from` failure, and a few registry/entrypoint branches. The old backlog editor rescanned delimiters after `split_frontmatter` had already rejected malformed input, so its second missing/open-delimiter checks were unreachable.
- Gate memory reached 779 MiB peak / 691 MiB p90, with 50.1 seconds of memory stalls. No coverage floor or exclusions were relaxed.

## Corrections after the second gate attempt — 2026-09-28

- Updated empty delegated-group contract tests to require generated help and exit 0; extraction conflict tests now require generated usage and exit 2. D-018 now records the distinction between parser syntax refusals (2) and runtime/data/filesystem failures (normally 1).
- Removed duplicate extraction and backlog-title syntax checks from domain handlers; `cli_registry` owns those checks before handler dispatch. Refactored backlog body preservation to use `split_frontmatter`'s parsed body boundary while slicing original bytes, retaining CRLF/body bytes without unreachable delimiter branches.
- Added behavioral coverage for backlog edit validation and byte preservation, wizard prompt errors and environment refusals, local lint with and without project context, unknown doctor/status selectors, missing decision IDs, interactive body-file failure, store-false defaults, JSON/text extraction conflicts, and the normal-import plus executable path of the daemon module. The core ownership inventory now records measured sizes and the new handler/registry boundary.
- A direct service-unit check found `nyxloomd/systemd/nyxloom-liveness.service` still launched removed `nyxloom doctor --liveness`. Updated it to `nyxloomctl doctor --liveness`, its operator guide and template comment, and current CLI references in recovery hints, module documentation, project configuration, and testing guidance. The `docs/USAGE.md` review also replaced the obsolete `nyxloom lint <project>` and `nyxloom gate verify` examples with project-local `nyxloom lint` and `./run-gate.py <lane>`. These necessary additions are recorded in P112 `scope.touch` and the handoff amendment above.
- Focused regression command using the worktree's sibling library source: `PYTHONPATH=src:../libraries/cli-extended/src /home/vscode/.venv/bin/python -m pytest -q -ra tests/test_backlog_entries.py tests/test_cli.py tests/test_cli_adoption.py tests/test_commands.py tests/test_resume_guard.py tests/test_control_auth.py tests/test_free_models.py tests/test_intake_bridge.py tests/test_route_doctor.py tests/test_liveness_units.py tests/test_session_extract_edge_contracts.py`; exit 0.
- An earlier focused probe accidentally used the main-checkout library path instead of the worktree sibling and failed collection on the missing `PromptCancelled` export. It made no code changes and is not evidence about the adoption worktree. The command above and the declared gate use `../libraries/cli-extended/src` from Nyxloom's project directory.
- After removing the last duplicate `--ledger`/`--json` check from the handler and adding a registry-dispatch oracle for conflicting extraction options before source access, this preflight passed: `PYTHONPATH=src:../libraries/cli-extended/src /home/vscode/.venv/bin/python -m pytest -q -ra tests/test_cli_adoption.py tests/test_cli_extract.py tests/test_core_characterization.py::test_inventory_sizes_are_within_the_declared_tolerance tests/test_liveness_units.py`; exit 0. The updated handoff also passed local `nyxloom lint` with stdout `clean` and empty stderr before its service-reference amendment; rerun the handoff lint against the final text.

## Installed-wheel proof for implementation commit 72d60c87 — 2026-09-28

- Build command: `/home/vscode/.venv/bin/python -m build --wheel --outdir /tmp/nyxloom-p112-wheel-72d60c87`; exit 0 using the isolated PEP 517 build environment.
- Artifact: `/tmp/nyxloom-p112-wheel-72d60c87/nyxloom-0.8.1.dev498+g72d60c87-py3-none-any.whl`.
- Installed with `/home/vscode/.venv/bin/python -m pip install --target /tmp/nyxloom-p112-installed-72d60c87 --no-deps <wheel>`; exit 0. From `/tmp`, `nyxloom.__file__` and `cli_extended.__file__` both resolve under that target.
- Metadata lists exactly `nyxloom`, `nyxloom-harness`, `nyxloomctl`, and `nyxloomd` with targets `nyxloom.cli:main`, `nyxloom.cli_harness:main`, `nyxloom.cli_ctl:main`, and `nyxloom.daemon_entrypoint:main`.
- Installed `nyxloom`, `nyxloom-harness`, and `nyxloomctl` each pass `--help` and `--version`; `nyxloom-harness extract --help` and `nyxloomctl doctor --help` pass. An in-process check confirms `questionary` is not imported and `/tmp/nyxloom-p112-wheel-state-72d60c87` is not created by these display paths.

## Third tester-unified attempt — 2026-09-28

Command: `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-adoption tester-unified` from `nyxloom/`.

- Judged commit: `72d60c87ed09a3816dbc0a4d334abf029de7dbb8`.
- `tester-unified`: `FAIL/COMMAND_FAILED`, exit 1 after 232 seconds. The baseline pytest command ran and reported one failure: `tests/test_planner_differential.py::test_legacy_baseline_is_the_committed_branch_point`.
- The failure was caused by the prior command-path sweep changing one literal in `tests/legacy_planner.py`, a frozen historical copy whose oracle proves equivalence to the branch-point blob. Restored the old `nyxloom merge` literal in that fixture; production guidance and current tests continue to use `nyxloomctl`.
- Focused proof after restoration: `PYTHONPATH=src:../libraries/cli-extended/src /home/vscode/.venv/bin/python -m pytest -q -ra tests/test_planner_differential.py::test_legacy_baseline_is_the_committed_branch_point`; exit 0.
- R1 did not run because baseline pytest failed. Peak memory was 772 MiB, p90 747 MiB, and memory-full stalls totaled 32.3 seconds.
- Progress was checked at about 90 seconds: the assay JSONL showed the baseline pytest lane active at 65 seconds. The earlier gate history showed 147- and 327-second runs; this attempt finished after 232 seconds.

## Fourth tester-unified attempt — 2026-09-28

Command: `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-cli-adoption tester-unified` from `nyxloom/`.

- Judged commit: `3029fca1037308fa7816740af0f3ec618c3d4027`.
- `tester-unified`: `FAIL/COMMAND_FAILED`, exit 1 after 243 seconds. Baseline pytest reported one failure: `tests/test_behavioral.py::test_scope_amendment_request_triggers_bounded_re_dispatch_not_blocked`.
- A dispatch raised `ValueError("invalid literal for int() with base 10: ''")` while reading `wrapper.pid`. The intermediate child creates/truncates that file before `Path.write_text()` writes the PID, so under parallel load the parent can observe the empty interval. A resumed attempt can also reuse an old PID marker.
- R1 did not run because baseline pytest failed. Peak memory was 836 MiB, p90 710 MiB, and memory-full stalls totaled 37.3 seconds.
- Updated `wrapper.launch_detached` to remove a stale PID marker before each launch and retry until it can parse a complete PID or reaches its existing 10-second failsafe. Added a deterministic test that presents a stale value, then an empty marker, then the current PID and verifies that only the current PID is returned.
- The wrapper and test additions are covered by P112 `scope.touch` and the handoff amendment above, under the user's authorization for necessary scope expansion.

The wrapper-race fix must be committed, then rebuild and verify the isolated wheel and repeat the full gate against that exact implementation revision.
