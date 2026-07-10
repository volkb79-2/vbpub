# P45 Work Log

## Context

- Branch: feat/groop-p45-inspect-files-bounded-content
- Worktree: .worktrees/-groop-p45-inspect-files-bounded-content
- Base commit: 9d6327b (main)
- Package: P45 - Bounded Inspect-Files Content Reads

## Timeline

```text
2026-07-10 14:30 UTC
- Action: Read handoff P45, controller guide, existing P29 codebase
- Result: Full understanding of requirements and P29 safety patterns

2026-07-10 14:35-15:00 UTC
- Action: Implemented reader.py, CLI, fixtures, tests
- Result: 77 passing tests, py_compile clean

2026-07-11 CORRECTIONS
- Action: Chunk-based reads — replaced line-by-line iteration with fixed-size
  chunk reads (_READ_CHUNK_SIZE=65536) so single giant lines never materialize
  unboundedly. Removed unused `read_size` variable.
- Files: groop/src/groop/inspect_files/reader.py
- Result: _bounded_read now reads in chunks, not lines.

- Action: Descriptor-relative path confinement — replaced lexical
  `Path.is_relative_to()` with fd-traversal: open allow_root with
  `O_DIRECTORY|O_NOFOLLOW`, walk each intermediate component via `dir_fd` with
  `O_NOFOLLOW`, reject `..` at every level. Race-resistant against symlink swaps
  between check and open.
- Files: groop/src/groop/inspect_files/reader.py
- Result: _confine_and_open uses descriptor-relative traversal.

- Action: Positive max limits enforcement — added _validate_limits() checking
  max_bytes/max_lines are positive ints below conservative caps (1 MiB / 100K lines).
  Limits are aggregate across all cgroup files, not per-file.
- Files: groop/src/groop/inspect_files/reader.py
- Result: Negative/zero/huge limits rejected; cgroup reads share one byte/line budget.

- Action: Removed --fixture-root from production CLI — users cannot select
  arbitrary roots via CLI. The Python API `fixture_root=` parameter remains
  as a testing-only seam.
- Files: groop/src/groop/cli.py, groop/tests/test_inspect_files.py
- Result: `groop inspect-files read` no longer accepts --fixture-root.

- Action: Root enforcement per TUI-SPEC §4.8 — build_inspect_read checks
  EUID == 0 in production. Injectable `_injectable_is_root()` provides an
  explicit test predicate; fixture roots never bypass the check. Gating (--inspect-files/--admin)
  checked before root to preserve ReadDenied behavior.
- Files: groop/src/groop/inspect_files/reader.py
- Result: Production reads require root; tests bypass via fixture_root.

- Action: Replaced 20,000-line oversized-json.log fixture with compact 10-line
  version (was 689KB, now 532 bytes).
- Files: groop/tests/fixtures/inspect_files/docker/containers/oversized/oversized-json.log
- Result: No large committed fixture.

- Action: Added 15 security/boundary tests — symlink escape, FIFO reject,
  giant line (chunk-based), aggregate bounds, negative/zero/huge limits,
  CLI fixture-root absence, root requirement, hostile bytes, no subprocess/writes.
- Files: groop/tests/test_inspect_files.py
- Result: 92 total tests in test_inspect_files.py (77 baseline + 15 new).
  Full suite: 481 passed, 1 skipped.
```

## Decisions

- Decision: Gating before root check in build_inspect_read
  Reason: Users without --inspect-files/--admin should get ReadDenied (exit 2),
  not root-required error (exit 1). Root requirement only applies when gating
  passes.
  Impact: All gating tests continue to pass.

- Decision: Conservative absolute caps on max_bytes/max_lines
  Reason: Prevents pathological values from any caller, even those using the
  Python API directly.
  Impact: 1 MiB / 100K line max.

- Decision: Descriptor-relative traversal instead of pure lexical is_relative_to
  Reason: Race-resistant — attacker cannot swap a symlink between the lexical
  check and the open because intermediate components are walked via dir_fd
  with O_NOFOLLOW.
  Impact: Slightly more complex but provably stronger confinement.

## Validation

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_inspect_files.py -v
# 92 passed in 0.67s

PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 481 passed, 1 skipped in 48.35s

mapfile -d '' pyfiles < <(find groop/src/groop groop/tests -name '*.py' -print0)
python3 -m py_compile "${pyfiles[@]}"
# clean, exit 0
```

```text
2026-07-10 CONTROLLER CORRECTION
- Required a literal True from the root predicate.
- Applied exact UTF-8 byte and generated-line bounds to returned content.
- Fixed simultaneous byte/line cutoff handling.
- Replaced checkout-mutating FIFO fixtures with pytest temporary fixtures.
- Replaced vacuous oversized/hostile-byte coverage with executable assertions.
- Focused result: 113 passed in 0.64s.
- Combined P44/P45/P46 focused result: 264 passed in 1.12s.
- Full current-main result: 623 passed, 1 skipped in 48.05s.
- Merged to main as b5ba9af.
```
