# P05 — static dashboard renderer

> Tier: haiku · Depends-on: none (storage/config frozen; no other package)
> · Read first: handoff/STANDING.md, src/handoffctl/render.py (docstring =
> normative: pages, element ids, redaction), docs/ARCHITECTURE.md §7.

## Owned files
- `src/handoffctl/render.py`
- `tests/test_render.py`

## Seed data (build in tests via storage on `tmp_state` + `sample_project`)
Two tasks in project 'demo':
- `demo-P01-sample`: ACTIVE, one attempt (route fake-cli, RUNNING, usage
  Usage(ACTUAL, 1000, 500, 200, 0.05, 'USD')), leases_held ['demo.stack'],
  notes 'implementing <script>alert(1)</script>', handoff_path set to the
  sample handoff; write an attempt log file containing
  'progress line\npassword=hunter2\n'.
- `demo-P02-done`: state MERGED (walk valid transitions via events or
  construct the statefile directly and save it), merge_commit 'a'*40,
  progress_units ['R1'], one EXITED attempt with usage ESTIMATED 0.10 USD.

## Oracles
1. `render_all(registry)` creates: index.html, history.html, dag.html,
   timeline.html, quality.html, live.html, task/demo/demo-P01-sample.html,
   task/demo/demo-P02-done.html — all under paths.www_dir().
2. index.html: contains `id="active-tasks"`; a row linking to
   `task/demo/demo-P01-sample.html`; the cost string `0.05 USD (actual)`;
   'demo.stack'; does NOT list demo-P02-done (MERGED goes to history).
   Escaping: `<script>` from notes appears only as `&lt;script&gt;`
   (assert raw '<script>alert' absent).
3. index.html pause banner: absent normally; after touching
   paths.pause_flag('demo'), re-render → `id="pause-banner"` present.
4. history.html: demo-P02-done row with merge_commit prefix `aaaaaaa`,
   progress unit 'R1', `0.10 USD (estimated)`.
5. task page P01: `id="log-excerpt"` present, contains 'progress line' and
   '[REDACTED]', NOT 'hunter2' (config._DEFAULT_REDACT covers password=);
   handoff body rendered inside `<pre>` (assert the contract sentence
   appears escaped/verbatim inside a pre block, and NO '<h1>' generated
   from the body's '# ' heading — no markdown rendering).
6. dag.html: `class="state-ACTIVE"` on P01's node; edges table lists the
   (P01 → demo.stack, mutex) row… (P01 has no deps; add depends_on by
   writing a third small statefile+frontmatter pair OR assert the mutex
   edge only — implementer's choice, but at least one edge row must be
   asserted).
7. timeline.html: one `class="lane"` per task; P01's bar title contains
   'att' and 'fake-cli'.
8. quality.html: row for (flash-high or route fake-cli — key by route_id)
   with attempts=2 aggregated across tasks and summed cost '0.15'.
9. Stale page removal: render, delete demo-P02-done's statefile, render
   again → its task page is gone.
10. Idempotence: two consecutive renders → byte-identical index.html
    (no timestamps in output except event-derived ones; use a fixed 'now'
    via monkeypatching render's clock if you need one — prefer deriving
    freshness from statefile.since only).

## Guidance
- Read the handoff body via statefile.handoff_path joined to the project
  root from the registry; missing file → task page renders with 'handoff
  file missing' note (no exception).
- Cost aggregation: sum only same-currency; mixed currencies → render both
  parts ('0.05 USD + 3.20 CNY'); basis-mix per docstring.
- Log excerpt: newest attempt with a log_path that exists; read last 64KB
  binary-safe (errors='replace'), then cfg.redact.
- Keep HTML building as small helper functions returning strings; one
  CSS constant. html.escape EVERY dynamic value at the point of insertion.
