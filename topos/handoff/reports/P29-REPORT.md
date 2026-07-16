# P29 Report — Inspect-Files Safety Skeleton

## What Was Built

- Added `topos/src/topos/inspect_files/` package with:
  - **`catalog.py`**: `InspectFilesKind` enum (`docker-json-log`, `systemd-journal`,
    `cgroup-files`), builder functions with path/argv safety validation,
    `INSPECT_CATALOG` dict, and lexical path normalisation.
  - **`plan.py`**: `InspectFilesPlan` dataclass with `to_jsonable()` and `to_text()`
    rendering, `DisabledInspector` dataclass for gated responses,
    `build_inspect_plan()` and `build_gated_inspect_plan()` functions.
- Added `topos inspect-files plan --kind --target [--inspect-files] [--admin] [--json]`
  CLI command gated on both `--inspect-files` and `--admin` flags:
  - Without both flags: disabled message + exit 2.
  - With both flags: deterministic JSON or text plan output — no content reads,
    no subprocess execution, no host mutation.
- Added focused tests:
  - **44 tests** in `topos/tests/test_inspect_files.py` covering gating (5),
    disabled-via-CLI (5), plan rendering (8), path/argv safety (13),
    no-execution/no-read structural checks (5), catalog completeness (2),
    CLI integration (6).
- Updated `README.md` (P29 row → Done), `docs/STATUS.md` (moved from Not Implemented
  to Implemented, updated v2 percentage and Quality Gate), `docs/ROADMAP.md`
  (P29 marked done).
- Added `docs/INSPECT-FILES.md` with the full safety contract.

## Worktree

- Branch: `feat/topos-p29-inspect-files-safety`
- Worktree: `/home/vb/volkb79-2/vbpub/.worktrees/-topos-p29-inspect-files-safety`
- Python: `/tmp/vbpub-topos-p29-venv/bin/python` (Python 3.13.5)

## Deviations from Handoff

- **CLI shape**: The handoff suggested `topos inspect-files plan --target ENTITY_OR_CONTAINER --kind docker-logs --admin`.
  The implemented form is `topos inspect-files plan --kind KIND --target TARGET [--inspect-files] [--admin] [--json]`,
  which mirrors the existing `topos action preview` pattern and keeps `--inspect-files`
  as an explicit flag (matching the spec's gating design). The handoff described
  `--inspect-files` as a top-level flag; in P29 it is a subcommand-level flag.
- **Plan kinds**: The handoff listed `docker-json-log` as `docker-json-log` (lowercase
  consistent with Docker's actual json-file log driver naming). `cgroup-files` uses
  semantic filenames rather than a fixed list of known cgroup entries from snapshot
  bundles.

## Test Evidence

```bash
/tmp/vbpub-topos-p29-venv/bin/python -m pytest topos/tests/test_inspect_files.py -v
# 42 passed in 0.35s

PYTHONPATH=topos/src /tmp/vbpub-topos-p29-venv/bin/python -m pytest topos/tests/test_inspect_files.py -q
# 44 passed in 0.33s after controller review

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests/test_inspect_files.py -q
# 44 passed in 0.29s on main after merge conflict resolution

/tmp/vbpub-topos-p29-venv/bin/python -m pytest topos/tests -q
# 243 passed in 30.27s

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q
# 261 passed in 28.94s on main after merge conflict resolution

/tmp/vbpub-topos-p29-venv/bin/python -m py_compile \
  topos/src/topos/inspect_files/__init__.py \
  topos/src/topos/inspect_files/catalog.py \
  topos/src/topos/inspect_files/plan.py \
  topos/src/topos/cli.py \
  topos/tests/test_inspect_files.py
# clean, exit 0

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m py_compile topos/src/topos/inspect_files/__init__.py topos/src/topos/inspect_files/catalog.py topos/src/topos/inspect_files/plan.py topos/src/topos/cli.py topos/tests/test_inspect_files.py
# clean, exit 0 on main after merge conflict resolution

PYTHONPATH=topos/src /tmp/vbpub-topos-p29-venv/bin/python -m topos.cli \
  inspect-files plan --kind docker-json-log --target c1 --inspect-files --admin --json
# {"command_previews": [["cat", "/var/lib/docker/containers/c1/c1-json.log"], ...],
#  "kind": "docker-json-log", "mode": "plan", ...}

PYTHONPATH=topos/src /tmp/vbpub-topos-p29-venv/bin/python -m topos.cli \
  inspect-files plan --kind docker-json-log --target c1
# exit 2, "file inspection is not enabled"

PYTHONPATH=topos/src /tmp/vbpub-topos-p29-venv/bin/python -m topos.cli \
  inspect-files plan --kind docker-json-log --target c1 --inspect-files
# exit 2, "admin mode is not enabled"
```

## Known Gaps

- Real file content reading, log tail/follow remain out of scope (P29 is
  planning-only).
- No daemon integration or TUI screen for file inspection.
- No Docker/systemd subprocess calls.
- No `--audit-log` equivalent for inspection plans (could be added in a
  future package if audit log integration is needed).
- The planner intentionally avoids `Path.resolve()`. Path previews are lexical
  and do not confirm whether the referenced file exists or whether an existing
  prefix is a symlink.

## Contract-Change Proposals

None. P29 is entirely additive and package-private.
