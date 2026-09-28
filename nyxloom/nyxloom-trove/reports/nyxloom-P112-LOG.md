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
