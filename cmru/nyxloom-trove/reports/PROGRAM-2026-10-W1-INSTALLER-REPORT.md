# PROGRAM-2026-10 W1-INSTALLER: report

Branch `cmru-w1-installer` (from `cmru-wave-2026-10` @ `90d1c0fdf`). Two implementers: the
first wrote code and tests (checkpoint `c15d5d873`); this successor closed INS-18, the
controller additions A/B, the docs, the plant/revert table and the gates.

Decision O4 part A is implemented: `cmru/src/cmru/templates/get.py.tmpl` is a generic,
fail-closed, transactional installer (install, update, status, rollback). Normative text is
SPEC S6.1-S6.17; the user-facing guide is CONSUMERS "Authoring an installer for your project".

## Controller additions

**A. Trusted-comment binding.** The signed manifest's minisign trusted comment must be exactly
`project=<name> tag=<tag> manifest_sha256=<hex>` (the installer checks `tag` and
`manifest_sha256`). Findings in the repo: producer PRIMITIVES exist and already emit exactly
that format: `cmru.manifest.build_trusted_comment` and `cmru.delegated.minisign_sign`
(`delegated.py` says they are "available only to an explicit project-owned release command").
Nothing in the `cmru release` pipeline calls them (`build_manifest`/`write_manifest` have no
release-path caller either; INS-03 / GETPY-REDESIGN R4). `docs/spec-minisign-bundle-signing.md`
is stale (it cites a release hook and a `--manifest-pubkey` flag that no longer exist) and its
`trusted_comment = "project=<name> version={{version}}"` example is a DIFFERENT format; it was not
edited (outside this package; flagged here). No estate `cmru.toml` sets `manifest_pubkey` (grep of
vbpub and dstdns). Outcome: case (i), no change needed to the producer; added
`TestSignature::test_cmru_producer_output_verifies_with_the_installer` (the real primitives sign,
the installer verifies and installs) and `test_signed_release_replayed_as_another_tag_refused`
(right digest, other tag). The render-time refusal of (ii) was NOT added because primitives
exist and a project-owned release step can legitimately sign today. **Reported gap:** no automatic
signing in `cmru release` (R4); a project that sets `manifest_pubkey` must sign from its own
release step until then, else its installs fail closed. Format documented in SPEC S6.15 and
CONSUMERS.

**B. Rollback after migration.** `rollback` with no `previous` (fresh install or just-migrated host)
and `rollback` on an unmigrated pre-W1 host now say: "the pre-migration layout is not a rollback
target; the first update after migration creates one". SPEC S6.6 and CONSUMERS document it.
Tests: `test_rollback_without_previous_refused`, `TestLegacyMigration::test_update_migrates_atomically`
(both assert the message).

**C. `ciu/installer/enroll.py` dead `manifest_pubkey` argument:** left as is.

## Findings closed (id, evidence, tests in `cmru/tests/test_installer_w1.py` unless noted)

| id | evidence / change | tests |
|---|---|---|
| INS-01 (parts that apply to A) | the verified in-memory manifest bytes are the only manifest used; adapter must be listed in `files` and is hashed; signature must bind tag + digest | `TestFailClosed::test_manifest_inside_bundle_cannot_differ_from_installed_copy`, `test_adapter_must_be_covered_by_manifest`, `test_adapter_hash_mismatch_refused`, `TestSignature::*` |
| INS-02 / R5 | unparseable/absent manifest, bad schema, missing wheel entry, missing sha256, size/hash mismatch are all exit 1 | `test_refused_and_root_unchanged[...]` (14 cases), `test_wheel_without_manifest_entry_refused`, `test_wheel_entry_defects_refused`, `test_wheel_size_mismatch_refused` |
| INS-04 / R3 | `--version` pins exactly, no latest lookup; tag grammar; manifest tag check; idempotent no-op re-verifies recorded digest | `TestPinning::*` |
| INS-05 | `--manifest-pubkey` flag removed; the key is pinned into the rendered file (SPEC S6.2/S6.15) | `TestRendering::test_pubkey_and_launchers_rendered` |
| INS-06 / INS-07 / INS-08 / R7 | per-release dir + own venv, `.incomplete`/`.complete`, atomic `current` swap, `state.json`, rollback re-verifies, variant persisted after the commit under the lock, a re-install never `rmtree`s the live release | `TestLayoutAndRollback::*`, `TestLegacyMigration::*` |
| INS-09 | `--config` implemented (`shared/host.toml`, 0600) | `TestConfigFlag::*` |
| INS-10 / R10 | every redirect hop re-checked (https + allowlist), `Authorization` never forwarded | `TestTransport::test_https_to_http_redirect_refused`, `test_redirect_to_unlisted_host_refused`, `test_no_authorization_follows_a_redirect`, `test_request_goes_through_the_guard` |
| INS-11 | empty `--github-token-stdin` is exit 2 with the explanation that stdin is the script under `curl ... python3 -` | `test_empty_stdin_token_is_config_error` |
| INS-12 / R9 | offline hash-locked wheel install (`--isolated --no-index --require-hashes`, `pip check`), cli-extended first | `TestWheels::*` |
| INS-13 / R11 | single-pass substitution, `json.dumps` literals, plain-name grammar, installer-field grammar shared by config load and render | `TestRendering::*`; `test_cli_config_deep_adversarial.py` |
| INS-18 (this successor) | `GitHubReleaseHost.resolve_latest(prefix, asset_suffix="")` picks the primary asset by the project's `asset_suffix`, a sidecar that cannot be fetched or is not `<64 hex>` raises `RuntimeError` instead of `sha256=None`; `resolve()` passes the suffix only when given; `cmru resolve` passes `installer.asset_suffix` and reports the error as exit 1. Only consumer of that class is the `cmru resolve` CLI (`release.py` uses `GitHubReleases`, a different class, untouched) | `tests/test_ins18_resolve_host.py` (8), updated `test_smaller_modules_adversarial.py::test_github_host_filters_releases_and_surfaces_sha_retry_failure`, `test_execution_boundaries_adversarial.py` (digest now a real hex) |
| INS-19 | real `minisign`, `venv`, `pip` used in the new tests (no mocks of the verifier); the old `probe_*.py` scripts were not ported literally | `TestSignature`, `TestWheels` |
| INS-20 | SPEC S6 rewritten, `plan-ki24-get-py-enroll.md` marked superseded, README paragraph and `getpy.py` docstring corrected | n/a (docs) |
| INS-24 | whitespace-only/odd sidecars are exit 1, the filename field is checked | `test_sidecar_parsed_strictly` (8 cases) |
| R8 | `launchers` -> `<root>/bin/<cmd>` symlinks following `current` | `TestWheels::test_install_launcher_rollback_and_lock`, `test_launcher_missing_from_venv_refused` |

Also: the old template/tests that pinned the lenient behaviour (`test_installer.py`,
`test_variants.py`, `test_installer_extensions.py`, `test_boundary_contracts.py`) were inverted by
the first implementer. This successor changed fixtures that built `SimpleNamespace` projects
without `installer` (`test_small_operational_residuals.py`, `test_release_coverage_gaps.py`,
`test_resolve_format.py`), and strengthened two tests found hollow by the plants:
`test_sidecar_parsed_strictly` (now uses the asset's real digest, so grammar is the only defect)
and `test_download_http_error_is_fatal` (an error body is streamed into the destination).
Added `test_pip_runs_offline_isolated_and_hash_locked` (argv is the contract: `--no-index` and
`--require-hashes` are not observable offline; with a hash lock pip enforces hashes anyway, so the
`--require-hashes` plant is an equivalent mutant except for this argv test).

## Findings rejected / out of scope

- **INS-14, INS-15, INS-16, INS-17 (agent):** the cmru agent and enrollment code are gone from cmru
  (W0-RETIRE, W1-CIU-ENROLL); nothing to fix here.
- **INS-22, INS-23 (enroll):** `enroll` is ciu's fragment (`ciu/installer/enroll.py`), tracked in
  ciu CIU-122/CIU-123.
- **INS-21, `authorized_keys` splitlines part:** same, ciu's. (Its other parts are covered above.)
- **R6 (`authorized_keys` safety) and R12 (agent alignment):** rejected for the same reason.
- **INS-03 / R4 (manifest producer, schema 2):** not in W1-INSTALLER scope; the producer primitives
  exist but are not wired into `cmru release` (see A). Remains open for a later package.
- **R1 (trust root: verify the installer before running as root):** a distribution/doc matter;
  CONSUMERS documents the download-verify-run form (`curl -o`, `sha256sum -c`, `python3 f install`).
- `docs/spec-minisign-bundle-signing.md` is stale (see A); not edited, to be rewritten with R4.

## Plant / revert table

Every plant was made with the Edit tool and reverted with the Edit tool (no `git checkout`);
`git diff` afterwards showed only the intended changes. "Failing tests" are those that failed
with the plant in place; with it reverted the file passes (131 tests at the time).

| item | plant | failing tests |
|---|---|---|
| R5 `files` hash | `_verify_files`: hash compare `if False` | `test_refused_and_root_unchanged[files entry hash mismatch]`, `test_adapter_hash_mismatch_refused` |
| R5 adapter coverage | `if False and ENTRYPOINT not in files` | `test_adapter_must_be_covered_by_manifest` |
| R5 lenient wheel | `_verify_wheel_sha256` returns on a missing entry | `test_wheel_without_manifest_entry_refused` |
| R5 absent manifest | `_read_bundle_manifest` returns a default instead of exiting | `test_refused_and_root_unchanged[absent manifest]` |
| R5 schema, tag | `_parse_manifest` schema check and tag check `if False` | `[unknown schema]`, `[boolean schema]`, `[tag mismatch]` |
| R10 redirect | `_check_url(newurl)` removed | `test_https_to_http_redirect_refused`, `test_redirect_to_unlisted_host_refused` |
| R10 token | `remove_header` lines removed | `test_no_authorization_follows_a_redirect` |
| R10 stdin token | empty-line check `if False` | `test_empty_stdin_token_is_config_error` |
| R10 non-200 | `if status != 200` -> `if False` | `test_download_http_error_is_fatal[404]`, `[500]` (after the test was strengthened) |
| R10 sidecar | `_parse_sidecar`: `text.split()[:1]` | 4 of the `test_sidecar_parsed_strictly` cases (other name, extra tokens, two lines, extra line) |
| R2 key flag | `-P` -> `-p` | `test_valid_signature_installs`, `test_cmru_producer_output_verifies_with_the_installer`, `test_status_reports_signed` |
| R2 trusted comment | tag/digest check `if False` | the 3 `test_trusted_comment_must_bind_tag_and_digest` cases and `test_signed_release_replayed_as_another_tag_refused` |
| R2 pre-flight | minisign check in `_preflight` `if False` | `test_minisign_required_before_any_network_io` |
| R3 pin | `if False and version` in `_resolve_tag` | `test_pinned_install_never_resolves_latest`, `test_fake_newer_release_does_not_change_pinned_install`, 4x `test_hostile_tag_refused_before_network`, `test_same_version_is_idempotent_noop`, `test_noop_reverifies_recorded_manifest_digest` |
| R7 failed apply | `_discard_release` does not remove the dir | `test_failed_adapter_apply_changes_nothing`, `test_failure_at_the_swap_removes_the_new_release` (+ `files`/wheel failure cases) |
| R7 prune | `_prune_releases` forgets `current` | 8+ `TestLayoutAndRollback` tests, including `test_prune_keeps_only_current_and_previous` |
| R7 crash cleanup | start-of-transaction `_prune_releases` removed | `test_crash_cleanup_never_touches_current_when_update_fails` |
| R7 INS-08 | `fresh = True` (rebuild an existing release) | `test_reusing_an_existing_complete_release` |
| R7 rollback verify | `_verify_release_intact` replaced by a plain path | `test_rollback_reverifies_previous`, `test_rollback_refuses_incomplete_previous` |
| migration | legacy current kept as `previous` | `test_update_migrates_atomically` |
| R8 launcher | symlink target `../venv/bin/<cmd>` | `test_install_launcher_rollback_and_lock` |
| R9 hash lock | `--no-index` and `--require-hashes` dropped | `test_pip_runs_offline_isolated_and_hash_locked` (only this one; see above) |
| R9 ordering | `_wheel_order` returns the declared order | `test_wheel_order_hoists_cli_extended`, `test_install_launcher_rollback_and_lock` |
| R11 placeholder | `_substitute` no longer raises on an unknown key | `test_unreplaced_placeholder_is_fatal`, `test_extension_with_unknown_placeholder_is_fatal` |
| R11 quoting | `_py_literal` -> `f'"{value}"'` | `test_values_round_trip_through_json_literals` |
| R11 plain names | `_plain` check `if False` | `test_quote_in_owner_refused_exit_2`, 4x `test_bad_values_raise_render_error` |
| INS-18 suffix | asset selection ignores `asset_suffix` | `test_asset_suffix_selects_the_primary_asset_not_api_order` |
| INS-18 fail-open | sidecar error swallowed, hex check off | `test_sidecar_fetch_failure_is_an_error_not_a_missing_digest`, 3x `test_malformed_sidecar_is_an_error` |

Weak spots found and fixed by the plants: the sidecar-grammar and non-200 tests were hollow
(they passed through an incidental digest mismatch or the empty-file check) and the replay case
had no test with a correct digest. Remaining weak spot: `test_crash_mid_install_is_cleaned_on_next_run`
passes without the start-of-transaction prune because the end-of-transaction prune also cleans;
only the failing-update case pins it.

## Gates

Run at code commit `60a18093e` (lanes refuse a dirty tree; this report update is the only later change):

- Full cmru suite via `pt.py`, no `--maxfail`, under the test lock: 3042 passed, 2 skipped (read
  from the saved output, separate step). An earlier full run found 6 failures (a docs TOML-fence
  count, 4 `SimpleNamespace` fixtures without `installer`, and a test pinning the old
  `sha256=None` behaviour); all fixed.
- ciu `test_getpy_enroll.py` + `test_ciu_host_enroll.py` with `PYTHONPATH=<wt>/ciu/src:<wt>/cmru/src:<wt>/libraries/cli-extended/src:<wt>/libraries/worktree/src`:
  172 passed, 8 skipped; the committed-`get.py` drift guard passes (`ciu/get.py` and `tls-edge/get.py` re-rendered after the template change).
- `coverage` lane: verdict PASS (the first run FAILED at 99.99% on the `_py_literal` raise branch,
  `getpy.py:363`; `test_unrenderable_value_type_is_a_render_error` closes it).
- `canary` lane: verdict PASS.
- Not run (rules): `mutation`, `gate`, `enroll`, any release/publish.

## Deviations

- `render_from_config` does NOT refuse `manifest_pubkey` (controller option (ii)): see A.
- `docs/spec-minisign-bundle-signing.md` not edited (stale; belongs with R4).
- The `--manifest-pubkey` CLI flag was removed rather than fixed (INS-05): the key is pinned in the
  rendered file, which also removes it from the attacker-controlled command line.
- Plants were applied to several independent locations at once where the failing-test attribution
  stayed unambiguous, and one at a time otherwise; the `prune` plant cascades into many tests by
  design.
- No rule violations on editing: all repository files were changed with Edit/Write. (The scratch
  python in the shell only read `git diff`.)

## CHANGES.md lines for the controller

```
- cmru get-py: the emitted get.py is now a fail-closed transactional installer: HTTPS-only with every redirect hop checked and the token never forwarded; the manifest (and a pinned minisign signature whose trusted comment must read `project=<n> tag=<tag> manifest_sha256=<hex>`) is verified from the bundle bytes before anything runs; every wheel is hash-locked and installed offline into a per-release venv; releases live in `releases/<tag>-<digest>/` with an atomic `current` swap, `state.json`, rollback and crash cleanup; `--version` pins exactly; `--config` installs `shared/host.toml`; optional `launchers`. (INS-01/02/04/05/06/07/08/09/10/11/12/13/20/24)
- cmru get-py / config: new `[project.installer]` keys `manifest_pubkey` and `launchers`, with a validated field grammar; a bad value is a render error (exit 2), never a warning.
- cmru: a pre-W1 installed host migrates on its next install/update (new release built beside it, atomic swap, then legacy dirs removed); the first migrated host has no rollback target and `rollback` says so.
- cmru resolve: the primary asset is chosen by the project's `asset_suffix`, and an unreadable or malformed checksum sidecar is now an error instead of `sha256=null` (INS-18).
```
