# P48 Work Log

## Context

- Branch: feat/groop-p48-inspect-files-journal-snapshot
- Worktree: .worktrees/-groop-p48-inspect-files-journal-snapshot
- Base commit: fef1728 (main, after P45/P46/P47/P50/P51/P52/P43/P40/P41/P55/P57/...)
- Package: P48 - Bounded Journald Inspection Snapshot
- Current objective: Add bounded non-following journald snapshot via fixed absolute journalctl argv

## Timeline

```text
2026-07-13 00:15 UTC
- Action: Read handoff P48, P45 report/log, existing P29/P45 codebase
- Result: Full understanding of requirements and patterns

2026-07-13 00:20-00:45 UTC
- Action: Updated catalog.py: enhanced _validate_systemd_target to reject
  option-like names (starting with dash), added + to allowed chars,
  updated preview commands to use absolute paths
- Files: groop/src/groop/inspect_files/catalog.py
- Result: Systemd unit validation now blocks -u, --unit, etc.

2026-07-13 00:45-01:15 UTC
- Action: Added journald runner and reader logic to reader.py:
  _JournaldRunResult dataclass, _default_journald_runner (subprocess.run,
  shell=False, bounded timeout), _validate_journald_read_target,
  _run_journald_snapshot, and wired SYSTEMD_JOURNAL into build_inspect_read
  with injectable journald_runner and journal_timeout kwargs
- Files: groop/src/groop/inspect_files/reader.py
- Result: journald content reads work via fixed absolute argv

2026-07-13 01:15-01:20 UTC
- Action: Updated CLI help text to mention systemd-journal in read subcommand
- Files: groop/src/groop/cli.py
- Result: CLI help shows systemd-journal as valid kind for read

2026-07-13 01:20-01:25 UTC
- Action: Updated __init__.py docstring, reader.py docstring to reflect
  subprocess-based journald reads
- Files: groop/src/groop/inspect_files/__init__.py,
  groop/src/groop/inspect_files/reader.py
- Result: Docstrings accurate

2026-07-13 01:25-01:40 UTC
- Action: Created journald fixture sample output, updated existing no-subprocess
  tests in test_inspect_files.py to acknowledge reader.py's intentional
  subprocess import, fixed test_unknown_kind_returns_error to use "nosuch-kind"
- Files: groop/tests/fixtures/inspect_files/journal/ssh-service-sample.txt,
  groop/tests/test_inspect_files.py
- Result: Existing tests pass

2026-07-13 01:40-01:55 UTC
- Action: Added 18 new tests: 5 systemd target validation + 13 journald content
  read tests covering success, JSON format, text format, timeout error, nonzero
  exit error, runner OSError, empty/dash/path target rejection, line truncation,
  gating denial, root requirement, and timeout validation
- Files: groop/tests/test_inspect_files.py
- Result: All 132 inspect_files tests pass

2026-07-13 01:55-02:05 UTC
- Action: Updated INSPECT-FILES.md, STATUS.md, RELEASE-READINESS.md,
  ROADMAP.md docs. Created P48-LOG.md.
- Files: groop/docs/INSPECT-FILES.md, groop/docs/STATUS.md,
  groop/docs/RELEASE-READINESS.md, groop/docs/ROADMAP.md
- Result: Docs updated to reflect journald snapshot support

2026-07-13 02:05-02:10 UTC
- Action: Full suite run
- Command: PYTHONPATH=groop/src python3 -m pytest groop/tests -q
- Result: 845 passed, 2 skipped in 121.53s
- Action: Full py_compile
- Result: ALL COMPILE OK
```

## Decisions

- Decision: Inject journald runner via Callable parameter (matching P46 pattern)
  Reason: Tests must not invoke actual journalctl. The injectable runner
  returns canned _JournaldRunResult without subprocess.
  Impact: Clean test isolation without mocking or monkey-patching.

- Decision: Timeout/nonzero exit returns typed error, never fallback content
  Reason: Per handoff contract: "never falls back to arbitrary reads"
  Impact: Journald read always returns either content or typed error.

- Decision: Use `--output=short-iso` for deterministic timestamps
  Reason: short-iso output is deterministic and locale-independent
  Impact: Fixture comparisons are stable across hosts.

- Decision: Default journal_timeout = 30s, absolute max = 60s
  Reason: Journald reads may block on slow journal files; 30s is reasonable,
  60s hard cap prevents pathological values.
  Impact: Bounded wall-clock risk.

## Validation

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_inspect_files.py -q
# 132 passed in 0.85s

PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 845 passed, 2 skipped in 121.53s

mapfile -d '' pyfiles < <(find groop/src/groop groop/tests -name '*.py' -print0)
python3 -m py_compile "${pyfiles[@]}"
# ALL COMPILE OK
```

## Controller / frontier-review post-merge validation (2026-07-13)

- Frontier review pass #2 approved; merged `--no-ff` into main.
- Post-merge full suite from main: `914 passed, 2 skipped, 1 warning in ~121s`
  (PYTHONPATH=groop/src, /home/vscode/.venv python3.14, pytest 8.4.2, textual 8.2.8),
  integrated tree including concurrently-merged pwmcp P02. No regressions.
- Note: full suite fails under `-W error` on main independently of this package
  (third-party `jsonschema`/`schemathesis` DeprecationWarning); pre-existing env condition.
