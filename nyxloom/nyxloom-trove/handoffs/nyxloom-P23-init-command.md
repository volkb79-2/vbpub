---
schema_version: 1
id: nyxloom-P23-init-command
project: nyxloom
title: "exec-nyxloom init <project_folder> — scaffold a trove from templates"
tier: sonnet5-high
input_revision: "51bce30"
depends_on: []
session: fresh
source: {kind: roadmap, ref: nyxloom-trove/backlog.md}
scope:
  touch: ["src/nyxloom/cli.py", "exec-nyxloom.py", "tests/test_cli.py"]
  forbid: ["src/nyxloom/daemon.py", "src/nyxloom/reconcile.py", "src/nyxloom/storage.py", "src/nyxloom/config.py"]
oracles:
  - id: O1
    observable: "`nyxloom init <dir>` creates <dir>/nyxloom-trove/ containing nyxloom.toml, STANDARD.md, AUTHORING.md, handoffs/, reports/, decisions.md, roadmap.md, backlog.md, archive/, agent-logs/ and a .gitignore with agent-logs/ — scaffolded from the bundled templates; a test asserts the tree exists and nyxloom.toml is valid TOML with a [project] id"
    negative: "init into a dir that already has a nyxloom-trove/ exits non-zero WITHOUT overwriting existing files (idempotent-safe)"
    gate: tester-unified
  - id: O2
    observable: "exec-nyxloom.py grows an `init <project_folder>` path that forwards to `nyxloom init` (docker exec into the controller when present, host fallback otherwise), reusing the existing routing"
    negative: "init with no <project_folder> arg exits 2 with a usage message"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires editing a forbidden daemon-core file"
---

# P23 — `exec-nyxloom init <project_folder>`

> Tier: sonnet5-high · Base branch: main. First self-hosted nyxloom package (dogfood).
> The daemon runs this on a dedicated implement branch in its own git worktree
> under `.worktrees/`; commit all work on that branch. This scaffolds the trove
> standard into a new project. See `nyxloom-trove/AUTHORING.md` and `STANDARD.md`
> for the target layout.

## Context to read first (read ONLY these)
- `nyxloom-trove/STANDARD.md` — the exact trove directory structure to scaffold.
- `nyxloom-trove/nyxloom.toml` — the config template to copy (adapt `id`).
- `src/nyxloom/cli.py` — the argparse `main()` + subcommand pattern (mirror an
  existing simple subcommand like `status`/`doctor` for the new `init`).
- `exec-nyxloom.py` — the wrapper's `docker exec`-vs-host routing to reuse.
- `tests/test_cli.py` — the CLI test pattern to mirror.

## Work
1. Add a `nyxloom init <project_folder>` CLI subcommand (`cli.py`): create
   `<project_folder>/nyxloom-trove/` with the STANDARD.md structure — copy the
   bundled `STANDARD.md` + `AUTHORING.md` verbatim (find them relative to the
   package, or the repo's canonical `nyxloom/nyxloom-trove/`), write a fresh
   `nyxloom.toml` from the template with `[project] id = <basename>` and the
   folder paths, and seed `handoffs/`, `reports/`, `decisions.md`, `roadmap.md`,
   `backlog.md`, `archive/.gitkeep`, `agent-logs/.gitkeep`, `.gitignore`
   (`agent-logs/`). Refuse (exit non-zero) if `<dir>/nyxloom-trove/` already
   exists — never overwrite.
2. `exec-nyxloom.py`: route an `init <project_folder>` invocation through the
   same container/host resolution the wrapper already does, forwarding to
   `nyxloom init`.
3. Tests (`tests/test_cli.py`): O1 (scaffolds the tree, nyxloom.toml valid),
   O1-negative (existing trove → non-zero, no overwrite), O2-negative (no arg
   → exit 2).

## Gate (the ONLY accepted gate)
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'

## BLOCKED rule
If a named contract cannot be met as specified, or the work requires editing a
forbidden daemon-core file (daemon.py/reconcile.py/storage.py/config.py), STOP —
write `BLOCKED: <reason>` to the LOG, commit, exit. Do not improvise.
