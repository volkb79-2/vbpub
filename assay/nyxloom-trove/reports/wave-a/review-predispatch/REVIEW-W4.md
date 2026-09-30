# REVIEW-W4: Wave A W4 (B123), test split, S1 receipt and CD32

**Reviewer:** a fresh adversarial reviewer (Opus), read-only.
**Date:** 2026-09-29.
**Branch:** `wave-a-w4-test-split`, reviewed at HEAD `2bdd7ef0`, against base `11ace522` (`git diff -M 11ace522..wave-a-w4-test-split`).
**Worktree:** `/workspaces/vbpub/.worktrees/wave-a-w4-test-split`, left untouched: `git status` is clean, and the `--ignored` state was the same before and after my runs.
**Scratch:** `/tmp/claude-1003/-workspaces-vbpub/0eeb333f-34bb-4155-942f-6ae260e97f0b/scratchpad/w4review/`.

No gate and no container was run. Every run used `nice -n 19 ionice -c3`, `-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1`. Docker was only ever a PATH stub, apart from one read-only `docker ps`.

## What I ran

| Run | Result |
|---|---|
| collect-only at HEAD | default 5629; `tests` 5405; `gate/tests` 344; `analysis/tests` 224. `tests gate/tests` and `gate/tests tests` both 5749. Three three-tree orders all 5973. `gate/tests/test_self_hosting.py` 7 |
| collect-only of the wheel-lane argv (`tests gate/tests -q --ignore=gate/tests/test_self_hosting.py --override-ini=pythonpath=`) | 5742 = 5749 − 7. `analysis/tests` is not collected (see "Checked and sound", item 4) |
| base `11ace522`, in a temporary detached worktree under scratch (removed afterwards) | default 5917; `tests` 5693; `analysis/tests` 224 |
| test-id comparison, base `tests` against HEAD `tests gate/tests` (basename::id) | **No test lost.** The 18 "lost" ids are the 6 wheel tests of `test_verdict_schema_is_packaged.py`, which reappear by name in `gate/tests/test_verdict_schema_wheel.py`, and 12 docs-example ids whose parameter is a docs line number that the docs edit shifted. There are 74 gained ids |
| `pytest tests --cov=src/assay --cov-branch` (once) | 5404 passed, 1 skipped, 1 failed (the known B134 `/tmp/.git` test). Coverage: 12571 statements with 1 missed, 5554 branches with 1 missed, and both are `git.py:456-457`, the B134 environmental case. **So `tests/` alone covers `src/assay` 100% line and branch apart from B134, and no moved test is the only cover of any `src/assay` line or branch.** The CD47 stand-ins are subsumed. (Local `hypothesis` is absent, so its property test skipped, and coverage is 100% even without it) |
| `gate/tests` (whole tree, once) | 333 passed, 11 skipped (the opt-in Go/Node qualification tests, the `ASSAY_SELF_HOSTING_VERDICT` witness check and the A-069 fallback). This matches the LOG |
| focused: `gate/tests/test_b105_report_check.py`, `gate/tests/test_self_lane.py`, `tests/core/test_import_contracts.py` | 81 passed |
| focused: receipt and CD32 bash tests in `gate/tests/test_distribution_gate.py` | 22 passed |
| `shellcheck` on both gate scripts | clean |
| a conftest-identity probe plugin (scratch, loaded with `-p`), four collection orders | see W4R-4 and item 5 |
| attacks against the real shell functions (function-only copy in scratch, docker PATH stub) | W4R-1 and W4R-2 reproduced. Both proposed fixes validated in scratch, shellcheck-clean |
| pytest 9.1.1 config-channel probe (scratch project) | W4R-3 reproduced |

## Findings

### W4R-1: MAJOR. A red `tester-unified` run can leave a receipt the checker accepts

**Where:** `assay/tools/tester-unified-gate.sh`: `cleanup_assay_gate_container` (lines 507-528), `run_registered_gate` (lines 621-637).

**Evidence.**
- The receipt path `$worktree/assay/.assay/registered-gate/tester-unified.json` lives inside the tree that `run_registered_tester_container` bind-mounts **read-write** into the tester container (`--mount type=bind,src=$host_repo_root,dst=/workspaces/vbpub`). That container runs the code of the commit under test, as uid 1003, the same uid as the host.
- The host clears the receipt once, at launch (line 632). On a red exit, `set -e` ends the script and the EXIT trap only removes the container. Nothing removes a receipt that appeared *during* the run.
- Reproduced in scratch (`w4review/attack/`): I stubbed `run_registered_tester_container` to do what a mis-pointed test would do, namely call the sourced `write_registered_gate_receipt "$1" "$(git -C "$1" rev-parse HEAD)" …` and then `return 1`. The result was `gate exit=1`, the receipt file present, and `b105_report_check.py --receipt-only … --expected-commit <HEAD> --expected-tree <tree>` printing `B105_TESTER_UNIFIED_PASS=…` with exit 0.
- The full `self-qualification` lane would therefore accept its report without any green same-commit `tester-unified` run. That is exactly what S1 forbids.
- `gate/tests` now contains code that calls the real `write_registered_gate_receipt` and `finish_registered_gate` (which resolves HEAD itself). The only thing between those tests and the real receipt is that they are pointed at a `tmp_path` worktree, so a one-token slip (for example `PROJECT_ROOT.parent`) produces a valid receipt inside a red run.
- The same hole covers the CD32 time-of-check/time-of-use race. If two runs in one worktree both pass `docker ps` before either launches, the order can be: A clears, B clears, A (green) writes, B (red) exits. The receipt then survives although the last run to finish was red, which contradicts CD11 ("the latest registered run at this commit passed").

**Fix (exact).** In `assay/tools/tester-unified-gate.sh`:

1. After the line `_assay_gate_logs_pid=""` (line 505), insert a new line:
   ```bash
   _assay_gate_receipt_to_clear=""
   ```
2. In `cleanup_assay_gate_container`, replace
   ```bash
       _assay_gate_container_launch_attempted=0
     fi
     exit "$result"
   }
   ```
   with
   ```bash
       _assay_gate_container_launch_attempted=0
     fi
     # (B123, S1) After launch, a non-zero exit never leaves a receipt behind: not
     # one the container itself wrote into the bind-mounted worktree, and not one a
     # concurrent run wrote after this run's own clear.
     if [[ $result -ne 0 && -n "$_assay_gate_receipt_to_clear" ]]; then
       rm -f "$_assay_gate_receipt_to_clear"
     fi
     exit "$result"
   }
   ```
3. In `run_registered_gate`, replace the line
   ```bash
     clear_registered_gate_receipt "$worktree"
   ```
   with
   ```bash
     clear_registered_gate_receipt "$worktree"
     _assay_gate_receipt_to_clear="$worktree/assay/.assay/registered-gate/tester-unified.json"
     trap cleanup_assay_gate_container EXIT
   ```
4. In the comment block `# --- the S1 receipt (B123) ---`, replace `and\n# removed at every launch so a red re-run at the same commit leaves no receipt.` with `and\n# removed at every launch and again on any non-zero exit after it, so a red run\n# at the same commit leaves no receipt, whoever wrote one during it.`
5. In `assay/docs/DESIGN-GUIDE.md` (S1 paragraph, line 2014), replace `it removes any old receipt at\nevery launch, so a red re-run leaves none.` with `it removes any old receipt at\nevery launch and again on any non-zero exit after launch, so a red run leaves none,\neven one the container wrote itself.`

**Test (add to `assay/gate/tests/test_distribution_gate.py`, right after `test_a_red_container_leaves_no_receipt_even_when_a_stale_one_existed`):**
```python
def test_a_red_container_that_wrote_a_valid_receipt_itself_leaves_none(
    tmp_path: Path, gate_functions: Path
) -> None:
    """The container has the worktree bind-mounted read-write, so code under test
    can write a perfectly valid receipt; a red exit must still leave none."""
    worktree, commit, tree = _receipt_worktree(tmp_path)
    env, log = _docker_stub(tmp_path, "")

    proc = run_bash(
        'run_registered_tester_container() { write_registered_gate_receipt "$1" '
        '"$(git -C "$1" rev-parse HEAD)" "$(git -C "$1" rev-parse "HEAD^{tree}")"; return 7; }\n'
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 7, proc.stdout + proc.stderr
    assert not (worktree / RECEIPT_RELATIVE).exists()
    assert "ASSAY_REGISTERED_GATE_COMPLETE" not in proc.stdout
```

**Oracle.**
- Deleting the new `trap cleanup_assay_gate_container EXIT` line in `run_registered_gate` makes this test red (the receipt remains).
- The existing O7 and O7a tests stay green: the busy-host exit 3 happens before the trap is armed, so the earlier receipt stays byte-identical.
- Validated in scratch (`attack/fns-fixed.sh`): forged-then-red gives exit 7 and no receipt; green gives exit 0 and the receipt; busy gives exit 3 with the receipt untouched; a plain red gives exit 7 and no receipt.

### W4R-2: MINOR. A failing `docker ps` is treated as "host free"; the gate clears the receipt, launches, and reports a "real" failure

**Where:** `assay/tools/tester-unified-gate.sh:623-628` (the CD32 block in `run_registered_gate`).

**Evidence.**
- `names="$(docker ps … | grep '^run-gate-' | paste -sd, -)" || true` swallows a `docker ps` failure, because `pipefail` fails the pipeline and `|| true` hides it.
- `names` is then empty, and the run captures C/T, clears the receipt and calls `docker run`, which then almost certainly fails with `die`, exit 1.
- Reproduced with a stub `docker` that exits 1 on `ps`: the stubbed tester launched with `receipt-present=no`, and the gate exited with status 1.
- Under CD21 (amended) and CD29, any non-zero status other than 3 counts as a real failure. A docker daemon hiccup is therefore reported as a real gate failure, and it has also deleted a valid same-commit receipt.
- The "prints nothing" and "grep finds nothing" paths are correct: `names` is empty and the run proceeds.

**Fix (exact; it extends CD32, so the carver should confirm the wording).** In `run_registered_gate`, replace
```bash
  local worktree="$1" host_repo_root="$2" cgroup_parent="$3" names commit tree
  # (CD32) One `docker ps`, no waiting: another session's gate on this shared host
  # makes this run inconclusive before it captures, clears or builds anything.
  names="$(docker ps --no-trunc --format '{{.Names}}' | grep '^run-gate-' | paste -sd, -)" || true
```
with
```bash
  local worktree="$1" host_repo_root="$2" cgroup_parent="$3" listing names commit tree
  # (CD32) One `docker ps`, no waiting: another session's gate on this shared host
  # makes this run inconclusive before it captures, clears or builds anything.
  # A `docker ps` that fails cannot show the host is free, so it is inconclusive too.
  if ! listing="$(docker ps --no-trunc --format '{{.Names}}')"; then
    echo 'ASSAY_GATE_INCONCLUSIVE=host check failed (docker ps) — rerun' >&2
    exit 3
  fi
  names="$(printf '%s\n' "$listing" | grep '^run-gate-' | paste -sd, -)" || true
```

Then append this sentence to the DESIGN-GUIDE S1 paragraph, after `and leaves the receipt as it was.`: ` A failing \`docker ps\` is treated the same way (\`ASSAY_GATE_INCONCLUSIVE=host check failed (docker ps) — rerun\`).`

**Test (add after `test_a_host_running_only_other_containers_proceeds_to_the_tester`):**
```python
def test_a_failing_docker_ps_is_inconclusive_and_leaves_the_receipt(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree, commit, tree = _receipt_worktree(tmp_path)
    earlier = worktree / RECEIPT_RELATIVE
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b'{"an earlier": "receipt"}\n')
    fake_bin = tmp_path / "failing-bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    proc = run_bash(
        "run_registered_tester_container() { echo LAUNCHED; }\n"
        f'run_registered_gate "{worktree}" "/host/vbpub" "dev-gates.slice"',
        gate_functions=gate_functions,
        env=env,
    )

    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "ASSAY_GATE_INCONCLUSIVE=host check failed (docker ps) — rerun" in proc.stderr
    assert "LAUNCHED" not in proc.stdout
    assert earlier.read_bytes() == b'{"an earlier": "receipt"}\n'
```
Validated in scratch (`attack/fns-fixed2.sh`): failing `ps` gives exit 3 with the receipt sha1 unchanged; busy still gives exit 3 with `run-gate-x,run-gate-y`; an idle or other-containers host still launches.

### W4R-3: MINOR. The "no second pytest config" pin misses pytest 9's `pytest.toml` and `.pytest.toml`, which silently drop pyproject's `pythonpath`

**Where:** `assay/gate/tests/test_self_lane.py:187-199`.

**Evidence.**
- The parametrize list is `["pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg"]`.
- pytest 9.1.1 (local; the gate image installs `pytest>=7.4.0` from `vbpub/requirements.txt`, built 2026-09-16, so almost certainly 9.x) also reads `pytest.toml` and `.pytest.toml`, and prefers them over `pyproject.toml`.
- Probe (`w4review/cfgprobe`): the project has pyproject `pythonpath = ["src"]` and an **empty** `pytest.toml` containing only `[pytest]`. `src` vanished from `sys.path`, and pytest printed only `configfile: pytest.toml (WARNING: ignoring pytest config in pyproject.toml!)`. A `pytest.toml` or `.pytest.toml` in the project root or in `tests/` with `addopts` also applied, giving `1 passed, 1 deselected`.
- `test_pytest_ini_options_pin_the_source_paths_and_test_trees` reads `pyproject.toml` directly, so it stays green while the B105 lanes would import `assay` from the run-venv wheel instead of the snapshot. That is exactly the A-468(c) failure these pins exist for.
- A `[tool.pytest]` table next to `ini_options` fails loudly ("Cannot use both"), so that path is safe.

**Fix (exact).** In `assay/gate/tests/test_self_lane.py`:

1. Replace
   ```python
   @pytest.mark.parametrize("name", ["pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg"])
   ```
   with
   ```python
   @pytest.mark.parametrize("name", ["pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg"])
   ```
2. In `test_pytest_ini_options_pin_the_source_paths_and_test_trees`, replace
   ```python
       ini = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
   ```
   with
   ```python
       pytest_table = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]
       assert set(pytest_table) == {"ini_options"}  # no pytest 9 native `[tool.pytest]` keys beside it
       ini = pytest_table["ini_options"]
   ```

**Oracle:** creating an empty `assay/pytest.toml` makes `test_no_second_pytest_configuration_file_can_shadow_pyproject[pytest.toml]` red.

**For the carver (outside W4, `src/` is forbidden):** `src/assay/mutation_witness.py` `_pytest_configs_allow_sequential` scans `pytest.ini`, `.pytest.ini`, `pyproject.toml`, `tox.ini` and `setup.cfg` only. An `addopts = ["-n", "auto"]` in a `pytest.toml` is therefore invisible to witness eligibility. File this as a backlog item.

### W4R-4: MINOR. `gate/tests/support.py` duplicates W2's loader (deviation D1 from CD31)

**Where:** `assay/gate/tests/support.py:18,33-53`, compared with `assay/analysis/tests/conftest.py:22-36`.

**Evidence.**
- **Identical in behaviour.** Both loaders run the same steps in the same order: the `sys.modules` check first, `spec_from_file_location("assay_judge_conftest", PROJECT_ROOT/"tests"/"conftest.py")`, `module_from_spec`, insertion into `sys.modules`, `exec_module`, and rollback on `BaseException`. `analysis_support.PROJECT_ROOT` is also `Path(__file__).resolve().parents[2]`.
- **No runtime hazard today.** My probe plugin ran four collection orders (`tests gate/tests`, `gate/tests tests`, `gate/tests analysis/tests tests`, `analysis/tests tests gate/tests`). In every order:
  - exactly one conftest plugin is registered per tree (`conftest`, `gate.tests.conftest`, `analysis.tests.conftest`);
  - `sys.modules["conftest"]` stays the judge's `tests/conftest.py`;
  - `assay_judge_conftest` is one second module object;
  - there is no double plugin registration;
  - fixtures resolve cleanly: gate tests use `git_repo`, `schema` and `validator` from `assay_judge_conftest` and `standalone` from `gate.tests.conftest`, while judge tests use `conftest`.
- **The double module execution is benign.** `tests/conftest.py` has no module-level side effects: an AST scan finds only constant assignments, `_shipped_schema_path()` and the `object()` sentinel.
- **The hazard is drift only.** Because the module name is shared, whichever loader runs first defines the module. If either copy ever changes (a path, a rollback rule), the behaviour becomes collection-order dependent.
- **D1's reason does not hold.** Importing `analysis.tests.conftest` pulls in only `analysis.tests.analysis_support`, which imports `pathlib` alone. That touches nothing in `analysis/**`, and `analysis` is already importable from `gate/tests`, because pytest prepends the package basedir `assay/`.
- **The fix is validated.** I applied it in a scratch **copy** of the tracked tree (not a worktree). Collection was 344 / 5749 / 5749 / 5973 / 5973, `test_self_hosting.py` under `--override-ini=pythonpath=` collected 7, and `test_self_lane.py` together with a judge file and `analysis/tests/test_analysis_package_boundary.py` gave 65 passed.

**Fix (exact).** In `assay/gate/tests/support.py`:

1. Delete the line `import importlib.util`.
2. After the line `import pytest` (and its following blank line), insert:
   ```python
   from analysis.tests.conftest import load_judge_conftest
   ```
3. Replace everything from the line `_JUDGE_NAME = "assay_judge_conftest"` down to and including `judge = _load_judge_conftest()` with:
   ```python
   #: The judge's `tests/conftest.py`, loaded once as `assay_judge_conftest` by the one
   #: loader W2 owns (CD31); importing it pulls only `pathlib` from the analysis tree.
   judge = load_judge_conftest()
   ```
4. In the module docstring, replace `The judge conftest is loaded only on demand here,\nunder the unique module name ``assay_judge_conftest`` (never ``conftest``), by\nthe same mechanism ``analysis/tests/conftest.py`` uses (W2, CD13): the module is\nloaded once and shared through ``sys.modules``.` with `The judge conftest is loaded through W2's one loader,\n``analysis.tests.conftest.load_judge_conftest`` (CD31), under the unique module\nname ``assay_judge_conftest`` (never ``conftest``), once per process.`
5. In `W4-LOG.md` D1, append: ` Reversed in the review fix pass (W4R-4): support.py now imports W2's loader.`
6. Add to `assay/gate/tests/test_self_lane.py`. Add `from pathlib import Path` to its imports, and add:
   ```python
   def test_the_tooling_tree_shares_the_one_judge_conftest_loader():
       """(CD31) One loader, one module object; the judge's `conftest` name is never rebound."""
       import sys

       from analysis.tests.conftest import load_judge_conftest
       from gate.tests import support

       assert load_judge_conftest() is support.judge is sys.modules["assay_judge_conftest"]
       assert Path(support.judge.__file__).resolve() == PROJECT_ROOT / "tests" / "conftest.py"
       bound = sys.modules.get("conftest")
       assert bound is None or Path(bound.__file__).resolve() == PROJECT_ROOT / "tests" / "conftest.py"
   ```

(If the carver prefers to keep D1, record it as a decision and apply step 6 only.)

### W4R-5: MINOR. The docs miss the operational rules of S1 and CD32 that the documented command sequence depends on

**Where:** `assay/README.md:984-990`; `assay/docs/CONSUMERS.md:70-75`; `assay/docs/DESIGN-GUIDE.md:2018-2019`.

**Evidence.**
- README and CONSUMERS never mention CD32's exit 3 or `ASSAY_GATE_INCONCLUSIVE=`; only DESIGN-GUIDE, `nyxloom.toml` and CHANGES do.
- No doc says that the receipt is **per worktree** (it lives in that worktree's ignored `.assay/`) or that **any later commit** invalidates it.
- That combination is the trap in the documented flow. Under CD44, the controller runs the gate and a fixer then commits O8/O10 evidence, so HEAD moves past the gated commit. A `--no-ff` merge creates a new commit as well. In both cases "run `tester-unified` first" becomes wrong unless it is re-run at the final HEAD. (Only `W4-LOG.md`/`W4-REPORT.md` state this.)
- The sequence is otherwise correct end to end: `tester-unified` (green, receipt) → `self-qualification` (pre-check → preflight → R0-R3 → full check with the receipt). While a `self-qualification` run is going, a `tester-unified` started in the same worktree sees the `run-gate-*` container, exits 3, and leaves the receipt alone.

**Fix (exact).**

1. `README.md`: replace `its own commit and tree (run \`./run-gate.py tester-unified\` first), then runs full-source` with `its own commit and tree (run \`./run-gate.py tester-unified\` first, in the same worktree and with no commit in between: any later commit, a docs-only or merge commit included, needs a fresh \`tester-unified\` run; \`tester-unified\` exits 3 with \`ASSAY_GATE_INCONCLUSIVE=\` when another \`run-gate-*\` container is running, which means rerun), then runs full-source`.
2. `docs/CONSUMERS.md`: replace `to proceed without a receipt for the commit and tree being judged (the preflight\ndoes not need one). No collected judge test` with `to proceed without a receipt for the commit and tree being judged (the preflight\ndoes not need one). The receipt lives in that worktree's ignored \`.assay/\` and names\nits HEAD, so run both lanes in the same worktree with no commit in between: any\nlater commit, a docs-only or merge commit included, needs a fresh \`tester-unified\`\nrun. \`tester-unified\` exits 3 with \`ASSAY_GATE_INCONCLUSIVE=host busy — rerun: <names>\`\nwhen another \`run-gate-*\` container is running; exit 3 always means rerun, and the\nreceipt is left as it was. No collected judge test`.
3. `docs/DESIGN-GUIDE.md`: replace `Run\n\`./run-gate.py tester-unified\` before \`./run-gate.py self-qualification\`.` with `Run\n\`./run-gate.py tester-unified\` before \`./run-gate.py self-qualification\`, in the same\nworktree and with no commit in between: any later commit, a docs-only or merge\ncommit included, needs a fresh \`tester-unified\` run first.`

### W4R-6: MINOR. Out-of-scope files that name moved test paths are not listed for their owners

**Where:** `assay/nyxloom-trove/reports/wave-a/W4-REPORT.md`, section "`src/assay` comments that name moved test paths".

**Evidence.**
- `git grep` finds two files W4 was forbidden to edit that still name the old paths, and that the REPORT does not hand to anyone:
  - `gate/distribution/build_release.py:25` names `tests/test_distribution_build_release.py`;
  - `gate/python/qualify_dstdns_sql.py:47` names `tests/test_gate_qualify_dstdns_sql.py -k restrict_key`.
- CD43's sweep inside W4's own scope is complete. A script over every W3-moved and W4-moved basename found 0 stale `tests/<name>.py` mentions in `tests/`, `gate/tests/`, `pyproject.toml` and `assay.toml`.
- `nyxloom-trove/2-product-definition.md` still carries old acceptance node ids (for example `tests/test_distribution_gate.py::…`), but it already carried W3-era ones (`tests/test_cli_run.py::…`), so that is not a W4 regression.

**Fix (exact).** Append to `W4-REPORT.md`, after the `src/assay` table:
```markdown
Outside `src/assay`, also forbidden to W4 and listed for their owners:

| Where | Names | Now | Owner |
|---|---|---|---|
| `gate/distribution/build_release.py:25` | `tests/test_distribution_build_release.py` | `gate/tests/test_distribution_build_release.py` | the next package allowed to touch `gate/distribution/` (W10 list) |
| `gate/python/qualify_dstdns_sql.py:47` | `tests/test_gate_qualify_dstdns_sql.py` | `gate/tests/test_gate_qualify_dstdns_sql.py` | W5 (replaces this harness) |
```

### W4R-7: MINOR. The S1 refusal test does not assert that the receipt is the cause

**Where:** `assay/gate/tests/test_b105_report_check.py:529-547`, `test_a_valid_full_report_is_refused_without_this_commits_receipt`.

**Evidence:** the test asserts only `returncode == 2` and the `B105_REPORT_REJECTED=` prefix. Its docstring argues that the report is valid, so only the receipt can refuse it. That argument depends on a second test in the same environment; the assertion itself would stay green if the report were refused for another reason. The implementer's O6 negative (17 tests red) shows the test is not hollow today; this is about keeping it that way.

**Fix (exact).** Replace
```python
    assert result.returncode == 2
    assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
    assert "B105_REPORT_ACCEPTED" not in result.stdout
```
in that test (the occurrence at line 545-547) with
```python
    assert result.returncode == 2
    assert result.stderr.startswith("B105_REPORT_REJECTED="), result.stderr
    assert "B105_REPORT_ACCEPTED" not in result.stdout
    reason = {
        "other-commit": "tester-unified receipt commit",
        "other-tree": "tester-unified receipt tree",
        "no-flag": "requires --tester-unified-receipt",
        "missing-file": "absent.json",
    }[receipt_kind]
    assert reason in result.stderr, result.stderr
```

### W4R-8: MINOR (optional hardening; the carver decides whether to fix now or file it). The receipt binds the HEAD the host saw, not the commit the container judged

**Where:** `assay/tools/tester-unified-gate.sh`: `run_registered_gate` (lines 630-636), `run_inner` (from line 407), and `make_exact_oid_clone`.

**Evidence.**
- The host captures C before launch and re-checks HEAD == C only after the container exits (`finish_registered_gate`).
- The container resolves HEAD on its own at its start (`make_exact_oid_clone`: `oid="$(git -C "$worktree" rev-parse HEAD)"`), and the self-hosted lane judges the live worktree.
- If HEAD moves after the host's capture and then moves back before `finish` (for example a checkout of another commit and back), the receipt names C although the container judged the other commit.
- This is unlikely in a one-agent worktree, but the receipt's claim ("a green run at C") is not mechanically bound.

**Fix (exact, if taken).**

1. Above `run_inner() {`, add:
   ```bash
   # (B123, S1) The host binds the receipt to the HEAD it captured before launch;
   # the container proves it judged that same commit, at its start and at its end.
   require_expected_head() {
     local worktree="$1"
     if [[ -n "${ASSAY_GATE_EXPECTED_COMMIT:-}" ]]; then
       [[ "$(git -C "$worktree" rev-parse HEAD)" == "$ASSAY_GATE_EXPECTED_COMMIT" ]] \
         || die "worktree HEAD is not the commit the host captured ($ASSAY_GATE_EXPECTED_COMMIT)"
     fi
   }
   ```
2. In `run_inner`, after `validate_worktree "$worktree"`, add `require_expected_head "$worktree"`. After the final `run_lint_phase "$scratch"`, add `require_expected_head "$worktree"`.
3. In `run_registered_tester_container`'s `docker run -d` argv, after the line `-e "CGROUP_PARENT_DEV_GATES=$cgroup_parent" \`, add `-e "ASSAY_GATE_EXPECTED_COMMIT=${ASSAY_GATE_EXPECTED_COMMIT:-}" \`.
4. In `run_registered_gate`, replace `run_registered_tester_container "$worktree" "$host_repo_root" "$cgroup_parent"` with `ASSAY_GATE_EXPECTED_COMMIT="$commit" run_registered_tester_container "$worktree" "$host_repo_root" "$cgroup_parent"`. It stays a plain call, never inside `||`/`if`; a temporary assignment on a bash function call reaches its child processes, which I verified.
5. Test, in `gate/tests/test_distribution_gate.py`:
   ```python
   def test_the_inner_run_refuses_a_head_other_than_the_captured_commit(tmp_path: Path, gate_functions: Path) -> None:
       worktree, commit, tree = _receipt_worktree(tmp_path)
       ok = run_bash(f'ASSAY_GATE_EXPECTED_COMMIT="{commit}" require_expected_head "{worktree}"', gate_functions=gate_functions)
       bad = run_bash(f'ASSAY_GATE_EXPECTED_COMMIT="{HEX40}" require_expected_head "{worktree}"', gate_functions=gate_functions)
       assert ok.returncode == 0, ok.stderr
       assert bad.returncode != 0
       assert "worktree HEAD is not the commit the host captured" in bad.stderr
   ```
   Also, in `test_registered_self_gate_uses_named_detached_container_and_wait_exit`, add `"ASSAY_GATE_EXPECTED_COMMIT": HEX40` to `env`, and add `assert f"ASSAY_GATE_EXPECTED_COMMIT={HEX40}" in run`.

## Checked and sound (per review item)

1. **Receipt trust chain.** Apart from W4R-1, W4R-2 and W4R-8, it holds.
   - **Stale receipt from an earlier commit:** refused, because commit and tree are compared exactly; the O5 cases pass.
   - **HEAD moved during the run:** `finish_registered_gate` refuses on either a commit or a tree mismatch, as tested.
   - **Red container exit:** the plain call under `set -e` propagates it (`return 7` → exit 7); it is not swallowed.
   - **Snapshot versus worktree:**
     - The driver reads `$project/.assay/…`, which is the bind-mounted worktree and not the B105 snapshot.
     - The receipt is gitignored (`vbpub/.gitignore:343 .assay/`), so it never dirties `clean_tree` or `ensure_source_unchanged`.
     - The pre-check uses the checker from the exact-OID clone.
   - **Checker refusal order:** usage errors come first, then the receipt, then the report (tested).
   - **`--receipt-only` with any other full-mode flag:** a usage error (the code checks all seven; the test covers one).
   - **Symlinked receipt:** it is followed, but its content must still name this commit and tree. `rm -f` and `mv -f` replace the link itself, so the outcome is fail-closed.
   - **Receipt from another worktree at the same commit:** acceptable. The commit fully determines the judged content (the inner run refuses a dirty `assay/`, and assay's own whole-tree DIRTY_TREE check brackets the lane). No fix is needed.
   - **Mode and ownership:**
     - The `tester-unified:local` image user is uid 1003, the same as the host `vscode` uid 1003, so the self-qualification container reads the 0644 receipt.
     - With `umask 077` and a different container uid, the check would fail closed (a refusal, never a false accept).
   - **Clear on launch when CD32 refuses at entry:** the receipt is byte-identical afterwards (O7a).
   - Failures of `cgroup-parent.sh` or `docker inspect` happen before `run_registered_gate`, so they also leave the receipt untouched.
2. **CD32.**
   - It is the first statement of `run_registered_gate`, before the capture, the clear and the launch.
   - Empty `docker ps` output and a grep with no match proceed correctly; a failing `docker ps` does not (W4R-2).
   - No container of this same gate run can be seen:
     - run-gate's `bare-host` lane starts no container, and nyxloom's gate runner starts none;
     - the tester container is named `run-gate-assay-selfhosted-*` but starts only after the check;
     - buildx builders are `buildx_buildkit_*`;
     - `docker ps` lists only running containers, so W5's later check will not see this run's exited tester.
   - Note: `docker ps` shows `run-gate-vbpub-mutation-3747550-1790644029` running **right now**, so a `tester-unified` launched at this moment would exit 3 by design.
3. **Split correctness.**
   - Every moved file tests tooling: the gate script, checker, wheel, zipapp, release builder, containers, or lane config.
   - No judge test reads `tools/`, `gate/`, a wheel or a zipapp: a grep for `tools/`, `gate/`, `b105_report_check`, the gate scripts and `build_release` in `tests/**` finds only unrelated fixture strings.
   - The coverage comparison above shows that no moved test is the only cover of any `src/assay` line or branch.
   - In-process `src/assay` use in moved tests: `test_self_lane` (`cli lanes`, `load_lane_file`), `test_b105_report_check` (`verify_document` as a fixture sanity check), `test_standalone` (`candidate_id_from_fields`) and `test_self_hosting`. All of these are also covered from `tests/`.
   - R2 kill-set loss from those in-process uses cannot be measured without an R2 run. It is plausibly nil, and the brief classified them as tooling.
   - `test_dependency_purity.py` holds static stdlib-import sweeps (a CD10-like shape), but the brief puts the file under tooling. Static sweeps add no R1 coverage and kill none of the four R2 operators, so B105 is unaffected.
4. **Collection and pins.**
   - The counts match the brief, and no test was lost against base.
   - The `test_self_lane.py` pins equal the exact lane argv and the exact CD15 ini dictionary. There is no `-o`, `--override-ini*`, `--ignore*`, `--deselect*` or `-p*` in the B105 lanes. The wheel lane keeps `--override-ini=pythonpath=` (A-130).
   - The `tester-unified` argv does **not** collect `analysis/tests`, and neither did the base argv. The `analysis` lane (`run_analysis_lane`, `[lanes.analysis]` R0+R1) covers it separately, from snapshot source, not from the wheel. That is W2's design and not a W4 regression.
5. **Loader.** See W4R-4. In every order: two module objects exist for `tests/conftest.py` (`conftest` and `assay_judge_conftest`) by design (CD14); there is one plugin registration per tree and no double conftest registration; `sys.modules["conftest"]` is stable.
6. **Tests.**
   - No sleeps, clocks or elapsed-time asserts. `timeout=` is used only as a failsafe.
   - No unrestored globals: `monkeypatch` is used throughout.
   - No real `docker` in the new bash tests: every call to `run_registered_gate` uses the PATH stub, and the stub's log is asserted.
   - O5, O6, O7 and O7a are non-hollow, per the implementer's recorded negatives, which I re-derived by reading the code.
   - One `# pragma: no cover` sits in `gate/tests/support.py:93`; it was moved verbatim from `tests/conftest.py`, as the brief directs, so it is not new code.
7. **Docs.** The two trees and S1 are stated in README, DESIGN-GUIDE and CONSUMERS; the gaps are in W4R-5. The vitest path in CONSUMERS is updated.
8. **Combined-axis attacks.**
   - **CD32 exit 3 × clear on launch:**
     - A busy refusal at an unchanged commit leaves a genuine earlier green receipt, which is correct because the refused run never launched.
     - At a new commit, the old receipt no longer matches, so the result is correct.
     - The concrete failure found is the race and container-write case (W4R-1).
   - **Commit binding × docs commits after the gate:** the documented workflow is not robust unless the docs say "same worktree, no commit in between" (W4R-5).
     - W4 itself is unaffected, because O10 is the preflight, which needs no receipt.
     - Wave A never runs the full lane (plan: "never the full `self-qualification` lane"), so this is a future trap, not a current break.
   - **`gate/tests` by explicit path × `testpaths` omitting it:**
     - A bare `pytest` never runs the tooling tests. This is documented in the pyproject comment and the README.
     - The B105 lanes cannot pick `gate/tests` up, and conftest discovery for `pytest tests` never loads `gate/tests/conftest.py`.
     - No failure found.

## Verdict

**MERGE-WITH-FIXES.** Fix W4R-1 before the controller's registered gate run. The gate must run at the final HEAD, so the fixes go in first. W4R-2 through W4R-7 are cheap and belong in the same fix pass. W4R-8 is optional; the carver decides whether to fix it now or file it.

Counts: **BLOCKER 0, MAJOR 1, MINOR 7.**
