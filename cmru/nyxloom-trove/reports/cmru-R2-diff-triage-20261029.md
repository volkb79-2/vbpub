# CMRU R2 diff-scoped triage — 2026-10-29

## Campaign identity

- Lane: `r2-debt`.
- Base: `3458fa588f87ef8fa7f63a27ce77fa89809ad1f8` (`cmru-v7.1.0` era).
- Head: `2e62261a1d3450dd0cbbbaa5349fb8d7fe22ffbb` (`cmru-v7.1.2`).
- Candidates: 48; killed: 28; survived: 20. The recorded campaign status is
  `failed`, as expected while survivors remain.
- Source evidence:
  - Campaign JSON: `.worktrees/r2-cmru-diff-20261029/r2-campaign-project/.assay/mutation-cmru-r2-debt-v7.1.0.json`.
  - Progress stream: `.worktrees/r2-cmru-diff-20261029/r2-campaign-project/.assay/progress-mutation-cmru-r2-debt-v7.1.0.jsonl`.
  - Read-only controller record: `/home/vscode/vbpub-release-program-20261027/codex/runs/cmru-r2-diff.last.txt`.
  - Runner: `/home/vscode/vbpub-release-program-20261027/evidence/r2-cmru-diff-runner.sh`.

## Survivor disposition

| Survivor | Mutated code | Classification | Behavioral oracle or justification |
|---|---|---|---|
| `cmru/src/cmru/cli.py:2714` `And->Or` | `current is not None and current is not previous` → `... or ...` | `UNTESTED-BEHAVIOUR` | A nested logging scope must leave its inherited outer tee open. `tests/test_release_logging_lifecycle.py::test_nested_logging_scope_leaves_the_outer_release_tee_open` checks output written after the inner scope is still logged. |
| `cmru/src/cmru/process_guard.py:29` `True->False` | `@dataclass(frozen=True)` → `frozen=False` | `UNTESTED-BEHAVIOUR` | Process records are immutable snapshot values. `tests/test_process_guard.py::test_process_records_cannot_be_changed_after_a_snapshot_is_created` proves a consumer cannot rewrite the recorded process identity. |
| `cmru/src/cmru/process_guard.py:80` `Or->And` | `not process_dir.exists() or not task_dir.exists()` → `... and ...` | `UNTESTED-BEHAVIOUR` | If a thread directory disappears while the process directory remains, that child is ignored. `tests/test_process_guard.py::test_read_children_ignores_a_thread_directory_that_disappeared` covers this race. |
| `cmru/src/cmru/process_guard.py:81` `[]->None` | The vanished-process branch returns `[]` → `None` | `UNTESTED-BEHAVIOUR` | A vanished process has no children and must return an iterable. `tests/test_process_guard.py::test_read_children_returns_an_empty_list_when_the_process_disappeared` asserts the list result. |
| `cmru/src/cmru/process_guard.py:121` `Eq->NotEq` | `pid == root_pid` → `pid != root_pid` | `UNTESTED-BEHAVIOUR` | A non-root process removed after an empty task listing is ignored. `tests/test_process_guard.py::test_task_enumeration_ignores_a_process_removed_after_an_empty_listing` constructs the disappearance deterministically. |
| `cmru/src/cmru/process_guard.py:121` `Or->And` | `pid == root_pid or process_dir.exists()` → `... and ...` | `UNTESTED-BEHAVIOUR` | An existing non-root process with an empty task directory is unavailable evidence and must refuse. `tests/test_process_guard.py::test_task_enumeration_refuses_an_existing_empty_nonroot_task_directory` checks it. |
| `cmru/src/cmru/process_guard.py:141` `GtE->Gt` | `close_paren >= 0` → `close_paren > 0` | `EQUIVALENT` | A valid `/proc/<pid>/stat` command-closing `)` follows the decimal PID and opening `(`, so its index cannot be zero. The difference is outside the valid proc-stat input domain. |
| `cmru/src/cmru/process_guard.py:142` `Lt->LtE` | `open_paren < 0` → `open_paren <= 0` | `EQUIVALENT` | A valid `/proc/<pid>/stat` begins with a decimal PID and a space, so the command-opening `(` has an index greater than zero. |
| `cmru/src/cmru/process_guard.py:142` `Or->And` (first `or`) | `open_paren < 0 or close_paren <= open_paren` → `... and ...` | `UNTESTED-BEHAVIOUR` | A record with a closing but no opening command parenthesis must be refused. `tests/test_process_guard.py::test_process_record_rejects_missing_or_reversed_command_parentheses` covers it. |
| `cmru/src/cmru/process_guard.py:142` `LtE->Lt` (parenthesis order) | `close_paren <= open_paren` → `<` | `EQUIVALENT` | If both parentheses exist they cannot have the same index. If both are absent, `fields` is empty and the final length guard already refuses the record. |
| `cmru/src/cmru/process_guard.py:142` `Or->And` (second `or`) | `close_paren <= open_paren or len(fields) <= 19` → `... and ...` | `UNTESTED-BEHAVIOUR` | A closing parenthesis before the opening one must be refused even when enough tokens follow. The reversed-parentheses case in `test_process_record_rejects_missing_or_reversed_command_parentheses` distinguishes it. |
| `cmru/src/cmru/process_guard.py:142` `LtE->Lt` (field count) | `len(fields) <= 19` → `len(fields) < 19` | `UNTESTED-BEHAVIOUR` | Exactly 19 fields cannot supply `start_time`. `tests/test_process_guard.py::test_process_record_rejects_a_stat_record_missing_the_start_time_field` requires the typed refusal. |
| `cmru/src/cmru/process_guard.py:197` `Is->IsNot` | `root_pid is None` → `root_pid is not None` | `UNTESTED-BEHAVIOUR` | Omitting `root_pid` must guard the current process. `tests/test_process_guard.py::test_descendant_guard_defaults_to_the_current_process_root` uses a fake proc tree rooted at the current PID. |
| `cmru/src/cmru/process_guard.py:237` `Lt->LtE` | `time.monotonic() < deadline` → `<= deadline` | `UNTESTED-BEHAVIOUR` | Cleanup must not begin another pass at the deadline. `tests/test_process_guard.py::test_descendant_cleanup_does_not_start_another_pass_at_the_deadline` drives the clock at the exact boundary. |
| `cmru/src/cmru/process_guard.py:249` `True->False` | `ctypes.CDLL(None, use_errno=True)` → `use_errno=False` | `UNTESTED-BEHAVIOUR` | A failed kernel `prctl` must report its actual errno. `tests/test_process_guard.py::test_prctl_reports_the_errno_from_the_failed_kernel_call` checks `EINVAL` from an invalid option. |
| `cmru/src/cmru/process_guard.py:265` `None->[]` | The SET branch returns `None` → `[]` | `EQUIVALENT` | `_prctl`'s SET return is ignored by both `enable_child_subreaper` and `restore_child_subreaper`; GET's integer return is unchanged. |
| `cmru/src/cmru/version.py:118` `True->False` | `text=True` → `text=False` in the origin query | `UNTESTED-BEHAVIOUR` | Refusal diagnostics must remain text, not bytes representations. `tests/test_release_version_plan.py::test_origin_preflight_keeps_git_diagnostics_as_text` compares the real Git diagnostic. |
| `cmru/src/cmru/version.py:120` `Or->And` | `result.returncode != 0 or not result.stdout.strip()` → `... and ...` | `UNTESTED-BEHAVIOUR` | A failed Git lookup must refuse even with partial stdout. `tests/test_release_version_plan.py::test_origin_preflight_refuses_a_failed_lookup_even_with_partial_output` checks it. |
| `cmru/src/cmru/version.py:121` `Or->And` (stderr priority) | `stderr.strip() or stdout.strip()` → `stderr.strip() and stdout.strip()` | `UNTESTED-BEHAVIOUR` | When both streams have diagnostics, stderr has priority. `tests/test_release_version_plan.py::test_origin_preflight_prefers_stderr_when_git_returns_both_streams` checks it. |
| `cmru/src/cmru/version.py:121` `Or->And` (empty fallback) | `stdout.strip() or "origin has no configured URL"` → `... and ...` | `UNTESTED-BEHAVIOUR` | Empty Git output must use the actionable fallback. `tests/test_release_version_plan.py::test_origin_preflight_uses_its_fallback_when_git_returns_no_diagnostics` checks it. |

## Behavioral proof

The 15 focused test nodes (16 cases including the malformed-stat parameter)
passed on the real source. Each of the 16 `UNTESTED-BEHAVIOUR` mutations was
then applied to a disposable copy of the CMRU project under `/tmp`; its mapped
focused test failed with pytest exit 1. No mutation was applied to this
worktree's source files. The isolated logs are under
`/tmp/cmru-r2-mutant-proof-lpwxunfg/proof-logs/`.

## Remaining R2 debt

The 48-candidate run only covered the source delta from the 7.1.0-era base to
7.1.2. It does not judge the full `cmru-v6.1.0` through 7.1.2 source. A
plan-only `assay plan cmru-first-release-r2 --file cmru/assay.toml
--allow-dirty --json` on `origin/main` `d2e16daa1` found 4,465 candidates in 35
files. This is close to the prior estimate of about 4,421; it ran no lane,
tests, campaign, or container. The JSON plan was saved outside the repository
at `/tmp/cmru-first-release-r2-plan-20261029.json`.

Suggested single-file shard order by risk (candidate counts from that plan):

1. **Release transitions and retained state (2,660):** `cli.py` 1,327;
   `transaction.py` 748; `gate_snapshot.py` 302; `release.py` 162;
   `tester_gate.py` 121.
2. **Configuration, version, and trust decisions (872):** `versions.py` 230;
   `config.py` 211; `version_registry.py` 126; `version.py` 119;
   `version_config.py` 113; `git_auth.py` 44;
   `credential_handoff.py` 26; `errors.py` 3.
3. **Publishing and project execution (531):** `changelog.py` 117;
   `handlers.py` 100; `runner.py` 82; `tool_deps.py` 62; `bundle.py` 43;
   `dependencies.py` 38; `ghcr.py` 27; `hosts/github.py` 22;
   `release_controller.py` 18; `delegated.py` 15;
   `delegate_targets.py` 7.
4. **Support and bootstrap surfaces (402):** `scaffold.py` 76; `getpy.py` 75;
   `release_notes.py` 69; `doctor.py` 41; `resolve.py` 35;
   `process_guard.py` 30; `standards.py` 30; `cli_support.py` 17;
   `output.py` 14; `manifest.py` 13; `hosts/__init__.py` 2.

KI-79 and KI-99 remain open. The exact-tag `cmru-v7.0.0` obligation and the
whole-source obligation are not closed by this diff-scoped run.
