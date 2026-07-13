# P69 Report — Web UI scoping and analysis

**Status:** Done (docs-only).

## Delivered

- `docs/WEB-UI-SCOPING.md`: code-cited P52/P63 operation audit, TUI-grounded
  page inventory, P53 response budgets, redaction posture, P67 trust-boundary
  verdict, stack options/recommendation, and draft successor handoff headers.
- `docs/DECISIONS-INBOX.md`: OPEN product decisions for framework, browser
  authentication/redaction posture, and v2 release scope, each with a
  recommendation and resume prompt.

## Key findings

1. The merged API can support a read-only browser overview through polling,
   but `current` is an all-frame one-shot read; no server push, projection, or
   downsampling exists.  P68 is the proposed push remedy.
2. Entity detail is available except for the TUI's local process list, which
   reads host paths directly and is intentionally omitted from browser v1.
3. P67 must be re-carved before dispatch: its handoff currently puts auth/TLS
   out of scope and does not contractually protect the new HTTP boundary.
4. No product source, dependency, framework install, or pin was changed.

## Validation

Environment: workspace container, Linux, Python reported by `python3`; no
source changes were made.

```bash
git diff --check
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error
```

`python3` is Python 3.14.6 and does not have pytest installed (`No module named
pytest`).  The available repository virtualenv was used for the actual
regression run:

```bash
timeout 900 env PYTHONPATH=groop/src /workspaces/vbpub/.venv/bin/python \
  -m pytest groop/tests -q -W error
# 1101 passed, 2 failed in 149.49s
```

The two failures do not involve any file changed by P69:

- `tests/test_report.py::TestReportCLI::test_zst_without_zstandard_exits_2`:
  this environment has `zstandard`, so the malformed fake frame reaches a
  decompression error and exits 1 rather than the test's expected no-extra exit
  2.
- `tests/test_ui_app.py::test_pilot_snapshot_hotkey_writes_bundle`: Textual
  pilot test observed no snapshot bundle after the `x` hotkey.

Self-review then ran the suite in the available clean Python 3.14.6 environment
without the optional `zstandard` extra:

```bash
timeout 900 env PYTHONPATH=groop/src /tmp/p43-clean-venv/bin/python \
  -m pytest groop/tests -q -W error
# 1101 passed, 2 skipped in 143.27s
```

This is the green full-suite result for the committed docs-only package. The
two skips are optional-`zstandard` paths.

The original `git diff --check` returned success while the new files were still
untracked, so it did not inspect them. `git diff --cached --check` then checked
the staged files successfully; self-review also ran `git show --check edbf698`
and a new `git diff --check`, both successfully. The final diff is restricted
to `groop/docs/**` and `groop/handoff/reports/**`.
