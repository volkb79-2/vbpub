---
schema_version: 1
id: nyxloom-P25-dashboard-project-info-archive
project: nyxloom
title: "dashboard: surface each project's gate/channels/folders + archive-collapse UX"
tier: sonnet5-high
input_revision: "82593d5"
depends_on: []
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
scope:
  touch:
    - "src/nyxloom/render.py"
    - "tests/test_render.py"
  forbid:
    - "src/nyxloom/config.py"
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/storage.py"
oracles:
  - id: O1
    observable: "config.html (from `_render_config`) surfaces, per registered project read from its ProjectConfig: the gate id(s) with their rendered argv, the notify channels (ntfy_url + notifications/feedback topic names), and the trove folder paths (trove, handoffs glob, reports_dir, archive_dir). A test asserts config.html for the sample project contains its gate command text, both topic names, and the archive/reports folder paths"
    negative: "config.html shows only the existing policy/pause/routing form — gate command, notify channels, and trove folders are absent"
    gate: tester-unified
  - id: O2
    observable: "the completed-packages listing (`_render_history`) renders at most `cfg.archive_keep_visible` most-recent completed packages inline and collapses any older ones inside an element hidden-by-default via CSS (an Archive toggle mirroring the existing carve-toggle pattern). A test seeds more completed than the cap and asserts only the cap is visible-by-default with the remainder behind the toggle"
    negative: "all completed packages render inline with no cap and no Archive toggle (the `archive_keep_visible` config is ignored)"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires editing a forbidden file (config.py/daemon.py/reconcile.py/storage.py)"
---

# P25 — dashboard project info pane + archive-collapse UX

> Tier: sonnet5-high · Base branch: main (input_revision 82593d5).
> Backlog **B4**. Pairs with P22 (drilldown/legend, already merged). The config
> pane and a carve-toggle already exist; this adds the two missing pieces — the
> per-project gate/channels/folders summary and the archive-collapse — reading
> only from `ProjectConfig`, no schema change. Work happens in a git worktree on
> the implement branch.

## Context to read first (read ONLY these)
- `src/nyxloom/render.py` `_render_config` (~line 1281) — the config.html
  builder to EXTEND with a per-project gate/channels/folders summary. Note it
  already loads `config.ProjectConfig.load(root)` per project.
- `src/nyxloom/config.py` (READ only) — `ProjectConfig` fields available:
  `gates` (dict of `GateDef` with `.argv`), `notify` (`NotifyConfig`:
  `ntfy_url`, `ntfy_topic`, `cmd_topic`), `handoff_globs`, `reports_dir`,
  and the `archive_dir` / `archive_keep_visible` policy read from nyxloom.toml.
- `src/nyxloom/render.py` `_render_history` (~line 748) — the completed-packages
  listing to add the archive cap + collapse to.
- `tests/test_render.py` `test_index_html_carve_toggle_default_off_via_css`
  (~line 203) — the hidden-by-default CSS toggle pattern to MIRROR for the
  Archive collapse; `seed_data` fixture (~line 23) for seeding tasks; and
  `_render_config` test `test_config_html_renders_carve_authority_select`.

## Work
1. `_render_config`: for each project section, add a read-only summary block
   built from its `ProjectConfig` — the gate id(s) and their `html.escape`d
   argv, the notify channels (`ntfy_url` + notifications/feedback topic names),
   and the trove folder paths (trove root, handoff glob, `reports_dir`,
   `archive_dir`). All escaped; no new JS/API (this is display-only).
2. `_render_history` (the completed listing): render at most
   `archive_keep_visible` (config value, default 10) most-recent completed
   packages inline; wrap the older remainder in an Archive collapse hidden by
   default via CSS — mirror the carve-toggle checkbox+CSS approach, not JS
   innerHTML. Order by completion recency so the newest stay visible.
3. Tests (`tests/test_render.py`): O1 (config.html contains gate command, both
   topic names, archive/reports folder paths for the sample project),
   O2 (seed more completed than the cap → only the cap visible-by-default, rest
   behind the Archive toggle). Use / extend the existing fixtures; if the cap
   default of 10 is awkward to exceed, seed a config with a low
   `archive_keep_visible`.

## Gate (the ONLY accepted gate)
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'

## Scope / forbid
Touch only `render.py` and `tests/test_render.py`. Do not touch `config.py`
(read its fields, do not add any) or any daemon-core file — out of scope and a
BLOCKED trigger. Keep the existing config.html / history.html tests passing.

## BLOCKED rule
If a named contract cannot be met as specified, or the work requires editing a
forbidden file (config.py/daemon.py/reconcile.py/storage.py), STOP — write
`BLOCKED: <reason>` to the LOG, commit, and exit. Do not improvise a workaround.
