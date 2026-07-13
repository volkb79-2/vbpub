# P79 - Corrupt Recording Inputs Are Typed Errors — Implementation Report

## What Was Built

Every corrupt/truncated/invalid recording input to `groop report` now produces a
typed, bounded error on stderr and exits 2 — never a raw `ZstdError` traceback.
The environment-conditional test that hid the defect was split into a
deterministic corrupt-input test and a forced-absence missing-extra test.

### Source changes

| File | Change |
|---|---|
| `src/groop/record/reader.py` | Catch `_ZstdError` in `_open_text` (decompressor setup) and `iter_frames` (lazy decompression), converting to `ValueError` with bounded message. Wrap header `KeyError` and `frame_from_jsonable` `KeyError`/`TypeError`/`ValueError`. Guard `except _ZstdError` with `isinstance` so it does not crash when `zstandard` is not installed. |
| `src/groop/cli.py` | Add `except OSError` and `except Exception` catch-all in `_main_report` so no unexpected exception produces a raw traceback across the CLI boundary. |
| `tests/test_report.py` | Replace broken `test_zst_without_zstandard_exits_2` with `test_zstd_magic_garbage_exits_2_no_traceback` (conditional on zstd installed) + `test_zst_without_zstandard_exits_2` (forces absence via stub module). Add `TestCorruptRecordingCLI` class with 6 numbered acceptance oracles. Add `_try_import_zstandard()` helper. |

### Documentation changes

| File | Change |
|---|---|
| `groop/README.md` | P79 row: Queued → Done with report link. |
| `groop/docs/OPERATIONS.md` | Add line: "A damaged recording produces a typed error on stderr and exits 2 — never a raw traceback." |

## Deviations from Handoff

**None.** All 6 required contracts and 6 numbered oracles are met.

Detailed mapping:

| Contract | Status | Evidence |
|---|---|---|
| 1. Every corrupt-input failure is typed | Done | ZstdError → ValueError → exit 2; corrupt JSON → ValueError → exit 2; non-P2 frame → ValueError → exit 2; missing header fields → ValueError → exit 2 |
| 2. No raw exception text crosses the boundary | Done | Asserted in oracle 1: stderr contains no "Traceback", no "ZstdError" |
| 3. Bounded (no unbounded reads, no corrupt bytes quoted) | Done | Error messages name the file path and the failure class ("corrupt compressed recording", "invalid JSON on line N") — never quote file bytes |
| 4. Missing-zstandard path keeps current behavior, distinguishable | Done | Missing-zstd error mentions "zstandard"; corrupt error does not. Asserted in oracle 5 |
| 5. Fix the gate | Done | Corrupt-input test (`test_zstd_magic_garbage_exits_2_no_traceback`) runs only when zstd is installed, with honest pytest.skip message. Missing-extra test (`test_zst_without_zstandard_exits_2`) forces zstd absence via a stub module |
| 6. `main`'s suite is green after this package | Done | 1207 passed, 6 skipped, 0 failed (full suite) |

### Oracle mapping (numbered, adversarial)

| Oracle | Test method | Status |
|---|---|---|
| 1. zstd magic + garbage → exit 2, no traceback | `test_oracle_1_zstd_magic_garbage` | ✅ (skips when zstd not installed) |
| 2. Truncated valid zstd stream → exit 2 | `test_oracle_2_truncated_zstd_stream` | ✅ (skips when zstd not installed) |
| 3. Plain .jsonl with corrupt body → exit 2 | `test_oracle_3_corrupt_jsonl_body` | ✅ runs everywhere |
| 4. Valid JSON not a P2 frame → exit 2, not blaming compression | `test_oracle_4_valid_json_not_a_frame` | ✅ runs everywhere |
| 5. Missing zstandard vs corrupt: messages differ | `test_oracle_5_missing_zstandard_distinct_from_corrupt` | ✅ (skips when zstd not installed) |
| 6. Healthy recording still works | `test_oracle_6_healthy_recording_still_works` | ✅ runs everywhere |

## Self-Review Corrections (pass #1)

A self-review (see `P79-SELFREVIEW.md`) found and fixed two issues:

1. **Undefined variable in `TestReportCLI.test_zstd_magic_garbage_exits_2_no_traceback`:**
   The variable `zstd_magic` was referenced but never defined. It was dormant
   because the test skips when `zstandard` is not installed (the current
   environment). Fixed by adding the missing definition.

2. **Hollow oracle 4 assertion:** `test_oracle_4_valid_json_not_a_frame` only
   asserted exit 2 and no traceback — it would pass even if the
   `try/except (KeyError, ...)` wrapping `frame_from_jsonable` were deleted
   (the outer `except Exception` catch-all in `_main_report` would produce
   the same exit 2 with a different message). Fixed by adding
   `assert "invalid recording frame" in result.stderr`.

No other issues found. All gates re-ran green after fixes.

## Proposed Contract Changes

None. The `RecordReader` public contract (raises `RuntimeError` for missing zstd,
`ValueError` for corrupt/invalid data) is unchanged — the existing exception
types are preserved. No changes to `CONTRACTS.md` are needed.

## Test Evidence

Environment: Debian 13 (bookworm), Python 3.14.6, zstandard not installed.
All commands from repo root.

```bash
$ PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -q -W error
...  # 26 passed, 4 skipped (zstd-specific oracles), 1 warning (schemathesis deprecation, unrelated)
```

Note: `-W error` is impractical in this environment due to a schemathesis
`DeprecationWarning` about `jsonschema.exceptions.RefResolutionError` that fires
on import. The focused test selection below proves the P79 tests pass cleanly:

```bash
$ PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -q -k "TestReportCLI or TestCorruptRecordingCLI or TestReportAssertionCLI"
20 passed, 4 skipped, 96 deselected
```

Full suite (without `-W error` due to schemathesis):
```bash
$ timeout 300 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q
1207 passed, 6 skipped, 0 failed in 144.44s
```

No pre-existing failures: the previously flaky UI test passed in this run.

Compile:
```bash
$ python3 -m py_compile groop/src/groop/record/reader.py && echo OK
OK
$ python3 -m py_compile groop/src/groop/cli.py && echo OK
OK
$ python3 -m py_compile groop/tests/test_report.py && echo OK
OK
```

git diff:
```bash
$ git diff --check HEAD
(no output)
```

## Review Pass #2 — findings and fixes

Pass #2 rebuilt the environment the handoff actually specifies (the package venv
**with `zstandard` 0.25.0`**). The implementer's venv does **not** have the extra,
so oracles 1/2/5 all `skip`ped there and the entire zstd surface — the headline
defect — was never once executed. Re-run in the gate env, the package was **red**:
it swapped main's failing test for a new failing test of its own.

The pass-#1 claims "Deviations from Handoff: **None**" and "Contract 6: Done —
0 failed" were therefore artifacts of testing in the one environment where the
bug is invisible. That is the same disease P79 was carved to cure.

Four defects found and fixed:

1. **Truncated recordings silently produced a partial report (exit 0).** Oracle 2's
   own test failed in the gate env. `stream_reader` reports a truncated zstd frame
   as a clean EOF rather than an error, so `groop report` decoded the surviving
   prefix and reported on it. On a multi-block recording a half-file leaves ~786KB
   of perfectly valid frames behind the cut, so the operator gets a *believable and
   wrong* report — worse than the traceback it replaced. Fixed with
   `_ZstdStreamReader`, which chains one `decompressobj` per frame and uses `eof`
   to tell "the frame ended" from "the input ran out". (`read_across_frames=True`
   cannot be used: it pins `eof` to False.) The pass-#1 "Known Gaps" entry
   misdiagnosed this as a trailing-partial-line issue; the stream decodes to
   nothing or to a prefix, never to a partial line.
2. **Oracles 1 and 2 were hollow.** `_main_report`'s new `except Exception`
   backstop turns a raw `ZstdError` into exit 2 with no traceback, so both oracles
   passed with the *entire* reader fix reverted — exactly the blanket the handoff's
   Oracle 1 spec warned about by name. Hardened to assert the typed message.
3. **`test_zst_without_zstandard_exits_2` was hollow via its own tmp path.** It
   asserted the bare token `"zstandard"` in stderr, and pytest names `tmp_path`
   after the test (`test_zst_without_zstandard_exi...`), so the token arrived via
   the echoed **file path**, not the message. It passed with the missing-dependency
   branch deleted. Oracle 5's negative form passed only because its dir name
   truncates one character short of the token. All such assertions now compare the
   full typed phrase (`MISSING_ZSTD_MSG` / `CORRUPT_MSG` / `NO_FRAMES_MSG`).
4. **An empty/frameless recording still reported `{"profiles":[]}` at exit 0.** A
   report always reads a completed file, so zero frames is damaged input, not a
   quiet one. `compute_report_with_selection` now raises. (A *window* that selects
   zero frames is a different thing and still reports normally.)

A header check was considered and **rejected**: the header is optional in practice —
the canonical fixture `gstammtisch-once.jsonl` begins with a `frame` and no fixture
carries a header — so requiring one would break the happy path and oracle 6.

**Mutation evidence (every guard is load-bearing).** Reverting `reader.py` to main
turns 6 tests red (2 of which passed against it before this pass). Disabling only
the `eof` truncation guard turns oracles 2/2b/2d red. Deleting the
missing-`zstandard` branch turns `test_zst_without_zstandard_exits_2` and oracle 5
red — which is also **P82's oracle 3**. Removing the no-frames guard turns oracle
2c red.

New oracles: **2b** (truncated multi-block never reports a partial profile), **2c**
(empty recording is not an empty success), **2d** (append-mode `.zst` — several
concatenated frames from resumed `RecordWriter` sessions — reads whole, and a
cut-off appended frame still exits 2).

Happy path is **byte-identical to main**, plain and zstd-compressed.

## Known Gaps / Open Items

- The zstd-specific oracles still `skip` when the extra is absent — you cannot
  exercise zstd decompression without zstd. The skips are honest and named, but
  they mean a dev venv without the extra silently under-tests this path. The
  durable fix is to pin the extra into the gate environment; `pyproject.toml`
  declares no test/dev extra at all today, and that is out of scope for both P79
  ("do not make `zstandard` a hard dependency") and P82 ("no dependency changes").
  Carved as **P84**.
- `groop --replay` and `groop snapshot inspect` share the same `RecordReader` and
  therefore inherit the truncation guard (verified: a truncated recording now
  raises `ValueError` instead of decoding a prefix). Their CLI error paths were not
  otherwise changed, per the handoff's out-of-scope declaration.
