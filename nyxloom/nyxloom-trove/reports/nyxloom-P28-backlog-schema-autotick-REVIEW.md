# nyxloom-P28-backlog-schema-autotick — REVIEW

Reviewer: independent frontier reviewer (Opus 4.8), fresh session. Date: 2026-07-16.
Branch: `feat/nyxloom-P28-backlog-schema-autotick` @ `b1309be` (+ review-fix `b1307f8`).
Handoff: `nyxloom-trove/handoffs/nyxloom-P28-backlog-schema-autotick.md`.

## Verdict

**APPROVED after review-fixes.** All four oracles are genuinely met, and the
tests are not hollow — I verified each by mutation, not by reading. The design
is sound: the optional HTML-comment header coexists with existing prose with no
migration, and the real `backlog.md` validates clean today.

I fixed three real defects, one of which was P28's own headline failure mode
reintroduced in an edge case (`tick_merged` reporting success while leaving
`status` un-ticked). All three were small and local — none architectural, none
warranting rejection.

Do NOT merge — per role contract, this branch is left for the pipeline.

## Verified git state (not the receipt)

Receipt fields were not trusted. Git state directly:

- `git log main..feat/…` → exactly one implementer commit, `b1309be`.
- The real worktree is `/workspaces/vbpub/.worktrees/feat/nyxloom-P28-…`, **not**
  the `/workspaces/vbpub/nyxloom` path the packet lists (that checkout is on
  `main`). It was **clean** — the packet's "no uncommitted changes" claim is
  confirmed. (The three modified `legacy-workflow-origin/*.md` files in the main
  checkout predate this task and are outside its scope.)
- Scope: `git diff main...HEAD --name-only` → exactly the five files in
  `scope.touch`. **No forbidden file touched** — `reconcile.py`, `daemon.py`,
  `config.py` are all untouched.
  - Caveat for future reviewers: two-dot `git diff main` here *falsely* shows
    `reconcile.py`/`daemon.py`/`config.py` as changed, because the branch was cut
    from `593a585` and `main` has since advanced to `8ccb8ad`. Only the three-dot
    (merge-base) diff answers "what did this branch change".

## Gate — re-run by me, not trusted from a report

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd <worktree>/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
→ EXIT_CODE=0   (473 passed as handed off; 478 passed after my fixes)
```

## Oracle verification (adversarial, by mutation)

Passing tests prove little on their own, so each mechanism was surgically broken
to confirm its tests actually bind to it.

| Mutation | Expected | Result |
|---|---|---|
| M1 `tick_merged` neutered to always no-op | O2/O3 fail | **FAIL** ✅ binds |
| M2 `lint_project` backlog fold removed | O1 fold fails | **FAIL** ✅ binds |
| M3 tick also rewrites a prose line | O3 byte-identity fails | **FAIL** ✅ binds |
| M4 unmatched tick writes spuriously | O4 no-op fails | **FAIL** ✅ binds |
| All mutations reverted | green | 20 passed ✅ |

Additionally verified against the **real** `nyxloom-trove/backlog.md` (not a
fixture): 15 legacy items parse, all default to `status=open`, and `validate()`
returns **zero** findings — O1's "a valid backlog yields zero findings" and the
handoff's no-lossy-migration requirement both hold on the actual file.

## Findings I fixed (commit `b1307f8`, all inside `scope.touch`)

**F1 — `tick_merged` silently left `status` un-ticked, and returned `True`
(correctness; P28's own headline bug).** When a linked item's header carried no
`status=` token, the bare `re.sub(r"\bstatus=\S+", ...)` matched nothing and
silently no-opped, while the sibling `merge_commit` path correctly branched
present/append. The function wrote `merge_commit`, returned `True` claiming
success, and left the item reading `open` after its handoff had merged — exactly
the "`Status:` line lies" problem this package exists to eliminate. Confirmed by
probe before the fix (`status: open`, returned `True`). Both fields now route
through one `_set_field()` helper that rewrites in place when present and appends
when absent. Reachable via any hand-authored header — the exact path by which
items adopt the schema. (Such a header is BLG1-invalid, but lint is advisory and
does not block a merge, so schema validity cannot be assumed at tick time.)

**F2 — the tick reflowed every line ending (O3 violation).** `read_text()`
applies universal-newline translation and `"\n".join(text.splitlines())` wrote LF
back, so a CRLF backlog had **all** of its prose rewritten — a free-prose write,
which O3 explicitly forbids. Note the `keepends` half of the fix is insufficient
alone: the `\r` is destroyed at *read* time. Now reads and writes with
`newline=""` plus `splitlines(keepends=True)`, so every untargeted line survives
byte-for-byte, final-newline state included. Low real-world reach (LF repo), but
it contradicted a named oracle.

**F3 — the "best-effort" hook was unguarded.** The handoff specifies the
`cmd_merge` call as *best-effort*, but it was called bare. Both `append_and_apply`
writes land **before** it, so an unreadable/undecodable `backlog.md` raised after
the task had already durably transitioned to MERGED — the CLI would die with a
traceback, never print the commit, and exit non-zero for a merge that in fact
succeeded. Now catches `(OSError, UnicodeDecodeError)`, warns to stderr, and still
returns the commit. (`UnicodeDecodeError` subclasses `ValueError`, not `OSError`,
so both are needed.)

**F4 — O3's test could pass vacuously (test quality).** Its loop asserted only
that *differing* lines were B3's header; a tick that changed nothing passed it.
Tightened to assert exactly one line changed, and that it went
`status=carved` → `status=merged`. The positive was covered by O2, so this was
latent, not an actual hole.

## Observations (no fix — not defects)

- **`resolve_path(cfg)` instead of the handoff's literal `cfg.backlog`**: a
  *justified* deviation. `ProjectConfig` has no `backlog` field, and adding one
  means touching forbidden `config.py`. The helper hard-codes the same
  `root / "nyxloom-trove"` convention `config.py` itself uses, and the module
  docstring says so explicitly. Correct call.
- **Two items linking the same `carved_handoff` → only the first ticks.** Matches
  the handoff's singular "the item whose `carved_handoff == task_id`". Left as-is.
- **Bullet id vs header id mismatch** (`- **B9 …**` with `id=B10`) is not flagged;
  the header id wins. Out of scope for P28's oracles, but worth a `BLG2` rule if
  P29 starts relying on the bullet/header correspondence.
- Schema's `decisions` pattern `^D-[0-9]+$` matches `decisions.py`'s own
  `D-\d+` heading regex — consistent, no drift.

## Report location

The packet names `topos/handoff/reports/<task>-REVIEW.md`; that path does not
exist in this repo. Filed under the established nyxloom convention alongside
`nyxloom-P25-…-REVIEW.md` and `P24-REVIEW.md` instead.
