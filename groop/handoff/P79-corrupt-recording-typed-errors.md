# P79 - Corrupt recording inputs are typed errors, not tracebacks

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P2 (merged), P54 (merged)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** making the reader typed requires changing the `RecordReader` public contract in `CONTRACTS.md` (propose it in the REPORT and BLOCK - do not silently widen a frozen interface)

<!--
CARVE SOURCE (controller-workflow-v2 §8): **review-derived**, but not a child of either
package under review - it was found by RERUNNING THE GATES FROM MAIN during the P72/P74
merge (which is exactly what that rule exists to catch). `main` currently has one
failing test, and the failure is not a flaky test: it is a real defect hiding behind a
test whose premise no longer holds. Evidence:
handoff/reports/P72-P74-MERGE-EVIDENCE.md.
-->

## Goal

A corrupt, truncated, or non-zstd-but-zstd-magic `.jsonl.zst` recording currently makes
`groop report` **crash with an unhandled exception traceback** and exit 1:

```
$ groop report corrupt.jsonl.zst --json
Traceback (most recent call last):
  ...
zstandard.backend_c.ZstdError: zstd decompress error: Unsupported frame parameter
exit=1
```

That is a raw exception crossing a CLI boundary, which the standing error-disclosure
contract forbids ("no raw exception text ... typed, bounded errors only"). It should be
a typed error and exit 2, exactly as an unreadable/unknown-format input already is.

## Why this was invisible until now

`tests/test_report.py::TestReportCLI::test_zst_without_zstandard_exits_2` *looks* like
it covers this. It does not. Its premise is "a `.jsonl.zst` file **without** the
zstandard extra exits 2", so it only exercises the missing-dependency path — and in any
environment where `zstandard` IS installed (the package venv: `zstandard` 0.25.0) it
instead walks straight into the decompressor with deliberately corrupt bytes, hits the
uncaught `ZstdError`, and fails on `assert 1 == 2`. So the suite has been **red on
`main`** in the clean venv, and the redness was reported for two waves running as "a
pre-existing environment failure" rather than read for what it says.

Two defects, then, and the test is the smaller one:

1. **The product defect:** corrupt/truncated compressed recordings are not handled.
2. **The gate defect:** an environment-conditional test that passes or fails depending
   on whether an optional extra happens to be installed is not a gate. It must assert
   the same thing in both environments, or skip *honestly and distinguishably* in one.

## Context To Read First (bounded)

- `src/groop/record.py` (or wherever `RecordReader` lives) - the read/decompress path,
  and how it currently reports an unknown format or a missing `zstandard`.
- `src/groop/cli.py` - `_main_report`, and how it turns reader errors into exit codes.
  Find the existing typed-error/exit-2 path and reuse it; do not invent a second one.
- `tests/test_report.py::TestReportCLI` - the CLI error tests, including the broken one.
- `groop/CONTRACTS.md` - input trust, error disclosure.
- Do **not** read actions, daemon, UI, MCP, BPF, or DAMON code.

## Required Contracts

1. **Every corrupt-input failure is typed.** Decompression failure, truncated stream,
   zstd magic on a non-zstd file, a JSONL body that is not valid JSON, a valid-JSON body
   that is not a P2 frame, and a missing/short header all produce a typed, bounded error
   on stderr and **exit 2** - never a traceback, never exit 1.
2. **No raw exception text crosses the boundary.** The message names the file and the
   failure class in groop's own words. It does not paste `ZstdError`, a library
   backend name, a stack frame, or an absolute internal path. (The user-supplied path
   they typed is fine; internal paths are not.)
3. **Bounded.** A corrupt file must not be read into memory unboundedly while trying to
   make sense of it, and the error message is bounded - a corrupt file's bytes never end
   up quoted in the error.
4. **The missing-`zstandard` path keeps its current behavior** (typed error naming
   `zstandard`, exit 2). Both paths are typed exits now; they must remain
   *distinguishable* - "you need the zstd extra" and "this file is damaged" are different
   operator actions and must not collapse into one message.
5. **Fix the gate.** `test_zst_without_zstandard_exits_2` must assert something true in
   both environments: split it into (a) a corrupt-input test that runs everywhere, and
   (b) a missing-extra test that forces the extra's absence (e.g. by blocking the import
   in the subprocess env) rather than depending on the ambient venv. An honest
   `pytest.skip` with a message naming why is acceptable for (b) only if forcing absence
   is genuinely impossible - state which you did and why in the REPORT.
6. **`main`'s suite is green after this package** (in the clean venv, from the repo
   root). That is the deliverable, not a side effect.

## Acceptance Oracles (numbered, adversarial)

Fixtures are tiny byte-blobs written in-test; do not add binary fixture files.

1. **zstd magic + garbage** (the exact case that crashes today: `b"\x28\xb5\x2f\xfd"` +
   junk) -> exit 2, typed message, and **assert the stderr contains no traceback marker**
   (`"Traceback"`, `"ZstdError"`) - a test that only checks the exit code passes against
   a `try: ... except Exception: sys.exit(2)` blanket, which is not what contract 2 asks
   for.
2. **Truncated valid zstd stream** (compress a real recording, cut it in half) -> exit 2,
   typed. Distinct from oracle 1: this one has a *valid* frame header.
3. **Plain `.jsonl` with a corrupt body** (valid header line, then `{"not": ` ) -> exit 2,
   typed. Proves the fix is not zstd-specific.
4. **Valid JSON that is not a P2 frame** -> exit 2, typed, and the message says so rather
   than blaming compression.
5. **Missing `zstandard` extra** -> exit 2, message names `zstandard`, and is
   **different** from oracle 1's message (assert both, in one test, so they cannot
   converge).
6. **A healthy recording still reports identically** - byte-compare the JSON output of
   `groop report` on `tests/fixtures/frames/gstammtisch-once.jsonl` before and after.
   This package must not touch the happy path.

## Out Of Scope

- Recovering partial data from a damaged recording (a corrupt file is an error, not a
  salvage operation).
- Changing the P2 record format, the header schema, or the writer.
- `groop --replay` / snapshot-inspect error paths, unless the same reader function is
  the one being fixed - in which case say so in the REPORT rather than expanding scope
  silently.
- Making `zstandard` a hard dependency.

## Docs

`groop/README.md` (work-package row), `docs/OPERATIONS.md` (one line: what a damaged
recording looks like and that it exits 2), `docs/STATUS.md`.

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -q -W error
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error
python3 -m py_compile <changed files>
git diff --check
```

Run the suite **from the repo root** - four tests shell out via the repo-root-relative
path `groop/src` and fail spuriously otherwise. The full suite must be **fully green**
after this package: `main` currently shows exactly one failure, and it is the one you
are fixing. State in the REPORT which environment each result came from, and whether
`zstandard` was installed in it (it changes what you are testing).
