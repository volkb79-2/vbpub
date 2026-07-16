# P18 — two-way decision chat: ntfy + UI ↔ a live decision agent

> Tier: sonnet · Date: 2026-07-15 · User feature request: a SEPARATE channel
> for important interaction (decision-needed); the user replies in ntfy (or
> the UI) and is patched through to a live conversation with a decision
> agent — ask follow-ups, get repo-grounded answers, finalize the decision.
> This is the sanctioned token-spending surface (discussion is opt-in; the
> read-only dashboard stays zero-AI). Read handoff/STANDING.md.
> DEPENDS-ON P12 (command listener — reuse its topic/loop infra) and the
> decisions module. Queue behind P16/P17 (shared daemon files).

## Concept

A `DecisionAgent` is a resumable claude session PER open decision, launched
by the daemon on first user reply, primed with the decision's DECISIONS-
INBOX entry + its resume prompt, and given READ-ONLY repo tools (Read/Grep/
Glob — NO Edit/Write/Bash-mutation) rooted at the project so it can answer
"get further info" by inspecting the code/specs. The daemon relays: user
message (from the decisions ntfy topic or a UI POST) -> agent turn ->
agent reply posted back to the topic + shown in the UI. On `decide <choice>`
the agent (or the user's explicit command) finalizes via the existing
decisions.decide() path.

## Owned files
- `src/nyxloom/decision_chat.py` (new — the bridge)
- `src/nyxloom/daemon.py` (wire the listener + endpoints; ~30 lines)
- `src/nyxloom/render.py` (decisions page: list OPEN decisions, per-entry
  chat transcript + an answer box)
- `src/nyxloom/config.py` (ONLY: NotifyConfig.decision_topic +
  decision_token_env; Policy.decision_agent_route default 'frontier-review',
  decision_agent_effort default 'high')
- tests: test_decision_chat.py (new) + minimal daemon/render additions.

## Channel + security
- New ntfy topic (config `decision_topic`, e.g. `nyxloom-decisions`) with
  its OWN read+write identity (provision `decision-chat` ntfy user, rw on
  that topic only — separate from the write-only publisher and read-only
  cmd-reader). DECISION_OPENED pushes go here (higher priority) in ADDITION
  to the normal events topic.
- Inbound message routing: a message on decision_topic is `<D-id>: <text>`
  or (if the topic has one active decision) bare text; loop-guard tag
  `decision-agent` on the daemon's own replies (never re-ingest).
- The decision agent's replies are MODEL-authored free text — they are the
  ONE sanctioned exception to the notification injection boundary BECAUSE
  the user explicitly opted into a conversation. Still: (a) pass replies
  through cfg.redact() before posting (no secret leakage even here);
  (b) the agent runs read-only (no Edit/Write/Bash) so it cannot act on
  injected instructions in repo content; (c) cap reply length.

## Behavior
1. DECISION_OPENED -> push to decision_topic: title, D-id, the question
   (from the inbox entry — typed field, safe), and "reply here to discuss".
2. First user reply for a D-id -> daemon launches a DecisionAgent (claude,
   Policy.decision_agent_route/effort, cwd = project root, read-only tool
   allowlist, `--append-system-prompt` = the inbox entry + its resume prompt
   + "You are discussing a product decision with the operator over a chat
   bridge. Answer concisely; you may Read/Grep the repo for facts. When the
   operator states a decision, end your reply with a line
   `DECISION: <choice> — <one-line rationale>` and nothing after."). Session
   id captured (stream-json first line) and stored on a DecisionChat record
   under the state dir.
3. Each subsequent reply -> resume that session with the user's message ->
   capture the agent's reply text -> redact -> post to decision_topic tagged
   `decision-agent` -> append to the DecisionChat transcript (persisted).
4. When an agent reply contains a `DECISION:` line OR the user sends
   `decide <D-id> <choice>`: call decisions.decide(), append
   DECISION_RESOLVED, release depends_on holds, post a confirmation.
5. UI (decisions.html): list OPEN decisions; each shows the question, the
   running transcript (redacted, html-escaped, textContent), and an answer
   box (POST /api/decision/reply {decision_id, text}) that drives the SAME
   bridge; plus a one-click "resolve" with the chosen option.

## Oracles
1. Bridge unit (fake claude via a stubbed adapters.build_dispatch/resume
   returning canned agent text): DECISION_OPENED -> push to decision_topic;
   first reply -> DecisionAgent launched, session captured; second reply ->
   session RESUMED (not relaunched), reply posted tagged decision-agent and
   NOT re-ingested (loop guard).
2. Finalize: an agent reply with `DECISION: option-b — ...` -> decisions
   .decide called, DECISION_RESOLVED event, depends_on holds released.
3. Injection/redaction: agent reply containing a secret-shaped string ->
   redacted before posting; agent tool allowlist excludes Edit/Write/Bash
   (assert the dispatch argv / permission set).
4. UI: decisions.html lists an OPEN decision with its transcript
   (html-escaped, no innerHTML); POST /api/decision/reply drives the bridge;
   unknown decision -> 404.
5. Full suite green.

## Rules
STANDING.md applies. Reuse P12's ntfy poll/reply transport (do not
re-implement). Do not commit. REPORT to handoff/reports/P18-REPORT.md;
receipt-only final message.
