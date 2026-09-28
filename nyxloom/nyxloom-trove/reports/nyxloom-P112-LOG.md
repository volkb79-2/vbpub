# nyxloom-P112 execution log

## Status: BLOCKED before implementation

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

The handoff owns `nyxloom/pyproject.toml`, `nyxloom/nyxloomd/supervise.sh`, and
`nyxloom/nyxloomd/docker-compose.yml`, but omits
`nyxloom/nyxloomd/Dockerfile`, `nyxloom/docker-bake.hcl`, and
`nyxloom/nyxloomd/ciu.compose.yml.j2`. `nyxloom/tests/test_daemon.py` explicitly
checks both compose files, so the Ciu template cannot remain on the old
healthcheck path while the pre-rendered compose file changes. The project
`STANDING.md` requires exact `scope.touch` ownership and says implementation
agents must not edit a handoff; therefore I stopped instead of extending scope
or leaving the shipped-image path inconsistent.

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
- No Nyxloom implementation files were changed and the Nyxloom gate was not
  started because this scope blocker is deterministically outside the
  implementation contract.

## Required amendment

A contract owner must add the Docker image/build and Ciu compose inputs needed
for the wheel and installed-service oracles to P112 `scope.touch` (at least
`nyxloom/nyxloomd/Dockerfile`, `nyxloom/docker-bake.hcl`, and
`nyxloom/nyxloomd/ciu.compose.yml.j2`), plus any exact adjacent test/build file
that review shows is necessary. Implementation remains stopped until that
amendment is present.
