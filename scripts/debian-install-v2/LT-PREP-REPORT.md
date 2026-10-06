# LT-PREP report: Mattermost notifications (netcup + debian-install-v2)

## Rebase outcome
Branch `cli-ext-w9b-debian` was rebased onto main by the first session (7 commits ahead of main at review time (2 of them the LT-PREP feature and report; the earlier "5" was stale); already-applied W9b commits dropped). Verified: no rebase in progress, clean tree apart from the predecessor's
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

## Review fix round 1
Item -> fix -> test -> plant result (plants run in a scratch copy of `scripts/debian-install-v2`; the two tests that fail in
any copy outside the repo layout, `test_entrypoint_wheel::test_the_repository_source_is_never_a_fallback` and
`test_wizard::test_missing_optional_prompt_package_has_a_project_specific_install_hint`, were deselected there; first failing test named).

1. **Injection.** `notify.sanitize_field` (control chars -> space, cap, escape `\ * _ [ ] ( ) # | ` ~ < >`, U+200B after `@`) on host label (64),
   server (64), run id (16), stage (32), event (300); excerpt keeps its fence escaping. `vbpub-notify` carries the same logic inline
   (host 64, message 600, 4xx not retried). `notify_host_label` is REJECTED at validation unless `[A-Za-z0-9 ._:/-]` (max 64).
   Tests: `test_mentions_are_defused_in_every_field` (@channel/@all/@here/@bob), `test_links_headings_tables_and_fences_in_event_are_inert`,
   `test_control_characters_collapse_and_fields_are_capped`, `test_notify_host_label_rejected_at_validation`, `test_notify_script_defuses_*`.
   Plants: no-ZWSP, no control collapse, no escaping, label validation off, notify-script no clean: all KILLED.
   Netcup side: `install-host.py`/`netcup_scp_client.py` have no message formatter (netcup sends no notifications), so nothing to sanitize there.
   Note: escaping makes step names show as `docker\_install`; accepted per ruling.
2. **Webhook subpaths.** One pattern `https?://[^\s'"`]*?/hooks/[^\s'"`]+` in `notify.py` and `netcup_scp_client.py` (`install-host.py` uses the
   latter). Netcup also masks the exact `MATTERMOST_WEBHOOK_URL` env value and any value seen under a webhook key.
   Tests: subpath, trailing `.`/`?`/`#`, quoted, wrapped, on both sides, plus exact-value masking. Plant old regex: KILLED.
3. **0600 race.** `bootstrap-remote.py` creates `remote-install-config.json` with `os.open(..., 0o600)` + `os.fchmod`. Other writers
   checked: `HostActions.write_file` uses a mkstemp temp file (0600) then chmod, `state.py` likewise; the webhook is never in `state.json`.
   Tests: 0600 under `umask(0)`, pre-existing 0644 file tightened. Plant 0644 without fchmod: KILLED.
4. **Retries.** 4xx other than 429 not retried; circuit breaker after 3 consecutive failed messages per process (one warning, then silent;
   a success resets the count; `notify.reset_breaker()`, autouse fixture in `conftest.py`); timeout stays 10 s.
   Tests: no retry on 400/401/403/404, retry on 429/500/503, breaker trip + single warning, success reset, literal `timeout == 10`.
   Plants: timeout 300 (M2) KILLED, 4xx retried KILLED, breaker off KILLED.
5. **Truncation.** Parts bounded before assembly (head fields capped, excerpt 800); the closing fence is always present.
   Test: 5000-char event + 9000-char excerpt, balanced fences, total <= 3500. Plants: excerpt cap off (M14) and event cap off: KILLED.
6. **Telegram-only is not byte-identical** (accepted): `state.json` now also records `notify_backend` (pinned) and `notify_host_label`;
   the "no credentials" text of `vbpub-notify` changed.
7. **`vbpub-notify`.** Sanitized and capped as in 1. Backend honoured by the other option: the installer writes ONLY the selected backend's
   credential files (test `test_only_the_selected_backends_credential_files_are_installed`), so the helper's file-presence check follows `notify_backend`.
8. **Docs.** READMEs now say there is no backend without a credential or `notify_backend`, and the wizard offers Mattermost first. Commit count fixed above.
9. **`_notify_stage`** is set first thing in `resume()`. Test `test_notify_stage_is_stage2_even_when_resume_fails_early`. Plant (late assignment): KILLED.
10. **Plants M2, M5, M11, M12, M14.** All KILLED: M2 `test_post_timeout_is_literally_ten_seconds`; M5 `test_redact_text_masks_the_exact_known_secret_even_when_not_a_url`;
    M11 (installer credential file 0644) `test_every_webhook_credential_file_is_requested_with_mode_0600` (it first survived because dry-run
    does not record the mode; a `write_file` spy now does); M12 `test_bootstrap_debug_never_prints_the_webhook_url`; M14 as in 5.

Tests after round 1: debian-install-v2 575 passed / 11 skipped; netcup 533 passed.

## LT-KEY: controller key retention restore + docker address pools

Supersedes the earlier partial LT-KEY stub (brief v2). The v1 design
(`controller_ssh_key_after_install`) was reverted; original names restored.

### Regression history (A)
- `4c1748c52` "rework netcup installer lifecycle": 2x2 policy. Host remove + local retain is the default; host remove + local remove and host retain + local retain are valid; host retain + local remove is REJECTED (a host key whose private half was deleted). Failure paths retain both. debian-install-v2 got `Config.retain_controller_ssh_key: bool = False`, env `RETAIN_CONTROLLER_SSH_KEY=yes|no` in bootstrap-remote.py, and the installer branch marking `controller_ssh_key_retained`.
- `ad389508f` "Decouple Netcup installer from custom script producer": host retention moved out of netcup (netcup keeps `--local-controller-key` / `NETCUP_SCP_API_CONTROLLER_KEY_LOCAL_RETENTION`); the host side became the producer's (debian-install-v2) setting. The reject rule was lost from netcup here.
- `e3cd117c1` "feat: adopt cli-extended for Debian installer": silently deleted `retain_controller_ssh_key` from debian-install-v2 (field, env mapping, README text, installer branch). Unannounced regression.

### Restored (A)
- config.py: `retain_controller_ssh_key: bool = False` (JSON boolean, validated by the existing bool check); bootstrap-remote.py `RETAIN_CONTROLLER_SSH_KEY` in `_BOOL_FIELDS` (yes/no; the shared `_env_bool` also accepts true/1/on/off and rejects anything else, e.g. `auto`, with a SystemExit naming the variable); installer.py resume() branch (step `controller_ssh_key_retained`, success, "configured to retain after successful stage2"); README text. Failure paths still never remove the key.
- Bundle / `--config-json` / `customscript.py`: carried automatically because `asdict(config)` is serialized into the `shlex.quote`d `VBPUB_CONFIG_EXTRA_JSON`; tested by shlex round-trip. Wizard: boolean prompt in the controller-ssh section. Install-complete messages say "controller key retained on host" (Mattermost event text and Telegram body) only when a pubkey is configured and retain is set.
- netcup install-host.py: `_custom_script_host_key_retention` detects `RETAIN_CONTROLLER_SSH_KEY=yes|no` (shell token) or `"retain_controller_ssh_key"` in the embedded `VBPUB_CONFIG_EXTRA_JSON` (bundle value wins, as in the bootstrap). `_reconcile_local_key_with_host` runs before authentication: host retain + explicit local remove (flag or `NETCUP_SCP_API_CONTROLLER_KEY_LOCAL_RETENTION`) is refused with exit 2; host retain + defaulted local remove becomes retain. Dry-run summary prints both settings.
- Limitation (documented in netcup/README.md): a customScript that is not shell-splittable, whose JSON cannot be parsed, or that sets the policy another way yields "not declared"; nothing is guessed and the dry-run prints `not declared by the customScript`.
- netcup CLI-SPEC-install-host.md is generated by cli-extended and no option/help text changed, so it was not hand-edited.

### Docker address pools (B)
- `Config.docker_default_address_pools: list`, default `[{"base":"10.240.0.0/16","size":24}]`. `validate_address_pools`: list of objects with exactly base+size; base a strict `ipaddress` network (host bits rejected); size an int (not bool) with prefixlen <= size <= 30 (v4) / 128 (v6); at most 16; same-family overlaps rejected. Empty list is valid.
- Rendered by `_configure_docker_daemon` into the same daemon.json as live-restore/log-driver; empty list omits the key and removes a stale one on re-run. Wizard: new `json` field kind (default shows the current JSON, bad JSON re-prompts). README updated. No customScript env var was added (optional); the bundle carries the list as JSON.

### C
- `validate_host_label` uses `re.fullmatch`; two cases added to `test_notify_host_label_rejected_at_validation`. `run_io_benchmark` untouched (v1 edits reverted; the wizard benchmark section is as it was).

### Tests run (own runs, pytest under flock/nice/ionice, PSI full avg60 4.4 at start)
- debian-install-v2 `debian_install_v2/tests`: 620 passed, 11 skipped. netcup `tests`: 556 passed.
- New: debian_install_v2/tests/test_controller_key_retention.py, test_docker_address_pools.py; scripts/netcup/tests/test_controller_key_retention.py. test_r1_remaining golden-ish assertion updated for the new default pool.
- Planted mutations, each killed: retain branch ignored (1 failed); default flipped to True (3 failed); netcup reject rule removed (3 failed); netcup default-to-retain removed (2 failed); pool not rendered (4 failed); `re.fullmatch` -> `re.match` (2 failed, earlier run).
