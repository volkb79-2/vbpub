# P104 log — exact snapshot coverage

## Baseline

The verified P103 suite had 2,018 cases. The P96 gap inventory recorded five
missing lines and three missing branch pairs in `snapshot/enrich.py`, plus
eleven missing lines and eight missing branch pairs in `snapshot/bundle.py`.

## Implementation and review

The initial implementation added 22 tests and reached empty whole-file missing
sets. Independent review nevertheless returned `CHANGES_REQUIRED`:

1. one test did not induce its claimed cgroup failure;
2. a tar handle was not closed;
3. fixed `/tmp` paths were unsafe under xdist;
4. unique-name exhaustion created 9,999 real files;
5. the receipts lacked literal and command evidence; and
6. enrichment assertions were incomplete.

The first repair removed the misleading test but added an unrelated exact
ancestor test, leaving 22 functions and cases. It closed the resource and
xdist issues and stopped creating files for exhaustion, but its assertions and
receipts still did not match its max-standard claims.

The controller then made the second repair:

- exact tuple/dictionary assertions include unit/container identity, return
  code, stderr, and error text;
- every filesystem test uses `tmp_path`, except the virtual non-I/O path whose
  `Path.exists` calls are mocked;
- the cgroup failure test asserts the complete destination tree and bytes;
- the tar reader is context-managed and archive membership is exact;
- notable-file output and error messages are exact; and
- the exhaustion test creates no files and proves all 10,000 lookups occurred.

No source, gate, dependency, pragma, or omit change was made.

## Verification

A focused xdist diagnostic passed 22/22 cases. The first attempt to preserve
coverage JSON through a second bind mount passed all 2,040 tests but exited 3
when pytest-cov could not write the root-owned `/evidence` mount. This was a
receipt-harness permission failure, not an accepted gate run.

Two subsequent complete runs executed the declared test and changed-line gate
conjuncts under `set -euo pipefail`, followed only by a non-masking receipt
printer:

```text
run 1: 2040 passed in 64.29s; diff-coverage OK; exit 0
run 2: 2040 passed in 65.57s; diff-coverage OK; exit 0
```

For both runs:

```text
enrich.py missing_lines=[] missing_branches=[]
bundle.py missing_lines=[] missing_branches=[]
enrich.py executed lines/branches=44/16
bundle.py executed lines/branches=176/52
target_record_sha256=349c96d89d46c11e9b08fb4cee5bbd56b47f4a330705599f0f012dd603f52d69
```

`git diff --check` and handoff lint are required once more after receipt edits.
