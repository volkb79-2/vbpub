# P48 — Frontier Review (pass #2, merge gate)

Reviewer: frontier review + merge-authority session (Opus high), per
`docs/controller-workflow-v2.md` §6-§8. Date: 2026-07-13.

Verdict: **APPROVED — merged `--no-ff`.**

## Scope / contract check

Diff touches only `topos/**` (14 files). Walked the handoff's 6 numbered
requirements against the diff; all met:

1. `systemd-journal` reuses the P45 `build_inspect_read()` gating / root check /
   `InspectFilesReadResult` / `InspectFilesReadError` / `ReadDenied` posture.
2. Unit-name validation rejects paths (`/`, `.`-prefix), option-like tokens
   (`-`-prefix), and unsafe chars — in both `catalog._validate_systemd_target`
   and the reader-side `_validate_journald_read_target` double-check.
3. Fixed absolute argv `("/usr/bin/journalctl", "--unit", target, "--no-pager",
   "--output=short-iso", "-n", str(max_lines))`, `shell=False`, no `--follow`,
   injected `journald_runner` seam (Python-API-only, not a CLI flag).
4. Timeout validated (1..60s) and typed; timeout/nonzero exit/OSError all return
   `InspectFilesReadError` with no fallback to arbitrary reads; output re-bounded
   post-capture via `_bound_rendered_text`.
5. 18 new tests (5 unit-validation + 13 journald read) — all assert observable
   outcomes (result type, content, path, truncation flag, error text), not mock
   bookkeeping. No live journalctl dependency.
6. Docs updated honestly (INSPECT-FILES, STATUS, RELEASE-READINESS, ROADMAP,
   MEASUREMENTS, README).

## Pass #1 (self-review) overlap — trial metric

| Finding | Source | flagged-by-pass-1 |
|---|---|---|
| Unused fixture `ssh-service-sample.txt` (dead scaffolding) removed | pass #1 | **yes** — pass #2 hygiene check would also catch it |
| `-W error` full-suite contract cannot be honored in this environment (repo-wide third-party `jsonschema`/`schemathesis` DeprecationWarning, fails identically on `main`) | pass #2 | **no** — pass #1 quoted only plain `-q` runs and did not note the `-W error` contract gap |

Pass-#1 overlap on P48: 1/2 findings.

## Minor notes (not blocking, no fix required)

- `_run_journald_snapshot` bounds output with `_DEFAULT_MAX_BYTES` rather than a
  caller-supplied `max_bytes`; the 1 MiB absolute cap is still enforced and
  `-n <max_lines>` bounds lines at the source, so the contract ("bound bytes")
  holds. Acceptable for a fixed-argv snapshot.

## Gate results (reviewer rerun, in-worktree)

Environment: `/home/vscode/.venv` (pytest 8.4.2, textual 8.2.8), Python 3.14.6,
`PYTHONPATH=topos/src`.

```
# focused
python -m pytest topos/tests/test_inspect_files.py -q
132 passed, 1 warning in 0.82s

# full suite (plain) — matches REPORT
python -m pytest topos/tests -q
845 passed, 2 skipped, 1 warning in 121.91s

# git diff --check main...HEAD
diff-check-clean
```

`-W error` note: the full suite fails under `-W error` in this environment, but
so does unmodified `main` (e.g. `test_ui_sparkline` — 19 failures), because
`schemathesis` imports a deprecated `jsonschema.RefResolutionError`. This is an
environment-wide pre-existing condition, not a P48 regression. Post-merge
validation from `main` recorded separately.
