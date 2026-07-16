# P18 Report — decision-chat bridge (ntfy/UI <-> a live decision agent)

**Result:** done
**Date:** 2026-07-16

## Deviations (read this first — reviewer sign-off needed)

### 1. Channel topology — THE headline conflict, resolved per orchestrator directive

The P18 handoff (`nyxloom-trove/handoffs/P18-decision-chat-bridge.md`) describes a
**separate, dedicated** ntfy topic for the escalation<->answer loop: a new
`decision_topic` / `decision_token_env` config pair with its own rw ntfy identity,
"separate from the write-only publisher and read-only cmd-reader."

The **current** project design (`nyxloom-trove/nyxloom.toml` `[notify]`, comment on
`feedback_topic`: *"decisions + escalation Q&A, bidirectional... unifies the old cmd
topic + the decision-chat escalation loop"*) explicitly **unifies** that loop onto the
SAME channel P12 already built for operator chat-ops. Confirmed against the live,
code-consumed config (`/workspaces/vbpub/topos/.nyxloom/project.toml`'s `[notify]`
section still uses the field names `ntfy_topic` / `cmd_topic` — there is no
`decision_topic` field anywhere in the frozen `config.py` dataclass, and `config.py`
is in `STANDING.md`'s frozen-file list for this wave, so one could not be added even
if the handoff's literal text called for it).

**Implemented:** the whole decision-chat loop (DECISION_OPENED push, first-reply
launch, resume turns, finalize, loop-guard) runs over `cfg.notify.cmd_topic` (P12's
existing feedback-channel field: read via `cmd_token_env`, write via `token_env`),
reusing P12's transport by **wrapping** `CommandListener.handle_message`
(`decision_chat.wrap_command_handler`, wired from `daemon.py._start_cmd_listener`)
rather than adding a parallel listener or a new topic/identity. A decision-shaped
message ("`D-id: text`", "`decide D-id choice`", or bare text when exactly one
decision-chat is active) is intercepted before P12's verb allowlist ever sees it;
everything else (including P12's own `nyxloomd-reply` loop-guard tag) falls through
to the original handler untouched.

**Net effect vs. the handoff text:** no dedicated `decision-chat` ntfy identity is
provisioned; there is one feedback topic/identity shared between operator chat-ops
and decision-chat Q&A, exactly as `nyxloom-trove/nyxloom.toml`'s current
`[notify]` design specifies. This is a real behavioral narrowing versus the P18
handoff's literal text and is the item most in need of reviewer sign-off.

### 2. Config knobs not added (config.py frozen this wave)

The handoff also asks for `NotifyConfig.decision_topic`/`decision_token_env` and
`Policy.decision_agent_route`/`decision_agent_effort` (default `'frontier-review'`/
`'high'`) in `config.py`. `config.py` is in `STANDING.md`'s frozen list
(`src/nyxloom/{__init__,types,paths,storage,config,leases}.py`) and was **not**
modified. Instead `decision_chat.py` hardcodes `DECISION_AGENT_TIER =
"frontier-review"` as a module constant — reusing the SAME tier `daemon.py`'s
`LaunchReview` action already dispatches reviews to — and picks the first route in
that tier via `config.Routes.for_tier()`. No `effort` override is applied (routes
carry their own `effort` field already). If per-agent tuning is later wanted,
`config.py`'s freeze needs lifting for a follow-up.

### 3. Dispatch prompt shape

`adapters.build_dispatch`'s frozen contract derives its prompt from
handoff/worktree/branch/gate/receipt fields — no parameter accepts arbitrary prompt
text, so it cannot literally carry the decision's `question`/`resume_prompt`. First
turns still call `build_dispatch` (satisfying "reuse the adapters seam", and the
oracle's own test strategy of stubbing `build_dispatch`/`build_resume`), then layer
the actual decision-priming text via an appended `--append-system-prompt` argument
— a real claude CLI flag, and literally named in the handoff's own Behavior section
— built ONLY from the `Decision` dataclass's typed `question`/`resume_prompt`
fields (never raw inbox prose). Resume turns (2nd+) use `adapters.build_resume`
directly with the user's new message as `prompt`, a perfect fit for that function's
existing contract.

## Summary

New module `src/nyxloom/decision_chat.py` implements the decision-chat bridge:
persistent per-`(project, decision_id)` chat records (session id + redacted
transcript) under `paths.project_dir(project)/"decision_chats"/`; first-turn launch
via `adapters.build_dispatch` + `--append-system-prompt` + a hardcoded read-only
tool policy (`--allowedTools "Read Grep Glob" --disallowedTools "Edit Write Bash"`,
appended unconditionally regardless of routes.toml content); resume turns via
`adapters.build_resume`; reply extraction that is stream-json-aware (skips a
`session_id` preamble line, digs a `result`/`text`/`message` field out of a trailing
JSON line) but degrades to raw text for fake/plain-text CLIs; `DECISION: <choice> -
<note>` detection that calls `decisions.decide()` then appends `DECISION_RESOLVED`
(mirroring `cli.cmd_decide`'s existing sequence) so `depends_on: [D-xxx]` holds are
released on the next reconcile pass; and an explicit `decide <D-id> <choice>` command
shape as an alternative finalize path. All ntfy pushes except the one sanctioned
free-text exception (`_post_feedback`, the agent's own reply — redacted + length
capped) use fixed templates over typed fields only, per the injection boundary.

`daemon.py`: `_start_cmd_listener` now wraps the listener's `handle_message` with
`decision_chat.wrap_command_handler`; `_reconcile_decisions` calls
`decision_chat.notify_decision_opened` right after a `DECISION_OPENED` event
(additional actionable push to the feedback channel, on top of the existing
notifications-channel push); new `POST /api/decision/reply` endpoint (400 missing
fields, 404 unknown decision, 405 on GET, 200 on success) driving the same
`advance_chat` path.

`render.py`: new `decisions.html` page (server-rendered, `html.escape`d throughout —
no client-side innerHTML needed to show the transcript) listing every OPEN/DISCUSSING
decision with its question, chat transcript (if any), and an answer box POSTing to
`/api/decision/reply`; nav link added.

## Files touched

- `src/nyxloom/decision_chat.py` (new)
- `src/nyxloom/daemon.py` (import + `_start_cmd_listener` wrap + `_reconcile_decisions`
  hook + `POST /api/decision/reply` + `_CONFIG_POST_PATHS` entry)
- `src/nyxloom/render.py` (import + `decisions.html` render function + nav link +
  `render_all` wiring)
- `tests/test_decision_chat.py` (new — 13 tests)
- `tests/test_daemon.py` (+2 tests: `/api/decision/reply` endpoint, cmd-listener wrap)
- `tests/test_render.py` (+1 test: `decisions.html` content/escaping)

`config.py`, `commands.py`, `decisions.py`, `adapters.py`, `types.py`, `paths.py`,
`storage.py` were read but **not** modified (see Deviations above for why config.py
and commands.py specifically were left alone despite the handoff naming them).

## Oracle table (P18 handoff's own oracles)

| Oracle | Status | Notes |
|---|---|---|
| 1. First reply launches (build_dispatch, session captured); second reply RESUMES (build_resume, not relaunched); reply tagged `decision-agent`, never re-ingested | PASS | `test_first_reply_launches_agent_and_captures_session`, `test_second_reply_resumes_session_and_finalizes_decision`, `test_loop_guard_ignores_own_tag_and_reply_tag` |
| 2. Finalize: `DECISION: option-b — ...` -> `decisions.decide` called, `DECISION_RESOLVED` event, depends_on holds released | PASS | `test_second_reply_resumes_session_and_finalizes_decision` (decide() + event asserted); hold release itself is a pre-existing `reconcile.py`/`decisions.open_ids` mechanism, not re-tested here (out of P18's owned files) |
| 3. Injection/redaction: secret-shaped reply redacted before posting; tool allowlist excludes Edit/Write/Bash (argv/permission set asserted) | PASS | `test_reply_redacted_before_posting_and_storing`; argv assertion also covered in oracle-1 test via the recorded final argv |
| 4. UI: decisions.html lists an OPEN decision + transcript (html-escaped, no innerHTML); POST /api/decision/reply drives the bridge; unknown decision -> 404 | PASS | `test_decisions_html_lists_open_decision_and_transcript` (render.py), `test_decision_reply_endpoint` (daemon.py: 200/404/400/405) |
| 5. Full suite green | PASS | see Gate output below |

Additional coverage beyond the five named oracles (negative/edge cases per
STANDING's "every bound/negative case gets a test that VIOLATES it"):
`test_no_review_route_configured_degrades_to_typed_reply`,
`test_wrap_command_handler_routes_decision_prefix_and_falls_through` (verb commands
and unknown D-ids fall through untouched), `test_decide_command_finalizes_via_feedback_channel`,
`test_bare_text_routes_only_when_exactly_one_chat_active` (no active chat -> falls
through), `test_notify_decision_opened_uses_typed_fields_only`,
`test_post_feedback_carries_free_text_with_loop_guard_tag`,
`test_find_project_for_decision_unknown_returns_none`,
`test_start_cmd_listener_wraps_handler_for_decision_routing`.

## Gate output (verbatim tail)

Ran exactly the STEP 4 command:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
  'cd /workspaces/vbpub/.worktrees/nyxloom-P18/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

Exit code: `0`. The `-q` summary line was not captured in that run's stdout (a local
capture quirk in this environment, not specific to this change — the identical
truncation also occurred running the plain devcontainer venv with `-q`); re-running
the SAME command with `-v` appended (verbosity only, same test selection, same
container/image, same worktree) reproduces the identical dot-progress output plus a
clean summary line:

```
tests/test_decision_chat.py ...........                                  [ 43%]
tests/test_decisions.py .......................                          [ 48%]
tests/test_doctor.py ................                                    [ 53%]
tests/test_frontmatter.py ...................                            [ 57%]
tests/test_integration.py ..                                             [ 58%]
tests/test_lint.py .....................................                 [ 67%]
tests/test_notify.py .......................                             [ 73%]
tests/test_properties.py .................                               [ 77%]
tests/test_reconcile.py ................................................ [ 89%]
                                                                         [ 89%]
tests/test_render.py .....................                               [ 95%]
tests/test_storage.py .....                                              [ 96%]
tests/test_wrapper.py ..............                                     [100%]

======================== 396 passed in 67.52s (0:01:07) ========================
```

396 passed, 0 failed, both runs exit code 0.

## Suggestions for the reviewer (not acted on)

- Confirm the channel-topology deviation (item 1 above) is the intended reading of
  the 2-channel design — if a genuinely separate decision-chat ntfy identity is
  wanted after all, that requires lifting config.py's freeze for this wave (a new
  `decision_topic`/`decision_token_env` pair) plus provisioning the identity in
  `nyxloom-trove/ntfy/`.
- `_find_sole_active_chat`'s "bare text routes only when exactly one chat is active"
  rule is a reasonable default for a single-operator pilot but will silently drop a
  bare reply once two decisions are being discussed concurrently — worth a UI/ntfy
  hint if that becomes common.
- Consider whether `DECISION_AGENT_TIER = "frontier-review"` sharing the SAME tier
  as `LaunchReview` review dispatch is desired long-term, or whether decision-chat
  should get its own tier once config.py's freeze lifts.
