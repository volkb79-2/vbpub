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

From the repository root, run the project lane:

```bash
./run-gate.py mutation
```

The lane runs the campaign in `tester-unified`, with a 120-second timeout for
each candidate. A timeout is recorded as a killed mutant so an infinite-loop
mutation cannot hold the lane indefinitely. The runner updates
`.assay/mutation-cmru.json` and appends per-candidate events to
`.assay/progress-mutation-cmru.jsonl`; another run resumes from completed killed
results and retries survivors or interrupted candidates. **Read the evidence
file rather than reasoning about a survivor:** it records each mutation's path,
operator, exact `Op->Op` description, outcome, and termination reason.

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

## 3. A gate step copied from `cmru.toml` — export the orchestration `[env]` first

The `argv` in a project's `[steps.*]` depends on environment the
**orchestration** layer injects from `cmru.orchestration.toml`'s `[env]` — not
the project's own `cmru.toml`. So a step copied out by hand — what you do when a
release goes red — needs that `[env]` block exported first; §1's snippet carries
the current set.

**KI-17 is fixed** (SPEC S2.6a): `tester-gate` now validates the whole required
set up front and, if anything is missing, aborts **once, naming every missing
variable together**, before any container spin-up — no more failing one variable
at a time. Each message now names `cmru.orchestration.toml [env]` (inherited
through `cmru release`) as the real source instead of pointing at the wrong
file. Exporting the `[env]` block by hand is still the way to reproduce a step
standalone; the tool just tells you the complete list in one shot now.
