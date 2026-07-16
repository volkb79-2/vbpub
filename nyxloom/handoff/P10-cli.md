# P10 — operator CLI

> Tier: haiku · Depends-on: interfaces of all modules (monkeypatch in
> tests) · Read first: handoff/STANDING.md, src/nyxloom/cli.py
> (docstring = normative subcommand table), types.py, storage.py.

## Owned files
- `src/nyxloom/cli.py`
- `tests/test_cli.py`

## Oracles (run via `cli.main([...])`, capsys for output)
1. `project add demo <root>` on a prepared root (reuse sample_project's
   layout pieces or point at its cfg.root after wiping the registry) →
   exit 0, registry lists it, PROJECT_REGISTERED event with actor kind
   'operator'; `project list` prints id and root.
2. `lint`: monkeypatch lint.lint_project → one error finding → exit 1 and
   a line matching `<path>:<line> L2 error <msg>` (line '-' when None);
   all-clean → exit 0 and 'clean' in output. Explicit path args call
   lint.lint_file per path (monkeypatch, assert called with Paths).
3. `doctor`: monkeypatched findings incl. one 'critical' → exit 1, table
   contains kind and severity; only warnings/info → exit 0.
   `doctor --rebuild` prints monkeypatched diffs; `--rebuild --write`
   passes write=True through (record kwargs).
4. `status`: seed one statefile via storage → output row has task id,
   state, route id of newest attempt, cost with basis; `--project demo`
   filters.
5. `render` → prints the www path (monkeypatch render.render_all →
   sentinel Path, assert printed).
6. `tick` → monkeypatch daemon.run_once → 7 → output contains '7',
   exit 0; `--project demo` forwarded.
7. `decide demo D-002 --choose "b" --note "why"` → decisions.decide called
   with (cfg-like, 'D-002', 'b', 'why', $USER) (record args; monkeypatch)
   AND a DECISION_RESOLVED event with decision_id 'D-002' and actor kind
   'operator' exists afterwards. decisions.decide raising DecisionError →
   exit 1, stderr starts 'error:' and NO event appended, NO traceback in
   output.
8. `discuss demo D-002` prints the monkeypatched command string verbatim.
9. `pause demo` → flag file exists + PAUSE_SET event (no task_id);
   `pause demo demo-P01-sample` → task flag + PAUSE_SET with task_id and
   statefile.paused True (seed the statefile first); `unpause` reverses
   both (flag gone, PAUSE_CLEARED, paused False).
10. `leases` with one held lease (acquire in-test) → row shows name and
    held=True with owner.
11. `digest demo` prints notify.digest's monkeypatched string; `events
    demo --type TASK_CREATED` prints only that type's lines.
12. `version` prints __version__ and exits 0 even when every sibling
    module import would raise (verified by the lazy-import structure:
    assert 'import nyxloom.daemon' does NOT appear at module top —
    inspect cli.py source in the test for 'from . import' outside
    handlers... keep it simple: assert version works after monkeypatching
    sys.modules['nyxloom.daemon']=None).
13. Unknown subcommand → exit 2, argparse usage on stderr.

## Guidance
- Resolve cfg per project id via config.load_registry + ProjectConfig.load
  inside a small helper `_cfg(project)`; unknown project → 'error:' +
  exit 1.
- Table output: simple str.ljust columns; no wrapping logic.
- $USER via os.environ.get('USER', 'operator').
- --debug global flag re-raises instead of catching (one test: DecisionError
  with --debug → pytest.raises).
