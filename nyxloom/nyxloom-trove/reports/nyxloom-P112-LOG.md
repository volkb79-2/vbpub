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
`input_revision` refreeze are being committed before implementation resumes.

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
