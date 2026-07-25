# Consumer-project gate adoption assessment

**Assessment date:** 2026-07-25  
**Projects:** dstdns, netcup-api-filter (naf), topos  
**Method:** static inspection only. No gate, container build, test suite, canary, or git-writing command was run.

## Executive assessment

All three projects declare gates, and all three have useful test content. None is fully adopted against the nyxloom standard.

The most urgent cross-project defect is gate selection. `nyxloom gate verify` prefers a `post-merge` gate and otherwise chooses the lexically lowest `implementation` gate (`nyxloom/src/nyxloom/gate_runner.py:30-46`). That means:

- dstdns selects `gate-probe`, whose payload is literal `true`, instead of `test-runner` (`/workspaces/dstdns/nyxloom-trove/nyxloom.toml:56-66`). This is a structurally certain **LAUNDERS** result if verified: a canary cannot make `true` fail.
- topos selects `py-compile`, not `topos-suite` (`topos/nyxloom-trove/nyxloom.toml:63-75`). The canary is a syntactically valid top-level `raise`, so compilation alone does not reject it. This is also a structurally expected **LAUNDERS** result.
- naf selects `e2e`, not `unit`, because `e2e` sorts first and all three gates are `implementation` phase (`/workspaces/netcup-api-filter/nyxloom-trove/nyxloom.toml:65-88`). That is the wrong verification gate: it depends on an already deployed app/network and is not the hermetic source-under-test matrix.

The immediate P0 action is therefore to keep helper gates but move them out of `implementation` phase, leaving exactly one authoritative implementation gate per project for `gate verify` to select. The full adoption order should then be:

1. **netcup-api-filter** — compact single Python source root, existing pytest-cov installation, security-boundary product, and a valuable interpreter matrix; the fastest high-value adoption.
2. **topos** — single Python source root and a mature `tester-unified` suite; xdist/parity work is the main uncertainty.
3. **dstdns** — retain its excellent runtime-faithful runner, but adopt last because its multi-root source tree, deliberately narrowed unit lane, full-collection pollution, and currently uncollectable `libs/common/tests` make the floor materially harder to define honestly.

## Assessment standard

The hard nyxloom contract is intentionally thin:

- At least one declared `[gates.*]` must accept `{worktree}`, run at a commit, and propagate every real failure as a non-zero exit with nothing masking it (`nyxloom/reference/STANDARD.md:136-149`).
- A meaningful gate should use a runtime-faithful environment separate from the cockpit, fail closed, and preferably enforce an affordable completeness floor in parallel (`nyxloom/reference/STANDARD.md:151-169`).
- A green run alone is insufficient. Tests must exercise real components, regression tests must be shown to fail before and pass after, canaries must land in the tested subtree, parallel and serial coverage need per-file parity, incidental child-process coverage must not be preserved as a substitute for deterministic tests, and run/verdict handling must stay separate (`nyxloom/reference/STANDARD.md:179-218`).

The adoption checklist adds:

- a declared gate with phase, `{worktree}`, and timeout;
- a separate runtime-faithful test environment;
- fail-closed propagation;
- changed-line coverage;
- parallel execution with serial/parallel coverage parity;
- known-bad canary rejection; and
- truthful `asserts=[...]` declarations (`nyxloom/docs/plan-gate-adoption.md:25-56`).

The reference implementation uses `pytest -n auto` with pytest-cov, then invokes a changed-line evaluator against coverage JSON (`nyxloom/nyxloom-trove/nyxloom.toml:52-72`). This detail is load-bearing: `coverage run -m pytest` observes only the xdist parent, while pytest-cov instruments and combines worker data (`nyxloom/nyxloom-trove/LESSONS.md:69-98`). The evaluator diffs merge-base(`main`, `HEAD`) on a feature branch or first-parent-to-merge on a merge commit, intersects changed executable lines with coverage JSON, and fails at a default 100% changed-line floor (`nyxloom/src/nyxloom/coverage_gate.py:19-38,126-189,207-282`).

The canary is not part of every ordinary gate invocation. `nyxloom gate verify` first requires known-good HEAD to pass, then creates up to four disposable commits carrying one subtree-scoped import-break and requires at least one to be rejected. Its verdicts are `TRUSTWORTHY`, `LAUNDERS`, `BROKEN`, `NO_GATE`, and `INCONCLUSIVE` (`nyxloom/src/nyxloom/gate_canary.py:1-51,143-166,196-206,297-321`; `nyxloom/src/nyxloom/cli.py:876-979`). GA4 periodic verification is still a planned automation package, so each project needs a documented manual cadence until it ships (`nyxloom/docs/plan-gate-adoption.md:106-119`).

## Common implementation rules

Each project should adopt a small project-owned changed-line evaluator based on `nyxloom.coverage_gate`, rather than importing nyxloom as a runtime dependency. Copy the algorithm and its tests, then adapt only source-prefix handling:

- preserve feature-branch merge-base and merge-commit first-parent behavior;
- consume pytest-cov JSON rather than reading `.coverage` directly;
- treat a changed Python file absent from the report as uncovered;
- ignore changed non-Python data files;
- default to 100% of changed executable lines;
- retain `# pragma: no cover` as the explicit unreachable-line escape hatch; and
- for dstdns only, support repeated `--source` arguments because it has several product roots.

Parallelism belongs in the gate argv, not global pytest `addopts`, so focused runs and mutation tests do not pay xdist startup cost (`nyxloom/nyxloom-trove/LESSONS.md:90-98`). Before switching the declared gate, run the serial suite twice, then one xdist+pytest-cov run, and compare per-file executed-line sets. Any line stable in both serial runs but absent in parallel needs a deterministic in-process test; do not configure coverage to recapture incidental fork-child execution (`nyxloom/reference/STANDARD.md:205-214`).

`asserts=["tests-pass", "changed-line-coverage", "canary-verified"]` must be added only after the new gate passes on known-good HEAD and `nyxloom gate verify` returns `TRUSTWORTHY`. The CLI can confirm `tests-pass` and `canary-verified`, but reports coverage declarations as declared-not-independently-verified (`nyxloom/src/nyxloom/cli.py:827-873`).

---

## dstdns

### Current-state audit

**Gate exists:** Yes. `test-runner` executes `MOCK_MODE=true pytest tests/unit -q` in the project runner with a 2700-second timeout (`/workspaces/dstdns/nyxloom-trove/nyxloom.toml:45-60`).

**Isolation/runtime fidelity:** Strong. The wrapper explicitly rejects cockpit gating and delegates to `container-exec.py` (`/workspaces/dstdns/scripts/testing-exec.sh:1-33`). The image is `FROM dstdns-app-base`, bind-mounts source/tests, and adds only test tooling (`/workspaces/dstdns/tools/test-runner/Dockerfile:1-15,25-29,65-77,120-128`). The operating guide says the runner is the only gate and records a validated isolated worktree stack path (`/workspaces/dstdns/nyxloom-trove/GUIDE.md:9-25,112-152`).

**Exit propagation:** The main path is fail-closed. `testing-exec.sh` uses `set -euo pipefail` and `exec`s the Python wrapper (`/workspaces/dstdns/scripts/testing-exec.sh:21-33`). The wrapper uses `subprocess.run(..., check=True)` for `docker exec`, so pytest failure exits the wrapper non-zero; no trailing echo or output pipe follows it (`/workspaces/dstdns/scripts/container-exec.py:529-567`). The outer TOML argv joins with `&&`, so a failing wrapper is not masked (`/workspaces/dstdns/nyxloom-trove/nyxloom.toml:56-60`).

**Coverage:** pytest-cov is already installed in the runner (`/workspaces/dstdns/tools/test-runner/Dockerfile:65-77`), and `.coveragerc` contains historical omissions (`/workspaces/dstdns/.coveragerc:1-14`), but the declared gate supplies no `--cov` and no floor. Historical work measured a global threshold, not the requested changed-line floor; the active declaration remains bare pytest.

**Parallelism:** None in the declared gate. pytest-xdist is not installed in the runner’s test-only extras (`/workspaces/dstdns/tools/test-runner/Dockerfile:65-77`).

**Test scope and quality:** The active gate intentionally runs only `tests/unit`, despite a much broader configured layout (`/workspaces/dstdns/pytest.ini:1-19`). The TOML comment records that full collection is red with 255 pre-existing failures and the unit lane is the temporary honest lane (`/workspaces/dstdns/nyxloom-trove/nyxloom.toml:49-55`). In addition, 61 `libs/common/tests` tests are uncollectable without a missing import-path/install fix and are invisible to every current gate (`/workspaces/dstdns/nyxloom-trove/4-backlog.md:252-264`). The unit lane mixes real-ish fakes/TestClient behavior with extensive mocking: for example, controller pipeline tests use fakeredis but patch the queue manager (`/workspaces/dstdns/tests/unit/test_controller_pipeline_logic.py:1-38,187-196`), while cancel tests replace both DB and Redis interfaces (`/workspaces/dstdns/tests/unit/test_cancel_endpoint.py:1-46,71-101`). This is not proof the tests are hollow, but it requires targeted real-component regression tests for changes at service boundaries.

**Discrimination/canary:** Not proven. Worse, the verifier currently selects `gate-probe`, because it sorts before `test-runner`; its command runs literal `true` (`/workspaces/dstdns/nyxloom-trove/nyxloom.toml:62-66`; selection rule at `nyxloom/src/nyxloom/gate_runner.py:30-46`). This is a definite current **LAUNDERS** configuration for `gate verify`, even though the real unit suite itself can fail.

### Gap table

| Requirement | Met? | Evidence | Gap / required action |
|---|---:|---|---|
| Declared `{worktree}` gate with phase and timeout | Yes | `nyxloom.toml:56-60` | Keep `test-runner` as the authoritative implementation gate. |
| Separate runtime-faithful test environment | Yes | `testing-exec.sh:1-33`; `tools/test-runner/Dockerfile:1-15,25-29` | Preserve `test-runner`; do not move to cockpit or generic `tester-unified`. |
| Non-masking exit propagation | Yes | `testing-exec.sh:21-33`; `container-exec.py:529-567` | Retain `exec`, `check=True`, and `&&`; never append a verdict echo or pipe. |
| Meaningful verification-gate selection | No | `nyxloom.toml:62-66`; `gate_runner.py:30-46` | Move `gate-probe` to `phase="review"` (or remove it from `[gates]`) so `gate verify` selects `test-runner`. |
| Changed-line coverage floor | No | Gate argv at `nyxloom.toml:57`; pytest-cov exists at `Dockerfile:65-77` | Add multi-root pytest-cov JSON and a repeated-source changed-line evaluator. |
| Parallel execution | No | Gate argv at `nyxloom.toml:57`; runner extras at `Dockerfile:65-77` | Add pytest-xdist and `-n auto` in gate argv. |
| pytest-cov used under xdist | No | No xdist/cov in active argv | Use pytest-cov, never `coverage run -m pytest`, for the parallel run. |
| Serial/parallel coverage parity checked | No evidence | No adoption record | Two stable serial runs, one xdist run, compare executed lines per file before activation. |
| Known-good pass + known-bad canary rejection | No | `gate-probe` is `true`; no `asserts` | Run `exec-nyxloom gate verify dstdns` after selection/floor fixes; require `TRUSTWORTHY`. |
| Canary targets code the gate observes | Partial | Unit lane only; broad source tree and excluded tests at `pytest.ini:5-19`, backlog `4-backlog.md:252-264` | Coverage roots must include every supported Python product root; repair uncollectable suites before widening their obligations. |
| Real component exercised; component under test not wholly mocked | Partial | fakeredis plus patched interfaces in `test_controller_pipeline_logic.py:187-196`; all-mock boundary in `test_cancel_endpoint.py:30-46` | Require at least one deterministic real in-process/service-boundary test for each changed behavior. |
| Regression test proven fail-before/pass-after | Not enforced | Standard requires it; current gate config has no mechanism | Make before/after evidence a backlog acceptance item and review requirement. |
| No incidental/fork-only coverage | Unknown | No parallel parity evidence | Treat serial-only fork-child lines as missing tests, not a coverage plumbing exception. |
| Run and verdict separate | Yes for current wrapper | `testing-exec.sh:21-33`; `container-exec.py:561-567` | Preserve. Canary is a separate operator/GA4 action, not chained into ordinary gate argv. |
| GA2 rigor declaration truthful | No | No `asserts` in `nyxloom.toml:56-66` | Add assertions only after observed `TRUSTWORTHY`. |
| GA4 periodic re-verification | No | GA4 remains planned (`plan-gate-adoption.md:106-113`) | Schedule manual verification after runner/test-layout changes and at least quarterly until GA4 ships. |

### Concrete upgrade specification

#### Prerequisites

1. Add `pytest-xdist>=3` to the test-only install in `tools/test-runner/Dockerfile`; pytest-cov is already present. Rebuild `dstdns/test-runner:latest` through the existing bake target (`/workspaces/dstdns/docker-bake.hcl:206-223`).
2. Add a project-owned `tools/test-runner/coverage_gate.py` and tests, based on `nyxloom/src/nyxloom/coverage_gate.py`, with repeated `--source`. The evaluator must union these product roots: `applications`, `libs`, `scripts`, `infra`, and `infra-global`. It must ignore `tests`, generated/build trees, legacy experiments, and non-Python files.
3. Fix or explicitly sequence B030 before broadening beyond `tests/unit`; its 61 tests currently do not collect (`/workspaces/dstdns/nyxloom-trove/4-backlog.md:252-264`). Do not make B040 depend on the already-red full suite: land the changed-line floor on the honest unit lane first, then widen after the existing full-scope pollution package meets the criterion recorded at `nyxloom.toml:49-55`.
4. Run serial/parallel parity before declaring the new gate. The suite has process/container-heavy behavior, so compare per-file executed-line sets and add deterministic in-process tests for stable serial-only lines.

#### Exact target gate configuration

```toml
[gates.test-runner]
argv = ["bash", "-c", "cd /workspaces/dstdns && ./scripts/testing-exec.sh 'cd {worktree} && MOCK_MODE=true pytest tests/unit -n auto -q --cov=applications --cov=libs --cov=scripts --cov=infra --cov=infra-global --cov-report=json:/tmp/dstdns-cov.json && python tools/test-runner/coverage_gate.py --repo . --base main --coverage-json /tmp/dstdns-cov.json --source applications --source libs --source scripts --source infra --source infra-global'"]
phase = "implementation"
timeout_seconds = 2700
environment = "test-runner"
asserts = ["tests-pass", "changed-line-coverage", "canary-verified"]

[gates.gate-probe]
argv = ["bash", "-c", "cd /workspaces/dstdns && ./scripts/testing-exec.sh 'true'"]
phase = "review"
timeout_seconds = 300
environment = "test-runner"
```

`gate-probe` remains available as an explicitly requested reachability diagnostic, but is no longer eligible for verification selection. During implementation, omit `canary-verified` from `asserts`; run `exec-nyxloom gate verify dstdns` from the nyxloom control surface, require `TRUSTWORTHY`, then add that assertion in the final config change. The ordinary gate must not invoke `gate verify` recursively.

#### Activation gates

1. Runner dependency smoke: both `pytest_cov` and `xdist` import inside `test-runner`.
2. Evaluator unit tests: added/edited executable uncovered line fails; covered line passes; comment/data file ignored; missing Python coverage fails; merge-base and first-parent resolution both pass crafted git tests.
3. Serial twice vs xdist once: no serial-stable executed line is lost without an accompanying deterministic test/fix.
4. Known-good HEAD: new `test-runner` gate exits 0.
5. Planted defect: `exec-nyxloom gate verify dstdns` returns `TRUSTWORTHY`.
6. Declaration: only after step 5, add `canary-verified`.

### Suggested replacement text for B040

> **B040 — Adopt and continuously verify the runtime-faithful dstdns gate.** Preserve `tools/test-runner` (`FROM dstdns-app-base`) and the `MOCK_MODE=true tests/unit` fast lane while full-scope pollution remains tracked separately.  
>  
> 1. **Fix verification selection:** keep `test-runner` as the only `implementation` gate; move `gate-probe` (`true`) to `review` or remove it from declared verification gates. Oracle: `gate_runner.select_verification_gate` resolves `test-runner`, never `gate-probe`.  
> 2. **Add gate tooling to the runner:** install pytest-xdist; retain pytest-cov; rebuild through the existing `docker-bake.hcl` test-runner target. Oracle: both plugins import in the rebuilt runtime-faithful image.  
> 3. **Add a tested multi-root changed-line evaluator:** project-owned adaptation of nyxloom’s merge-base/first-parent coverage gate supporting repeated sources (`applications`, `libs`, `scripts`, `infra`, `infra-global`) and a default 100% changed-executable-line floor. Oracle: crafted evaluator tests prove pass/fail/error behavior.  
> 4. **Make parallel coverage honest:** run the unit lane serial twice and xdist+pytest-cov once; compare per-file executed lines. Add deterministic in-process tests for every serial-stable/parallel-missed line; do not recapture fork-child incidental coverage. Oracle: parallel executed lines are a per-file superset of the stable serial set after justified exclusions.  
> 5. **Declare the exact gate:** `pytest tests/unit -n auto` under pytest-cov, emit JSON, then invoke the evaluator with `&&`; no pipe/trailing command may mask either result. Oracle: known-good HEAD passes and an uncovered changed line fails.  
> 6. **Prove rejection:** run `exec-nyxloom gate verify dstdns`; require `TRUSTWORTHY`. Only then declare `asserts=["tests-pass","changed-line-coverage","canary-verified"]`. Record the target and verdict.  
> 7. **Widen separately:** after the existing full-collection pollution criterion is met and B030’s 61 common tests collect without ad hoc `PYTHONPATH`, expand the gate suite/source obligations and repeat parity + canary verification.  
> 8. **Keep it true:** rerun gate verification after changes to test discovery, source roots, runner image, or wrapper, and quarterly until GA4 automates the cadence.

### Effort and risk

**Effort: L.** The Docker change is small; the complexity is defining honest coverage across a large multi-service/multi-root Python tree while the authoritative suite is deliberately narrowed.

**Main risk:** a 100% changed-line floor over broad roots will correctly expose code changed outside the unit lane. That must result in deterministic tests or an explicitly narrower first-stage source contract—not blanket omissions. Existing full-collection pollution and the uncollectable `libs/common/tests` lane make a one-shot “cover everything” rollout likely to produce false confidence or an unusable gate.

---

## netcup-api-filter (naf)

### Current-state audit

**Gate exists:** Yes. The declared `unit` gate runs `tests/` in Python 3.11 and 3.9 runner images with `set -e` (`/workspaces/netcup-api-filter/nyxloom-trove/nyxloom.toml:65-72`). The matrix is a real strength because Passenger targets include both versions (`tooling/test-runner/README.md:8-18,52-58`).

**Layout discrepancy:** The checked-in helper now defaults to Python 3.11, 3.9, and 3.14 (`tooling/test-runner/testing-exec.sh:123-145`), and project state claims a three-version green gate (`nyxloom-trove/STATE.md:10-18,48-57`). The actual nyxloom declaration still runs only `local` (3.11) and `py39` (`nyxloom.toml:65-72`). This report specifies the declared two-version contract; adding py314 should be a separate explicit reconciliation after confirming that image is part of the required ship matrix.

**Isolation/runtime fidelity:** Strong and already implemented, contrary to B039’s “confirm/build” wording. The runner derives runtime and test closure from the project requirements, uses version-specific images, installs git, and provides a full passwd/group/HOME/XDG identity (`tooling/test-runner/Dockerfile:40-78,80-104`). The doctrine explicitly names the devcontainer as cockpit and the runner as ship gate (`tooling/test-runner/README.md:1-23`).

**Exit propagation:** The declared `unit` loop uses `set -e`; any `docker run` failure aborts before the next loop/exit and there is no output pipe or trailing success command (`nyxloom.toml:68-72`). The helper variant accumulates the last non-zero status and exits it (`testing-exec.sh:136-145`), so it also does not silently return zero.

**Coverage:** This is the clearest gap. `pytest.ini` enables source coverage and reports but no threshold (`pytest.ini:1-7`), while every declared gate explicitly appends `--no-cov`, disabling it (`nyxloom.toml:68-85`). The documented unit baseline is only 24% overall, with route/backend modules intentionally expected to be covered by E2E (`docs/TESTING_INFRASTRUCTURE.md:249-288`). A changed-line floor is appropriate because it does not require raising that legacy global percentage in one step.

**Parallelism:** None. pytest-xdist is absent from `requirements-dev.txt` and the Python 3.9 compatibility branch (`requirements-dev.txt:10-15`; `tooling/test-runner/Dockerfile:53-74`).

**Test quality:** Better than the bare gate suggests. Unit fixtures create the real Flask app and a per-test SQLite database (`tests/conftest.py:22-56`), and portal scope tests drive real routes/DB while asserting independent fake-backend mutation state (`tests/test_portal_record_scope.py:1-18,51-110`). Property tests and prior mutation testing are documented (`docs/TESTING_INFRASTRUCTURE.md:34-44,292-321`). However, known UI tests still convert failure conditions into skips or contain skeletons; P06 O2/O3 remain open (`nyxloom-trove/STATE.md:34-47`; `nyxloom-trove/handoffs/naf-P06-test-suite-integrity.md:22-44,50-90`). `pytest.ini` also globally uses `-x`, so the run stops at the first failure (`pytest.ini:5-7`; backlog B033 at `nyxloom-trove/4-backlog.md:37`).

**Discrimination/canary:** Not proven. The verifier currently selects `e2e`, not `unit`, due lexical ordering and common `implementation` phases (`nyxloom.toml:68-88`; `gate_runner.py:30-46`). The config also says the repo is not yet mounted/registered with nyxloomd (`nyxloom.toml:9-11`), so `exec-nyxloom gate verify naf` has an external prerequisite before it can yield a real verdict. Current status is **LAUNDERS risk / unverified**, not an observed LAUNDERS verdict.

### Gap table

| Requirement | Met? | Evidence | Gap / required action |
|---|---:|---|---|
| Declared `{worktree}` gate with phase and timeout | Yes | `nyxloom.toml:55-72` | Preserve the repo-inside-worktree mount strategy. |
| Separate runtime-faithful test environment | Yes | `Dockerfile:40-104`; `README.md:1-23` | B039 should say “preserve,” not “confirm/build.” |
| Multi-version compatibility | Yes, with doc/config drift | Declaration: `nyxloom.toml:65-72`; helper/state: `testing-exec.sh:123-145`, `STATE.md:17` | Keep 3.11+3.9 now; separately decide/reconcile py314. |
| Non-masking exit propagation | Yes | `nyxloom.toml:68-72`; helper `testing-exec.sh:136-145` | Replace `set -e` with `set -euo pipefail` in the target argv for clarity; retain no pipes/trailing echo. |
| Meaningful verification-gate selection | No | `e2e`, `trove`, `unit` all implementation at `nyxloom.toml:68-88` | Move `e2e` and `trove` to `review`; leave `unit` as the only implementation verification gate. |
| Changed-line coverage floor | No | `--no-cov` at `nyxloom.toml:69,85`; no floor at `pytest.ini:5-7` | Remove `--no-cov`; use pytest-cov JSON plus project evaluator. |
| Parallel execution | No | No xdist dependency or `-n` | Install pytest-xdist in both version branches; run `-n auto`. |
| pytest-cov used under xdist | No | Coverage disabled | Explicit pytest-cov flags in gate argv; never `coverage run` under xdist. |
| Serial/parallel coverage parity checked | No evidence | No adoption record | Two serial runs plus xdist comparison; resolve stable misses. |
| Known-good pass + known-bad canary rejection | No / blocked on registration | `nyxloom.toml:9-11`; no `asserts` | Register/mount naf, then require `TRUSTWORTHY`. |
| Canary targets code the gate observes | Expected after floor | Source root is `src/netcup_api_filter`; coverage config at `pytest.ini:5-7` | Single-root evaluator makes every changed Python source line observable even if legacy total coverage is low. |
| Real component exercised; component under test not wholly mocked | Mostly | Real app/SQLite at `tests/conftest.py:22-56`; independent backend truth at `test_portal_record_scope.py:15-18` | Keep targeted fakes, but retain at least one real request/DB/independent-state assertion per behavior. |
| Regression test fail-before/pass-after | Partial discipline, not gate-enforced | P06 has explicit negative oracles at `naf-P06...md:22-39` | Require recorded pre-fix failure/post-fix pass for each regression package. |
| No hollow/skip-instead-of-fail tests | No | P06 open O2/O3 at `naf-P06...md:27-44,60-69` | Finish P06; do not count those tests as proof meanwhile. |
| Run and verdict separate | Yes for current commands | Direct docker exit in `nyxloom.toml:68-85` | Keep canary verification as a separate control-plane action. |
| GA2 rigor declaration truthful | No | No `asserts` | Add only after `TRUSTWORTHY`; do not claim mutation in ordinary gate. |
| GA4 periodic re-verification | No | GA4 planned at `plan-gate-adoption.md:106-113` | Manual cadence until automation exists. |

### Concrete upgrade specification

#### Prerequisites

1. Add `pytest-xdist>=3` to `requirements-dev.txt`.
2. Add a Python-3.9-compatible `pytest-xdist` constraint to the Dockerfile’s 3.9 branch alongside its existing compatible pytest/pytest-cov packages (`tooling/test-runner/Dockerfile:53-74`).
3. Add and unit-test `tooling/test-runner/coverage_gate.py`, a project-owned single-source adaptation of nyxloom’s evaluator with `--source src/netcup_api_filter`.
4. Rebuild both `netcup-api-filter/test-runner:local` and `netcup-api-filter/test-runner:py39`.
5. Mount/register the repo with nyxloomd before the canary step; the config explicitly says this is not yet true (`nyxloom.toml:9-11`).
6. Run coverage parity separately for Python 3.11 and 3.9. Version-conditional code means one interpreter’s coverage is not a substitute for the other’s.

#### Exact target gate configuration

```toml
[gates.unit]
argv = ["bash", "-c", "set -euo pipefail; for img in netcup-api-filter/test-runner:local netcup-api-filter/test-runner:py39; do echo \"--- gate: $img ---\"; docker run --rm -v /home/vb/volkb79-2/netcup-api-filter:/workspaces/netcup-api-filter:rw $img bash -c 'cd {worktree} && /opt/tester-venv/bin/python -m pytest tests/ -q -p no:cacheprovider -o addopts= --strict-markers -n auto --cov=src/netcup_api_filter --cov-report=json:/tmp/naf-cov.json && /opt/tester-venv/bin/python tooling/test-runner/coverage_gate.py --repo . --base main --coverage-json /tmp/naf-cov.json --source src/netcup_api_filter'; done"]
phase = "implementation"
timeout_seconds = 2400
environment = "test-runner"
asserts = ["tests-pass", "changed-line-coverage", "canary-verified"]

[gates.trove]
argv = ["bash", "-c", "docker run --rm -v /home/vb/volkb79-2/netcup-api-filter:/workspaces/netcup-api-filter:rw netcup-api-filter/test-runner:local bash -c 'cd {worktree} && /opt/tester-venv/bin/python tooling/test-runner/lint_trove.py'"]
phase = "review"
timeout_seconds = 300
environment = "test-runner"

[gates.e2e]
argv = ["bash", "-c", "docker run --rm --network naf-dev-network -e PLAYWRIGHT_SERVER_WS -v /home/vb/volkb79-2/netcup-api-filter:/workspaces/netcup-api-filter:rw netcup-api-filter/test-runner:local bash -c 'cd {worktree} && /opt/tester-venv/bin/python -m pytest ui_tests/tests -q -p no:cacheprovider --no-cov'"]
phase = "review"
timeout_seconds = 3600
environment = "test-runner"
```

`-o addopts=` deliberately removes global `-x` and the old implicit coverage flags, after which the gate supplies its complete strict/xdist/pytest-cov policy explicitly. `e2e` remains an available, explicitly requested gate and may keep `--no-cov` until deployment-to-worktree source attribution is designed; it is not the changed-line coverage verdict and must not be selected by `gate verify`.

During rollout, omit `canary-verified`; after registration, run `exec-nyxloom gate verify naf`, require `TRUSTWORTHY`, and only then add it. If the project decides the py3.14 OCI target is mandatory in every nyxloom gate, append `netcup-api-filter/test-runner:py314` to this exact loop in a separately verified change; do not let STATE/helper claims silently differ from the declared gate.

#### Activation gates

1. Both runner images import pytest-cov and xdist.
2. The evaluator’s unit tests cover merge-base, merge first-parent, missing file, uncovered/covered executable lines, comments, and malformed JSON/git failures.
3. Serial-twice/parallel-once parity passes independently on 3.11 and 3.9.
4. Known-good HEAD passes both images without `--no-cov`.
5. A changed untested executable line fails both the relevant coverage evaluator and the outer loop.
6. After nyxloom registration, `exec-nyxloom gate verify naf` returns `TRUSTWORTHY`.
7. Finish P06 O2/O3 separately; the coverage floor must not be presented as proof that UI assertions are meaningful.

### Suggested replacement text for B039

> **B039 — Upgrade and verify the existing runtime-faithful multi-Python gate.** Preserve `tooling/test-runner` and the declared Python 3.11 + 3.9 Passenger matrix; the separate environment already exists.  
>  
> 1. **Correct verification selection:** leave `unit` as the only `implementation` gate; move `trove` and deployment-dependent `e2e` to `review`. Oracle: nyxloom selects `unit` for verification.  
> 2. **Reconcile the version contract:** document whether py3.14 is mandatory in the nyxloom ship gate; the helper/STATE currently say 3.11+3.9+3.14 while `nyxloom.toml` says 3.11+3.9. Add py314 only through an explicit, gated decision/change.  
> 3. **Install parallel tooling:** add pytest-xdist to `requirements-dev.txt` and a compatible constraint to the Python 3.9 Docker branch; rebuild both declared images. Oracle: both images import xdist and pytest-cov.  
> 4. **Add a project-owned changed-line evaluator:** adapt nyxloom’s merge-base/first-parent algorithm for `src/netcup_api_filter`, default 100% changed executable lines, with pure-core and git-boundary tests.  
> 5. **Replace coverage suppression:** remove `--no-cov` from `unit`; neutralize legacy global addopts and explicitly run `pytest tests/ --strict-markers -n auto --cov=src/netcup_api_filter --cov-report=json:...`, then the evaluator with `&&` in every matrix image.  
> 6. **Prove parallel honesty:** compare two serial coverage runs with one xdist+pytest-cov run per interpreter; fix stable serial-only lines with deterministic in-process tests.  
> 7. **Register and prove rejection:** make naf reachable to nyxloomd, run `exec-nyxloom gate verify naf`, require `TRUSTWORTHY`, then declare `tests-pass`, `changed-line-coverage`, and `canary-verified`.  
> 8. **Close test-content debt independently:** complete naf-P06 O2/O3 (skip-instead-of-fail and skeleton tests) and require regression tests to demonstrate fail-before/pass-after. Coverage is execution evidence, not assertion-quality evidence.  
> 9. **Reverify on cadence:** after changes to source layout, matrix, image, test discovery, or coverage config, and quarterly until GA4 ships.

### Effort and risk

**Effort: M.** The source root is simple and pytest-cov already exists; work is concentrated in xdist compatibility, the evaluator, matrix parity, and nyxloom registration.

**Main risk:** `--no-cov` may have been added to avoid the global `pytest.ini` coverage/`-x` policy or matrix overhead. Remove it only with the explicit argv above and measured timings. The 24% legacy baseline is not itself a blocker to a changed-line floor, but route/backend changes will correctly demand tests that exercise those new lines. A second risk is declaring py3.14 support in prose/helper code without actually running it in the nyxloom gate.

---

## topos

### Current-state audit

**Gate exists:** Yes. `topos-suite` runs the entire `topos/tests` suite in `tester-unified` with a 1800-second timeout (`topos/nyxloom-trove/nyxloom.toml:50-67`). `py-compile` is a second fast syntax check (`nyxloom.toml:69-75`).

**Isolation/runtime fidelity:** Strong. `tester-unified` is explicitly the vbpub gating container, builds a Python 3.14 venv from each project’s declared extras, and gives the run UID a full identity (`tester-unified/Dockerfile:1-20,22-43,45-61`). topos’s `[dev]` extra includes runtime optional paths and pytest (`topos/pyproject.toml:5-19`), while the suite has a session-level guard that changes the process exit to failure if required optional extras are missing (`topos/tests/conftest.py:40-128`). This is meaningful false-green protection.

**`{worktree}` limitation assessment:** The comment claiming the gate “has no `{worktree}` substitution” is stale (`topos/nyxloom-trove/nyxloom.toml:59-62`). The actual current argv at line 64 contains `cd {worktree}`, and the container bind maps the whole vbpub repo to the same `/workspaces/vbpub` prefix. Because topos worktrees live under the monorepo (`nyxloom.toml:16-17`), the current substitution reaches the detached checkout. The real limitation is portability: the Docker source bind is hard-coded to `/home/vb/volkb79-2/vbpub`, so it works only where that host path maps to `/workspaces/vbpub`. Update the misleading comment during implementation, but preserve the validated mount until a host-path configuration seam is available.

**Exit propagation:** `topos-suite` uses `&&` inside the container and direct `docker run`; pytest non-zero reaches nyxloom. No pipe or trailing echo masks it (`nyxloom.toml:63-67`). `py-compile` is weaker: it embeds a `git diff | tr` pipeline without `set -o pipefail`, performs shell word splitting on file names, and a `git diff` failure can be hidden by `tr` before `py_compile` runs (`nyxloom.toml:69-75`). It is not suitable as the ship verdict.

**Coverage:** None in the gate. tester-unified currently happens to contain coverage/pytest-cov/xdist through nyxloom’s own test extra (`nyxloom/pyproject.toml:8-12`; `tester-unified/Dockerfile:36-43`), but topos’s own `[dev]` extra declares only pytest plus runtime extras (`topos/pyproject.toml:12-19`). Relying on a sibling project to supply gate tooling violates the runner’s project-derived dependency intent (`tester-unified/Dockerfile:7-11,24-43`).

**Parallelism:** None in the declared argv. The suite has extensive subprocess, socket, thread, and filesystem behavior; fixed `/tmp` defaults appear in tests such as `test_squeeze.py` while many other tests correctly use `tmp_path`. xdist activation therefore needs collision/flakiness work, not just a flag.

**Test quality:** Strong overall. The suite guards optional-extra skips at process-exit level (`tests/conftest.py:40-128`), and gate-environment tests keep declared extras and guard coverage aligned (`tests/test_gate_environment.py:1-50`). Many tests exercise real CLI/subprocess/socket/filesystem behavior rather than only call bookkeeping; for example, action tests assert non-zero propagation, bounded failure, exact argv, and audit artifacts (`topos/tests/test_actions.py`, including the contracts visible around lines 542-655, 1093-1148). The project’s own status records review-found defects that passed package oracles, a useful reminder that coverage and green tests do not replace adversarial review (`topos/README.md:268-271`).

**Discrimination/canary:** Not proven, and current selection is wrong. `py-compile` sorts before `topos-suite` (`nyxloom.toml:63-75`; `gate_runner.py:30-46`). The known-bad canary inserts `raise AssertionError(...)` after module prologue (`nyxloom/src/nyxloom/gate_canary.py:196-206`); this is syntactically valid and therefore survives compilation. Current verification is structurally expected to report **LAUNDERS**.

### Gap table

| Requirement | Met? | Evidence | Gap / required action |
|---|---:|---|---|
| Declared `{worktree}` gate with phase and timeout | Yes | Actual argv at `topos/nyxloom-trove/nyxloom.toml:63-67` | Correct stale lines 59-62; retain monorepo-root cwd. |
| Separate runtime-faithful test environment | Yes | `tester-unified/Dockerfile:1-20,36-61` | Preserve shared runner and full identity. |
| Complete optional-path test environment | Yes | `topos/pyproject.toml:12-19`; `tests/conftest.py:40-128` | Extend `[dev]` with gate tools so topos owns its closure. |
| Non-masking exit propagation | Suite yes; compile no | Suite `nyxloom.toml:63-67`; compile `:69-75` | Never use `py-compile` as ship verdict; move it to review. |
| Meaningful verification-gate selection | No | `py-compile` sorts first; `gate_runner.py:30-46` | Make `topos-suite` the only implementation gate. |
| Changed-line coverage floor | No | No cov flags at `nyxloom.toml:64` | Add pytest-cov JSON and topos-owned evaluator for `topos/src/topos`. |
| Parallel execution | No | No `-n` at `nyxloom.toml:64` | Add pytest-xdist and `-n auto` in argv. |
| pytest-cov used under xdist | No | No current coverage | Use pytest-cov explicitly; do not use `coverage run`. |
| Serial/parallel coverage parity checked | No evidence | No adoption record | Two serial runs plus xdist comparison; address subprocess/fixed-path cases. |
| Known-good pass + known-bad canary rejection | No | No `asserts`; compile-selected canary survives syntax check | Run `exec-nyxloom gate verify topos` after selection/floor fix; require `TRUSTWORTHY`. |
| Canary targets code the gate observes | Expected after floor | Single source root at `topos/pyproject.toml:24-28` | Floor should cover `topos/src/topos`; test canary candidate selection explicitly. |
| Real component exercised; component not wholly mocked | Mostly | Real artifact/process assertions in `test_actions.py`; filesystem fixtures throughout | Keep injected seams for privileged operations, but retain CLI/artifact/socket behavioral tests. |
| Regression fail-before/pass-after discipline | Partial | Review-found misses documented at `topos/README.md:268-271` | Add explicit pre-fix failure evidence to future regression package acceptance. |
| No skip-based false green | Strongly met for required extras | `tests/conftest.py:100-128`; `test_gate_environment.py:25-50` | Preserve under xdist; verify session hooks behave correctly in controller/workers. |
| No incidental/fork-only coverage | Unknown | Subprocess-heavy suite, no parity record | Add deterministic in-process tests for stable serial-only lines exposed by xdist. |
| Run and verdict separate | Yes for suite | Direct docker/pytest exit at `nyxloom.toml:64` | Keep canary separate. |
| GA2 rigor declaration truthful | No | No `asserts` | Add after observed verification only. |
| GA4 periodic re-verification | No | GA4 planned at `plan-gate-adoption.md:106-113` | Manual cadence until automation. |

### Concrete upgrade specification

#### Prerequisites

1. Add `pytest-xdist>=3` and `pytest-cov>=5` to `topos[dev]` (`topos/pyproject.toml:12-19`) so topos does not acquire its gate tools incidentally through nyxloom’s extra.
2. Rebuild `tester-unified:local` from the repo root. Keep its full UID/GID/HOME/XDG setup.
3. Add and unit-test `topos/tools/coverage_gate.py`, a project-owned adaptation with source `topos/src/topos` when invoked from the vbpub worktree root.
4. Audit fixed `/tmp` and singleton socket/process names for worker collisions. Convert test artifacts to `tmp_path`/worker-qualified names where necessary.
5. Run two serial coverage passes and one xdist+pytest-cov pass. Because the suite launches subprocesses, expect some serial-only child coverage; replace it with deterministic in-process tests rather than enabling broad subprocess capture merely to preserve the count.

#### Exact target gate configuration

```toml
[gates.topos-suite]
argv = ["bash", "-c", "docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'cd {worktree} && PYTHONPATH=topos/src /opt/tester-venv/bin/python -m pytest topos/tests -n auto -q --cov=topos/src/topos --cov-report=json:/tmp/topos-cov.json && PYTHONPATH=topos/src /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-cov.json --source topos/src/topos'"]
phase = "implementation"
timeout_seconds = 1800
environment = "tester-unified"
asserts = ["tests-pass", "changed-line-coverage", "canary-verified"]

[gates.py-compile]
argv = ["bash", "-c", "set -euo pipefail; cd {worktree}; mapfile -d '' files < <(git diff -z --name-only main...HEAD -- '*.py'); ((${#files[@]} == 0)) || python3 -m py_compile \"${files[@]}\""]
phase = "review"
timeout_seconds = 120
environment = "local"
```

The revised compile helper fixes pipeline and filename handling but remains advisory. `topos-suite` becomes the only implementation gate and therefore the verifier’s selection. During rollout omit `canary-verified`; run `exec-nyxloom gate verify topos`, require `TRUSTWORTHY`, then add it. Also replace the stale “no `{worktree}`” config comment with an accurate note: substitution works, while the hard-coded host bind is environment-specific.

#### Activation gates

1. A freshly rebuilt `tester-unified` proves topos’s own `[dev]` installs pytest-cov and xdist.
2. Existing optional-extra negative tests still force a non-zero session exit.
3. Coverage evaluator unit/boundary tests pass.
4. Two serial runs are internally stable; xdist has per-file executed-line parity after deterministic fixes.
5. Known-good HEAD passes the new suite.
6. An uncovered changed source line fails the coverage verdict.
7. `exec-nyxloom gate verify topos` returns `TRUSTWORTHY`; only then add the canary assertion.

### Suggested replacement text for B-046

> **B-046 — Parallelize, add changed-line coverage, and mechanically verify the existing tester-unified gate.** Preserve `tester-unified`, the full `topos/tests` suite, monorepo-root execution, and the optional-extra skip guard.  
>  
> 1. **Fix verification selection:** retain `topos-suite` as the only `implementation` gate; move `py-compile` to `review` and make its pipeline/filename handling fail-closed. Oracle: nyxloom selects `topos-suite`; a valid-syntax import-break is not “verified” by compile alone.  
> 2. **Correct config documentation:** the live argv does contain `cd {worktree}`; replace the stale no-substitution comment and document the remaining fixed host-bind portability constraint.  
> 3. **Own the test closure:** add pytest-xdist and pytest-cov to `topos[dev]`; rebuild tester-unified. Oracle: tools come from topos’s declared extra, not a sibling project’s incidental dependency.  
> 4. **Add a tested changed-line evaluator:** project-owned adaptation for `topos/src/topos`, preserving merge-base/first-parent semantics and a default 100% changed-executable-line floor.  
> 5. **Make xdist safe:** inventory fixed `/tmp`, socket, daemon, and process names; worker-qualify or move them to `tmp_path`. Run serial twice and compare per-file executed lines with xdist+pytest-cov; add deterministic in-process tests for stable parallel misses.  
> 6. **Declare the exact gate:** `pytest topos/tests -n auto` under pytest-cov JSON followed by the evaluator with `&&`; keep parallelism out of global addopts. Oracle: known-good passes, uncovered changed source fails, and the required-extra skip guard still exits non-zero.  
> 7. **Prove rejection:** run `exec-nyxloom gate verify topos`, require `TRUSTWORTHY`, then declare `tests-pass`, `changed-line-coverage`, and `canary-verified`.  
> 8. **Maintain test quality:** every regression test records fail-before/pass-after; continue real CLI/socket/artifact assertions and adversarial review because prior review found defects that package oracles missed.  
> 9. **Reverify on cadence:** after source-layout, runner, test-discovery, optional-extra, or coverage changes, and quarterly until GA4 ships.

### Effort and risk

**Effort: M.** The source/runner design is already clean, and the coverage evaluator has one source root. Most work is xdist hardening and parity analysis.

**Main risk:** subprocess/thread/socket tests and fixed `/tmp` defaults may collide under `-n auto`; pytest-cov may also stop crediting forked-grandchild execution. Both are useful findings. Fix isolation and add deterministic tests rather than capping workers or restoring incidental child coverage without evidence.

---

## Cross-project summary

### Prioritized rollout

**P0 — immediately correct verifier selection in all configs:**

1. dstdns: `gate-probe` → `phase="review"`, `test-runner` remains implementation.
2. naf: `e2e` and `trove` → `phase="review"`, `unit` remains implementation.
3. topos: `py-compile` → `phase="review"`, `topos-suite` remains implementation.

This P0 is independently verifiable without building the full coverage upgrade: load each config and assert `select_verification_gate` returns the real test gate. Do not declare `canary-verified` yet.

**Full adoption order:**

1. **naf first (M):** smallest source topology, existing pytest-cov, high security value, multi-version gate.
2. **topos second (M):** mature single-root suite and runner; invest in xdist isolation/parity.
3. **dstdns third (L):** multi-root evaluator and suite-scope cleanup require the most design work.

### Common prerequisites

- Every project’s own test dependency source must declare pytest-cov and pytest-xdist:
  - dstdns test-runner Dockerfile: add xdist; cov already present.
  - naf `requirements-dev.txt` and Python 3.9 Docker branch: add xdist; cov already present.
  - topos `[dev]`: add both explicitly, even though tester-unified currently receives them through nyxloom.
- Rebuild the appropriate runner image(s) after dependency changes.
- Add a project-owned, unit-tested changed-line evaluator rather than importing the nyxloom package as a consumer runtime dependency.
- Put `-n auto` and `--cov` in the declared gate command, not pytest global addopts.
- Run two serial coverage passes before the parallel comparison, so intrinsic nondeterminism is not mislabeled an xdist defect.
- Preserve full run-UID identity in every runner. dstdns and naf/topos already encode the runner-vs-cockpit distinction and identity mechanics.
- Keep the run and verdict separate. `gate verify` is an operator/periodic control-plane action, never a recursive suffix on the ordinary gate.
- Add `asserts` only after observed evidence; coverage/mutation claims are not inferred from a green canary.
- Until GA4 exists, rerun canary verification after any gate argv, runner image, dependency, source-root, or test-discovery change, plus a quarterly baseline.

### LAUNDERS risk classification

| Project | Current classification | Why |
|---|---|---|
| dstdns | **Structurally LAUNDERS under `gate verify`** | The selected gate is lexically first `gate-probe`, whose test payload is `true`; no planted source defect can change its exit (`dstdns/nyxloom-trove/nyxloom.toml:62-66`). |
| naf | **Unverified / material LAUNDERS risk** | Verifier selects deployment-dependent `e2e`, not the hermetic source matrix; coverage is explicitly disabled; repo is not yet registered/mounted, so no observed canary verdict exists (`naf/nyxloom-trove/nyxloom.toml:9-11,68-88`). |
| topos | **Structurally expected LAUNDERS under `gate verify`** | Verifier selects `py-compile`; the import-break canary is valid syntax and compilation does not execute it (`topos/nyxloom-trove/nyxloom.toml:69-75`; `nyxloom/src/nyxloom/gate_canary.py:196-206`). |

These classifications apply to the current **verification selection**, not to the proposition that the underlying pytest suites can never fail. dstdns’s `test-runner`, naf’s `unit`, and topos’s `topos-suite` all contain real discriminating tests. The upgrade makes those suites the selected verdict, adds a deterministic completeness floor, and proves their rejection behavior mechanically.

## Definition of fully adopted

A project is complete only when all of the following are recorded:

1. exactly one authoritative implementation/post-merge gate is selected by `gate verify`;
2. it uses the runtime-faithful non-cockpit runner;
3. its real test and coverage-evaluator failures propagate non-zero without masking;
4. pytest-cov collects xdist worker coverage and a changed-line evaluator enforces the declared source roots;
5. two serial runs establish stability and the parallel run has explained/fixed per-file parity;
6. known-good HEAD passes;
7. a subtree-scoped known-bad canary is rejected and the verdict is `TRUSTWORTHY`;
8. `asserts` matches observed rigor;
9. regression work requires fail-before/pass-after evidence and at least one real behavioral path where mocking would otherwise replace the component under test; and
10. a manual or automated re-verification cadence keeps the canary claim current.
