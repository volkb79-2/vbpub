# P79 Merge Evidence — validated from `main`

Merge commit: `8ed1853` (`--no-ff`), on top of `07891aa`.
Review-fix commit: `f48500a` (pass #2).

## The environment is the story

P79's defect is only reachable when the optional `zstandard` extra is installed.
This devcontainer's venv (`/home/vscode/.venv`) does **not** have it. The package
was implemented and self-reviewed there, so its zstd oracles all skipped and the
code it exists to fix was never executed — it reported green while being red.

Both environments are therefore reported below. The gate env was reconstructed as
an overlay venv (`/tmp/p79-venv`: `zstandard` 0.25.0 + the parent venv's
site-packages), matching the `zstandard` 0.25.0 the handoff names.

## Validation from `main` (post-merge)

```text
# gate env (zstandard 0.25.0)
$ PYTHONPATH=groop/src /tmp/p79-venv/bin/python -m pytest groop/tests -q
1 failed, 1338 passed in 212.66s
FAILED test_ui_app.py::test_pilot_snapshot_running_status_appears_immediately

# no-zstd env (the ambient venv)
$ PYTHONPATH=groop/src python3 -m pytest groop/tests -q
1331 passed, 8 skipped in 208.04s
```

`test_zst_without_zstandard_exits_2`, red on `main` for two waves, is **green in
both environments**. That was P79's deliverable and P82's entire goal.

### The one remaining failure predates P79

`test_pilot_snapshot_running_status_appears_immediately` fails on **unmodified
`main`** roughly 1 run in 3 in isolation (verified: 3 isolated runs on `main` →
pass, pass, fail). It is a UI timing flake; P79 touches the recording reader and
never loads the UI. `test_record_cli_runs_ui_and_writes_frames` is flaky the same
way (passes standalone, fails under full-suite load). Both carved as **P85** —
they are not P79 regressions and did not block the merge.

## What pass #2 changed (the package as merged ≠ the package as submitted)

Re-run in the gate env, the submitted package was **red**: it swapped `main`'s
failing test for a failing oracle of its own (`test_oracle_2_truncated_zstd_stream`).

| Defect | Consequence |
|---|---|
| Truncated `.zst` decoded to its surviving prefix | `groop report` returned a **believable, wrong** report at exit 0. On a multi-block recording a half-file leaves ~786KB of valid frames behind the cut. Worse than the traceback it replaced. |
| Oracles 1 & 2 hollow | `_main_report`'s `except Exception` backstop turned a raw `ZstdError` into exit 2 with no traceback, so both passed with the **whole reader fix reverted** — the exact blanket the handoff's Oracle 1 spec warned about by name. |
| `test_zst_without_zstandard_exits_2` hollow via its own tmp path | It asserted the bare token `"zstandard"`; pytest names `tmp_path` after the test (`test_zst_without_zstandard_exi…`), so the token arrived via the **echoed file path**, not the message. It passed with the degradation branch deleted. |
| Empty/frameless recording | Reported `{"profiles":[]}` at exit 0 instead of failing. |

A header check (the obvious fix for "empty input") was **rejected**: the header is
optional in practice — the canonical fixture `gstammtisch-once.jsonl` begins with a
`frame` and no fixture carries one — so requiring it would break the happy path.

The fix is `_ZstdStreamReader`, chaining one `decompressobj` per frame and using
`eof` to distinguish "the frame ended" from "the input ran out". This also reads
**append-mode** recordings (each `RecordWriter` session appends its own zstd frame —
a first attempt that stopped at the first frame's end silently dropped every
resumed session, caught by `test_record_round_trip_zst_path`) and still rejects a
cut-off appended frame.

## Mutation evidence — every guard is load-bearing

| Mutation | Result |
|---|---|
| `reader.py` reverted to `main` | 6 tests red (2 of which **passed** against it before this pass) |
| Truncation `eof` guard disabled | oracles 2, 2b, 2d red |
| Missing-`zstandard` branch deleted | `test_zst_without_zstandard_exits_2` + oracle 5 red — this is **P82's oracle 3** |
| No-frames guard removed | oracle 2c red |

Happy path is **byte-identical to `main`**, plain and zstd-compressed.

## Follow-on carves (§8, review-derived)

- **P84** — pin the gate environment. The extra is unpinned, so the zstd oracles
  skip in some venvs; an honest, named skip still let a shipped bug through, so the
  skip is not the fix — the unpinned environment is the bug.
- **P85** — the two UI/record timing flakes above.
- **P82 — superseded by P79.** Its goal (repair the red gate) is delivered, and its
  in-progress branch adds a `_ZSTD_FORCE_UNAVAILABLE` test seam to production
  source, which P79 achieves without. Abandon rather than rebase.

## Environment note

Reproducing the gate env required installing `zstandard`, which briefly leaked into
the shared venv at `/home/vscode/.venv`. It was uninstalled and the venv verified
back to its original state (`zstandard` absent). The overlay at `/tmp/p79-venv` is
the only place it remains.
