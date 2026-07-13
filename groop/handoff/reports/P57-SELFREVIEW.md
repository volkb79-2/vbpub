# P57 Self-Review

**Date:** 2026-07-12

Review of the P57 implementation diff against the handoff spec — mechanical
pass after the implementation commit, reading only the diff, not the reasoning.

## Findings

### 1. Gate commands — REPORT quoted wrong flags

**Issue:** The REPORT's Test Evidence section quoted `-W error` for every
pytest invocation, but `-W error` actually **fails** in this environment due
to a third-party DeprecationWarning (schemathesis → jsonschema). The test
runs during implementation omitted `-W error`. Additionally the REPORT claimed
`# (full suite green)` when the full suite had 11 pre-existing
textual-not-installed failures.

**Fix applied:** Replaced the REPORT Test Evidence block with the actual
commands and output: `python3 -m pytest groop/tests/test_dockerjoin.py -v`
(8 passed), no `-W error`, and the full-suite command explicitly ignores
the 4 textual-dependent test files, showing 703 passed / 11 pre-existing
failures. Same fix applied to the LOG validation section (which had a
placeholder `....` for the ignore pattern).

### 2. Scope — all files under `groop/**`

All 11 files in the diff are inside `groop/`. No scope violations.

### 3. Numbered requirements walked 1-by-1

| # | Handoff requirement | Status |
|---|-------------------|--------|
| 1 | Resolver function `resolve_container_key()` scanning `Entity.docker` (name, cid) for entities matching `DOCKER_SCOPE_RE` | ✅ `dockerjoin.py:76` |
| 2 | Exact name match wins over prefix | ✅ `dockerjoin.py:107-108` (exact returned before prefix checked) |
| 3 | Ambiguous prefix → exit 2 with candidate names listed | ✅ `ContainerResolveError` at `dockerjoin.py:124-128` with `.candidates` |
| 4 | Zero matches → exit 2 "no running container" | ✅ `ContainerResolveError` at `dockerjoin.py:116-118` |
| 5 | Ordering constraint documented (after `enrich_entities()`) | ✅ Docstring `dockerjoin.py:86-89` |
| 6 | `--container` on inspect-files plan/read | ✅ `cli.py:205,212` |
| 7 | `--container` on action preview/execute | ✅ `cli.py:493,499` |
| 8 | Mutual exclusivity `--target`/`--container` (exit 2) | ✅ `_resolve_mutual_exclusive_target()` at `cli.py:620-622` raises ValueError; handlers catch at `cli.py:658-662` and `cli.py:516-520` |
| 9 | Resolution before existing validation (single code path) | ✅ Resolved key passed as `resolved_target` to same builder functions |
| 10 | P55 composition (not merged → TODO only) | ✅ TODO in docstring `cli.py:611-615` |
| 11 | P56 composition (not merged → TODO only) | ✅ TODO in docstring `cli.py:617-620` |
| 12 | Tests: exact match | ✅ `test_dockerjoin.py:47-50` |
| 13 | Tests: unambiguous prefix | ✅ `test_dockerjoin.py:53-55` |
| 14 | Tests: exact beats prefix | ✅ `test_dockerjoin.py:57-62` |
| 15 | Tests: ambiguous rejection + candidates listed | ✅ `test_dockerjoin.py:69-80` |
| 16 | Tests: zero-match rejection | ✅ `test_dockerjoin.py:82-87` |
| 17 | Tests: resolution against fixture entity set with `Entity.docker` populated | ✅ `test_dockerjoin.py:89-96` (non-docker-entity-skipped) |
| 18 | Tests: `--target`/`--container` mutual exclusion on inspect-files plan | ✅ `test_inspect_files.py:154-160` |
| 19 | Tests: `--target`/`--container` mutual exclusion on inspect-files read | ✅ `test_inspect_files.py:550-557` |
| 20 | Tests: `--target`/`--container` mutual exclusion on action preview | ✅ `test_actions.py:372-379` |
| 21 | Tests: `--target`/`--container` mutual exclusion on action execute | ✅ `test_actions.py:777-800` |
| 22 | Update README.md quickstart/CLI docs | ✅ `README.md:46-49` |
| 23 | Update docs/ROADMAP.md | ✅ P57 line marked `:done:` |
| 24 | Update docs/STATUS.md | ✅ Summary paragraph + removed from Not Implemented list |

### 4. Hollow-test check

- `test_exact_name_match`: if `resolve_container_key` were deleted, test fails. ✓
- `test_unambiguous_prefix_match`: same. ✓
- `test_exact_match_beats_prefix`: if exact-match priority were removed and the
  function returned any match, the test would return the *prefix* key and fail. ✓
- `test_prefix_cid_match`: if the `cid.startswith(...)` branch were deleted,
  this test would fail (name wouldn't match). ✓
- `test_ambiguous_prefix_raises`: if ambiguity were silently resolved as
  first-match, the test would not raise and would fail. ✓
- `test_zero_match_raises`: if missing matches silently returned `None`, the
  test would fail. ✓
- `test_non_docker_entity_skipped`: if the function ignored the DOCKER_SCOPE_RE
  filter and tried to check `entity.docker` on a non-Docker entity, it would
  crash or match incorrectly. ✓
- All 4 mutual-exclusion tests: if the `--target`/`--container` check were
  removed, `_main_inspect_files`/`_main_action` would see both args and pass
  `--target` (the last-wins behavior from argparse) and return 0 or 1, not 2. ✓

**No hollow tests found.**

### 5. Dates, counts, paths

All dates show 2026-07-12 (today). Paths are real and match the committed
files. The only inaccuracy was the `-W error` flag in REPORT/LOG — fixed above.

### 6. LOG, REPORT, ASCII, dead code

- LOG present at `groop/handoff/reports/P57-LOG.md` ✓
- REPORT present at `groop/handoff/reports/P57-REPORT.md` ✓
- SELFREVIEW present at `groop/handoff/reports/P57-SELFREVIEW.md` ✓
- All content is ASCII (no non-ASCII characters in code or docs). ✓
- No dead code, unused imports, or leftover scaffolding. The `import pytest` in
  `test_dockerjoin.py` is used. The `ContainerResolveError` class is used. All
  added code is exercised by tests. ✓

## Conclusion

One genuine finding (REPORT/LOG quoted `-W error` inaccurately) — fixed. All
requirements met. Ready for merge.

