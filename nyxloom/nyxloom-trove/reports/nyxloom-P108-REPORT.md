# nyxloom-P108 — decision-chat pushes were silently dropped after the Mattermost cutover

Date: 2026-09-09
Branch: `nyxloom-P108` (worktree `/workspaces/vbpub/.worktrees/nyxloom-p108`)
Scope: WRITE side of the decision-chat bridge only. The inbound (read) transport
is explicitly out of scope and is NOT migrated here.

## The defect

`src/nyxloom/decision_chat.py` (PACKAGE P18) has two outbound pushes,
`notify_decision_opened` and `_post_feedback`. Each independently built its OWN
`NotifyConfig` hardcoded to the ntfy shape, behind this guard:

```python
if not (cfg.notify.ntfy_url and cfg.notify.cmd_topic):
    return
nc = NotifyConfig(ntfy_url=cfg.notify.ntfy_url, ntfy_topic=cfg.notify.cmd_topic,
                   token_env=cfg.notify.token_env)
```

P106/P107 (NL-17) retired ntfy and gave `notify.py` a `NotifyBackend` seam that
`notify.send()` dispatches over via `resolve_backends(nc)`, selected by an
explicit `NotifyConfig.backend`. `nyxloom-trove/nyxloom.toml` now declares
`[notify] backend = "mattermost"` and no longer points at ntfy. `decision_chat.py`
was never wired onto that seam, so `cfg.notify.ntfy_url` has been `None` since
the cutover and the guard has been permanently true: every "a decision was
opened" push and every decision-agent reply was swallowed — no error, no log
line, no fallback. A regression by omission, not by a wrong branch.

`_post_feedback` matters in practice as well as in principle: `daemon.py:3937`
drives decision-chat turns from the UI (`decisions.html`), so the reply push is
reachable today even with the inbound chat transport down.

## The fix

Both pushes now hand `cfg.notify` straight to `notify.send()` through one shared
`_push()` helper.

The guard was **not** replaced with a smarter guard. "Is any channel configured,
and which one?" is `notify.py`'s question — it owns the backend registry, and
`send()` already no-ops harmlessly (`(False, "unconfigured")`) when
`resolve_backends()` is empty. Re-encoding that question in `decision_chat`
against one backend's field names is exactly the defect being fixed, so a second
copy of it would be a smaller version of the same bug.

### Channel-topology decision (carve step 2) — option (a), reuse `cfg.notify`

Verified before choosing:

* `MattermostBackend.is_configured()` requires only `nc.webhook_url`; the
  incoming-webhook URL **is** the credential (`webhook_url_env`,
  `NYXLOOM_WEBHOOK_URL`).
* `nyxloom/mattermost/ciu.defaults.toml.j2` provisions exactly **two**
  `[[mattermost.provision.webhooks]]`:
  * `daemon_webhook_url` → channel `alerts`, user `nyxloom-daemon`;
  * `installer_webhook_url` → channel `installs`, user `nyxloom-installer`,
    which is *deliberately not a member of `alerts`* ("a compromised install
    host cannot read nyxloom's own operator traffic").
  There is **no** webhook for a feedback/decisions/ops channel.
* `NotifyConfig` does carry a `mattermost_channel` field (option (b)'s
  candidate), and `nyxloom.toml` sets it to `"alerts"`. But it is a single
  config-wide value, not a per-call override, and there is no live webhook
  credential behind any other channel. Retargeting the daemon webhook at a
  channel that is not provisioned, or borrowing the installer's credential,
  would each be inventing infrastructure — out of scope for this package.

So option (b) is not available on the ground, and option (a) is taken: for
OUTBOUND pushes the two channels of P18's docstring collapse to one. Decision
pushes land in `alerts` alongside ordinary daemon notifications.

This is a real behavioral narrowing, and it is *the same kind* of narrowing P18's
own docstring already records: P18 declined to mint a third ntfy topic/identity
and ran the whole decision loop over P12's existing `cmd_topic`. Doing the
analogous thing one level further at the Mattermost boundary is the consistent
choice, not a shortcut. It is written down as deviation **1b** in that docstring
rather than left implicit.

### The ntfy rollback path keeps P18's original topology (review finding N1)

The collapse above is **Mattermost's**, not a rewrite of P18's deviation 1.
ntfy is the one backend that still has two distinct topics, and it is kept
selectable on purpose (`nyxloom.toml`: "ntfy (RETIRED — kept for rollback)").
The first cut of this package handed `cfg.notify` verbatim to `notify.send()`,
which for `NtfyBackend` posts to `{ntfy_url}/{ntfy_topic}` — but `config.py` maps
the trove's `notifications_topic` → `ntfy_topic` and `feedback_topic` →
`cmd_topic`, and P18 sends both decision pushes to `cmd_topic` specifically.
On the rollback path that first cut would have put decision-agent replies on the
write-only progress topic and aimed the "reply here" hint at a topic
`CommandListener` does not poll — the same class of mis-routing this package
exists to remove, reintroduced for one backend.

`_decision_channel()` now redirects: when the **first** resolved backend is ntfy
and `cmd_topic` is set, the send goes out on
`replace(nc, ntfy_topic=nc.cmd_topic)` — a per-send copy, never a mutation of
the project config. First-resolved rather than "the chain contains ntfy",
because with no explicit selector the legacy chain is `(ntfy, webhook)` and ntfy
is the one that actually delivers; a fallthrough to webhook carries the swapped
topic harmlessly since no non-ntfy backend reads `ntfy_topic`. Every other
backend has a single destination, so the config passes through untouched.

### Consequences handled, not papered over

1. **"Reply here to discuss" would now be a lie.** The inbound half is ntfy-only
   (`commands.CommandListener` long-polls ntfy's `/{topic}/json`), so nothing
   reads a Mattermost reply. The push body is gated on the new
   `commands.cmd_transport_configured(cfg)` — a module-level predicate, now the
   single source for that condition and reused by `_find_cmd_config()`, so it
   cannot drift the way the outbound guard did. Without a live inbound
   transport the text points at the decisions page instead.
2. **`click` was `cfg.notify.ntfy_url`, i.e. `""` post-cutover**, so the
   Mattermost payload rendered no link at all. It is now `DECISIONS_UI_URL`
   (`http://127.0.0.1:8942/www/decisions.html`) — the same loopback dashboard
   base `notify.notification_for` hardcodes for every other push, pointed at the
   page where an operator can actually answer.
3. **`CommandListener._run`'s "nothing to poll" branch spun in total silence.**
   The daemon's only sign of life was `command listener started`, after which it
   polled nothing forever. It now warns **once**, edge-triggered (reset when a
   config appears, and on `start()` so a stop/start cycle re-arms it), naming
   ntfy as the requirement. This is the small fail-visibly improvement the carve
   authorized — a log line, not a transport.
4. **DECISION_OPENED now produces two same-titled messages in `alerts`** — the
   generic `notification_for` push and `notify_decision_opened`'s own. Accepted
   deliberately: the second carries the actionable "how to answer" text the
   first lacks, and two beats the zero this package found. It de-duplicates on
   its own the moment a second channel exists to send one of them to. Disclosed
   explicitly in deviation 1b rather than left to be discovered.
5. **`notify.mattermost_payload`'s §13 rationale was made false by this
   package** and is restated rather than left standing. `_post_feedback` is now
   the first caller to interpolate model-authored prose into a `body` that
   `mattermost_payload` renders as unescaped Markdown. The rendering is
   **accepted, not overlooked**: the text is `cfg.redact()`-ed, capped at
   `MAX_REPLY_CHARS`, produced by an agent dispatched with a read-only tool
   allowlist, and delivered to an operator-only channel. What is conceded is
   cosmetic-to-minor — odd Markdown, a model-authored link, or an
   `@here`/`@channel` Mattermost would honour. Escaping only that one body is
   the tighter fix and is recorded in the docstring as belonging with the next
   change to that seam.

## In scope but deliberately NOT fixed

`commands._send_reply()` builds an ntfy-shaped `NotifyConfig` with the same
literal shape. **This is named, not silently skipped**, and it is a genuinely
different case:

* it is only reachable from `_handle_line`, which only runs after
  `_find_cmd_config()` / `cmd_transport_configured(cfg)` held — i.e. ntfy is
  live by construction;
* a reply must land on the channel its command was **read from**. Routing it
  over `cfg.notify` would post the answer to Mattermost while the question came
  from ntfy, splitting one Q&A across two channels — strictly worse than today.

It re-migrates together with the inbound transport, not before. A WHY comment
saying so now sits on the function.

## Genuinely out of scope

The inbound transport itself: `commands.CommandListener._listen_once` long-polls
`{ntfy_url}/{topic}/json?poll=0&since=...`, which has no Mattermost equivalent.
Building one is the separate, larger shared "read inbound messages from
Mattermost" capability (also wanted by a chatbot package). `commands.py`'s
read-loop transport is untouched here beyond the one-shot visibility warning.

## Tests

Five new/changed cases in `tests/test_decision_chat.py` and
`tests/test_commands.py`. **Each was verified to fail against the pre-fix
source** (`git stash` of the two `src/` files → 5/5 red), per the carve's
warning about vacuously-true assertions:

| test | pre-fix failure |
| --- | --- |
| `test_notify_decision_opened_sends_on_mattermost_only_config` | 0 sends — the silent drop |
| `test_post_feedback_sends_on_mattermost_only_config` | 0 sends — the silent drop |
| `test_notify_decision_opened_omits_reply_hint_without_inbound_transport` | no `DECISIONS_UI_URL` / stale "Reply here" |
| `test_cmd_transport_configured_requires_all_three_ntfy_facts` | predicate did not exist |
| `test_unconfigured_inbound_transport_logs_once` | idle listener never warned |

Two more added in the review round, both verified red against the **first cut of
this package** (stash of `decision_chat.py` → both failed on the destination
topic, `+ notifications`):

| test | catches |
| --- | --- |
| `test_ntfy_rollback_delivers_on_the_feedback_topic_not_the_progress_topic[opened\|feedback]` | N1 — asserts the delivered `ntfy_topic` is `cmd_topic`, and that `cfg.notify` itself is not mutated |
| `test_mattermost_config_is_passed_through_unredirected` | the converse — the redirect must not fire for a channel-bound webhook |

`test_notify_decision_opened_keeps_reply_hint_when_inbound_transport_live`
passes both before and after, on purpose: it pins the behavior that must be
*preserved* when ntfy is live.

The two central regression tests assert on the **resolved backend**
(`notify.resolve_backends(nc)` → `["mattermost"]`), i.e. the real resolver, not a
restatement of any one backend's field names — so they stay honest if the
backend registry changes again.

Existing tests updated: the four pre-existing push tests configured a
now-irrelevant ntfy shape and one asserted `nc.ntfy_topic == "feedback"`. They
now use a shared `_mattermost_only()` helper matching the live post-cutover
config, and assert `nc is cfg.notify`.

## Self-census bookkeeping (both caught by the gate, not guessed)

* `src/nyxloom/exception_census.py`: `LEGACY_BUDGET["decision_chat.py"]` 7 → 6.
  The two push functions' identical send-failure handlers collapsed into one in
  `_push` — a handler removed, not reclassified. `test_exception_census.py`
  fails a budget that is too generous, by design.
* `nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`:
  `decision_chat.py` recorded size 552 → **676** (the recorded value was already
  stale at 591 before this change; the added docstring pushed the drift past the
  10% tolerance). `test_core_characterization.py` asks for exactly this
  re-measure. `notify.py` 788 → 819 and `commands.py` 451 → 467 were re-measured
  in the same pass: both were still *inside* tolerance, but leaving a knowingly
  stale number is what let `decision_chat.py`'s drift accumulate unnoticed in
  the first place.

No new events; nothing in the event-sourced path was touched.

## Gate

`run-gate tester-unified` from the worktree's `nyxloom/`, verdict read
separately from `.assay/verdict-tester-unified.json`.

* First run (commit `cd79c7d6`): **FAIL / COMMAND_FAILED** — the two self-census
  tests above. R1 changed-line coverage was already **PASS at 100.0%**.
* Second run (`f4985220`): **PASS**, R0 + R1, changed-line coverage 100.0%.
* Third run, after the review round (branch HEAD): **PASS**, R0 + R1,
  changed-line coverage 100.0%.

## Review round

Independent review returned **ACCEPT-with-nits** (gate re-verified green; the
reviewer reproduced all 5 original tests failing against a scratch pre-fix
tree). All five findings are addressed in one round, per this project's
precedent for non-blocking nits:

| finding | resolution |
| --- | --- |
| **N1** ntfy rollback path mis-routed to the progress topic | fixed properly via `_decision_channel()`; 2 new tests pin the destination topic |
| **N2** `mattermost_payload`'s §13 rationale no longer true | docstring restated; the unescaped-Markdown concession is now a recorded decision with its bounding safeguards named |
| **N3** stale two-channel comments + undisclosed duplicate | deviation 1b discloses the duplicate; `decision_chat.py` and `daemon.py:1364` comments corrected |
| **N4** `start()` did not reset `_unconfigured_logged` | one-line reset added |
| **N5** two comments misquoted the removed guard | corrected to `if not (cfg.notify.ntfy_url and cfg.notify.cmd_topic)` |

## Commits

* `cd79c7d6` — the fix: both pushes onto the configured backend, the
  `cmd_transport_configured` predicate, the idle-listener warning, docstring
  deviation 1b, tests.
* `f4985220` — self-census bookkeeping and this report.
* branch HEAD — review round: N1 fix + its tests, N2–N5, re-measures.

Not merged; `main` untouched. No second review round requested.
