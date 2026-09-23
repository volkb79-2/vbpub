# scripts/telegram

Telegram notify integration for vbpub's install/bootstrap tooling — used
primarily by `scripts/debian-install-v2` (via `scripts/netcup/.env`) to push
stage-boundary progress and failure messages for a live install run to a
human watching on desktop or mobile.

## Files

- `telegram_client.py` — stdlib-only (no `requests`, so it runs inside the
  shared vbpub test-gate image) message-sending primitives with source
  attribution. Consumed by callers such as `benchmark.py`, `sysinfo-notify.py`,
  and `ssh-keygen-deploy.py`'s subprocess invocation.
- `telegram_setup.py` — ops helper for standing up a forum-enabled supergroup
  (Telegram Topics) as the notify target, so each install run gets its own
  forum thread, plus chat-id discovery, bot-token validation, and MTProto
  (user-account) chat creation. See its own docstring for the full CLI.
- `telegram_debug_reader.py` — reads bot updates via `getUpdates` long-polling
  for debugging what a bot has actually received, with offset persistence.

## Why Telegram, and its known pain points

Telegram was chosen originally because the tooling (bot API, a second reader
bot, MTProto user-session watching) was already fully set up and working.
Real, live-tested pain points that make it a poor fit for wide, long,
structured log/status content specifically:

- **~4096 character message limit** — forces splitting a single log/status
  chunk into multiple separate messages.
- **Weak Markdown** (MarkdownV2) — no real tables; tabular data has to be
  hand-formatted as monospace text.
- **Narrow mobile-UI wrapping** — wide preformatted content (tables, log
  lines) wraps awkwardly on a phone screen.

## Competitor / alternatives research (2026-09-08)

Full research task from the netcup/debian-install-v2 live-test incident (see
`scripts/netcup/LIVE-TEST-HANDOFF-2026-09-08.md` and the
`resume-2026-09-08-netcup-debian-install-v2-livetest` memory record) — two
questions: is there a real Telegram test environment or mock server worth
using for verification, and is there a genuinely better channel for this
specific content shape (long, wide, tabular, streamed from a bash/python
bootstrap script, viewed live on desktop+mobile, needing per-run
topic/thread grouping)?

### Telegram's own test environment (real, official)

A separate test environment exists, run on separate test DCs:

- Create a separate test account via a hidden gesture in an official client
  — Desktop: Settings → Shift+Alt+right-click "Add Account" → "Test Server";
  iOS: tap the Settings icon 10× → Accounts → "Login to another account" →
  Test; macOS: click Settings 10× to open the debug menu, ⌘+click "Add
  Account".
- Create a bot with @BotFather as usual, inside that test account.
- Calls go to `https://api.telegram.org/bot<token>/test/METHOD_NAME` instead
  of the normal path — same server software, so behavior (forum topics,
  formatting, etc.) should be realistic.
- Caveat: flood limits are not raised there, and can at times be stricter.

**Verdict: use this for real end-to-end notify verification** instead of the
real production `chat_id`, the way this session's live-test work did.

### Telegram Bot-API mock servers (real, but solve a different problem)

Several real, maintained open-source mocks exist:
[werdnum/telegram-bot-api-mock](https://github.com/werdnum/telegram-bot-api-mock)
(Python/FastAPI), [jehy/telegram-test-api](https://github.com/jehy/telegram-test-api)
(Node), [zerosixty/teremock](https://github.com/zerosixty/teremock) (Rust,
teloxide-specific, 15-30x faster than hitting the real network),
[vb64/telemulator3](https://github.com/vb64/telemulator3) (Python,
pyTelegramBotAPI-specific).

**Verdict: good for CI unit tests** ("did our code call sendMessage with the
right payload?") — not a substitute for the real test environment above when
verifying actual delivery/rendering.

### Is there something structurally better than Telegram?

| # | Channel | Message length | Real tables | Per-run topic grouping | Setup cost |
|---|---|---|---|---|---|
| 1 | **Discord** (webhook + embeds) | 4096/embed field, 6000 total | No — same monospace workaround as Telegram | **Yes** — `thread_name` creates a real per-run thread via plain webhook, no bot needed | None — just a webhook URL |
| 2 | **Slack** (webhook + Block Kit) | ~4000 recommended, 40000 hard cap | **Yes** — real table blocks | Yes (threads) | Lowest — free workspace, no server to run |
| 3 | **Mattermost** (webhook) | 16383/post (~4x Telegram) | **Yes** — real rendered tables | Threads (looser than Discord's per-run model) | Real — self-host or pay for cloud |

**Discord** is the standout complement/replacement: it solves the one thing
Telegram genuinely can't do well (native per-run topic grouping without a
bot, via `thread_name`) while keeping the same "one HTTP POST" simplicity
already in use here — but it does **not** fix the no-real-tables problem.
If rendered tables matter more than mobile-app polish, **Slack** gets there
with the least new infrastructure (no server to run at all), and
**Mattermost** if self-hosting is preferred and needs 4x the per-message
budget.

**Ruled out, with reasons:**

- **ntfy** (already tried in the sibling `nyxloom` project) — confirmed the
  suspected problem, and it's worse than expected: same 4096-byte default
  message limit as Telegram, *plus* an
  [open bug](https://github.com/binwiederhier/ntfy/issues/1515) where its
  Android app truncates long text that the web app shows in full.
- **Matrix/Element** — an
  [open, unfixed bug](https://github.com/element-hq/element-web/issues/28474)
  renders Markdown tables as monospace `<pre><code>` blocks instead of real
  HTML tables — the identical table problem Telegram has today — plus
  self-hosting a homeserver (Synapse/Dendrite) is a much heavier lift than
  any webhook-based option.
- **Pushover** — hard 1024-character message cap (worse than Telegram for
  long log chunks); HTML and monospace formatting are mutually exclusive.
- **Gotify** — native Markdown support, but no thread/topic concept at all
  and no evidence it's built for this content shape.

**Worth knowing about:** [Apprise](https://github.com/caronc/apprise) is a
unified library/self-hostable REST gateway that fans one call out to 90+
services (Telegram, Discord, Slack, Mattermost, ntfy, Gotify, Pushover,
Matrix, ...). It doesn't fix any service's own length/table/width limits,
but is worth considering if the team ever wants fan-out to multiple channels
at once, or to swap channels without rewriting this directory's equivalent
per service.

### Status

Research only — no channel switch or Apprise adoption has been implemented.
Telegram (real production chat) remains the live notify channel; the test
environment above is the recommended path for verification instead of the
production chat_id.
