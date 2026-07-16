# P15 — configure the factory from the UI (policy + routing matrix)

> Tier: sonnet · Date: 2026-07-15 · User directive: "we want to be able to
> configure all from the UI: the degree of parallel agents, the routing
> matrix — which CLIs/models for which task". Read handoff/STANDING.md.

## Design decision (spec amendment, decided by the user)

The pilot dashboard was read-only (SPEC §13). Amended: **CONFIG mutations
become allowed through audited loopback endpoints**; workflow-STATE
mutations (cancel/supersede/merge) remain CLI-only. Every config write
appends a CONFIG_CHANGED event (types.EventType.CONFIG_CHANGED exists)
with {scope, key, old, new} — typed values only, and secrets are
structurally impossible here (config files never contain secret values).

## Owned files

- `src/nyxloom/daemon.py` (HTTP handler additions + config reload)
- `src/nyxloom/render.py` (new config.html page + nav link)
- `src/nyxloom/config.py` — you MAY add two functions ONLY:
  `update_project_policy(root, changes: dict) -> None` and
  `update_routes(changes: dict) -> None` (surgical TOML edits — see below);
  nothing else in the frozen file.
- `tests/test_config_ui.py` (new), plus minimal additions to
  tests/test_daemon.py / test_render.py.

## Scope of configurability (v1)

1. **Per-project policy** (writes `<root>/.nyxloom/project.toml`
   `[policy]` keys): max_active_tasks, ready_queue_target,
   max_attempts_per_task, wave_max_diffs, stall_log_quiet_seconds,
   attempt_max_wall_seconds (if P14 added it — tolerate absence),
   reconcile_interval_seconds. Integer-validated against sane bounds
   (1..64, interval 5..600).
2. **Pause/unpause buttons** per project (reuse the exact CLI semantics:
   flag file + PAUSE_SET/PAUSE_CLEARED with actor OPERATOR 'ui').
3. **Routing matrix** (writes $XDG_STATE_HOME/nyxloom/routes.toml):
   per tier, reorder/select which route ids are active (from the set of
   DEFINED routes — the UI never creates new route definitions in v1, it
   maps tiers to existing routes). Renders current tiers table + route
   definitions read-only table (cli/model/variant/effort/status).

## Mechanics

- Endpoints (loopback HTTP, same server): POST /api/config/policy
  {project, key, value}; POST /api/config/pause {project, paused:bool};
  POST /api/config/tier {tier, routes:[route_id,...]}. JSON responses;
  400 on validation failure; every success appends CONFIG_CHANGED and
  triggers re-render. NO other mutating endpoint.
- TOML editing MUST be surgical line-editing (preserve comments/layout):
  match `^<key> = <value>` inside the [policy] section / `routes = [...]`
  line under the [tiers.<tier>] header; refuse (400 + no write) if the
  anchor is not found. Never reserialize whole files.
- Config reload: policy is re-read every pass already (ProjectConfig.load
  in run_pass — verify; if cached, invalidate). Routes: Routes.load happens
  per dispatch already. So no daemon restart needed — assert that in a test.
- config.html: plain form(s) + vanilla JS fetch POSTs; show current values;
  after save, reload the page. Same CSS/nav as other pages. NOTE for the
  tracked copy: routes.toml edits change ONLY the live state file — render a
  visible hint on the page: "tracked copy nyxloom/routes.host.toml may
  now differ — sync it in git when satisfied".

## Factory-state control (added 2026-07-15, user directive)

4. **Pause MODES** replace the boolean pause. The project pause flag file's
   CONTENT becomes the mode; semantics enforced in reconcile.plan_project:
   - absent flag = `run`: everything dispatches.
   - `drain-handoffs` (mode 2; also the meaning of a legacy EMPTY flag
     file — today's behavior): no NEW task leaves QUEUED, but in-flight
     handoffs proceed through their FULL pipeline — resumes, stall
     restarts, and review launches still happen until reviews finish.
   - `drain-agents` (mode 1): no new agent process of ANY kind starts —
     no dispatch, no ResumeAttempt, no LaunchReview; running processes
     finish and the factory comes to rest (tasks parked in
     AWAITING_REVIEW/INTERRUPTED as they land).
   UI: per-project buttons Run / Drain handoffs / Drain agents with the
   current mode displayed; POST /api/config/pause gains {mode:
   "run"|"drain-handoffs"|"drain-agents"}. Events: PAUSE_SET payload
   {"mode": ...} / PAUSE_CLEARED. CLI `pause <project> [agents|handoffs]`
   and the ntfy command verb `pause <project> [agents|handoffs]` accept
   the optional mode (default handoffs); `unpause` = run. commands.py may
   be touched ONLY for this verb-arg extension (strict regex widened to
   `^(help|status|pause|unpause|digest)( [a-z][a-z0-9-]{0,30}){0,2}$`).
5. **'last activity' column per agent**: index.html active-tasks table and
   task-page attempts table gain a last-activity age (from the newest
   attempt log's mtime at render time — paths.attempt_dir; '-' when no
   log). Human units (e.g. '3m', '2h05m'). This is the operator's
   at-a-glance liveness view; keep it cheap (one stat per attempt).

## Oracles

1. POST policy change -> project.toml line updated in place (comments
   intact — assert a known comment survives), CONFIG_CHANGED event with
   old/new, next run_pass uses the new cap (planner receives it).
2. Bounds: max_active_tasks=0 or 999 -> 400, file untouched, no event.
3. Tier remap: POST tier flash-high -> ["claude-sonnet5-high"] rewrites
   only that line; unknown route id in list -> 400.
4. Pause via UI -> flag + event; unpause reverses.
5. config.html renders current policy values and tier table; contains no
   inline secrets (assert token strings absent) and no innerHTML use.
6. Traversal/method safety: GET on POST endpoints -> 405/404; unknown
   project -> 404. Full suite green.
7. Pause modes (planner tests): drain-handoffs -> QUEUED stays put while a
   waiting wave still yields LaunchReview and an INTERRUPTED-with-handle
   attempt still yields ResumeAttempt; drain-agents -> none of the three;
   run -> all. Legacy empty flag file behaves as drain-handoffs. UI/CLI/
   ntfy verb each set the mode file + event (one test per surface).
8. last-activity: seeded attempt log with known mtime renders the expected
   age string in both tables.

## Rules

STANDING.md applies. Do not commit. REPORT to
handoff/reports/P15-REPORT.md; receipt-only final message.
