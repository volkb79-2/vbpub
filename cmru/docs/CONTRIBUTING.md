# Working on cmru — things that cost someone real time

`SPEC.md` is normative: it says what cmru MUST do. This file is the other half —
what actually bites you while changing it. Add to it when something costs you an
hour.

**Two lessons found here were general, so they live where every project sees
them, not in this file:**

* **A check is only as strong as what it actually compares** — the
  narrower-than-its-message defect shape, with the four instances this repo
  produced: estate `AGENTS.md`.
* **Read the exit status from the job, never the wrapper**: estate `AGENTS.md`.
* **Why coverage, mutation and canary catch disjoint defect classes**, and why
  some surviving mutants are a defect in the code's *shape* rather than a gap in
  the tests: `assay/docs/DESIGN-GUIDE.md` §3a, with the how-to in
  `assay/docs/CONSUMERS.md`.

What follows is cmru-specific.

---

## 1. cmru gates itself with a mutation campaign — run it before you believe you are done

cmru's own gate is **100% statement and branch coverage, a changed-source
mutation campaign, and a cause-sensitive coverage canary**. Only the first is
cheap, and it is the weakest. A change set here reached a passing suite at 100%
statement *and* branch coverage while the campaign found **six surviving
mutants** — one in the single line deciding whether a pinned version was stale.

```bash
cd <repo root>
export CMRU_WHEEL_BUILDER_IMAGE=wheel-builder:local \
       CMRU_TESTER_UNIFIED_IMAGE=tester-unified:local \
       CMRU_TESTER_MEMORY=3g CMRU_TESTER_MEMORY_SWAP=16g CMRU_TESTER_CPUS=1.5 \
       CMRU_TESTER_CGROUP_PROBE_IMAGE=debian:trixie-slim
cmru tester-gate --cwd cmru -- /bin/sh -ec 'mkdir -p .assay && exec /opt/tester-venv/bin/python \
  tools/mutation_campaign.py --assay-zipapp tools/assay/assay-1.0.0.pyz --repo-root .. \
  --project-root . --base origin/main --max-mutants 10000 \
  --evidence .assay/mutation-cmru.json --require-candidates \
  -- /opt/tester-venv/bin/python -m pytest tests -q --cov=src/cmru --cov-branch \
     --cov-fail-under=100 --cov-report=json:coverage.json' > /tmp/mut.log 2>&1
echo "MUTATION_EXIT=$?"
```

It is slow — a container start plus a full pytest run per candidate — so run it
detached. **Read `.assay/mutation-cmru.json` rather than reasoning about a
survivor:** it names every mutant, its operator, and its exact `Op->Op`
description (`GtE->Gt`, `Lt->LtE`), which usually makes the cause obvious in
seconds.

## 2. You cannot dry-run your own unreleased changes

`S-CLI.5` makes `release` fetch `origin/main`, create a worktree at **that exact
remote commit**, and re-exec there. That is deliberate: a release must never
publish from a dirty caller tree.

The consequence is easy to miss and there is no warning for it: **`cmru release
--dry-run` from your branch runs `origin/main`'s cmru, not yours**, and will
report success while exercising none of your changes.

To integration-test unreleased cmru changes, use the read-only verbs, which run
in-process:

```bash
PYTHONPATH=cmru/src python3 -m cmru.cli status       --config ./cmru.orchestration.toml
PYTHONPATH=cmru/src python3 -m cmru.cli dependencies --config ./cmru.orchestration.toml
PYTHONPATH=cmru/src python3 -m cmru.cli tool-deps    --config ./cmru.orchestration.toml
```

and call the guarded plan computation directly — see
`version.detect_changed_projects`'s release-path keyword arguments.

## 3. A gate step copied from `cmru.toml` does not run standalone

The `argv` in a project's `[steps.*]` depends on environment the
**orchestration** layer injects from `cmru.orchestration.toml`'s `[env]`. Nothing
in the step says so, and the error messages name the project's own `cmru.toml`,
which is the wrong file.

Reproducing a step by hand — what you do when a release goes red — therefore
fails **one missing variable at a time**, each costing a container spin-up.
Export the whole `[env]` block first; §1's snippet carries the current set.
Tracked as **KI-17**.
