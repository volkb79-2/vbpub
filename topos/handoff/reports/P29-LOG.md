# P29 Work Log

## Context

- Branch: feat/topos-p29-inspect-files-safety
- Worktree: /home/vb/volkb79-2/vbpub/.worktrees/-topos-p29-inspect-files-safety
- Base commit: ec0ebe0 docs(topos): carve P29 inspect files safety
- Package: P29 Inspect-files safety skeleton (v2 foundation)
- Current objective: Implement disabled-by-default, read-only inspect-files planning module and CLI plan command

## Timeline

```text
2026-07-09 UTC
- Action: Created git worktree on branch feat/topos-p29-inspect-files-safety from main
- Commands: git worktree add -b feat/topos-p29-inspect-files-safety .worktrees/-topos-p29-inspect-files-safety main
- Result: Worktree ready at ec0ebe0

- Action: Created inspect_files package with catalog (docker-json-log, systemd-journal, cgroup-files kinds), plan builder with dual gating (--inspect-files + --admin), path/argv safety validation, JSON/text rendering
- Files changed: topos/src/topos/inspect_files/__init__.py, catalog.py, plan.py
- Result: Package compiles, exports public API

- Action: Added CLI inspect-files plan command
- Files changed: topos/src/topos/cli.py (parse_inspect_files_args, _main_inspect_files, dispatch in main)
- Result: topos inspect-files plan --kind ... --target ... --inspect-files --admin [--json] works

- Action: Fixed stray gate.add_argument left in parse_inspect_files_args (multi_edit artifact)
- Action: Fixed missing --json arg in parse_bpf_args (lost during multi_edit)
- Files changed: topos/src/topos/cli.py

- Action: Wrote focused tests (42 tests)
- Files changed: topos/tests/test_inspect_files.py
- Result: 42 focused tests pass

- Action: Ran full suite
- Commands: python3 -m pytest topos/tests -q
- Result: 243 passed (201 original + 42 new)

- Action: py_compile clean for all changed files
- Commands: python3 -m py_compile topos/src/topos/inspect_files/__init__.py ...
- Result: clean, exit 0

- Action: Updated docs
- Files changed: topos/README.md, topos/docs/STATUS.md, topos/docs/ROADMAP.md
- Result: P29 marked Done in README, STATUS updated, ROADMAP updated

- Action: Added INSPECT-FILES.md safety contract doc
- Files changed: topos/docs/INSPECT-FILES.md
- Result: Safety contract documented

- Action: Wrote log and report

- Action: Committed feature branch

- Action: Controller review patched lexical path handling before merge
- Files changed: topos/src/topos/inspect_files/catalog.py,
  topos/tests/test_inspect_files.py, topos/docs/INSPECT-FILES.md,
  topos/handoff/reports/P29-LOG.md, topos/handoff/reports/P29-REPORT.md
- Result: Replaced `Path.resolve(strict=False)` with lexical normalization,
  fixed absolute `/sys/fs/cgroup/...` target handling, rejected cgroup `..`
  traversal, rejected unsafe Docker target characters, and added focused
  regressions.
```

## Decisions

- Decision: Mirror P21 admin-action-gating pattern (catalog enum + plan builder + gated dispatch)
  Reason: DRY — P21 established the pattern for disabled-by-default, preview-only CLI subcommands
  Impact: P29 follows the same architecture but adds path safety (path previews + validation) which P21 did not need

- Decision: Dual gating (--inspect-files AND --admin) instead of single flag
  Reason: Per handoff doc, the inspection feature is independently gated from admin actions
  Impact: Both flags are required, matching the spec's intent that file inspection is sensitive even in admin mode

- Decision: cgroup-files kind returns 20+ known cgroup filenames as path_previews, no command_previews
  Reason: Cgroup files are plain text reads, not commands — command_previews would be misleading
  Impact: Commands list is empty for this kind, consistent with the no-execution contract

- Decision: Path safety uses lexical normalisation without `Path.resolve()`
  Reason: `Path.resolve(strict=False)` can inspect existing path prefixes and
  follow symlinks; the safety contract requires path previews only
  Impact: Path previews are deterministic and do not depend on host filesystem
  state.

## Blockers

- None.

## Validation

```bash
/tmp/vbpub-topos-p29-venv/bin/python -m pytest topos/tests/test_inspect_files.py -v
# 42 passed in 0.35s

PYTHONPATH=topos/src /tmp/vbpub-topos-p29-venv/bin/python -m pytest topos/tests/test_inspect_files.py -q
# 44 passed in 0.33s after controller review

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests/test_inspect_files.py -q
# 44 passed in 0.29s on main after merge conflict resolution

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m py_compile topos/src/topos/inspect_files/__init__.py topos/src/topos/inspect_files/catalog.py topos/src/topos/inspect_files/plan.py topos/src/topos/cli.py topos/tests/test_inspect_files.py
# clean, exit 0 on main after merge conflict resolution

/tmp/vbpub-topos-p29-venv/bin/python -m pytest topos/tests -q
# 243 passed in 30.27s

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q
# 261 passed in 28.94s on main after merge conflict resolution

/tmp/vbpub-topos-p29-venv/bin/python -m py_compile \
  topos/src/topos/inspect_files/__init__.py \
  topos/src/topos/inspect_files/catalog.py \
  topos/src/topos/inspect_files/plan.py \
  topos/src/topos/cli.py \
  topos/tests/test_inspect_files.py
# clean, exit 0

PYTHONPATH=topos/src /tmp/vbpub-topos-p29-venv/bin/python -m topos.cli \
  inspect-files plan --kind docker-json-log --target c1 --inspect-files --admin --json
# {"kind": "docker-json-log", "mode": "plan", ...}

PYTHONPATH=topos/src /tmp/vbpub-topos-p29-venv/bin/python -m topos.cli \
  inspect-files plan --kind docker-json-log --target c1
# exit 2, "file inspection is not enabled"

PYTHONPATH=topos/src /tmp/vbpub-topos-p29-venv/bin/python -m topos.cli \
  inspect-files plan --kind docker-json-log --target c1 --inspect-files
# exit 2, "admin mode is not enabled"

PYTHONPATH=topos/src /tmp/vbpub-topos-p29-venv/bin/python -m topos.cli \
  inspect-files plan --kind docker-json-log --target /etc/passwd --inspect-files --admin
# exit 2, "must be a container id or name, not a path"
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
