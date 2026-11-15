<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-07; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/mt-cli-REVIEW2.md -->
<!-- relation: mounttrace CLI review; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

**Verdict: MERGE**

**Summary:** all six first-review blockers are fixed at `ae5bfafa3`; the claimed exact-head gates, rewritten-commit provenance, behavioral fixes, and guard mutations check out. Two non-blocking documentation/test-strength nits remain.

# Independent re-review of `mt-cli-20261109`

Reviewed head: `ae5bfafa3f3ee6c449e94d0ef4bed527448f49f0`  
Fetched `origin/main`: `b6484d83aeccff008fd6d8620c6c8f111c656d05`  
Merge base: `942a521bc46995d49ea323f67c0a032fdb1fc21d`  
Review worktree status: clean; no reviewed files changed.

## Blocking

None.

## Nits

1. **The backlog still attributes the review-fixed behavior only to the pre-fix commits.**

   `scripts/mounttrace/KNOWN_ISSUES_TODO_BACKLOG.md:21`, `:22`, `:23`,
   `:144`, `:158`, and `:170` cite `c0c0302f0` / `6794cf18f` and the earlier
   `28 passed, ... 14 subtests` result. Those statements are now incomplete:
   `-v`, removed-mode documentation, bare errors, and the VM row selector were
   fixed in `ae5bfafa3`, whose final suite result is `32 passed, 22 skipped,
   21 subtests passed`.

   Exact fix: append `review fixes ae5bfafa3` to the MT-08/09/10 status citations,
   and update the MT-09/MT-10 status paragraphs to the final exact-head counts.

2. **The automated error matrix does not pin all five subcommands or both error
   phases.**

   `scripts/mounttrace/tests/test_mounttrace.py:563` tests explain/compare and
   `:531` tests explain under watch; the shared `selected_pid_specs()` branches
   for audit, predict, and soulmask are independent and should remain covered.
   Independent review probes confirmed all current command-specific paths work,
   so this is not a current behavior failure.

   Exact fix: parameterize the error test across `explain`, `compare`, `audit`,
   `predict`, and `soulmask`, both at initial read and in a watch refresh, for
   bare ENOENT/ESRCH and bare PermissionError; assert exit 1 and the
   PID-specific versus multi-view message.

## First-review blocker verification

| Blocker | Independent result |
|---|---|
| `-v` header truncation | **FIXED.** At `COLUMNS=80`, a 132-character long-path fixture produced a default header of exactly 80 columns and zero default lines over 80; `-v` emitted the complete 137-character `host <path>` header. Removing the `verbose` branch at `mounttrace.py:762` made `test_verbose_preserves_a_long_header_at_80_columns` fail with the expected middle-cut header. |
| Removed modes in current documentation | **FIXED.** Repo-wide grep found the five removed spellings only in negative parser tests and historical backlog Finding/Fix text, never as current usage. Three documented shapes ran with safe substitutions and `--no-probe`: `explain`, `compare`, and `audit`; all returned 0. Adding `--all-hidden` to `README.md` made `test_current_docs_do_not_advertise_removed_option_modes` fail. |
| Gate labels | **FIXED.** Both report-named invocation logs contain `run-gate.commit=ae5bfafa3f3ee6c449e94d0ef4bed527448f49f0`; no `--allow-dirty` appears. Mounttrace `suite` PASS: 32 passed, 22 skipped, 21 subtests. Debian R0/R1 PASS: 1608 passed, 16 deselected. |
| Bare missing-PID/permission errors | **FIXED.** Real bogus PID `999999:/` returned exit 1 with `pid 999999 vanished (...)`, `a selected process vanished (...)`, or the equivalent clear text for all five commands, both with and without `--watch`. Injected bare and `/proc/42` ENOENT, ESRCH, and EACCES cases produced the same actionable class across all five commands; bare EACCES during a watch refresh was also checked for all five. No raw `[Errno ...]` remained. |
| VM test KeyError | **FIXED.** `report_rows(output, table_name)` applies the table-name predicate before iterating `table['rows']` (`tests/test_mounttrace.py:117`), and the topology test selects `PATH WALK` before indexing `SOURCE` (`:958`). Removing the predicate deterministically recreated `KeyError: 'SOURCE'` in the focused helper test. |
| Missing Luna trailers | **FIXED.** All five commits from `origin/main..HEAD` carry exactly `Co-Authored-By: Codex gpt-6-luna <noreply@openai.com>`. Each old/new pair has `git diff --quiet` status 0 (`7a1cd02fb`→`c0c0302f0`, `2df7ab98b`→`6794cf18f`, `d938e6e82`→`7409d1418`, `2c23cb83b`→`a634d61bb`), and the cumulative pre-fix/final rewrite trees are equal. |

## First-review nits

- MT-09 now says `CODE COMPLETE (VM/Wings acceptance owed)` rather than `FIXED`
  (`KNOWN_ISSUES_TODO_BACKLOG.md:22`, `:158`): **fixed**.
- `soulmask --help` now explains both `--state-file` and `--volumes-root`,
  including their defaults (`mounttrace.py:1413`): **fixed**.

## MT-08 / MT-09 / MT-10 rechecks

- Actual host topology `compare / /proc --no-probe` returned **3** and rendered
  `DIFFERENT`, as required.
- Actual `soulmask <self-pid> --volumes-root /tmp --no-probe --json` returned
  **0** and contained one bundled `kv.audits` entry plus `state_file` and
  `volumes_root`.
- The focused JSON snapshot passed with the exact top-level key set
  `{schema, command, header, verdict, findings, kv, tables}` and `schema == 1`.
- Default width remained bounded; the verbose path retained all content.

## Independent mutation probes

Every probe was applied alone in `<controller-scratch>/rv-mtcli2`, run through the
narrow owning test with `nice -n 10`, and restored before the next probe.

| Mutation | Expected witness | Result |
|---|---|---|
| Restore unconditional `middle_cut(header)` | verbose long-header test | KILLED |
| Add `--all-hidden` to README | removed-mode docs test | KILLED |
| Remove `FileNotFoundError` from selected-process mapping | bare ENOENT test | KILLED |
| Ignore `table_name` in `report_rows()` | VM row selector helper | KILLED (`KeyError: 'SOURCE'`) |
| Make `Report.exit_status()` always return 0 | DIFFERENT compare test | KILLED (0, expected 3) |
| Skip bundled `add_audit()` from Soulmask | namespace-free Soulmask test | KILLED (`kv.audits` missing) |
| Rename JSON `schema` key | JSON snapshot | KILLED |

## Focused verification run

Scratch copy: `<controller-scratch>/rv-mtcli2`, made with `git archive` from
`ae5bfafa3` and committed as a throwaway baseline. The namespace-free
selection ran once before and once after mutations:

```text
nice -n 10 /opt/mdt/venv/bin/python -m pytest \
  scripts/mounttrace/tests/test_mounttrace.py -q -p no:cacheprovider \
  -k 'not TopologyTests and not VmRootTests'

32 passed, 22 deselected, 21 subtests passed
```

The final scratch tree was clean (`git status --short` empty, `git diff --check`
clean).

## Merge check

After fetching the moved `origin/main` (`b6484d83a`):

```text
git merge-tree --write-tree origin/main HEAD
bbaa534e4dad0814f1a64f93eb3140c698bbf552
exit 0
```

There are no textual or rename conflicts. The target-side changes since the
branch base do not intersect the exact files changed by this branch, although
both touch the `debian-install-v2` project area.

## Unchecked / owed

- The QEMU VM lane and real Wings-tree validation at `<host>`/`<host>` remain
  owed; this review had no host access and correctly did not run them.
- No live Docker/container topology was exercised. The namespace-dependent
  classes were not run in the cockpit; the exact-head registered suite log was
  inspected instead.
- The two nits above do not invalidate current behavior or gate evidence.

## Provenance snapshot

```text
branch head       ae5bfafa3f3ee6c449e94d0ef4bed527448f49f0
origin/main       b6484d83aeccff008fd6d8620c6c8f111c656d05
merge-tree tree   bbaa534e4dad0814f1a64f93eb3140c698bbf552
merge-tree exit   0
worktree status   clean
```
