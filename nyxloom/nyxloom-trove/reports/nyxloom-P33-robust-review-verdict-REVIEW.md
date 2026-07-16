# nyxloom-P33-robust-review-verdict — INDEPENDENT FRONTIER REVIEW

- Reviewer: independent frontier reviewer (merge gate), 2026-07-16
- Reviewed commit: `9e17a89` (implementer) on `feat/nyxloom-P33-robust-review-verdict`
- Review-fix commit: `3ebb9b4` (mine, this branch)
- Gate: `tester-unified`, re-run by me from the branch's own worktree

VERDICT: APPROVED

(The single machine-readable verdict line above is deliberately the only
line-anchored `VERDICT:` token in this file — the parser this package adds
treats two conflicting verdict lines as ambiguous and fail-safes to rejected,
so every other mention here is inline-code quoted.)

## Summary

The design is right and I am approving it. Deriving the merge decision from the
durable `<task>-REVIEW.md` artifact and fail-safing to rejected is exactly the
correct answer to the P26 rubber-stamp incident, and all four oracles are met by
real (non-hollow) tests. The implementer's fail-safe instinct was also what kept
the two defects I found from being dangerous.

But the package as committed did **not** work in this repo. I found two
independent defects that both broke oracle O2 — "the approval path is preserved"
— in the real nyxloom layout while the O2 test passed. I fixed both on the
branch rather than rejecting, because the architecture is sound and each fix is
surgical and inside the declared scope (`daemon.py` + `tests/test_daemon.py`).

Both defects failed in the *safe* direction (everything rejected, nothing
rubber-stamped), so no bad code could have merged. But their combined live
effect would have been a **total pipeline stall**: no nyxloom task could ever
reach `MERGE_READY` again, including this one.

## Findings

### F1 (major, fixed) — `git show` resolves paths from the repo root, not `-C`

`_parse_review_verdict` ran:

```
git -C <cfg.root> show feat/<task>:<cfg.reports_dir>/<task>-REVIEW.md
```

`git show <rev>:<path>` resolves a **bare** `<path>` from the **git repo root**
and ignores `-C`. But `reports_dir` is relative to `cfg.root`, and `cfg.root` is
not always the repo root. nyxloom self-hosts precisely that way — its own
`nyxloom-trove/nyxloom.toml` says so in a comment:

```
worktree_root = "../.worktrees"   # vbpub is the git repo; nyxloom is a subdir
```

So the real call became `show feat/<task>:nyxloom-trove/reports/...` resolved
against `/workspaces/vbpub`, where that path does not exist (the real one is
`nyxloom/nyxloom-trove/reports/...`). git itself confirms, with the fix in its
own hint:

```
$ git -C /workspaces/vbpub/nyxloom show main:nyxloom-trove/handoffs/nyxloom-P33-robust-review-verdict.md
fatal: path 'nyxloom/nyxloom-trove/handoffs/...' exists, but not 'nyxloom-trove/handoffs/...'
hint: Did you mean 'main:nyxloom/nyxloom-trove/handoffs/...' aka 'main:./nyxloom-trove/handoffs/...'?
```

`returncode != 0` → fail-safe → `"rejected"`. **Every** review, approvals
included, would have been rejected.

Fixed by prefixing the path with `./`, which git reads relative to `-C`. I
verified `./` works in *both* layouts (nested root and `cfg.root == repo root`),
so the existing flat-repo tests keep passing.

### F2 (major, fixed) — the packet's report path was stale, and P33 made it load-bearing

The packet told the reviewer to write `topos/handoff/reports/<task>-REVIEW.md` —
a hardcoded literal matching no nyxloom project. `topos/` does not exist under
the nyxloom root at all; `/workspaces/vbpub/topos` is a *different project*.
nyxloom's actual `reports_dir` is `nyxloom-trove/reports`.

This was harmless while the verdict came from the receipt — nothing read the
file. P33 makes that exact file the merge decision, so the mismatch becomes a
correctness bug: the daemon reads `cfg.reports_dir`, the reviewer is told to
write somewhere else, and the review fail-safes to rejected.

Past reviewers evidently ignored the stale instruction and used the conventional
`nyxloom-trove/reports/` (see the committed `nyxloom-P25-…-REVIEW.md`,
`nyxloom-P28-…-REVIEW.md`). Correct instinct — but a merge gate must not depend
on the reviewer disobeying its own prompt.

Fixed by deriving the packet line from `cfg.reports_dir`, so prompt and parser
cannot drift apart. (I followed the corrected path for this very report.)

### F3 (test gap, fixed) — the fixture could not see either defect

`tests/conftest.py::sample_project` runs `git init` **at** `root`, so
`cfg.root == repo root` in every test, and the O2/O3 tests write the report
straight to `cfg.reports_dir`, bypassing the packet instruction. The two live
failure modes were structurally unreachable, which is why the suite was green.

Added `test_parse_review_verdict_when_project_root_is_a_repo_subdir`, which
builds the real nested layout (project root under the repo root) and covers
approved / rejected / missing. Strengthened the O4 packet test to assert the
packet names `cfg.reports_dir` and no longer carries the stale `topos/` path.

**Both new tests fail on the parent commit `9e17a89`** with exactly the
predicted symptom — `AssertionError: assert 'rejected' == 'approved'` — so they
are genuine regression tests, not decoration.

## Oracle verification

- **O1** (done receipt + rejected report → `REVIEW_REJECTED`, `REVIEW_RECORDED`
  result `rejected`) — met. Reproduces the P26 case faithfully. Verified the
  transition and the payload, not just the state.
- **O2** (approved report → `MERGE_READY`) — met by the test as committed, but
  **broken in the real environment** by F1+F2. Met for real after my fixes;
  the new nested-root test now pins it.
- **O3** (missing / ambiguous → `REVIEW_REJECTED`) — met. Missing, ambiguous
  (conflicting verdict lines), and non-`DONE`-receipt defense-in-depth are all
  covered.
- **O4** (packet carries the machine-readable verdict instruction) — met, and
  now additionally pins that the packet points at the file the daemon reads.

## Things I checked and found correct

- `re` and `subprocess` are already imported in `daemon.py`; no import bug.
- The verdict regex is well-chosen: `^\s*VERDICT:\s*(APPROVED|REJECTED)\b` with
  `MULTILINE` will not match a backtick-quoted mention, so prose discussing the
  contract does not poison the parse. Set-based comparison (`== {"APPROVED"}`)
  makes conflicting lines ambiguous → rejected, which is the right default.
- Branch derivation `feat/{task_id}` is consistent with every other use in
  `daemon.py`; branch refs are shared across worktrees, so the reviewer's commit
  is visible from `cfg.root`.
- The `REVIEW_RECORDED` payload semantics change (`done`/`blocked` →
  `approved`/`rejected`) breaks no consumer: the event schema leaves `payload`
  unconstrained (`additionalProperties: true`); `daemon.py:882` matches
  `result == "rejected"` and, as the handoff predicted, **finally works** for
  the first time; `_recent_review_follow_ups` passes the string through to the
  carve packet as informational text, where the new values read better.
- Non-`DONE` receipts short-circuit to rejected without reading the report —
  correct, and keeps the `BLOCKED:` signal as defense-in-depth per the handoff.
- Scope respected: only `daemon.py` + `tests/test_daemon.py` on the whole
  branch. `wrapper.py`, `reconcile.py`, `config.py` untouched. My fixes stay
  inside the same two files, so no `escalate_if` trigger fired.

## Gate evidence (re-run by me, not pasted from a report)

Run from the branch's own worktree, in the declared `tester-unified` container:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd /workspaces/vbpub/.worktrees/feat/nyxloom-P33-robust-review-verdict/nyxloom \
           && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

- at `9e17a89` (as committed by the implementer): green, 472 passed.
- at `3ebb9b4` (with my review fixes): green, 473 passed.

The first run is the point: the suite was green while the feature could not
work here at all.

## Follow-up worth carving (not blocking)

`_parse_review_verdict` is now correct, but the underlying trap is general —
`cfg.root` is not the git repo root, and any *future* `git show <rev>:<path>`
against `cfg.root` will silently resolve from the wrong place. This is the same
family as backlog `B15` (lint wrong-root). Worth a small helper (e.g. a
`_git_show(cfg, rev, rel_path)` that owns the `./` prefix) plus a nested-root
fixture in `conftest.py` so the self-hosted layout is exercised by default
rather than by a single bespoke test. Out of scope here; `conftest.py` is marked
FROZEN and this package's scope is two files.

## Verdict reasoning

The core contract is delivered and the fail-safe direction is right. The two
defects I found were real and would have stalled the pipeline completely, but
they are path-resolution and stale-string bugs, not architectural ones — the
parse/fail-safe design needed no change — so per the review contract they are
small defects, fixed on the branch rather than grounds for rejection. All four
oracles now hold in the real environment, verified against the nested layout
this project actually uses, with regression tests that fail without the fixes.
