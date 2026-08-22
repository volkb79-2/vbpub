# Backlog-entries wave — test evidence record (2026-08-21)

Verification record for `feat/nyxloom-backlog-entries` (commit bf508c2b and
its amend chain). Worktree:
`.worktrees/nyxloom-backlog-entries`. Every claim below was measured, not
inferred; commands are reproduced so the record can be replayed.

## 1. tester-unified gate (ship signal) — PASS

Launched via `cmru tester-gate` from the worktree (handles the four
manual-run traps itself; env values sourced from `cmru.orchestration.toml
[env]`, never invented):

```bash
CMRU_TESTER_MEMORY=3g CMRU_TESTER_MEMORY_SWAP=16g CMRU_TESTER_CPUS=1.5 \
CMRU_TESTER_CGROUP_PROBE_IMAGE=debian:trixie-slim \
cmru tester-gate --cwd nyxloom --image tester-unified:local -- \
  bash -c "PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -n auto -q \
           --cov=src/nyxloom --cov-report=json:/tmp/nyxloom-cov.json && \
           PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate \
           --base main --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom"
```

Final result (`GATE_EXIT=0`):

- full suite green in tester-unified (python 3.14, xdist `-n auto`);
- **diff-coverage OK: 465/465 changed executable lines covered
  (100.0% ≥ 100.0% floor)** — D-064-L2 satisfied.

Gate history on this branch (kept honestly): run 1 FAILED diff-coverage at
89% — it caught 51 uncovered CLI refusal/discovery branches behind
happy-path-only tests; the fixes for that failure are part of this branch.
Run 2 failed NO-MEASUREMENT (uncommitted file) — the gate diffs committed
HEAD only; commit and re-run. Runs 3+ green; final re-run after the last
code change is the one recorded above.

## 2. Mutation campaign — 86/86 killed

Manual detached tester-unified run per root AGENTS.md's four-trap recipe
(dual mount physical+devcontainer paths, `safe.directory '*'`,
`--cgroup-parent=$CGROUP_PARENT_DEV_BACKGROUND` read from the environment,
detached → wait → logs):

```bash
docker run -d --name nyxloom-mutation3 --cgroup-parent=$CGROUP_PARENT_DEV_BACKGROUND \
  -e CGROUP_PARENT_DEV_BACKGROUND=$CGROUP_PARENT_DEV_BACKGROUND \
  -v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub \
  -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub \
  -w /workspaces/vbpub/.worktrees/nyxloom-backlog-entries/nyxloom \
  tester-unified:local bash -c "git config --global safe.directory '*' && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.mutation_gate \
    --base main --source src/nyxloom --repo . \
    --test '/opt/tester-venv/bin/python -m pytest -q -x \
            tests/test_backlog_entries.py tests/test_cli.py'; echo MUTATION_EXIT=\$?"
```

Final: `mutation OK: 86/86 mutants killed`, `MUTATION_EXIT=0`.

Campaign history:

| round | killed | outcome |
|---|---|---|
| 1 | 74/87 | 13 survivors → all classified individually; 7 real behavioral gaps + 3 UTF-8-only `ensure_ascii` flips + 2 missing rc assertions + 1 edge case |
| 2 | 85/87 | remaining: line-315 `parents=` flip (test existed but was accidentally nested inside a helper — never ran) and line-513 `Gt->GtE` |
| 3 | 85/86 | 513 eliminated by RESTRUCTURE, not by test: the blank-trim guard had an unreachable arm (bullet lines are non-blank by `_ITEM_RE`), rewritten guard-less so the mutant class cannot arise |
| 4 | 86/86 | clean |

**Real defect found by mutation that 100% line coverage missed**:
`cmd_backlog_list --status` sliced `lines[:7]` instead of `lines[:6]`,
leaking the first data row into every filtered listing. A coverage-only
verdict would have shipped it.

## 3. Module-level coverage — 100% line + branch

`tests/test_backlog_entries.py`: 75 tests (entry contract, index freshness,
promotion byte-safety incl. comment survival, status machine, auto-tick
token surgery, CLI verbs incl. merge tick and every refusal path, config
plumbing, docs-sync).

## 4. Assay assessment (recorded for NL-1)

Assay ships both engines this estate's gates do not yet use:
`assay.mutation` (bounded `run_mutation`, R2 claims) and `assay.canary`
(cause-sensitive judge-sensitivity proofs). No project gates on them yet —
ciu = R0+R1, cmru = R0, assay's own = R0 permanently (A-046/A-133). ciu's
`assay.toml` calls nyxloom's own judge "the retired
`nyxloom.coverage_gate`"; nyxloom remains its last consumer. Filing the
adoption as backlog entry NL-1 (the new system's first dogfooded entry).
