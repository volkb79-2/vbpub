# ciu-P37 — CIU-71: `docker compose --project-directory <repo_root>` — REPORT

Worktree: `/workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu`
Branch: `fix/ciu-P37-compose-project-directory`
Final commit: `7e4e34f3dc86b39ce501a67494812ef3b0531a1d`
Backlog entry closed: `KNOWN_ISSUES_TODO_BACKLOG.md` `## CIU-71`.

## What was done

**The bug.** A stack's relative `build.context` (e.g. `build_context = "."`
in `ciu.defaults.toml.j2`, rendered into `ciu.compose.yml.j2`'s `build:
context:`) resolved against the compose file's own directory, not the repo
root, because `ciu` never invoked `docker compose` with
`--project-directory <repo-root>`. Every other path CIU resolves (bind
mounts, hostdirs, secret/configfile paths) is already repo-root-relative, so
a stack author writing `build_context = "."` and a Dockerfile that `COPY`s a
repo-root-relative path (`COPY tests/fixtures/mock_data ...`) hit a
build-time path-not-found error that gave no hint the actual cause was a
missing CIU flag. Live repro (backlog): `ciu up --dir infra/mock-targets`
(dstdns-P147b) -> `resolve : lstat .../infra/mock-targets/tests: no such
file or directory`.

**The fix.** An exhaustive sweep for `["docker", "compose"` (and other
compose-invoking helpers) across `src/ciu/*.py` confirmed the backlog's own
finding: exactly two real `docker compose` invocation-argv-constructing
sites exist, both in `src/ciu/engine.py`:

1. `execute_docker_compose_with_logs(file_args, *, cwd, env, project)` —
   the ONE function both `main_execution` (native `up`, S8.1) and
   `run_shipped` (`--shipped` passthrough, S8.6) call. `cwd` is the stack
   dir; `repo_root` is a separate, already-resolved variable in scope at
   both call sites (from `REPO_ROOT`/`--define-root`, never transformed
   after resolution). `repo_root: Path` is now a REQUIRED keyword-only
   parameter, and the constructed argv gains `--project-directory
   <repo_root resolved>` right after `-p <project>`.
2. `reset_service`'s `docker compose ... down -v --remove-orphans`
   construction. `repo_root: Optional[Path] = None` was already a
   parameter (used only in the identity-project fallback branch). A NEW
   unconditional guard now raises `[CIU-71] repo_root is required...` if
   `repo_root` is `None` regardless of which branch resolved the project
   name — `--project-directory` is needed either way. Kept `Optional` at
   the type level (so the pre-existing project_name/label_prefix
   validation-ordering tests stayed correct) but effectively required at
   runtime.

No other compose-invoking site exists — `deploy.py` delegates ALL per-stack
compose lifecycle to these three `engine.py` functions (`main_execution`,
`run_shipped`, `reset_service`); its own `procutil.docker(...)` calls are
`ps`/`network`/`volume`/`rm` inspection/cleanup, never `compose up`/`down`.

**Two adjacent findings, deliberately NOT fixed (out of CIU-71's stated
scope — "stay inside `src/ciu/`" / "the compose-file-invoking call sites"):**

- `src/ciu/dev.py:_build_dev_image` (`ciu dev`) runs a plain `docker build`
  with a stack-relative `context` — the SAME defect class (relative path
  resolves against the wrong base), but a DIFFERENT command (`docker
  build` has no `--project-directory` equivalent at all — the fix shape
  would be "resolve `context` to an absolute repo-root-relative path
  before building the argv", not "add a flag"). Not filed as a new backlog
  entry — recorded here for the next triager rather than expanding this
  package's scope on my own judgment.
- `src/ciu/cli.py:_bake` (`ciu bake`, `docker buildx bake`) has no
  compose-file / cwd-anchored path control either, but it is explicitly
  documented in its own docstring as "byte-identical to the pre-existing
  v1 behaviour" and is not a `docker compose` invocation at all (bake
  reads `docker-bake.hcl` by convention, never a compose file's
  `build.context`) — a different mechanism, out of CIU-71's family.

**Tests.** New `tests/tests/test_ciu_compose_project_directory.py`
reproduces the live failure mode with a REAL directory tree shaped exactly
like the dstdns-P147b repro (a repo root carrying `tests/fixtures/
mock_data`, a stack whose Dockerfile `COPY`s that repo-root-relative path,
and an already-"rendered" `ciu.compose.yml` with `build.context = "."`),
calls the REAL `execute_docker_compose_with_logs` (only `subprocess.Popen`
is stubbed, to CAPTURE rather than run the argv — the ciu test suite is
deliberately Docker-free, matching `tester-unified`'s "no Docker socket"
gate boundary, confirmed in `tester-unified/Dockerfile`), applies Compose's
own documented `--project-directory` path-resolution rule to the captured
argv, and asserts the Dockerfile's COPY source is actually reachable from
the resolved build context — exactly the fact whose absence produced the
live repro's `lstat` error. A second test covers the `--shipped` path the
same way. Extended `test_ciu_compose_project.py` (`TestComposeUpProjectArg`,
`TestResetDownProjectScoping`) and `test_ciu_reset_service.py` with
unit-level argv-position/value assertions and a guard-raise test. Updated
every pre-existing test that called `execute_docker_compose_with_logs` /
`reset_service` without `repo_root` (7 call sites across 6 files) so the
signature change didn't silently defeat their own assertions.

**Docs** (AGENTS.md "user-facing docs are part of the change"):
`docs/SPEC.md` gains **S8.1a** (new normative subsection under `## S8 —
Compose execution`, cross-referenced from S8.6 and S8.7's opening
sentence); `README.md`'s "DooD / path correctness for free" bullet now
covers `build.context`; `docs/CONSUMERS.md` gains **§18** with a worked
repo-root-relative `build_context` example and a migration note for a
consumer who added a stack-relative workaround for the pre-fix bug.
`docs/CONFIG.md` was checked and correctly left untouched — `build_context`
is arbitrary stack-author TOML, never a CIU-reserved config key.
`docs/FEATURES.md` has an analogous "DooD path correctness" table row not
touched, per the task's explicit doc-scope list (SPEC/README/CONSUMERS
only) — noted rather than expanded on my own judgment.

## Controlled-wrong-implementation verification (done manually, not
committed)

Reverted `execute_docker_compose_with_logs`'s `--project-directory` insertion
(restoring the pre-fix `cmd = ["docker", "compose", "-p", project,
*file_args, "up", "-d"]`) and re-ran the new reproduction tests:

```
$ python3 -m pytest tests/tests/test_ciu_compose_project_directory.py -q
...
FAILED tests/tests/test_ciu_compose_project_directory.py::test_relative_build_context_resolves_against_repo_root_not_stack_dir
FAILED tests/tests/test_ciu_compose_project_directory.py::test_shipped_path_also_resolves_against_repo_root
2 failed, 1 warning in 0.29s
```

`test_relative_build_context_resolves_against_repo_root_not_stack_dir`
failed with:
```
AssertionError: build.context resolved to <tmp>/repo/infra/mock-targets, not the repo root <tmp>/repo -- CIU-71 regressed
```
(the resolved context fell back to the stack dir — exactly the pre-fix
defect; had the test proceeded past that assertion it would have hit the
"COPY source not reachable... lstat" message too, since the stack dir does
not contain `tests/fixtures/mock_data`). Restored the fix and re-ran: both
tests green again. Did the same for `reset_service`'s `down_cmd`
(temporarily removed its new `--project-directory` insertion and the
guarding `raise`): `TestResetDownProjectScoping::
test_down_project_directory_scoped_to_repo_root_when_pair_present` failed
with `AssertionError: assert ['-f', 'ciu.compose.yml'] == ['--project-d...
irectory_sc0']` (the flag was simply absent). Restored the fix; confirmed
green (`diff -q` against the pre-revert file showed the restore was exact).

## Real gate — three refused/failed attempts, one authoritative pass-through

Ran `./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/
ciu-P37-compose-project-directory` (from inside `ciu/`) FOUR times total, in
order, verdict read in a separate step from invocation each time (never
piped):

1. **Refused, dirty tree** (before the implementation was committed) —
   expected, not a gate result.
2. **Refused, stale assay pin**: `run-gate.toml [lanes.ciu.pins.assay]`
   declared `2.2.0` while the vendored artifact was already
   `assay-2.3.0.pyz` (pre-existing drift, unrelated to CIU-71 — CIU-77).
   Resolved by rebasing onto `main`, which had already picked up a sibling
   implementer's `b8102bc2` pin-string fix.
3. **Ran, FAIL/COMMAND_FAILED**: 3 failing tests out of 3266 collected — the
   already-known CIU-76 (`test_re_expiring_...`) plus TWO
   `test_ciu_deploy_actions.py` tests hardcoding `sys.dont_write_bytecode is
   False`, wrong specifically inside `tester-unified`'s
   `PYTHONDONTWRITEBYTECODE=1`-declared environment (`assay.toml [lanes.ciu]
   env`). I independently root-caused this (confirmed via
   `PYTHONDONTWRITEBYTECODE=1 pytest ...` locally, reproducing with and
   without CIU-71's own diff present) and drafted a CIU-78 backlog entry —
   then, before committing it, found `main` had already gained a sibling
   implementer's `aa6cf1fd fix(ciu): CIU-78 -- ...` (whose own message says
   "Independently found and filed by both ciu-P36 and ciu-P38" — three of
   us hit it independently). Discarded my own drafted entry rather than
   file a duplicate, and rebased onto `main` again to pick up the real fix.
4. **Final, authoritative run** (below), against `7e4e34f3` — this
   package's actual, final content, on top of every upstream fix landed
   during gating.

### Real gate verdict — run-gate.py stdout (verbatim)

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 23 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-ciu-3884741-1788137381 --cgroup-parent dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice -v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'set -euo pipefail && export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig && git config --global --replace-all safe.directory '"'"'*'"'"' && cd /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu && (cd /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu/tools/assay && sha256sum -c assay-2.3.0.pyz.sha256) && { reported=$(/opt/tester-venv/bin/python tools/assay/assay-2.3.0.pyz --version) || { echo "run-gate: pin '"'"'assay'"'"': version probe failed: /opt/tester-venv/bin/python tools/assay/assay-2.3.0.pyz --version" >&2; exit 2; }; hit=0; for tok in $reported; do tok=${tok#"${tok%%[![:punct:]]*}"}; tok=${tok%"${tok##*[![:punct:]]}"}; case "$tok" in v[0-9]*) tok=${tok#v} ;; esac; if [ "$tok" = 2.3.0 ]; then hit=1; fi; done; if [ "$hit" != 1 ]; then echo "run-gate: pin '"'"'assay'"'"' version mismatch: declared 2.3.0, artifact reports: $reported — fix pins.assay.version or republish the artifact" >&2; exit 2; fi; } && mkdir -p .assay && /opt/tester-venv/bin/python tools/assay/assay-2.3.0.pyz run ciu --file assay.toml --verdict-json .assay/verdict-ciu.json'
assay-2.3.0.pyz: OK
ciu: FAIL/COMMAND_FAILED (exit 1)
  commit: 7e4e34f3dc86b39ce501a67494812ef3b0531a1d
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: lane 'ciu' failed with exit 1; full container logs preserved at /tmp/run-gate/run-gate-vbpub-ciu-3884741-1788137381.log
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 1
```

Exit code read separately (never through the pipe above): `run-gate.py`
itself exited **1**. `.assay/verdict-ciu.json`'s own fields (read separately
again, via `json.load` — not grepped out of a text stream):

```
"commit": "7e4e34f3dc86b39ce501a67494812ef3b0531a1d"
"lane": "ciu"
"outcome": "FAIL"
"reason_code": "COMMAND_FAILED"
"exit_code": 1
"declared_rigor": ["R0", "R1"]
```

Per-claim breakdown:
- **R0 (the full `run-ciu-tests.py` suite must exit 0): FAIL /
  COMMAND_FAILED.** `pytest` itself exited 1 because ONE test failed:
  `tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again`
  — this is CIU-76 (already filed 2026-08-31, "OPEN", by a sibling
  implementer gating an unrelated fix; independently reproduced and
  root-caused by me too, see LOG). It is a real-wall-clock/test-fixture
  mismatch in `worktree.apply_lease` (no `now:` override), permanently
  broken for any date after ~2026-08-26, structurally unrelated to compose
  invocation, `engine.py`, or anything this package touches.
- **R1 (100% line + branch coverage of `src/ciu`): PASS.** `pct: 100.0`,
  `covered`/`branches_covered` == `executable`/`branches_total`,
  `files_missing_coverage: []`. The full per-file table (from the verdict's
  own `result_stdout_tail`) shows `src/ciu/engine.py 952 0 322 0 100%` —
  every line and branch of the file CIU-71 actually changed is covered,
  same as every other file in the tree.

Final summary line from the same run: `1 failed, 3265 passed, 6 warnings in
41.38s`. The ONE failure is CIU-76; every other test — including all new and
updated tests this package added — passed inside the real, isolated
`tester-unified` container, not just locally.

**Honest characterization of the overall verdict:** the gate's OVERALL
outcome is `FAIL`, and I am not characterizing it as "passed." The failure
is attributable, with direct evidence (reproduced independently, root-caused
in `worktree.py`, and matching an already-filed backlog entry by exact test
name and exact assertion), to CIU-76 — a pre-existing defect this package
neither introduced nor is scoped to fix. R1, the coverage judgment that
actually measures this package's own change, is a clean 100% PASS. Two
OTHER pre-existing gate-environment defects (the stale assay pin / CIU-77,
and the `PYTHONDONTWRITEBYTECODE` test bug / CIU-78) were hit and resolved
along the way by rebasing onto fixes that landed on `main` from sibling
implementers during this package's own gating window — documented in full
in the LOG, including the CIU-78 filing collision (my own independent
finding, discarded in favor of the already-landed fix once discovered).

## Scope discipline

Touched: `src/ciu/engine.py` (the two real invocation sites); test files
under `tests/` (one new file, six existing files updated for the new
`repo_root` requirement); `docs/SPEC.md`, `README.md`, `docs/CONSUMERS.md`
(per the task's explicit doc-scope list); this LOG and REPORT. Nothing else.
`src/ciu/dev.py` and `src/ciu/cli.py`'s adjacent, out-of-scope findings were
investigated (to confirm they are NOT the same call-site family and
genuinely out of scope) but not edited. No backlog entry was filed or
edited by this package in the end — CIU-76/77/78 were all already filed (or
independently filed-and-fixed) by sibling implementers before this
package's final gate run; my own drafted CIU-78 entry was discarded,
uncommitted, once the collision was discovered.

## Result

Not blocked. Implementation shipped, gated for real, and independently
re-verified via a controlled-wrong-implementation revert on both fix sites.
Commit hash (real, read via `git log -1 --format=%H`, not predicted):
`7e4e34f3dc86b39ce501a67494812ef3b0531a1d`.
