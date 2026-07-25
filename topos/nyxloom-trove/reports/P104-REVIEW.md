# P104-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P104-snapshot-coverage
**HEAD:** 6e7b4719 (confirmed)
**Verdict:** **CHANGES_REQUIRED**

## Method

Read the P104 handoff, REPORT, SELFREVIEW, LOG, target sources (`snapshot/enrich.py`,
`snapshot/bundle.py`) with `nl -ba`, all P104 tests, and existing snapshot tests.
Ran the exact `topos-suite` gate twice (host bind, no rebuild), applied the literal
16-line/11-pair residual checker AND the complete whole-file checker for both
targets. Compared complete executed/missing sets for parity. Investigated every
controller concern with read-only diagnostics inside tester-unified.

## Independent gate verification (two xdist runs)

Run 1: **2040 passed, exit 0** in 66s. Run 2: **2040 passed, exit 0** in 67s.

```
OK enrich: literal_lines=[] literal_arcs=[]  whole_lines=[] whole_branches=[]
OK bundle: literal_lines=[] literal_arcs=[]  whole_lines=[] whole_branches=[]
PASS: both files whole-file 100%
```

**PARITY CONFIRMED** — identical complete executed/missing sets both runs.
O1 and O4 satisfied: both target files whole-file empty.

Test counts: 22 functions = 22 collected cases, 2040 total (2018 + 22). ✓

## Findings

### F1 — `test_copy_cgroup_files_read_failure` induces no failure (HIGH)
**File:** topos/tests/test_p104_snapshot_coverage.py:114-126

```python
def test_copy_cgroup_files_read_failure():
    tmp = Path("/tmp") / "test_cg"
    tmp.mkdir(parents=True, exist_ok=True)
    cg = tmp / "cg"; cg.mkdir()
    (cg / "memory.min").write_text("100")
    dst = tmp / "dst"
    _copy_cgroup_files(dst, cg, "")
    assert dst.exists()
```

The test name claims "read failure" but no failure is induced. With
`entity_key=""`, `src = cgroup_root` (the `cg` directory itself), and the
ancestor loop calls `_ancestor_keys("")` which returns `[""]`. For ancestor
`""`, `ancestor_src = cgroup_root`, so the function reads
`cg / "memory.min"` — which exists and is readable. No `OSError` is raised,
no `except OSError` block is entered. The assertion `dst.exists()` passes
because the function succeeds normally, not because it handled a failure.

The test does not exercise its claimed target (line 117, `except OSError:
pass` in the ancestor copy block). Coverage for line 117 comes from
`test_copy_cgroup_ancestor_read_failure` (line 184-198) only — the first
test is redundant for coverage and misleadingly named.

**Repair oracle:** Either (a) remove the test (coverage comes from the
second test), or (b) rename it to accurately describe what it tests
(e.g., `test_copy_cgroup_files_empty_entity_key`) and add a
comment explaining it exercises the `entity_key == ""` path.

### F2 — Tar file handle leaked in `test_safe_extract_unsafe_member` (MEDIUM)
**File:** topos/tests/test_p104_snapshot_coverage.py:138-139

```python
    with pytest.raises(RuntimeError, match="refusing unsafe archive"):
        _safe_extract(tarfile.open(archive, "r"), tmp)
```

`tarfile.open(archive, "r")` returns a `TarFile` object that is never
closed. The `with` statement on the write side (line 134) is correct, but
the read-side `tarfile.open` is passed directly to `_safe_extract` without
a context manager. `_safe_extract` does not close its argument. The
`TarFile` handle leaks — the file descriptor remains open until garbage
collection. On some platforms or under xdist, this can cause
`ResourceWarning` or `TooManyOpenFiles` errors.

Diagnostic confirmation: `TarFile closed after _safe_extract? False`.

**Repair oracle:** Wrap in a context manager:
```python
    with tarfile.open(archive, "r") as tar:
        with pytest.raises(RuntimeError, match="refusing unsafe archive"):
            _safe_extract(tar, tmp)
```

### F3 — Fixed `/tmp` paths risk xdist worker collisions (MEDIUM)
**File:** topos/tests/test_p104_snapshot_coverage.py

Multiple tests use fixed subdirectory names under `/tmp`:
- `Path("/tmp") / "test_cg"` (line 116)
- `Path("/tmp") / "test_se"` (line 131)
- `Path("/tmp") / "test_ubp"` (line 168)

Under xdist with multiple parallel workers (`-n auto`), two workers may
simultaneously create the same `/tmp/test_cg` directory, causing race
conditions, spurious failures, or one worker deleting another's files.

Other P104 tests correctly use `tempfile.mkdtemp(prefix=...)` for unique
paths (lines 187, 204). The three tests above should follow the same
pattern or use pytest's `tmp_path` fixture.

**Repair oracle:** Replace `Path("/tmp") / "test_*"` with
`tempfile.mkdtemp(prefix="test_*_")` in all three tests, or convert to
pytest fixture using `tmp_path`.

### F4 — `test_unique_bundle_path_exhaustion` creates 10,000 files (LOW)
**File:** topos/tests/test_p104_snapshot_coverage.py:166-177

```python
    for i in range(1, 10000):
        (tmp / f"test-{i}.txt").write_text("")
```

Creates 9,999 files on disk. This is slow (~0.5-1s), consumes inodes, and
is unnecessary — the exhaustion logic can be proven with a much smaller
range. The function `_unique_bundle_path` iterates from index 1 upward; if
all 9,999 indices are occupied, it raises RuntimeError. The same behavior
can be demonstrated with, e.g., 3 files and a mock/patch that limits the
MAX to a small number, or by testing at the boundary rather than filling
every slot.

**Repair oracle:** Patch `_MAX_UNIQUE_TRIES` (or the constant limiting the
loop) to a small value like 5, create 5 files, and assert exhaustion.
Avoid creating 10,000 real files.

### F5 — Receipt omits literal sets and exact commands (MEDIUM)
**File:** P104-LOG.md, P104-REPORT.md

The handoff O3 requires: "prints empty literal intersections and whole-file
missing sets for two runs." The LOG says "both run1 and run2: all target
lines=[] and pairs=[]" — symbolic, not literal sets. The REPORT condenses
both runs into one line: `run1 enrich.py: lines=[] pairs=[] | bundle.py:
lines=[] pairs=[]`. Neither prints the literal before/after residual sets
as established by P102/P103 precedent.

The handoff step 9 requires: "literal before/after sets, whole-file final
sets, exact commands/exits." The LOG and REPORT omit:
- The literal 16-line and 11-pair sets from the handoff
- Per-run literal intersection output
- Per-file per-run whole-file `missing_lines` / `missing_branches`
- Exact gate command and exit codes

**Repair oracle:** Expand LOG and REPORT to include literal before/after
sets, per-run literal and whole-file intersections, and exact commands
per the P102/P103 evidence precedent.

### F6 — Multiple status assertions are incomplete (LOW)
**File:** topos/tests/test_p104_snapshot_coverage.py:30-51

`test_collect_systemctl_show_oserror` asserts `result is None` and
`status["status"] == "error"` but does not verify that `status["error"]`
contains the OSError message. The same pattern applies to several
enrichment tests — the `status["error"]` field's content is not checked.

Conversely, the docker inspect tests (lines 53-82) DO assert
`"permission denied" in status["error"]` — a better pattern.

**Repair oracle:** Add error-message assertions to the systemctl tests
matching the docker inspect pattern: `assert status["status"] == "error"`
plus `assert "unit not found" in status.get("error", "")`.

## Checks passed

- **Both files whole-file 100%**: enrich.py and bundle.py — `missing_lines=[]`,
  `missing_branches=[]` both runs. ✓
- **22 functions = 22 cases**, 2040 total. ✓
- **No product source edits**. ✓
- **No duplicates** with existing snapshot tests. ✓
- **No pragma, omit, gate, dependency changes**. ✓
- **No sleep, random, host-proc reliance**. ✓
- **No mutation/fail-before overclaim**. ✓
- **git diff --check**: clean. ✓

## Verdict

**CHANGES_REQUIRED.** The gate is correct — both files at exact 100% with
parity. Six findings require repair: one test induces no failure (F1), one
leaks a tar file handle (F2), three tests use fixed `/tmp` paths risking
xdist collisions (F3), one creates 10,000 unnecessary files (F4), the
receipt omits literal sets and exact commands (F5), and status assertions
are incomplete (F6). Concrete mechanical repair oracles provided for all.
