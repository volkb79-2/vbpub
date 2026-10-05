# W9b report: debian-install-v2 adopts cli-extended as a released wheel

Branch `cli-ext-w9b-debian`. Scope: `scripts/debian-install-v2/**` plus this report.
Not run (by instruction): `r1-vm-real-commit`, `gate`, `r2`.

## Gate verdicts (each lane through the flock, verdict read in a separate step)

```
run-gate: lane 'r0-r1' verdict PASS; exit_code 0; log /tmp/run-gate/lanes/r0-r1/2a8acf9a702f41c87d3c5610da3803ac.log
run-gate: lane 'fake-integration' verdict PASS; exit_code 0; log /tmp/run-gate/lanes/fake-integration/e3b6c377877346a9486d982ef7889b53.log
run-gate: lane 'r3' verdict PASS; exit_code 0; log /tmp/run-gate/lanes/r3/74e1a2627f9a0bb9d5567adf02477313.log
```

r0-r1 summary line: `460 passed, 11 skipped in 30.28s` (TOTAL coverage 95%). r3: `canaries: 3 rejected, 0 survived`.

## Oracle evidence

Test files are under `scripts/debian-install-v2/debian_install_v2/tests/`.

- **O1 bootstrap** (`test_bootstrap_remote.py`, all urllib faked through a `_FakeNet` router that also records URL, timeout and User-Agent):
  - pinned pair: `test_pinned_env_pair_is_used_without_any_network_call`
  - half-pinned: `test_half_pinned_wheel_is_an_error_before_any_network_call[...]` (both directions, exact message)
  - latest.json path: `test_latest_pointer_default_path_reads_url_and_sha256` (URL, timeout 60, User-Agent), `test_latest_pointer_url_is_overridable_by_env`
  - missing/empty/non-string field: `test_latest_pointer_missing_or_empty_field_names_the_field[...]` (6 cases, message names the field); not JSON / not an object; fetch failure
  - sha256 mismatch, nothing written: `test_install_wheel_sha256_mismatch_reports_both_digests_and_writes_nothing` (both digests in the message, an existing stale wheel is left untouched), `test_install_wheel_mismatch_does_not_even_create_the_install_dir`
  - non-zip / missing `cli_extended/__init__.py` (also a nested `vendored/cli_extended/__init__.py`): `test_install_wheel_rejects_a_payload_that_is_not_the_library[...]`
  - non-`.whl` URL (also `.tar.gz`, `.whl/download`, wrong prefix, `%2F` traversal, empty path): `test_install_wheel_refuses_a_url_that_is_not_a_cli_extended_wheel_name[...]`, asserts no network call
  - stale-wheel cleanup: `test_install_wheel_leaves_only_one_cli_extended_wheel` (older wheels and a same-name stale wheel go; an unrelated wheel and file stay)
  - the library source subtree is no longer extracted: `test_fetch_subtree_extracts_only_the_matching_subtree`
  - end to end with faked urllib: `test_main_fetches_tree_and_released_wheel_end_to_end_with_faked_urllib`
- **O2 entrypoint** (`test_entrypoint_wheel.py`, `invoke_script` with `home=tmp_path/home`, in a subprocess):
  - one wheel imported from the wheel: `test_one_wheel_beside_the_entrypoint_is_imported_from_the_wheel` asserts `CLI_EXTENDED_FILE=<install>/cli_extended-0.0.0-py3-none-any.whl/cli_extended/__init__.py`; `test_the_wheel_beside_the_entrypoint_wins_over_a_library_on_the_path`
  - two wheels: `test_two_wheels_are_refused_with_exit_2_naming_both` (exact message, exit 2)
  - none and not installed: `test_no_wheel_and_no_installed_library_exits_2_with_the_exact_message`, plus `test_the_repository_source_is_never_a_fallback` (the real entrypoint, in a checkout where the library source is a sibling, exits 2); `test_no_wheel_uses_an_installed_library`
- **O3 wheel fixture**: `build_wheel()` in `test_entrypoint_wheel.py` zips the imported library package into `cli_extended-0.0.0-py3-none-any.whl` with a minimal `.dist-info`. No pip, no network. The no-site interpreter is a wrapper script `exec python -S "$@"`.
- **O4**: gate verdicts above; `surface check` and `audit` transcripts below.

### Hand-planted mutations, watched dying
```
bootstrap-remote.py   `if actual != sha256:` -> `if actual == "":`
  FAILED test_install_wheel_sha256_mismatch_reports_both_digests_and_writes_nothing
  FAILED test_install_wheel_mismatch_does_not_even_create_the_install_dir     (2 failed, 53 passed)
debian-install-v2.py  `if len(wheels) > 1:` -> `if len(wheels) > 2:`
  FAILED test_two_wheels_are_refused_with_exit_2_naming_both   (returncode 0, stdout 'debian-install-v2 2.0.0')
```
Both restored; the 62 tests of those two files pass again.

### Surface lifecycle and audit
`cli-extended.toml` (standalone form, module factory `debian_install_v2.bootstrap:build_cli`), `docs/CLI-SPEC.md` (semantic table plus generated region), `docs/cli-review.toml` (62 active cases, every one linked to behavior tests carrying `cli_case`), `docs/cli-surface.json`, `docs/cli-review-findings.toml` (F-001..F-010).

```
$ cli-extended surface sync   -> CLI surface files synchronized.
$ cli-extended surface check  -> CLI surface check passed.      (3 open minor/note findings reported, none blocker/major)
$ cli-extended audit
[PASS] AC-01 [PASS] AC-04 [PASS] AC-05 [PASS] AC-07 [PASS] AC-08 [PASS] AC-10 [PASS] AC-12
[PASS] AC-16 [PASS] AC-17 [PASS] AC-18 [PASS] AC-22 [PASS] AC-25
[MANUAL] AC-19 skills-packaged   -> F-008 wontfix (no agent workflow; rationale recorded)
[MANUAL] AC-20 doctor            -> F-009 wontfix (plan and verify already cover it)
[MANUAL] AC-24 dependency-declared -> F-010 open minor (no version floor enforced; remedy recorded)
audit: 12 pass, 0 warn, 0 fail, 3 manual
```
LLM review: `surface pack` bundle read in full against the rubric. Findings: F-001 (major, fixed) `disable-stage2 --dry-run` printed "Stage-two service disabled..." while executing nothing, now prints the planned-actions JSON; F-002/F-003 (minor, fixed) install description restated the verb, missing `resume --dry-run` and `status --json` examples; open: F-004 (`verify` has no `--json`), F-007 (library `--dry-run` help is generic), F-010. wontfix with rationale: F-005, F-006, F-008, F-009.
The manifest was exported with the library revision of this branch; it was produced under a stub `cli_extended-0.2.0.dist-info` (see friction 1) so `library_contract` is v1 and the identity line in the pack reads `cli-extended 0.2.0`.

## Docs disposition

| File | Change |
| --- | --- |
| `README.md` | new "Library dependency and remote bootstrap" section: wheel resolution order, exit-2 messages, `VERSION`, env table (`CLI_EXTENDED_LATEST_URL`, `CLI_EXTENDED_WHEEL_URL` + `CLI_EXTENDED_WHEEL_SHA256`), links to the design guide and CLI-SPEC |
| `docs/DESIGN-GUIDE.md` | "Remote bootstrap stays self-contained" rewritten: two-fetch design, rejected alternative (library inside the branch archive), no source fallback |
| `docs/CONSUMERS.md` | remote dry-run paragraph now covers the wheel; pinning example and failure modes |
| `docs/CLI-SPEC.md` | new: caller surfaces, product-owned semantic table, generated surface region |
| `bootstrap-remote.py` module docstring | fetches the released wheel; `Library:` env block |
| `docs/cli-review.toml`, `cli-surface.json`, `cli-review-findings.toml` | new (surface lifecycle) |

## What changed in the product

- `VERSION` (`2.0.0`); `bootstrap.IDENTITY = CliIdentity.resolve(..., version_file=VERSION)`; the `__version__ = "2"` literal is gone (`test_identity_version_comes_from_the_version_file_not_a_literal` also asserts the attribute no longer exists). Tests that pinned `2` now pin `2.0.0`.
- `CliRegistry(expected_exceptions=DOMAIN_ERRORS, unexpected_exceptions="report")`; `main()` no longer passes the tuple.
- `VerbSpec(dry_run=True)` on `install`, `resume`, `disable-stage2`; the hand-rolled `--dry-run` option and `getattr(args, "dry_run")` sites are gone (`runtime.dry_run`).
- Constraint: `Requires("--repo-url", ("--bootstrap-url",))` on `build-customscript`; `--repo-url` and `--repo-branch` lost their argparse defaults (the builder function already defaults `None`).
- Subprocess invoke helpers in the CLI tests replaced by `make_invoker`, `invoke_script`, `invoke_module` with `home=tmp_path`.
- `conftest.py` (`pytest_plugins`), `pytest.ini` (`cli_extended_config`, a single rootdir so reviewed node ids are stable), `run-gate.toml` (fake-integration gets `--cli-case-partial`, r3 gets the worktree library on `PYTHONPATH`), `tools/canary-run.sh` (library on the path and `--cli-case-partial` for the single-test canary runs).

## Deviations and decisions

1. VERSION source: per controller decision (`VERSION` = `2.0.0`, `CliIdentity.resolve`).
2. `install_wheel` additionally requires the filename to start with `cli_extended-` (brief: ends in `.whl`), because the entrypoint globs `cli_extended-*.whl` and an arbitrary name would be silently ignored. Tested.
3. The wheel is resolved (pin check and pointer fetch) before the tarball download, so a half-set pin fails with no network use; it is downloaded after the tree.
4. `inuse_partition_editor` tests (`test_inuse_partition_editor*.py`) keep their `subprocess` helpers: that script is a standalone stdlib tool, not a cli_extended consumer, and `_r1` runs real image and loop-device operations that must not be touched here.
5. `disable-stage2 --dry-run` output changed (F-001, bug fix); the existing test only asserted exit 0.
6. `--repo-url` now also refuses the canonical URL without `--bootstrap-url` (F-005, wontfix with rationale).
7. Process note: the new wheel tests were appended to `test_bootstrap_remote.py` with a shell heredoc (`cat >>`), against the Edit/Write-only directive; everything else went through Edit/Write.
8. `r2` was not run. Its `assay.toml` environment is `PYTHONPATH = "."` only, so the mutation lane has no library on its path; every CLI test already imported `cli_extended` before this change, so this is pre-existing, but the controller should verify r2 can import it. Adding the library path to `assay.toml` would trip the audit's `no-path-hacks` (only `run-gate.toml` is exempt).

## Library friction (for the controller)

1. `python -m cli_extended.cli` (the documented source-checkout command) fails with `distribution 'cli-extended' is not installed` because its own identity resolves from the distribution. I used a stub `cli_extended-0.2.0.dist-info` in the scratchpad on `PYTHONPATH`. The CONSUMERS Netcup-pilot snippet (`PYTHONPATH=../../libraries/cli-extended/src python -m cli_extended.cli surface check`) no longer works as written; either document the stub or let the tool fall back to a `version_file`.
2. `invoke_script(python=...)` takes one interpreter string, so no `-S`/`-I`. To prove "library not installed" I wrote a wrapper script. Also an explicit `python=` still inherits the caller's `PYTHONPATH` (the gate sets it to the library), so tests need `env={"PYTHONPATH": None}`. A `python_args` parameter or an `isolated=True` switch would remove both workarounds.
3. The pytest plugin is strict by default: any lane that runs a subset (fake-integration, canary single tests, a mutation tool, a focused local run) must add `--cli-case-partial`, and a missing flag shows up as a hundred-line collection error. Also node ids are rootdir-relative, so without a `pytest.ini` the ids in the catalog differ between `pytest tests/` and `pytest tests/test_x.py` runs on projects that have no ini file. Worth a CONSUMERS note, or the plugin could warn when the rootdir has no ini.
4. `--debug-raw` adds a `[WARN] --debug-raw is active ...` line and `[DEBUG]` lines on stderr; a byte-for-byte "unchanged" assertion needs filtering. Not documented in the contract text I read.
5. `Requires`/`Conflicts` require referenced options to default to `None`, so a defaulted option (`--repo-url`) had to lose its argparse default; `Requires` cannot say "differs from the default" (F-005).
6. The library-owned `--dry-run` help is one generic sentence for every verb (F-007); a per-verb sentence on `VerbSpec(dry_run=...)` would help.
7. Authoring cost: each settings-consuming verb produces five generated cases for `--config`/`--config-json` (member x2, spelling x2, conflict), so 8 verbs gave 62 catalog rows. Sharing one case across identical option declarations on several verbs would cut the review load without losing coverage.
8. Audit heuristics gave two false positives that I fixed by rewording tests: AC-01 flags any file containing `CliIdentity(` plus `re.` plus `VERSION` (a test that built a pinned identity), and AC-25 flags a comment that mentions `libraries/cli-extended` next to `sys.path`.
9. `CliIdentity.resolve` plus the wheel/zipimport path worked unchanged under `python -S` with the wheel on `sys.path`; skills resources inside a zip were not exercised (this tool registers no skills).
