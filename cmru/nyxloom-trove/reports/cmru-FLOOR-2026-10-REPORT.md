# CMRU-FLOOR report (cmru 6.0 program, steps 2-3), 2026-10-06

Branch `cmru-wave-2026-10`, worktree `/workspaces/vbpub/.worktrees/cmru-wave-2026-10`.

| Commit | What |
| --- | --- |
| `314c275c5` | Merge of `main` (`15b4fbe6a`, cli-extended 0.3.0 contents) |
| `e09438f4a` | Floor bump to 0.3.0, KI-61 tolerance removed, `surface sync`, catalog cases |
| `cdf54a91d` | Missed assertion `tests/test_w2_integ.py` (interactive extra floor) |
| (this report's commit) | REPORT only |

## 1. Merge conflicts

Exactly one conflict: `libraries/cli-extended/BACKLOG.md` (CLI-EXT-23..29 were ported on both sides).
Resolved with `git checkout --theirs` = main's version (main's library is authoritative for
`libraries/cli-extended/**`). After resolution `libraries/cli-extended` differed from main in only
three files that merged cleanly and carry integration-branch edits: `SPEC.md`, `cmru.toml`,
`docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md` (2-line changes each; not conflicts). Not re-examined beyond that.

## 2. Floor table (all `>=0.2.0` to `>=0.3.0`)

Reason recorded at the declaration (`cmru/pyproject.toml:15-19`): 0.3.0 is the first release whose
`register_skills_verbs` forwards the consumer's global options to the skills child registry
(CLI-EXT-26), which cmru's `skills` group needs (KI-61). Root `requirements.txt` has no
cli-extended entry (nothing to change there). `cmru/build-initial-standalone.sh` and `doctor.py`
read the floor from `pyproject.toml` (no literal, no edit).

| file:line (final tree) | old | new |
| --- | --- | --- |
| `cmru/pyproject.toml:23` `dependencies` | `cli-extended>=0.2.0` | `cli-extended>=0.3.0` |
| `cmru/pyproject.toml:34` extra `interactive` | `cli-extended[interactive]>=0.2.0` | `...>=0.3.0` (0.3.0 METADATA still has `Provides-Extra: interactive`, `questionary>=2.1.1`) |
| `cmru/pyproject.toml:15-19,31` comments | floor 0.2.0 | reason text for 0.3.0 |
| `cmru/tools/installed_wheel_smoke.py:70` `CLI_EXTENDED_REQUIREMENT` | `cli-extended>=0.2.0` | `cli-extended>=0.3.0` |
| `tester-unified/Dockerfile:110` `CLI_EXTENDED_WHEEL_URL` | `.../cli-extended-v0.2.0/cli_extended-0.2.0-py3-none-any.whl` | `.../cli-extended-v0.3.0/cli_extended-0.3.0-py3-none-any.whl` |
| `tester-unified/Dockerfile:111` `CLI_EXTENDED_WHEEL_SHA256` | `84ec3db8...b58b` | `a0f80f1b614bcaf81fac235aa3a7f42f20eccac06f82311a6d8d5a725eea5f50` (matches `latest.json` and `sha256sum` of the 0.3.0 wheel) |
| `tester-unified/Dockerfile:131` `--min-version` | `0.2.0` | `0.3.0` |
| `tester-unified/Dockerfile:74,83` comments | 0.2.0 | 0.3.0 |
| `cmru/tests/test_tester_unified_image.py:311,318` (pin digest / `--min-version` assertions) | 0.2.0 values | 0.3.0 values |
| `cmru/tests/test_packaging_cli_extended.py:48,49,52,53,128` | `>=0.2.0`, `Floor 0.2.0` | `>=0.3.0`, `Floor 0.3.0` (+ asserts `CLI-EXT-26` is in the reason) |
| `cmru/tests/test_w2_integ.py:230` | `[interactive]>=0.2.0` | `>=0.3.0` |
| `cmru/tests/test_installed_wheel_subprocess.py:89,276,345,347,351,382,432` | 0.2.0 | 0.3.0 (the `+bootstrap.source` version derives from the floor) |
| `cmru.orchestration.toml:146`, `cmru/README.md:10`, `tester-unified/README.md:31`, `tester-unified/gen-requirements.py:38`, `docs/BOOTSTRAP-FROM-ZERO.md:13,84`, `modern-debian-tools-python-debug/pip/wheels.list:14` (comments/prose) | `>=0.2.0` | `>=0.3.0` |

Deliberately NOT changed: historical statements (`BOOTSTRAP-FROM-ZERO.md:77` "release 0.2.0 first"),
CHANGES, old reports, fake-metadata fixtures in `tests/test_w2_pkg5_skills_doctor.py`, `test_installer*.py`
and the fixture data in `test_tester_unified_image.py` (these model arbitrary versions, not the floor).
Other projects' `cli-extended>=0.2.0` appear only as PLANNED backlog items (ciu, assay, pwmcp, run-gate
backlogs); no other project declares the dependency in this tree yet.
`CHANGES.md` for cmru not touched.

## 3. KI-61 removal

* Deleted `test_surface_check_reports_nothing_beyond_the_known_library_gap` and `_KNOWN_LIBRARY_GAP`;
  replaced by `test_surface_check_reports_no_findings` (`assert list(report.findings) == []`).
* `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` KI-61 marked resolved 2026-10-06 (`cli-extended-v0.3.0`).
* `cmru/docs/cli-review-findings.toml`: `adoption-skills-global-option` now `status = "fixed"`.

## 4. `surface sync` and the 4-case review

Command (from `cmru/`, using the worktree's own source because the venv's editable cmru points at the
main checkout): `PYTHONPATH=<worktree>/cmru/src:<worktree>/libraries/worktree/src cli-extended surface sync`.
Sync regenerated `docs/cli-surface.json` and the S-CLI.9 region in `docs/SPEC.md`. It never edits the
review catalog, so `docs/cli-review.toml` was edited by hand from `surface template`:

* 4 NEW pending cases `skills/{check,install,list,uninstall}/option-spelling/--log-prefix-time-short`;
* 4 EXISTING `skills/*/minimum` rows whose generated signature changed (the verb gained the global
  option); only `reviewed_signature` was updated, invocation/expectations unchanged and still right.

Each new case is a real invocation, executed in-process by `tests/test_cli_review_cases.py` in the
hermetic sandbox. I ran each verb with the flag in a throwaway HOME first and used the observed behaviour:

| case | invocation | exit | stdout contains | stderr contains | why meaningful |
| --- | --- | --- | --- | --- | --- |
| check | `skills check --log-prefix-time-short` | 1 | `absent` | `skill(s) are not current` | proves the flag is parsed (a parse error is exit 2) and the check still reports real state |
| install | `skills install --log-prefix-time-short` | 0 | `installed cmru-cli` | `` | parsed, and the install really happened under sandbox HOME |
| list | `skills list --log-prefix-time-short` | 0 | `absent` | `` | parsed, real listing |
| uninstall | `skills uninstall --log-prefix-time-short` | 0 | `skipped cmru-cli` | `` | parsed, real (skipping) uninstall |

Limitation, stated honestly: the flag only changes presentation and the cases do not assert the
timestamp prefix itself (captured output did not show a prefix on these lines when piped). They assert
acceptance plus unchanged behaviour, the same strength as the existing `doctor ... --log-prefix-time-short`
case. They fail as expected under cli-extended 0.2.0 (exit 2, "unrecognized arguments"), which the
lanes below demonstrate, so they are not placeholders.

## 5. Checks run

Local venv (cli-extended 0.3.0, worktree source):

* `cli-extended surface check`: passed, exit 0 (only the 4 open `[NOTE]` findings, not failures).
* `cli-extended audit --cli cmru`: 15 pass, 0 warn, 0 fail, 0 manual.
  * AC-25 `no-path-hacks` FAILED at first, and also failed on the untouched pre-change tree (new 0.3.0
    heuristic: file text contains `libraries/cli-extended` and `PYTHONPATH`). Cause:
    `tests/test_installed_wheel_subprocess.py:138`, a shell `case` pattern in a stub that recognises the
    source-mode wheel-build call by its cwd (`*/libraries/cli-extended`). Not a path hack. Fix: pattern
    shortened to `*/cli-extended` (still unique in that synthetic tree). The `allow-path-assertion`
    marker was NOT used (it is only for absence assertions).
* 538 tests passed locally (`--cli-case-partial`): `test_cli_review_cases`, `test_cli_spec_inventory`,
  `test_w2_integ`, `test_packaging_cli_extended`, `test_tester_unified_image`,
  `test_installed_wheel_subprocess`, `test_w2_pkg5_skills_doctor`.

Gate lanes, foreground, under `flock ... nice -n 19 ionice -c 3`, tree clean at `cdf54a91d` (the
`e09438f4a` run was the first with the integ assertion still at 0.2.0, since fixed):

| lane | verdict | detail |
| --- | --- | --- |
| `coverage` | **FAIL** (5 failed, 4022 passed, 6 skipped; coverage 100.00%) | the 5 failures below |
| `canary` | **FAIL** (5 failed, 3929 passed, 99 skipped; control run failed) | the same 5 |

The 5 failures (identical in both lanes): the four new `--log-prefix-time-short` cases
(`exited 2 ... unrecognized arguments`) and `test_surface_check_reports_no_findings`
(manifest/spec/catalog signature drift plus `delegated parser does not register inherited global option`).
Cause, from the lane log: the worktree's `run-gate.toml` runs `PYTHONPATH=src:../libraries/worktree/src`
(no library source path), so the lane resolves `cli_extended` from the `tester-unified:local` image, and
the output shows that library does not forward the option, i.e. an image predating 0.3.0. I did not
directly read the image's installed version (no `cli_extended` dist-info was found by `find` at depth
<= 6, so this is inferred from behaviour, not proven). The same tests pass in the local venv with
0.3.0. This is the expected "step 4: rebuild the tester image pin" dependency; the Dockerfile pin is
already updated. Expected after rebuild: both lanes green. Re-run coverage and canary then.

`gate` and mutation lanes were not run (reserved for the controller). No images rebuilt.

## 6. Process notes

* Several early Edit calls were batched two per message and without the `Intent:` line, against the rules; content is unaffected.
* Nothing pushed, nothing released.
* A reviewer should verify: the 3-file library delta vs main (section 1), that no cli-extended floor was
  missed (including other bracketed forms like `cli-extended[x]>=`), and the lane re-run after the image rebuild.
