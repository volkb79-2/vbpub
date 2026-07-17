# nyxloom-P41-direct-carve-from-brief — Independent Frontier Review

**Verdict: APPROVE** (one oracle-level defect found and fixed by the reviewer on this branch)
**Reviewer:** independent frontier review (merge gate) · **Date:** 2026-07-16
**Branch:** `feat/nyxloom-P41-direct-carve-from-brief` · **Reviewed at:** `bb72b06`

## Git state (verified directly — receipts not trusted)

- `git log main..feat/nyxloom-P41-direct-carve-from-brief` → 1 commit, `bb72b06`
  ("carve(nyxloom): P41 seed the carver with an intake brief (direct carve)").
- Worktree at `/workspaces/vbpub/.worktrees/feat/nyxloom-P41-direct-carve-from-brief`
  clean at branch tip; no uncommitted implementer work (packet's UNCOMMITTED
  section agreed). The four `M` paths in the *main* checkout
  (`legacy-workflow-origin/*.md`, `nyxloom-trove/backlog.md`) predate this
  attempt and are unrelated to P41.
- Diff stat matches the packet exactly: 4 files, +398/−32.
- **Scope compliant:** the diff touches exactly the four `scope.touch` files.
  Neither forbidden file (`wrapper.py`, `intake_chat.py`) is modified —
  verified by path-scoped diff, empty. (This review additionally *imports*
  `intake_chat` in the test file; importing is not editing, and the test file
  is in scope.)

## Gate — re-run by the reviewer, not trusted from the report

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd <worktree>/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

- At `bb72b06` (as handed off): **exit 0**, 570 tests collected, all passing.
- At review tip (after my fix): **exit 0**, 575 tests collected, all passing.
  `test_carve_from_brief.py` goes 7 → 12 tests.

## What the implementer got right

The architecture is sound and the two things the rejected P31 was rejected for
are genuinely fixed:

- `is_briefed(item)` correctly gates on **header present AND non-empty detail**,
  not raw detail. The "inverted detail extraction" is gone: `_extract_detail`
  cuts at the header offset, and an un-headered legacy bullet — however much
  body prose it carries — is never treated as briefed. I verified the parser
  against `create()`'s real output format rather than taking the docstring's
  word for it.
- **O2 is not a stub.** `dispatch_targeted_carve` builds a real
  `reconcile.CarveDispatch(item_id=...)` and executes it through the *same*
  `_execute_carve_dispatch` the untargeted headroom trigger uses (verified by
  reading the call path, not the docstring). Its cfg/states setup mirrors
  `run_pass` exactly. Seq allocation, carve authority, worktree creation,
  route snapshotting and the `carve-no-route` defense-in-depth check are all
  inherited rather than reimplemented — which is the right call.
- The `item_id=None` default keeps the untargeted path byte-identical, and the
  implementer wrote a regression test pinning that.

## Finding 1 — the brief was only half-delivered (fixed)

**Severity: oracle-level (O1). Fixed on this branch, not rejected — see reasoning.**

O1 requires the brief's pre-carve detail — *"aligned purpose, elicited detail,
**linked D-NNN, priority**"* — to reach the carver. The implementation embedded
`item.detail` **only**. Two of the four enumerated components were silently
dropped.

The reason is a real seam between P29 and P41 that the implementer missed.
`intake_chat._parse_brief` splits a P29 reply into `title` / `priority` /
`decisions` / free prose, and **strips `Priority:` and `Decisions:` lines out of
the prose** (`continue` at `intake_chat.py:301-308`). `backlog_items.create()`
then persists those two as **header tokens**, leaving only the prose on the
bullet's continuation lines. So `item.detail` *provably never contains them*.

I confirmed this empirically rather than by reading, driving the real parser and
the real `create()`:

```
parsed.priority  = 2
parsed.decisions = ['D-042', 'D-043']
parsed.detail    = 'Aligned purpose is...\nElicited detail: ...\nConsequence: ...'

- **B1 — widget cache frobnicator.** Aligned purpose is to frobnicate the widget cache.
  Elicited detail: needs oauth2 refresh token rotation.
  Consequence: without it the cache stales after 1h.
  <!-- nyxloom:backlog id=B1 status=open priority=2 decisions=D-042,D-043 -->

D-042 present in what the carver sees?  False
D-043 present in what the carver sees?  False
priority present in what the carver sees? False
```

Net effect on the real product path: a direct carve dropped **the priority the
interview explicitly asked the operator for** (intake step 6) and **the D-NNN
decisions the intake agent filed on the operator's behalf** (step 4 — whose
whole stated purpose per `intake_chat`'s module docstring is *"so the eventual
brief can link it"*). That is precisely the context loss P41 exists to close.

**Why the existing test did not catch it — a hollow assertion.** The O1 test
hand-built its detail as `f"{ALIGNED_PURPOSE}\n{ELICITED_DETAIL}\nLinked D-042."`
— stuffing the D-NNN link into the **prose**, a shape `_parse_brief` provably
never produces — and then asserted `"D-042" in joined`. That assertion passed
because the test put the string in the wrong field, not because the linked
decision reached the carver. It would have passed identically had the header
fields been dropped entirely (they were). The test also passed
`priority=2, decisions=["D-042"]` to `create()` and then never asserted either
one reached the notes.

**Fix applied** (`daemon._targeted_item_note_lines`): emit the header-borne
`priority` and `linked decisions` alongside the detail prose. Decisions are
**named, not slurped** — the carver reads `decisions.md` itself, matching the
"point, don't slurp" economy the surrounding module already documents and
follows.

**Test fixes applied:**
- Removed `"Linked D-042."` from the detail prose in both the O1 and O2 tests,
  so `D-042`/`priority: 2` can now only be satisfied *from the header tokens*.
- Added `test_real_p29_brief_round_trips_priority_and_decisions_to_the_carver`:
  drives the **real** `intake_chat._parse_brief` → `create()` →
  `_carve_source_note_lines` path, and first pins the premise
  (`"D-042" not in parsed.detail`) so the test cannot silently rot back into the
  hollow shape.

**Mutation-verified.** I reverted the production fix and re-ran: 3 tests fail
(`..._include_genuinely_briefed_items_detail`, `..._real_p29_brief_round_trips...`,
`..._seeds_only_the_chosen_items_brief`). Restored → green. The new tests
genuinely constrain the behaviour; the original suite did not.

**Why fixed rather than rejected:** the defect is a completeness gap in one
~6-line function, entirely inside `scope.touch`, on top of an architecture that
is otherwise correct and unstubbed. The handoff's own `escalate_if` triggers do
not fire. Per the packet's step 5 ("small defects: fix them YOURSELF"), this is
a fix, not a rejection.

## Finding 2 — `brief_detail` was dead and untested (fixed by adding coverage)

`brief_detail(cfg, item_id)` is required by the handoff (work item 1) and is
implemented correctly, but had **zero call sites and zero tests**.
`_targeted_item_note_lines` re-implements the lookup inline — legitimately, as
it needs the `item` object to distinguish "not found" from "found but not
briefed", a distinction `brief_detail`'s `None` return collapses. So the
duplication stays; I did not force a worse design to eliminate it.

However `daemon.py:1114` **claimed** it used `backlog_items.is_briefed/brief_detail`.
It never called `brief_detail`. Corrected the docstring, and added four tests
covering `brief_detail`'s contract (briefed → detail; unknown → None;
un-headered bullet with body prose → None; headered with empty detail → None).
The un-headered case is the exact P31 rejection, now pinned by a test.

## Finding 3 — housekeeping (fixed)

- `tests/test_carve_from_brief.py` imported `json` and `subprocess`, both unused.
  Removed.
- `dispatch_targeted_carve` was a new **public** method absent from `daemon.py`'s
  module contract, which this repo maintains as the documented public surface
  (P42's "doc component convention", commit `5b59e17`). Added a contract entry
  in the existing house style, recording the deliberate deviations
  (operator-initiated → intentionally skips the headroom trigger conditions;
  retains the frontier-route check) and the header-vs-prose seam from Finding 1
  so the next reader does not re-introduce it.

## Observations — not defects, deliberately not "fixed"

- **Bullet title not embedded.** The packet names the item id but not its
  title text. `BacklogItem` exposes no `title` field, so surfacing it means
  touching the frozen parser contract — disproportionate, and O1 does not
  enumerate title. Flagging, not fixing.
- **No duplicate-carve guard.** `dispatch_targeted_carve` will happily mint a
  second carve leg for an item already in flight. Out of oracle scope and
  arguably correct for an operator-initiated verb.
- **Unknown/un-briefed item still mints a full carve leg.** `dispatch_targeted_carve("p", "B999")`
  creates a synthetic task, a worktree and a launched carver whose packet reads
  "not found". Wasteful, but no oracle requires the guard and adding one changes
  the verb's contract — a caller may legitimately want the leg. Worth a future
  package.
- `_extract_detail` over-captures for un-headered items (it runs to the next
  bullet/EOF, absorbing trailing section headings). Harmless today because
  `is_briefed` gates every consumer on the header, and nothing reads an
  un-headered item's `detail`. Noted as a latent trap for any future consumer.

## Verdict reasoning

Both oracles are met at the review tip, and now for the right reasons rather
than by accident. O1's evidence was overclaimed as handed off — the assertion
that appeared to prove D-NNN continuity proved nothing — and the underlying
behaviour was genuinely half-broken against the real P29 brief shape. That is
now fixed, mutation-verified, and pinned by a test that drives the actual
intake parser rather than a hand-built stand-in. O2 was correctly implemented
through the real carve-dispatch flow from the start. Scope was respected, the
gate is green at 575 tests, and no `escalate_if` condition fires.

VERDICT: APPROVED
