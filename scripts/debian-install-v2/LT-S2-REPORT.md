# LT-S2: stage2 launch blocker, silent failure, credential warning, backports comment (2026-10-06)

Evidence: live v1001 lane (LT-F-v1001-01/-02/-04). Base head `aff124ba1`.

## Changes

- **F1 (blocker)**: `STAGE2_SERVICE` now runs `python3 <install dir>/debian-install-v2.py resume --yes`
  (the entrypoint puts the `cli_extended-*.whl` beside it on `sys.path`). No `-m debian_install_v2...`
  remains in any rendered unit; the only other occurrences are an in-repo test (`test_module_main_exit`),
  a docs row marked tests-only, and comments.
- **F2 (major)**: stage2 unit has `OnFailure=vbpub-bootstrap-failed@%n.service`. New template unit
  `vbpub-bootstrap-failed@.service` runs `debian_install_v2/failure_notify.py %i`
  (imports: stdlib + `debian_install_v2.notify`, which is stdlib-only; both asserted by an AST test).
  It writes `status=failed`, `failed_unit`, `failed_at`, `failed_journal_tail`, `last_error` into
  `state.json` (atomic, 0600), and posts via Mattermost webhook or Telegram using
  `$CREDENTIALS_DIRECTORY`, then `<state_dir>/credentials`, then `/etc/vbpub/credentials`.
  Journal lines and message go through `redact_text` (webhook URL, Telegram token, known secrets);
  stdout/stderr never carry the URL or token. It always exits 0. Only the stage2 unit is a bootstrap
  unit; no other bootstrap unit exists.
- **F3 (minor)**: no `LoadCredential=` line in root-storage mode. Also fixed in passing: systemd mode with
  Telegram previously put both credentials on ONE `LoadCredential=` line (systemd accepts one `ID:PATH`
  per line); it now emits one line per credential.
- **F4 (docs)**: comment at the 600 pin in `templates.py` corrected; README apt section has the same note.
  Pin value unchanged (600).
- Docs: README "Stage2 launch and failure notification", CLI-SPEC row marked tests-only,
  `testing/vm/README.md` hint no longer uses `python3 -m debian_install_v2`.

## Tests

- `tests/test_bare_host_launch.py`: stages the installed layout like stage 1 (whole project subtree copied,
  wheel written through `bootstrap-remote.py`'s `write_wheel`; the wheel is zipped from the library under
  test), renders units with the real `Installer`, runs each ExecStart under `python -E -S` (no PYTHONPATH,
  no site-packages) in the unit's WorkingDirectory. A guard test proves a bare interpreter cannot import
  `cli_extended` without the wheel. Deviation from the brief: `-E -S` rather than `-I -S`, because `-I`
  also strips the cwd that `-m` relies on, which would make the planted `-m` form fail for the wrong reason.
  Also covers `OnFailure=` text, no-module-form scan, LoadCredential in both modes x both backends.
- `tests/test_failure_notify.py`: stdlib-only AST check, bare-host end-to-end (real local HTTP receiver,
  fake `journalctl` emitting the webhook URL and a Telegram token), message format, state.json fields and
  mode, redaction, Telegram path, missing journalctl/state/credentials, failed post, unit-name sanitising.
- Updated `test_gstammtisch_incorporation.py::test_stage2_unit_module_path_and_workdir_resolve`.

## Verification actually run (host pytest, `python3 -m pytest debian_install_v2/tests`, flock + nice + ionice)

- Clean tree: 770 passed, 11 skipped.
- Plants (each followed by a full-suite run, then reverted):
  1. `-m debian_install_v2.bootstrap` ExecStart restored: 4 failed (bare-host launch gets the real
     `ModuleNotFoundError: No module named 'cli_extended'`).
  2. `OnFailure=` removed: 1 failed (`test_stage2_unit_has_onfailure_pointing_at_the_notifier_template`).
  3. `import cli_extended` added to the notifier: 3 failed (AST test, bare-launch, bare end-to-end).
  4. `LoadCredential=-` emitted in root-storage mode: 3 failed.

## Gates

Run on commit `820a79973` (clean tree), serially under flock/nice/ionice, verdicts read in a separate step
from the saved output:

- debian-install-v2 `r0-r1`: PASS, exit 0, 770 passed, 11 skipped.
- netcup `suite`: PASS, exit 0, 683 passed.
- r2 (mutation), the VM lane and any live-host test were not run.
