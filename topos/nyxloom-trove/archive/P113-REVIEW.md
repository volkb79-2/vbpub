# P113-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P113-execute-primitives-coverage
**HEAD:** 8f74d77d5de37a87bea0baff6c2ec65adcda5d3e (confirmed)
**Verdict:** **APPROVED** (with receipt deficiency noted)

## Preflight

- `pwd`: `/workspaces/vbpub/.worktrees/feat/topos-P113-execute-primitives-coverage`
- Git top-level: same ✓
- HEAD: full sha matches required ✓
- `git status`: clean ✓

## Independent gate evidence

Full xdist gate: **2,169 passed, exit 0** in 55s.

```
P113 literal intersection: lines=[]  arcs=[]
P113: ALL LITERAL LINES/ARCS CLOSED
```

All 32 handoff lines and 24 handoff branch pairs are absent from
`missing_lines`/`missing_branches`. Remaining execute.py gaps (lines 322+,
branches 326+) are correctly deferred to P114/P115. O1 satisfied.

9 test functions, 13 collected cases (2 parametrized). 2,169 total
(2,156 + 13). No product source, gate, dependency, pragma, or omit changes.

## Literal line verification (nl -ba)

All 32 lines verified against source at HEAD:

| Line | Source |
|------|--------|
| 83 | `return ""` |
| 95 | `return _bound_output(value.decode("utf-8", errors="replace"))` |
| 123 | `except (KeyError, OSError):` |
| 124 | `user = "unknown"` |
| 132 | `raise ValueError("identity uid is invalid")` |
| 138 | `raise ValueError("identity user is invalid")` |
| 140 | `raise ValueError("identity user contains control characters")` |
| 146 | `raise ValueError("timeout must be a finite positive number")` |
| 161 | `raise ValueError("execution plan does not match...")` |
| 163 | `raise ValueError("execution plan is not an immutable preview plan")` |
| 165 | `raise ValueError("execution plan argv is invalid")` |
| 168 | `raise ValueError("execution plan argv does not match the catalog")` |
| 178 | `raise ValueError("execution executable is not a fixed absolute path")` |
| 180 | `raise ValueError("execution argv shape is invalid")` |
| 186 | `raise _AuditError("audit path must be absolute")` |
| 188 | `raise _AuditError("secure audit open is unavailable")` |
| 198 | `raise _AuditError("audit parent contains unsafe path syntax")` |
| 201 | `except FileNotFoundError:` |
| 202 | `os.mkdir(component, 0o700, dir_fd=parent_fd)` |
| 203 | `next_fd = os.open(component, flags, dir_fd=parent_fd)` |
| 211 | `raise _AuditError("audit parent is not a private directory")` |
| 213 | `raise _AuditError("production audit parent is not root-owned")` |
| 224 | `if require_root_owner and existing.st_uid != 0:` |
| 225 | `raise _AuditError("production audit target is not root-owned")` |
| 238 | `raise _AuditError("audit target is not a regular file")` |
| 240 | `raise _AuditError("audit target permissions are too broad")` |
| 242 | `raise _AuditError("production audit target is not root-owned")` |
| 245 | `except BaseException:` |
| 246 | `os.close(fd)` |
| 247 | `raise` |
| 264 | `raise _AuditError("audit clock returned a non-finite timestamp")` |
| 291 | `raise _AuditError("audit record exceeds bounded size")` |

All 24 branch pairs covered. ✓

## Test quality audit (9 functions, 13 collected cases)

| Test | Cases | Assertion |
|------|:-----:|-----------|
| Output/identity/timeout/audit boundaries | 1 | Exact empty string, Unicode replacement, `user=="unknown"`, 4 parametrized identity coercions, timeout ValueError, non-finite audit error, complete audit record dict |
| Validation refuses forged plans | 1 | 4 parametrized plan mutations + executable-path + argv-shape — all exact ValueError texts |
| Safe audit refuses unsafe path/open | 1 | Absolute-path error + missing O_NOFOLLOW error |
| Safe audit rejects unsafe parent component | 1 | `_AuditError` for traversal + `close` calls `[10, 11]` |
| Safe audit creates private component | 1 | `mkdir` call, open sequence, `fchmod` call |
| Safe audit rejects unsafe parent metadata | 2 | World-writable + non-root-owned — exact errors + close `[10, 11]` |
| Safe audit rejects unsafe leaf metadata | 4 | Non-root-owned, directory, too-broad perms, non-root-owned — exact errors + close |
| Safe audit keeps existing leaf + rethrows interrupt | 1 | `fchmod` call; `KeyboardInterrupt` → close `[12, 10]` → re-raise |
| Write JSON record refuses unbounded payload | 1 | `_AuditError` for oversized payload |

## Specific review-focus verification

### Safe-audit fake seams (O3)
The `_fake_audit_os` helper patches OS primitives (`os.open`, `os.stat`,
`os.fstat`, `os.mkdir`, `os.close`, `os.fchmod`, `os.fdopen`). The target
function `_open_safe_audit` is called directly — it is NOT mocked. The
patched OS seams prove:
- **Refusal**: unsafe path syntax, missing O_NOFOLLOW, world-writable parent,
  non-root-owned parent/target, non-regular-file target, too-broad permissions
- **Creation**: private directory with `0o700`, file with `fchmod 0o600`
- **Cleanup**: every refusal path closes open file descriptors (verified by
  `calls["close"]` assertions)
- **BaseException propagation**: `KeyboardInterrupt` during `fdopen` closes
  both FDs (12, 10) before re-raising — lines 245-247 contract proven ✓

### BaseException cleanup blocks (lines 245-247)
The handoff states: "The two BaseException cleanup blocks are deliberate
only if the resource closes and KeyboardInterrupt re-raises; prove that
contract rather than narrowing them by reflex." The test
`test_safe_audit_keeps_existing_root_owned_leaf_and_rethrows_interrupt`
proves: `fdopen` raises `KeyboardInterrupt` → `os.close(12)` → `os.close(10)`
→ `KeyboardInterrupt` re-raises. The `BaseException` catch at line 245
correctly handles cleanup while allowing `KeyboardInterrupt`/`SystemExit`
to propagate. ✓

### No hollow/count-only assertions
All assertions are exact string comparisons, complete dict equality, exact
error message matches, or exact call-sequence lists. Zero `len()`, `is not
None`, `>=`, `any()`, or assertion-free bodies. ✓

### No product source edits
Diff confirms zero changes to `execute.py` or any other source file. ✓

## Receipt deficiency

The handoff step 9 requires "Write P113-LOG.md, P113-REPORT.md, and
P113-SELFREVIEW.md." None of these files exist in
`topos/nyxloom-trove/reports/`. The only evidence is the test file and
this independent review. The implementer must produce the three receipt
files with literal before/after sets, per-run intersections, exact
commands/exits, collection arithmetic, and parity evidence per O5.

## Verdict

**APPROVED.** All 32 literal lines and 24 branch pairs in the P113 handoff
are closed — verified by independent full xdist gate. Remaining execute.py
gaps (lines 322+) correctly deferred to P114/P115. Safe-audit fake seams
prove real security refusal, creation, cleanup, and KeyboardInterrupt
propagation without mocking the target. No hollow, count-only, or duplicate
assertions. No product source edits.


---

# FINAL RECEIPT SIGN-OFF APPROVED — 2026-07-25 (commit 8e5813ca)

**Reviewer:** Reasonix (receipt-only review of 8e5813ca)
**Referenced:** P113-LOG.md, P113-REPORT.md, P113-SELFREVIEW.md

## Receipt verification

All six requirements from the prior review's deficiency note are satisfied:

### 2x controller clean-commit gates with matching hash ✓
LOG records two runs from exact implementation commit `8f74d77d`, both
producing identical normalized `execute.py` record hash
`7446e44f3192c076403a44dc812ac54e68430319f25dde50ee0612a4c34a4588`.
The independent Pro run corroborates with 2,169 cases, exit 0, empty
literal intersections.

### Exact literal before/after sets ✓
REPORT prints all 32 before lines and 24 before branch pairs matching the
handoff. Both run 1 and run 2 intersections are `lines=[] pairs=[]`.

### Correct arithmetic: 2,156 + 13 = 2,169 ✓
LOG documents 9 test functions / 13 collected cases. REPORT states
"2,156 to 2,169 cases." The 13 collected cases come from 7
non-parametrized + 2 parametrized (2 × 1 + 1 × 4) test functions.
Independent gate confirmed 2,169 total.

### No whole-file claim ✓
REPORT states: "This is a literal primitive tranche, not whole-file
completion. Later execute.py gaps starting at line 322 remain explicitly
assigned to P114/P115."

### Discarded no-data diagnostic explicitly excluded ✓
LOG documents the initial focused run with file-path `--cov` selector
producing `module-not-imported`/`no-data-collected` warnings and states:
"The no-data receipt was discarded."

### Flash abort accurately classified ✓
LOG classifies the implementer's preflight as "session-health
runner/worktree contract violation, not implementation evidence" with
controller takeover under L12.

## Verdict

**APPROVED.** The three receipt files (LOG, REPORT, SELFREVIEW) truthfully
close the prior deficiency with exact literal before/after sets, matching
two-run controller gate evidence, correct arithmetic, explicit whole-file
disclaimer, and accurate classification of discarded and non-evidence runs.
