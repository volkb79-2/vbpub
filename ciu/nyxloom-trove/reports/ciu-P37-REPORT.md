# ciu-P37 — CIU-71: `docker compose --project-directory <repo_root>` — REPORT

Worktree: `/workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu`
Branch: `fix/ciu-P37-compose-project-directory`
Final commit: `d7830f9e0c1b720d75accec1667f7e0f43bf0ec1` (round 3, post-rebase
onto current `main`; round 2's backlog closeout was `2329d1ba8637b293`,
round 1's `7e4e34f3` was the mechanism — all rebuilt across several commits
below, then rebased onto `main` tip `25d02d94` in round 3, see that
section).
Backlog entry: `KNOWN_ISSUES_TODO_BACKLOG.md` row `CIU-71`, marked **FIXED**
in review round 2 (round 1's header claiming it was already "closed" was
WRONG — caught by independent review, see round 2's blocker 4). CIU-79
filed (not fixed) in the same round.

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

## Scope discipline (round 1)

Touched: `src/ciu/engine.py` (the two real invocation sites); test files
under `tests/` (one new file, six existing files updated for the new
`repo_root` requirement); `docs/SPEC.md`, `README.md`, `docs/CONSUMERS.md`
(per the task's explicit doc-scope list); this LOG and REPORT. Nothing else.
`src/ciu/dev.py` and `src/ciu/cli.py`'s adjacent, out-of-scope findings were
investigated (to confirm they are NOT the same call-site family and
genuinely out of scope) but not edited. No backlog entry was filed or
edited by round 1 in the end — CIU-76/77/78 were all already filed (or
independently filed-and-fixed) by sibling implementers before this
package's final gate run; my own drafted CIU-78 entry was discarded,
uncommitted, once the collision was discovered. **This left CIU-71 itself
OPEN in the backlog while round 1's own header claimed it "closed" — a real
contradiction, caught in review (blocker 4 below), fixed in round 2.**

## Review round 2 — independent adversarial review, ACCEPT-conditional

Independent adversarial review ran a live `docker compose` acceptance probe
and confirmed the CORE MECHANISM (the `--project-directory` insertion
itself) is correct — no `engine.py` behavior change was required. Four
blockers, all docs/backlog, plus two decision asks:

### Blocker 1 — `docs/CONSUMERS.md` §18's worked example doesn't actually work

Live-proven by the reviewer: moving `build.context` moves the `dockerfile:`
lookup with it (Compose resolves `dockerfile` relative to `context`, not
`--project-directory`). §18's original example had the Dockerfile at
`infra/mock-targets/Dockerfile` with `build_context = "."` but never set
`dockerfile:`. I independently re-reproduced this live before writing the
fix:

```
$ docker compose -f ciu.compose.yml -p ciu-p37-probe --project-directory <repo_root> build
...
failed to solve: failed to read dockerfile: open Dockerfile: no such file or directory
```

Fixed by adding `dockerfile: infra/mock-targets/Dockerfile` (repo-root-
relative, since it now resolves against the repo-root context) — confirmed
green with a real build:

```
$ docker compose -f ciu.compose.yml -p ciu-p37-probe --project-directory <repo_root> build
...
 ciu-p37-probe-mock_targets  Built
```

Fix landed: `docs/CONSUMERS.md` §18 and `docs/SPEC.md` S8.1a both now state
that `context` and `dockerfile` move together, with a repo-root-relative
`dockerfile` in the worked example; the migration note now covers both
keys, since a stack that only reverted `build_context` and left
`dockerfile` stack-relative (or unset) breaks the same way one field over.
`src/ciu/dev.py:317-330`'s `_build_dev_image` was cited as ciu's own
existing precedent for the context/dockerfile coupling (`Path(context) /
dockerfile`) — see CIU-79 below, filed from the SAME investigation.

### Blocker 2 — S8.1a's load-bearing justification was factually inverted

`docs/SPEC.md` claimed "every other path CIU resolves ... is already
repo-root-relative." The code says the opposite. Confirmed by reading the
actual source (not taken on the reviewer's word):

- `src/ciu/engine.py` (`create_hostdirs`'s `_resolve_entry`/`_seed`):
  `path = stack_dir / path` (hostdir path), `path = stack_dir /
  f"vol-{service_name}-{purpose}"` (auto path), `src = (stack_dir /
  seed_rel).resolve()` (seed dir) — all STACK-DIR-relative.
- `src/ciu/secrets/materialize.py` (`ASK_FILE`): `file_path = stack_dir /
  file_path`, in both the resolve path and `list_secrets`' description path
  — STACK-DIR-relative.
- `src/ciu/composefile.py` (configfile `schema`/`template`):
  `schema_path = stack_dir / schema_rel`, `template_path = stack_dir /
  template_rel`, and the schema key's own validation error text literally
  says "must be a file path relative to the stack dir" — STACK-DIR-relative.

Fixed in all FOUR locations the reviewer named — `docs/SPEC.md` S8.1a,
`README.md`'s DooD bullet, `docs/CONSUMERS.md` §18, and
`src/ciu/engine.py`'s `execute_docker_compose_with_logs` docstring — with
the true claim: CIU's other relative paths are stack-dir-relative;
`build.context`/`dockerfile` are the deliberate exception, because a
Dockerfile `COPY` of a repo-shared asset needs the repo root. Still a
defensible design — it just wasn't the one written down. The backlog's
CIU-71 row (marked FIXED in this round) also had its own rationale text
corrected for the same reason (see the decision-ask note below).

### Blocker 3 — `--project-directory` silently relocates `.env` lookup

Live-proven by the reviewer, independently re-confirmed by me before
writing the fix:

```
# .env beside the compose file: FOO=from_stack_env
# .env at the repo root:        FOO=from_repo_env
$ docker compose -f ciu.compose.yml config | grep -A1 environment
    environment:
      FOO: from_stack_env          # WITHOUT --project-directory
$ docker compose -f ciu.compose.yml --project-directory <repo_root> config | grep -A1 environment
    environment:
      FOO: from_repo_env           # WITH --project-directory -- silently shadowed
```

Also confirmed the mitigating fact I documented: a value already present in
the subprocess's own environment (what CIU passes as `env=compose_env`,
S8.2) outranks EITHER `.env` file for compose's variable substitution:

```
$ FOO=from_shell_env docker compose -f ciu.compose.yml --project-directory <repo_root> config | grep -A1 environment
    environment:
      FOO: from_shell_env
```

**Decision (asked by the reviewer, not left open):** accept the relocation;
do NOT add a second `--env-file <stack_dir>/.env` flag to pin the old
lookup. Reasoning: CIU never relies on bare `.env` itself (S8.2's
`env=compose_env` is always explicit and already outranks any `.env` file
for interpolation, confirmed above); a stack-local `.env` is a consumer
pattern CIU does not itself support or encourage (it has its own secrets/
configfile mechanisms for exactly this); and a second existence-conditional
flag would reintroduce the implicit-behavior-by-file-presence shape CIU's
own secrets/configfile design otherwise avoids. Documented (not silently
dropped) in `docs/SPEC.md` S8.1a and `docs/CONSUMERS.md` §18's migration
note, including the "check for a stack-local `.env`" instruction for
migrating consumers.

### Blocker 4 — backlog untouched, REPORT self-contradiction

Confirmed and fixed: `KNOWN_ISSUES_TODO_BACKLOG.md`'s CIU-71 row is now
marked `FIXED — ciu-P37: ...`, matching ciu-P36's `4b471e63`/`aa6cf1fd`
convention (row rewritten with the fix summary + review-round corrections;
the "Last updated" header block gained a new top paragraph, demoting the
prior one to "Previously, ..."). This REPORT's own header (above) no longer
claims a premature "closed" — it now points here.

### Decision ask 1 — `src/ciu/dev.py`'s identical defect

Filed as **CIU-79** (confirmed free: `grep CIU-79
KNOWN_ISSUES_TODO_BACKLOG.md` before writing, on top of a re-verified
merge-base with `main` — see "Rebase check" below) — table row + this
REPORT's own round-1 analysis as the body. Not fixed: different command
(`docker build`, no `--project-directory` equivalent), different fix shape
(resolve `context` to an absolute repo-root-relative path before building
the argv), genuinely a separate package.

### Decision ask 2 — CIU-71's own recorded rationale for choosing fix (a)

Confirmed in the LOG: fix (a) — the `--project-directory` flag — was
prescribed by the carve/task itself, not a unilateral choice between (a)
and (b) on my part, so blocker 2's correction (CIU's other paths are
stack-dir-relative, not repo-root-relative as the original rationale
claimed) does not change WHICH fix was right, only WHY. (a) remains
correct on independent grounds: a Dockerfile `COPY` of a repo-shared asset
genuinely needs the repo root, regardless of what convention CIU's other
paths follow. Confirmed with eyes open, not left as a dangling
inconsistency.

### Non-blocking items addressed

- `test_ciu_compose_project_directory.py`'s docstring pointed at this
  REPORT for `docker compose config` probe output that didn't exist yet —
  now it does (blockers 1 and 3's probe transcripts above are the actual
  evidence the docstring's pointer resolves to).
- `engine.py`'s `--shipped` dry-run message now includes
  `--project-directory` (the pre-existing `-p` omission there is
  unrelated/out of scope, per the review's own note — left alone).

### Rebase check (verified myself, not trusted from the review summary)

```
$ git merge-base HEAD main
aa6cf1fd6217ba2035cb2c7cf5adea488823e3b8         # unchanged from round 1
$ git diff --name-only aa6cf1fd 384993b6 --      # ciu-P36's merge, on top of the same merge-base
ciu/docs/CONFIG.md
ciu/KNOWN_ISSUES_TODO_BACKLOG.md                 # the ONE overlapping file
ciu/nyxloom-trove/reports/ciu-P36-LOG.md
ciu/nyxloom-trove/reports/ciu-P36-REPORT.md
ciu/src/ciu/worktree.py
ciu/tests/tests/test_ciu_worktree_lease.py
ciu/tests/tests/test_ciu_worktree_reap.py
```

No overlap with anything round 1 or round 2 touched except
`KNOWN_ISSUES_TODO_BACKLOG.md` (ciu-P36 edited CIU-69/76's rows and the
header; this package edits CIU-71/79's rows and the header) — different
table rows, low collision risk, and the coordinator's own guidance said a
rebase was not needed. Did not rebase; a downstream merge reconciles the
backlog file's independent row edits.

## Real gate — round 2 (post-review-fix, final)

Ran `./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/
ciu-P37-compose-project-directory` (from inside `ciu/`) against round 2's
tip, `2329d1ba8637b293379b4584c0739055b9876786`. Exit code and verdict both
read in a separate step from the invocation (never piped).

### Round-2 gate verdict — run-gate.py stdout (verbatim)

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 23 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-ciu-21947-1788139027 --cgroup-parent dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice -v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'set -euo pipefail && export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig && git config --global --replace-all safe.directory '"'"'*'"'"' && cd /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu && (cd /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu/tools/assay && sha256sum -c assay-2.3.0.pyz.sha256) && { reported=$(/opt/tester-venv/bin/python tools/assay/assay-2.3.0.pyz --version) || { echo "run-gate: pin '"'"'assay'"'"': version probe failed: /opt/tester-venv/bin/python tools/assay/assay-2.3.0.pyz --version" >&2; exit 2; }; hit=0; for tok in $reported; do tok=${tok#"${tok%%[![:punct:]]*}"}; tok=${tok%"${tok##*[![:punct:]]}"}; case "$tok" in v[0-9]*) tok=${tok#v} ;; esac; if [ "$tok" = 2.3.0 ]; then hit=1; fi; done; if [ "$hit" != 1 ]; then echo "run-gate: pin '"'"'assay'"'"' version mismatch: declared 2.3.0, artifact reports: $reported — fix pins.assay.version or republish the artifact" >&2; exit 2; fi; } && mkdir -p .assay && /opt/tester-venv/bin/python tools/assay/assay-2.3.0.pyz run ciu --file assay.toml --verdict-json .assay/verdict-ciu.json'
assay-2.3.0.pyz: OK
ciu: FAIL/COMMAND_FAILED (exit 1)
  commit: 2329d1ba8637b293379b4584c0739055b9876786
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: lane 'ciu' failed with exit 1; full container logs preserved at /tmp/run-gate/run-gate-vbpub-ciu-21947-1788139027.log
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 1
```

`.assay/verdict-ciu.json` (read separately via `json.load`):

```
"commit": "2329d1ba8637b293379b4584c0739055b9876786"
"outcome": "FAIL"
"reason_code": "COMMAND_FAILED"
"exit_code": 1
```
- **R0: FAIL / COMMAND_FAILED.** ONE test failed:
  `test_re_expiring_after_an_extend_becomes_lease_expired_again` — CIU-76,
  same as round 1. This branch's merge-base with `main` is still `aa6cf1fd`
  (unchanged — the coordinator's rebase-not-needed guidance was correct,
  verified above), and CIU-76's actual fix landed on `main` only via
  ciu-P36's LATER merge (`384993b6`), which this branch does not include
  (deliberately, per the "no rebase needed" guidance — the two branches'
  only overlapping file, `KNOWN_ISSUES_TODO_BACKLOG.md`, reconciles at
  merge time, not before). Still unrelated to CIU-71/CIU-79 or anything
  round 2 touched.
- **R1: PASS.** `pct: 100.0`, full `src/ciu` line+branch coverage,
  unchanged from round 1 (round 2 touched no `src/ciu` files with logic —
  only `engine.py`'s dry-run print string, and docs/backlog).

Final summary line: `1 failed, 3265 passed in 32.78s` — identical failure
count and identical single failure to round 1's run, confirming round 2's
doc/backlog/message-string changes introduced no regressions.

## Review round 3 — second independent adversarial review, ACCEPT-conditional

Two more small items, both docs/rebase hygiene, no code change.

**Item 1 — `.env` framing in `docs/CONSUMERS.md` §18 was itself backwards.**
Round 2's fix said a stack-local `.env` is "now SHADOWED by a repo-root
`.env`, if one exists" and the migration bullet said "confirm no repo-root
`.env` now shadows it" — both wrong. `docker compose` reads `.env` ONLY
from `--project-directory` (S8.1a); it never also checks the compose
file's own directory. So a stack-local `.env` is dropped UNCONDITIONALLY —
whether or not a repo-root `.env` exists — not "shadowed" only when one
happens to exist. Live-reproduced before writing the fix, independent of
the reviewer's own probe:

```
$ cat ciu.compose.yml   # stack dir, build.context/dockerfile as in the S18 example
$ echo 'FOO=from_stack_env' > .env                 # stack-local .env, no repo-root .env at all
$ docker compose -p t1 --project-directory /repo/root -f ciu.compose.yml config | grep -A2 FOO
      FOO: (unset — not present in the rendered config at all)
```
Removing `--project-directory` and re-running the same `config` against
the SAME stack-local `.env` resolves `FOO: from_stack_env` — confirming
the drop is caused purely by `--project-directory`'s presence, not by any
repo-root `.env` competing with it. Fixed the paragraph ("stops being read
at all... dropped whether or not a repo-root `.env` exists") and the
migration bullet ("move its values into CIU's own config/secret
mechanisms or into a repo-root `.env`") in `docs/CONSUMERS.md` §18.
Independently re-checked `docs/SPEC.md` S8.1a's own `.env` paragraph
(1225-1240): it says relocation "changes which `.env` a stack picks up,
the same way it changes `build.context`" and never claims the stack-local
file is conditionally shadowed — confirmed accurate, left untouched, per
the reviewer's own assessment.

**Item 2 — rebase onto current `main`.** This branch had deliberately not
rebased past merge-base `aa6cf1fd` in rounds 1-2 (per earlier "not needed"
guidance), leaving CIU-76 — fixed upstream by ciu-P36's `384993b6` merge —
still failing here as a known, unrelated, pre-existing gap (R0 FAIL,
R1 100% PASS in both prior gate runs). `main` has since advanced to
`25d02d94` (one further commit, `docs(assay): Wave B controller log --
checkpoint 2, endorse required-fields fork`, no `ciu/` files touched —
verified via `git log --name-only 384993b6..main -- ciu/` returning
nothing before rebasing). Ran `git rebase main`; one conflict, in
`KNOWN_ISSUES_TODO_BACKLOG.md`'s "Last updated" header paragraph (both
branches had rewritten it). Resolved by keeping this branch's CIU-71/CIU-79
paragraph as the (chronologically later) "Last updated" entry, moving
ciu-P36's CIU-69/CIU-76 paragraph down to a new "Previously, 2026-08-31"
entry ahead of the pre-existing CIU-78 and CIU-76/77-filed entries, and
adding one clause noting the round-3 `.env`-framing correction. No table
row conflicted; `grep -n "^<<<<<<<\|^=======\|^>>>>>>>"` across the repo
after `--continue` found none. `git status --short` clean; `git merge-base
HEAD main` now equals `main`'s own tip (`25d02d9446146b107e60e238506a779618d5fb30`)
exactly.

### Real gate — round 3 (post-rebase, final)

Ran `./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory`
(from inside `ciu/`) against the post-rebase tip. Exit code and verdict
both read in a separate step from the invocation.

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 23 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-ciu-209786-1788140377 --cgroup-parent dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice -v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'set -euo pipefail && export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig && git config --global --replace-all safe.directory '"'"'*'"'"' && cd /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu && (cd /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu/tools/assay && sha256sum -c assay-2.3.0.pyz.sha256) && { reported=$(/opt/tester-venv/bin/python tools/assay/assay-2.3.0.pyz --version) || { echo "run-gate: pin '"'"'assay'"'"': version probe failed: /opt/tester-venv/bin/python tools/assay/assay-2.3.0.pyz --version" >&2; exit 2; }; hit=0; for tok in $reported; do tok=${tok#"${tok%%[![:punct:]]*}"}; tok=${tok%"${tok##*[![:punct:]]}"}; case "$tok" in v[0-9]*) tok=${tok#v} ;; esac; if [ "$tok" = 2.3.0 ]; then hit=1; fi; done; if [ "$hit" != 1 ]; then echo "run-gate: pin '"'"'assay'"'"' version mismatch: declared 2.3.0, artifact reports: $reported — fix pins.assay.version or republish the artifact" >&2; exit 2; fi; } && mkdir -p .assay && /opt/tester-venv/bin/python tools/assay/assay-2.3.0.pyz run ciu --file assay.toml --verdict-json .assay/verdict-ciu.json'
assay-2.3.0.pyz: OK
ciu: PASS (exit 0)
  commit: d7830f9e0c1b720d75accec1667f7e0f43bf0ec1
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P37-compose-project-directory/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

`.assay/verdict-ciu.json` (read separately via `json.load`, full document):

```json
{
    "commit": "d7830f9e0c1b720d75accec1667f7e0f43bf0ec1",
    "outcome": "PASS",
    "exit_code": 0,
    "lane": "ciu",
    "scope": "S1",
    "declared_rigor": ["R0", "R1"],
    "claims": [
        {"rigor": "R0", "status": "PASS", "source": "computed", "verified_by_assay": true},
        {"rigor": "R1", "status": "PASS", "source": "computed", "verified_by_assay": true,
         "coverage": {"pct": 100.0, "executable": 22, "covered": 22,
                      "branches_total": 2, "branches_covered": 2,
                      "missing_lines": {}, "missing_branch_lines": {},
                      "branch_capability": "reported"}}
    ],
    "judgment": {
        "r1": {"mode": "changed_lines", "fail_under": 100.0, "require_branch": true,
               "coverage_format": "coverage-py-json"},
        "resolved": {"base": "25d02d9446146b107e60e238506a779618d5fb30",
                     "language": "python", "source_roots": ["src"]}
    },
    "env_declared": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "src"},
    "env_effective": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "src", "...": "..."}
}
```

- **R0: PASS.** `verified_by_assay: true` — the first fully green R0 across
  all three rounds. CIU-76 (the sole failure in rounds 1-2) is gone now
  that the branch's rebase carries ciu-P36's upstream fix.
- **R1: PASS.** `pct: 100.0`, 22/22 executable lines, 2/2 branches,
  `verified_by_assay: true`. `judgment.resolved.base` is
  `25d02d9446146b107e60e238506a779618d5fb30` — confirming the changed-lines
  coverage judgment recomputed its base against the NEW `main` tip
  post-rebase, not a stale pre-rebase one.
- **`outcome: PASS`, `exit_code: 0`** overall — a clean gate, no caveats,
  no known-unrelated failures to explain away.

## Result

Not blocked, and now fully clean. Round 1 shipped the mechanism
(independently confirmed correct by review's own live acceptance probe, no
code defect found). Round 2 fixed all four documentation/backlog blockers
plus both decision asks, every corrected claim independently re-verified
against a real `docker compose config`/`build` before being written down —
not taken on the review's word. Round 3 fixed a backwards `.env` framing
inside round 2's OWN fix (also independently live-reproduced before
writing) and rebased onto current `main`, picking up CIU-76's upstream fix.
Final real gate verdict (round 3, above): **`outcome: PASS`, exit 0** — R0
PASS, R1 PASS at 100% line+branch coverage of `src/ciu`, both
`verified_by_assay: true`. No open failures, known or otherwise. Final
commit hash (real, read via `git log -1 --format=%H`, not predicted):
`d7830f9e0c1b720d75accec1667f7e0f43bf0ec1`.
