# LT-PREP report: Mattermost notifications (netcup + debian-install-v2)

## Rebase outcome
Branch `cli-ext-w9b-debian` was rebased onto main by the first session (5 commits ahead of main before this
package; already-applied W9b commits dropped). Verified: no rebase in progress, clean tree apart from the predecessor's
work, which this session completed and committed.

## Design as built
- `debian_install_v2/notify.py` (stdlib only): `effective_backend` (explicit `notify_backend` wins; else inferred from the
  present credential; both present and unset raises a config error; none present means `none`), `validate_webhook_url`,
  `redact_text`, `format_mattermost_message` (the single format function, target-host side), `post_webhook`
  (`{"text": ...}` only, 2 attempts, 1.5 s backoff, 10 s timeout, never raises, logs only the host part).
- Config: `notify_backend`, `mattermost_webhook_url` (secret, `repr=False`, not persisted to `state.json`),
  `notify_host_label`. `require_notify_credentials` runs at initial install only (stage 2 re-validates with credentials stripped).
- Webhook reaches the target through the same path as the Telegram token (customScript env var, `shlex.quote`d;
  credential file under root-storage/systemd modes). `bootstrap-remote.py` maps `MATTERMOST_WEBHOOK_URL`, `NOTIFY_BACKEND`.
- Wizard offers the backend and webhook URL. Failure messages carry a short (800 char) redacted fenced tail. No PAT, no attachments.
- Redaction: webhook URL masked in `--debug`/`--debug-raw`, errors, and netcup `_redact_secrets` / `_redact_for_log`
  (`netcup_scp_client._mask_webhook_urls`, dict keys `mattermost_webhook_url`/`MATTERMOST_WEBHOOK_URL`).
- netcup `install-host.py` sends no notifications itself; it only redacts and passes values through the customScript producer.

## Message samples (prefix: label (server) | run id | stage | event)
- start: `⏳ **netcup-1** (`vmi123`) | run `ab12cd34` | stage1 | <one-line host summary>`
- reboot disabled: `⚠️ ... | stage1 | complete; reboot disabled, stage2 needs a manual resume`
- reboot: `✅ ... | stage1 | complete; rebooting into stage2`
- resumed: `⏳ ... | stage2 | resumed after reboot`
- verbose step: `⏳|✅|❌ ... | <stage> | <step>: <status> - <detail>`
- complete: `✅ ... | stage2 | install complete (duration 0:42:10)`
- failure: `❌ ... | stage2 | install FAILED` followed by a fenced redacted tail (excerpt of the error).

## Tests
`tests/test_mattermost_notifications.py` (selection, payload, failure tolerance, retry bound, redaction, customScript quoting
with hostile characters, milestones), `test_telegram_notifications.py` adapted, netcup `tests/test_install_host.py` redaction tests.

## Gates (non-mutation, non-VM)
- netcup `suite`: PASS (524 passed).
- debian-install-v2 `r0-r1`: PASS (529 passed, 11 skipped; coverage 95%).

## Deviations
- Mattermost is the default only in the sense of inference/docs; with no credentials the backend is `none`.
- Docs: debian README, CONSUMERS.md, known-shape.json, netcup README, TODO.md updated. `CLI-SPEC*.md` are generated and
  untouched; no new CLI options were added. No live calls were made.
