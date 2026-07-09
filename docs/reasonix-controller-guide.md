# Reasonix Controller Guide

This guide records the controller workflow used for `groop` handoff packages.
It is intentionally operational: commands, monitoring habits, merge protocol,
and lessons learned from using Reasonix as an implementation agent.

## Controller Role

The controller should keep the main session for architecture, carving, review,
merge, and evidence. Reasonix agents should do bounded implementation packages
in dedicated worktrees.

Default package flow:

1. Carve or update `groop/handoff/P<NN>-<slug>.md`.
2. Add a Planned row to `groop/README.md` and `groop/docs/ROADMAP.md`.
3. Commit the carve on `main`.
4. Start a Reasonix agent from `main` in `.worktrees/-groop-p<NN>-<slug>`.
5. Let the agent implement, test, write `P<NN>-LOG.md` and `P<NN>-REPORT.md`,
   and commit its feature branch.
6. Review the diff, patch issues in the feature worktree, rerun tests.
7. Merge with `--no-ff`, validate from `main`, record evidence, and commit the
   evidence update.

## Worktree Protocol

Use repo-local worktrees, not `/tmp`, because `.worktrees/` is gitignored and
available to agents without extra host permission surprises.

```bash
git worktree add -b feat/groop-p28-io-cap-saturation \
  .worktrees/-groop-p28-io-cap-saturation main
```

Each agent prompt should state:

- branch name;
- exact worktree path;
- work only inside that worktree;
- touch only `groop/**`;
- read the handoff and required context;
- run focused tests, full suite, and `py_compile`;
- write log/report files;
- commit the feature branch.

## Starting Agents

Fresh implementation agent:

```bash
reasonix run --model deepseek-v4-flash --max-steps 0 \
  'Implement groop P29 ... follow groop/README.md Workflow protocol exactly ...'
```

Continue the latest Reasonix session to reuse cache:

```bash
reasonix run -c --model deepseek-v4-flash --max-steps 0 \
  'Continue with groop P28 ...'
```

Parallel two-stream run, when packages do not overlap:

```bash
reasonix run -c --model deepseek-v4-flash --max-steps 0 --dir /path/to/vbpub \
  'Implement groop P32 ... worktree .worktrees/-groop-p32-daemon-status ...'

reasonix run --model deepseek-v4-flash --max-steps 0 --dir /path/to/vbpub \
  'Implement groop P33 ... worktree .worktrees/-groop-p33-release-smoke ...'
```

Useful help:

```bash
reasonix --help
reasonix run --help
```

Important flags:

- `--model deepseek-v4-flash`: good default implementation model.
- `--max-steps 0`: no artificial step cap.
- `-c` / `--continue`: resume latest saved session and preserve useful cache.
- `--resume PATH`: resume a specific saved session.
- `--copy`: duplicate a resumed session if the original is locked.
- `--metrics PATH`: write token/cache/cost summary JSON if a durable metric
  artifact is useful.
- `--dir PATH`: run from a specific project root.

## Cache Strategy

Reasonix shows token/cache/cost lines while running, for example:

```text
179872 tok · in 179715 (179456 cached / 259 new) · out 157 · ¥0.0042
```

The cached/new split is the key number. Continuing related Groop/TUI packages
with `reasonix run -c` produced high cache reuse and low marginal cost. For a
separate area of work, start a fresh Reasonix session so it builds an
independent cache around that domain.

Practical pattern:

- same stream, similar files: use `run -c`;
- independent stream: use fresh `reasonix run`;
- parallel streams: use separate feature branches and worktrees, then serialize
  merges on `main`.

Recent P32/P33 example:

- P32 continued the daemon-context session with `run -c`; its progress lines
  showed roughly `315k` input tokens with more than `314k` cached by the end.
- P33 used a fresh release-confidence session; it built a separate cache around
  acceptance docs/tests and ended around `104k` tokens with most later input
  cached.
- The controller still reviewed both branches completely. Cache efficiency is a
  cost signal, not a correctness signal.

When durable stats matter, add a metrics artifact:

```bash
reasonix run --metrics .worktrees/reasonix-pNN-metrics.json ...
```

If no metrics file is used, read the terminal progress line:

```text
315444 tok · in 315148 (315008 cached / 140 new) · out 296 · ¥0.0070
```

Interpretation:

- total tokens used by that session line;
- input tokens, split into cached and new;
- output tokens;
- estimated cost shown by Reasonix.

The cached/new split is the most useful controller signal. A high cached count
means continuing that session is cheap for nearby work; a high new-token count
means the task probably belongs in a fresh domain-specific session.

## Monitoring

Prefer sparse monitoring. For long runs, wait for completion or poll every few
minutes:

```bash
# Codex tool-level equivalent:
# poll the running session without writing input
```

Avoid detailed commentary for every agent step. It costs controller context and
usually does not improve the result. Useful updates are:

- agent started and worktree created;
- agent found a real blocker;
- agent finished with commit/test evidence;
- controller found a review issue.

If Reasonix completes real work but gets stuck satisfying its own internal
`complete_step` bookkeeping, stop it after confirming the feature branch is
committed:

```text
Ctrl-C
```

Then continue with controller review.

Logs:

- Every handoff should require `groop/handoff/reports/P<NN>-LOG.md`.
- The log should contain observable actions, commands, files changed, decisions,
  blockers, and next steps.
- Do not trust dates or test counts until the controller verifies them.
- After controller patches, append the controller validation to both `LOG.md`
  and `REPORT.md`.

## Review Checklist

Always review before merging. Reasonix is useful, but not a trusted committer.

Check:

```bash
git -C .worktrees/-groop-pNN-slug status --short --branch
git -C .worktrees/-groop-pNN-slug log --oneline --max-count=5
git -C .worktrees/-groop-pNN-slug diff --stat main...HEAD -- groop
git -C .worktrees/-groop-pNN-slug diff --find-renames main...HEAD -- groop
```

Common issues found:

- wrong dates in logs/reports;
- overclaimed evidence, such as "after merge" before merge;
- missing handoff requirements;
- tests that pass but do not assert the important behavior;
- stale docs after a review patch;
- subtle correctness gaps around edge cases.
- environment-specific claims, such as dependency failures in an agent venv
  that do not reproduce in the controller validation venv.

Patch in the feature worktree, then commit a controller review commit:

```bash
git add groop/...
git commit -m "test(groop): polish PNN <topic> review"
```

## Validation

Use the package venv if available, but force imports from the checkout being
validated:

```bash
PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q
```

Focused examples:

```bash
/tmp/p25-venv/bin/python -m pytest groop/tests/test_io_cap_saturation.py -q
/tmp/p25-venv/bin/python -m py_compile \
  groop/src/groop/collect/cgroup.py \
  groop/src/groop/collect/collector.py
```

After merge, rerun from `main`, record the exact result in
`groop/docs/STATUS.md` and `groop/handoff/reports/P<NN>-LOG.md` /
`P<NN>-REPORT.md`, then commit:

```bash
git merge --no-ff feat/groop-pNN-slug -m "Merge groop PNN <topic>"
PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q
git add groop/docs/STATUS.md groop/handoff/reports/P<NN>-*.md
git commit -m "docs(groop): record PNN merge evidence"
```

## Lessons Learned

- Reasonix is cost-effective when the prompt and handoff are specific.
- `run -c` materially improves cache reuse for a sequence of related packages.
- Separate fresh sessions are useful for independent areas because each builds a
  focused cache.
- It follows the `.worktrees/` protocol reliably when the exact command is in
  the prompt.
- It often writes useful tests, but review must check whether they assert the
  behavioral contract rather than implementation trivia.
- It can struggle with edit conflicts and internal `complete_step` receipts; do
  not spend controller time watching every retry.
- Logs and reports are valuable resumability artifacts, but dates/evidence need
  controller verification.
- Keep handoffs small. Packages with clear files, tests, and explicit out-of-
  scope constraints finish more reliably.
- Parallel agents are feasible only for non-overlapping areas. Merge them one at
  a time and expect minor README/ROADMAP/STATUS reconciliation.
- Two-agent parallelism works best when each stream owns a different context:
  for example daemon CLI work in one continued session and release measurement
  work in a fresh session.
- Reasonix may introduce polished but non-ASCII output in new files; normalize
  to the repo's ASCII default unless the file already has a reason to use
  Unicode.
- Subprocess and UI-import tests need special scrutiny. P33 initially had a
  test that passed alone but failed in the full suite because other UI tests had
  already imported `groop.ui`; controller validation caught it.
- If an agent installs or mutates local tooling to make tests pass, treat that
  as agent-environment evidence only. Re-run in the controller's known venv
  before merge.
