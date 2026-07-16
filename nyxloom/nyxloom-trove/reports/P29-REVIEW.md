# P29-REVIEW — independent frontier review (merge gate)

Reviewer: Opus 4.8, fresh session. Single-task packet.
Date: 2026-07-16. Commit reviewed: `e472598`.

## Verdict

**APPROVED after review-fixes.**

The package is well-built: a faithful, deliberately re-authored sibling of
`decision_chat.py` with the read-only+redacted posture intact, clean
persistence, and a genuinely structured brief. All four oracles hold *as
written*.

But two real defects sat underneath the oracles, both of which I reproduced
and fixed on the branch:

1. **The operator's feature request never reached the model on turn 1** — the
   agent was asked to interview about a request it was never shown. This is
   the feature's core input.
2. **A `BRIEF:`/`PRODUCT_CALL:` block past 1200 chars was silently discarded** —
   the interview's entire output, lost with no error, in exactly the shape a
   real finalize turn takes.

Neither is architectural: both are wiring omissions inside `intake_chat.py`,
fixed in ~6 lines plus regression tests. The design did not need to change,
which is why this is a fix-and-approve rather than a rejection.

## Git state (verified, not taken from the receipt)

- `git log main..feat/nyxloom-P29-intake-agent-backend` → exactly one commit,
  `e472598`.
- Worktree `/workspaces/vbpub/.worktrees/feat/nyxloom-P29-intake-agent-backend`
  → `git status --porcelain` empty before my fixes. The packet's "no
  uncommitted changes" claim is accurate.
- Files touched: the four in `scope.touch` plus `backlog_items.py` (see F1).
  **No forbidden file** (`reconcile.py` / `daemon.py` / `decision_chat.py`) is
  in the commit — verified by `git diff --name-only`, not by reading the
  report.

## Gate (re-run by me)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd .../feat/nyxloom-P29-intake-agent-backend/nyxloom && \
           PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

- As committed: **exit 0**, 493 passed.
- After my review-fixes (+4 tests): **exit 0**, 497 passed.

(This suite's pytest config suppresses the summary line; I assert on the
process exit code and the dot count, not on pasted output.)

## Defect D1 (fixed) — the intake agent never saw the request

**The bug.** `advance_intake`'s first turn built the argv as
`build_dispatch(...) + --append-system-prompt <prompt> + READONLY_ARGV_SUFFIX`.
`user_text` appears in *none* of them. It is passed only on the **resume**
path (`build_resume(..., prompt=user_text)`). So on turn 1 the model received
a system prompt instructing it to "(1) confirm your understanding of the
request back to the operator" — with the request nowhere in its context.

**Why nothing caught it.** The oracle O1 only demands the prompt reference the
context sources and be read-only; the tests assert on canned stub replies, so a
model that never saw the request still "replies" fine. This is the exact hollow
spot adversarial review exists to find: the tests are green and the feature is
inert.

**Why this is worse than the template.** `decision_chat` drops first-turn
`user_text` too, but harmlessly: its subject is a `D-` entry it loads via
`_find_decision` and bakes into the system prompt from typed fields. **Intake
has no such on-disk source** — the request exists *only* as `user_text`. The
inherited shape was safe in the parent and load-bearing in the child.

**Reproduction (before the fix).** Recording the real argv the subprocess was
invoked with:

```
AssertionError: operator's opening request NEVER reached the model on turn 1
assert 'I want a dark mode toggle with per-user persistence' in
  "--append-system-prompt You are conducting a feature-intake interview (PROBE-1)
   ... and nothing after. --allowedTools Read Grep Glob --disallowedTools Edit Write Bash"
```

**The fix.** Thread `user_text` into `_first_turn_system_prompt(...)` as a
verbatim `The operator's request, verbatim:` section. This follows the
template's own precedent: `decision_chat` likewise carries its subject
(`question`/`resume_prompt`) in via `--append-system-prompt`, for the same
stated reason — `build_dispatch`'s frozen contract has no free-prose prompt
parameter. No new seam, no forbidden import.

**Not an injection-boundary breach.** SPEC §13's typed-fields-only rule governs
`notify.notification_for` (what nyxloom *pushes* to a channel). This is the
operator's own message going *into* their own session — not model/log prose
being reflected outward. The read-only+redacted posture is untouched.

## Defect D2 (fixed) — the reply cap silently ate the finalize block

**The bug.** `reply = cfg.redact(reply_raw)[:MAX_REPLY_CHARS]` (1200), and then
**both** `_parse_product_calls(reply)` and `_parse_brief(reply)` ran against the
*truncated* text. The `BRIEF:` block is by construction at the **end** of a long
recap turn — precisely where the cap bites.

**Reproduction (before the fix).** A finalize turn with a realistic recap
preamble, 1354 chars:

```
[reply is 1354 chars; MAX_REPLY_CHARS=1200]
[brief_id=None  backlog items=[]]
AssertionError: BRIEF silently LOST: reply truncated before parsing
```

The whole interview's product — the brief — is discarded. No exception, no
log line, `brief_id` stays `None`, backlog empty. The operator is told the
conversation happened and gets nothing. The same hazard applied to
`PRODUCT_CALL:`, i.e. a product call could be swallowed and its `D-NNN` never
filed — which is O3's stated negative ("product calls are silently baked into
the brief prose with no D-NNN record") arriving by a different road.

**Why the cap is here at all.** It is inherited cargo. In `decision_chat` the
cap is an **ntfy message-length bound on text that module POSTS**
(`_post_feedback`). `intake_chat` imports no `notify` and posts nothing — the
cap has no transport rationale here; it is purely a storage/echo bound.

**The fix.** Redact first (the security invariant is unchanged), parse the
**full** redacted reply, and cap only what is stored in the transcript and
returned. I deliberately did **not** raise `MAX_REPLY_CHARS`: any fixed cap can
be exceeded, so gating persistence on it is the defect — not its value. The
brief's Detail body now persists in full to the backlog regardless of the cap,
which is what O2 actually wants.

## Review-fixes committed (by me, on the feat/ branch)

- `src/nyxloom/intake_chat.py`: D1 + D2 (~6 lines, both commented with the
  constraint that motivates them).
- `tests/test_intake_chat.py`: 4 tests.
  - `test_first_turn_launches_...` extended: asserts the request is in the argv.
  - `test_first_turn_request_survives_shell_metacharacters` (new): quotes/`$`/
    newlines reach the agent verbatim as one argv element.
  - `test_brief_past_the_reply_cap_still_finalizes` (new).
  - `test_product_call_past_the_reply_cap_still_files_decision` (new).

**Anti-hollow check on my own tests.** I stashed the source fix and re-ran them
against the original code — all four fail:

```
FAILED test_first_turn_launches_readonly_redacted_session_with_context_refs
FAILED test_first_turn_request_survives_shell_metacharacters
FAILED test_brief_past_the_reply_cap_still_finalizes
FAILED test_product_call_past_the_reply_cap_still_files_decision
```

They fail on the real bugs and pass with the fix. Fix restored; gate re-run
green.

## Oracle verification

**O1 — read-only + redacted, context-seeded first turn. Holds.**
`READONLY_ARGV_SUFFIX` is byte-identical to `decision_chat`'s and appended
**unconditionally after** whatever the route's `dispatch_extra` contributes, so
the posture never depends on `routes.toml` being right. `cfg.redact()` is
applied before anything is stored or returned — I confirmed with a live
`sk-…` token that it is absent from both the reply and the persisted
transcript. The turn log keeps raw pre-redaction output, same as the sibling.
`decision_chat.py` is genuinely **mirrored, not imported** — no import, no edit.
The O1 negative (write/exec tools or unredacted context) does not occur.

**O2 — `BRIEF:` → structured backlog item. Holds** (and is now reachable at
realistic lengths, per D2). I checked `create()` beyond the tests, since P28's
own history (`B16 id collisions`) makes id allocation the sharp edge:

```
- **B1 — Add dark mode.** Purpose: eye strain.
  Consequences: CSS swap.
  <!-- nyxloom:backlog id=B1 status=open priority=2 decisions=D-001,D-002 -->
- **B2 — Second item.** body
  <!-- nyxloom:backlog id=B2 status=open -->

ids: [('B1','open',2,['D-001','D-002']), ('B2','open',None,[])]
BLG1 lint findings: []
```

Ids increment correctly and the emitted headers pass P28's *own* schema lint —
the item is structured in the sense P28 means, not merely well-shaped prose.
**No id-collision bug:** `_ITEM_RE` matches un-headered items too, so `create()`
counts today's entire un-headered backlog when allocating `B<N>`. Detail prose
is re-indented onto continuation lines, so a `- **B…`-looking detail line
cannot forge a sibling item.

**O3 — product call → `D-NNN` + brief link. Holds.** `open_decision` allocates
over `parse_inbox`, which matches `OPEN|DISCUSSING|DECIDED|DROPPED` — so
resolved entries are counted and a new `D-` cannot collide with a decided one.
File-write only, with the reconcile tick left to emit `DECISION_OPENED`; that
correctly avoids a double-write and keeps `reconcile.py` untouched. The
`opened_decisions` fallback (brief links decisions the model forgot to restate)
is a genuinely good call — it makes O3's negative hard to reach even when the
model misbehaves.

**O4 — `intake` CLI verb. Holds.** Launch-then-resume proven through
`cli.main(...)` with `build_dispatch`/`build_resume` call counts, not mocked
internals.

## Findings (non-blocking)

### F1 — `backlog_items.py` is touched but absent from `scope.touch`

The handoff's *prose* explicitly orders it ("Add a `create(...)` if P28 only
exposed parse/validate/tick"), P28 indeed had no `create()` (verified against
`main`), and the file is not in `forbid`. The implementer did exactly what was
asked; the handoff's own `scope.touch` list is inconsistent with its Work
section. Recording it so the omission is not read as scope drift — **no fault
of the package**. Worth tightening in future carves.

### F2 — `Decisions:` inside the Detail body would be mis-parsed

`_parse_brief` applies the `Priority:`/`Decisions:` field regexes to *every*
line after `BRIEF:`, including lines inside the `Detail:` body. A detail line
beginning `Decisions: we should also...` would be captured as decision ids and
comma-split into junk, which then lands in the header as
`decisions=we should also...` — and `_FIELD_RE` (`(\w+)=(\S+)`) would clip it to
`decisions=we`. Low probability (the prescribed format puts `Detail:` last, and
`Priority:` is digits-anchored so prose cannot trip it) and it corrupts one
link field rather than losing the brief. Left alone: fixing it properly means
stopping field-scanning at `Detail:`, which is a parser change beyond a
review-fix. Backlog-worthy.

### F3 — Detail paragraph breaks are flattened

`_parse_brief` skips blank lines, so a multi-paragraph Detail body persists
single-spaced. Cosmetic; the prose survives.

### F4 — Long requests are not length-checked on the first turn

`build_dispatch` enforces `argv_max` on *its own* prompt, but the appended
`--append-system-prompt` (now carrying `user_text`, per D1) is not checked
against it. A pathologically long paste could hit the OS argv limit (~2MB) and
surface as an `OSError` from `_run_subprocess_turn` — which is caught and
written to the turn log, so it degrades loudly rather than silently. Same
characteristic `decision_chat` already has with `resume_prompt`. Not worth a
cap today; noted so it is a known edge, not a surprise.

## Reasoning for the verdict

The architecture is right, and that is what decides fix-vs-reject. The module is
a real sibling rather than a fork: the security posture is reproduced exactly
and independently, `decision_chat.py` is neither imported nor edited, no
forbidden file is touched, and the escalation rule was not needed because the
mirror genuinely worked. Persistence, id allocation, and the decision-link
fallback all survive checks harder than the ones the tests make — in particular
both id-allocation paths are collision-safe against un-headered and
already-resolved entries, which is where this project has been bitten before.

The two defects I found are exactly the kind that green tests hide: both live in
the gap between "the stub replied" and "a real model, mid-interview, would have
worked". Both were reachable in one edit each, with no design change, so the
contract says fix them, and I did — with regression tests that I verified fail
against the original code.

**APPROVED.** Do not merge on my authority; this review is advisory to the merge
step. The implementer's LOG/REPORT is still absent from the branch (I am not
permitted to write it), so F1–F4 are recorded here.
