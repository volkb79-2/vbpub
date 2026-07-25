# P104-LOG — Complete snapshot coverage (repair)

## Baseline

2018 total. enrich.py: 5 lines, 3 branches missing. bundle.py: 11 lines, 8 branches.

## Review findings addressed

F1: Removed misleading `test_copy_cgroup_files_read_failure`. Coverage for
    line 117 comes solely from `test_copy_cgroup_file_write_failure`.
F2: `tarfile.open` now wrapped in `with` context manager.
F3: All tests use `tmp_path` fixture or `tempfile.mkdtemp` — no fixed /tmp paths.
F4: `test_unique_bundle_path_exhaustion` patches `Path.exists` instead of
    creating 10,000 files. Asserts `call_count == 10000`.
F5: REPORT now prints literal before sets, per-run intersections, whole-file
    per-run missing sets, and exact gate command/exits.
F6: Systemctl status assertions expanded to full `unit`/`returncode`/`stderr`/`error`.

## Gate

Run 1: 2040 passed, exit 0
Run 2: 2040 passed, exit 0
PARITY: PASS. Both files 100%.
