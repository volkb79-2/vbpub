# nyxloom-P30-intake-ui-tab — REVIEW

Reviewer: independent frontier reviewer (Opus 4.8), fresh session. Date: 2026-07-16.
Branch: `feat/nyxloom-P30-intake-ui-tab` @ `6ac4859` (+ review-fix `bf80f06`).
Handoff: `nyxloom-trove/handoffs/nyxloom-P30-intake-ui-tab.md`.

## Verdict

**APPROVED after review-fix.** Both oracles are met, the design is the one the
handoff asked for (a single guarded loopback-only POST route joining the
existing `_CONFIG_POST_PATHS` set — no rework of the read-only server, so the
BLOCKED rule correctly did not fire), and the tests are real rather than hollow:
the `advance_intake` stub is monkeypatched on the module object the daemon
resolves at call time, so it genuinely intercepts, and the signature I checked
against P29 matches positionally.

I found and fixed one real defect — the endpoint trusted a client-supplied
`intake_id`, which is exactly O2's `negative` ("trusts input"). It was small and
local (a format check at the trust boundary), not architectural, so per the role
contract I fixed it rather than rejecting.

Do NOT merge — per role contract, this branch is left for the pipeline.

## Verified git state (not the receipt)

Receipt fields were not trusted. Git state directly:

- `git log main..feat/nyxloom-P30-intake-ui-tab` → exactly one implementer
  commit, `6ac4859`. My review-fix `bf80f06` sits on top.
- The real worktree is `/workspaces/vbpub/.worktrees/feat/nyxloom-P30-intake-ui-tab`,
  **not** the `/workspaces/vbpub/nyxloom` path the packet lists (that checkout is
  on `main`). Same packet inaccuracy P28's reviewer recorded — worth fixing in
  the packet generator.
- It was **clean** — the packet's "no uncommitted changes" claim is confirmed.
  (The modified `legacy-workflow-origin/*.md` files in the main checkout predate
  this task and are outside its scope.)
- Scope: `git diff main...HEAD --name-only` → exactly the three files in
  `scope.touch` (`daemon.py`, `render.py`, `tests/test_intake_ui.py`). Forbidden
  `reconcile.py` untouched; `intake_chat.py` (P29's) untouched.

## Gate — re-run by me, not trusted from a report

`tester-unified`, run by me in the container against the branch worktree:

- at `6ac4859` (as handed off): **exit 0**, 513 passed.
- at `bf80f06` (after my fix): **exit 0**, 518 passed (+5 from my regression
  test); `tests/test_intake_ui.py` alone: 11 passed.

## Findings

### F1 — `intake_id` was trusted: path traversal + stored XSS (FIXED, `bf80f06`)

`_post_intake` validated `intake_id` only as "is a non-empty `str`". `/api/intake`
is the one route on this surface that lets a caller **name the record it writes** —
every other id either must already exist (P18's `decision_id` goes through
`decision_chat.find_project_for_decision`, which 404s unless the id is an already-OPEN
decision) or is minted server-side. So this is a genuinely new exposure, not an
inherited one. Unconstrained, the id reached two sinks:

**1. A filesystem path.** `advance_intake` → `_chat_path(project, intake_id)` =
`_chat_dir / f"{intake_id}.json"`, plus a turn-log dir `_chat_dir / intake_id /`.
I reproduced this **against the real `advance_intake`, not a stub**: the sample
project has no `frontier-review` route (`INTAKE_AGENT_TIER`), so `_pick_route`
returns `None` and `advance_intake` early-returns via `save_chat(chat)` **without
spawning any subprocess**. POSTing `intake_id="../../../../pwned-outside"`
returned `200 {"ok": true, ...}` and wrote `pwned-outside.json` four levels above
`intake_chats/`.

**2. An `onclick=` JS string literal in `intake.html`.** `html.escape()` is the
wrong escaper for that context — it emits `&#x27;`, and the HTML parser decodes
character references in attribute values *before* the event-handler body is
compiled as JS. Verified with `html.parser`, not assumed:

```
rendered : onclick="sendIntakeReply('demo', 'x&#x27;);alert(1);(&#x27;')"
JS engine: sendIntakeReply('demo', 'x');alert(1);('')     ← alert(1) executes
```

`x');alert(1);('` needs no `/`, so it is a legal filename, survives `save_chat`,
and comes straight back out of render's `glob("*.json")` stem listing → stored XSS.

**Fix:** constrain the id at the trust boundary to exactly what `new_id("intake")`
emits (`_INTAKE_ID_RE = re.compile(r"intake-[0-9a-f]{12}")`, `fullmatch`). Omitting
`intake_id` to open a fresh conversation is unchanged. Both sinks close, and the
repro now returns 400. Added a parametrized regression test asserting 400 **and**
that a rejected id never reaches `advance_intake`.

I deliberately did **not** re-escape the `onclick` idiom in `render.py`: it is
pre-existing house style (P15 `savePolicy`/`setPauseMode`, P18 `sendDecisionReply`
— lines 1386–1578), and with the id constrained, every value flowing into it is
now server-minted or config-constrained. Rewriting the shared idiom is a
surface-wide change, not P30's job.

### F2 — mitigating context on severity (not a blocker)

The server binds `("127.0.0.1", port)` (`daemon.py:1863`), so this is loopback-only
and P30 did not broaden exposure — O2's loopback requirement holds. F1 therefore
needs local access or a browser-borne vector to reach, which is why I treated it
as a fix-not-reject. It is still a real contract violation: the handoff names
"input treated as untrusted" as the oracle, and F1's traversal wrote outside the
project state dir with a `200 ok`.

### F3 — pre-existing, surface-wide: POST body ignores `Content-Type` (NOT P30's; recommend D-decision)

`_read_json_body` (`daemon.py:2002`) parses the body as JSON without checking
`Content-Type`. A cross-origin `<form enctype="text/plain">` can therefore send a
body that parses as JSON as a CORS *simple* request — no preflight — giving CSRF
against the loopback surface. This affects **all five** POST routes (P15's three,
P18's one, P30's one); P30 merely joined the existing pattern.

Out of scope for P30, and the handoff's `escalate_if` explicitly routes
server-surface changes to a `D-NNN`. **Recommend raising a D-decision** to require
`Content-Type: application/json` (and/or an `Origin`/`Sec-Fetch-Site` check) across
`_CONFIG_POST_PATHS`. Flagging, not fixing.

## Oracle-by-oracle

**O1 — Intake tab renders (list + start form).** MET. `_render_intake` writes
`intake.html`; `NAV` gains the link; the tab lists open conversations
(`brief_id is None`) with transcript + reply box, and a per-project start form.
`test_intake_html_has_start_form_and_open_conversation` asserts the tab, the
`<textarea>` form, the listed conversation, and — good instinct — that
`<b>add dark mode</b>` is HTML-escaped. `test_intake_html_omits_finalized_conversation`
covers the negative. Not hollow: I confirmed the render path really round-trips
through P29's `save_chat`/`load_chat` (no stubbing there).

**O2 — Guarded loopback POST calling `advance_intake`, echoing the reply.** MET
(after F1's fix). `_post_intake` is the single new route, added to the POST-only
set so GET → 405 (asserted). It calls `intake_chat.advance_intake(cfg, project,
intake_id, text.strip())` — signature verified against P29's real definition — and
echoes `{"ok", "intake_id", "reply"}`. No shell, no eval, no dynamic dispatch;
`advance_intake` redacts via `cfg.redact` before storing/returning. The happy-path
test drives a real HTTP round-trip on an ephemeral port and asserts a **second
turn reuses the same `intake_id`**, which is the part that actually proves "advances
a turn" rather than "responds 200".

## Minor notes (not defects, not fixed)

- `render.py` duplicates P29's `_chat_dir` literal (`paths.project_dir(project) /
  "intake_chats"`) rather than reusing it — reasonable, since `_chat_dir` is
  private and `intake_chat.py` was off-limits, but it is a coupling that will rot
  if P29 ever moves the directory.
- The `intake_id` validator ideally lives next to `new_id` in `types.py`, but
  `types.py` is outside `scope.touch`, so I kept it in `daemon.py` — which is
  defensible anyway, as that is the trust boundary. Worth consolidating if a
  second caller ever needs it.
- `_post_intake` returns `repr(exc)[:200]` on failure. This is unredacted internal
  text, but it mirrors P18's `_post_decision_reply` exactly, so I left it as house
  style rather than diverging in one route. Fold into F3's D-decision if the surface
  is revisited.
- `_render_intake` renders the filename stem as the id while loading the record by
  that stem; a file whose internal `intake_id` disagrees with its name would display
  inconsistently. Not reachable now that ids are constrained.
