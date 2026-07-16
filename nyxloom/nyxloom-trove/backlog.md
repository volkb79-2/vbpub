# nyxloom dev backlog — un-carved polish items

Confirmed with the user 2026-07-16. These become P23+ handoffs; once
self-hosting is on (pending handoffs frontmatter-converted → nyxloom enrolled in
the daemon registry), nyxloom dispatches them itself (dogfooding).

- **B1 — two-channel notify + FQDN.** Emit to `notifications` (progress, shared,
  project-as-tag) and `feedback` (decisions + escalation, bidirectional; unifies
  the old `cmd` topic + decision-chat). ntfy on `nyxloom.<fqdn>` derived from
  ciu identity (`{{ project_name }}.{{ public_fqdn }}`, wildcard-covered).
  Code: `notify.py`/`config.py` read `notifications_topic`/`feedback_topic`.
  Live step: redeploy ntfy + ACLs + **user re-subscribes on phone**.
- **B2 — nyxloom-trove config discovery + schema.** Daemon finds
  `<root>/nyxloom-trove/project.toml`; a JSON schema for project.toml so
  `nyxloom lint` validates config (bad gate argv, missing worktree_root,
  unresolved `[refs]`).
- **B3 — `exec-nyxloom init <project_folder>`.** Scaffold a trove from bundled
  templates via the running instance (proves folder access). Adds an `init`
  subcommand to the exec-nyxloom wrapper + `nyxloom.cli`.
- **B4 — dashboard reads project.toml + archive UX.** Show each project's gate,
  channels, folders. Keep last `archive_keep_visible` (10) completed visible;
  older behind an **Archive** button. (Pairs with P22 legend/drilldown.)
- **B5 — migrate topos + dstdns to nyxloom-trove.** One deliberate pass each
  (`git mv handoff nyxloom-trove/handoffs`, repoint config).
- **B6 — self-host activation.** Frontmatter-convert nyxloom's NOT-YET-
  IMPLEMENTED handoffs (P16 carver, P18 decision-chat, P22 dashboard), then add
  nyxloom to the daemon registry so it dispatches its own backlog.
- **B7 — daemon state = a persistent `nyxloom-state` docker volume.** Move the
  source of truth (event log, statefiles, registry, routes, leases, pidfile)
  off the host-home XDG path (`~/.local/state/nyxloom`, a transitional artifact
  of the CLI-auth home bind) into a dedicated volume mounted into the nyxloomd
  container — container-native, `git clean`-safe. Agent logs move the other way:
  into each project's `nyxloom-trove/agent-logs/` (gitignored by default). Code:
  `paths.py` (state root from `NYXLOOM_STATE_DIR` → `/var/lib/nyxloom`; agent-log
  dir → the project trove), the nyxloomd ciu stack (add the volume, set the env,
  drop the home-bind reliance for STATE — keep it only for CLI auth), + a
  one-shot migration of existing state. Daemon-core: gate + rebuild after merge.
- **B8 — daemon resume-safety (carved as P26).** Replace the manual "DON'T
  restart the daemon needlessly" operator rule with automatic detection: a
  resumed session that keeps dying is currently resumed forever (resumes reuse
  one attempt record, so `attempts_count` never trips `max_attempts_per_task`).
  Detect repeated failed resumes → stop resuming that session → fresh-start a
  new attempt (new session) under configurable `max_resume_failures` /
  `resume_progress_grace_seconds`, or BLOCK cleanly when the fresh-attempt
  budget is gone. Inactivity (tier-1/2 stall + wall-clock cap) already exists;
  this only adds the resume-failure→fresh-start decision in `reconcile.py`.
  Daemon-core: gate + rebuild after merge. Depends on B2/P24 (config schema).
- **B9 — feature-intake exploration agent (the factory's front door).** A new
  UI tab + conversational agent (SIBLING of `decision_chat.py` P18: reuse its
  ntfy/UI transport, resumable read-only redacted claude session, and confirm-
  to-finalize pattern). User starts with a ROUGH feature request; the agent (1)
  reads project/product context (`[refs]` docs + roadmap + recent handoffs),
  (2) interviews the user to confirm SHARED understanding (purpose/scope), (3)
  elicits the details needed to build the right thing, (4) surfaces product +
  technical consequences and files any genuine product calls as **`D-NNN`
  decisions** (wiring `depends_on: [D-NNN]` into the eventual handoff), (5)
  estimates blockers / prior work / competing roadmap items over the
  `depends_on` graph + headroom signal, (6) asks desired PRIORITY and slots it
  in, (7) on user satisfaction persists a structured **pre-carve brief**
  (aligned purpose, elicited detail, consequences, linked decisions, priority)
  as an enriched backlog item — carry the brief into the P16 carver as seed
  context so "direct carve" loses NO context. Phases: P-α schema+auto-tick
  (=B10), P-β intake agent backend, P-γ UI tab, P-δ direct-carve-from-brief.
  Open D-calls: brief = new doc vs enriched backlog item (lean: enriched
  backlog); does `priority` drive dispatch order (scheduler change). Depends on
  B10.
- **B14 — daemon project mounts derived from the registry.** The nyxloomd stack
  hardcodes its project binds (`ciu.compose.yml.j2` volumes: vbpub + dstdns),
  duplicated into the pre-rendered `docker-compose.yml` and kept in sync only by
  a comment. The registry already knows every project root, so a project can be
  **registered and unreachable** — which is exactly what happened to
  netcup-api-filter (its `D-001`; one-line fix + drift test carved as **P27**).
  Principled fix: render the binds from the registered project roots (ciu template
  reads the registry, or a documented render step), so `project add` cannot
  produce a project the daemon cannot see. Consider the reverse guard too:
  `project add` (or `doctor`) should FAIL when the root is not visible from inside
  the container, instead of registering a project that silently never dispatches.
  Depends on P27 landing the tactical fix first.
- **B10 — roadmap/backlog light schema + daemon auto-tick on merge.** Give
  roadmap/backlog items a parseable structure (id, status, priority, links to
  carved handoffs / D-decisions) like `decisions.md` has, schema-validated
  (extends P24). Then the daemon writes ONE typed, mechanical update: on
  handoff merge, tick/annotate the linked roadmap/backlog item (the same reflex
  that archives handoffs) — making the roadmap self-updating and fixing the
  "Status: line lies" problem at its root. STRICTLY typed writes only; the
  daemon never free-authors roadmap prose (injection-boundary + typed-fields-
  only doctrine). Prerequisite for B9. Daemon-core: gate + rebuild after merge.
- **B12 — lint L7 forbid-existence vs. chained handoffs.** L7 errors when a
  `forbid` path does not exist on the base branch, but a chained handoff often
  forbids a file a NOT-YET-MERGED predecessor phase creates (e.g. P30/P31
  forbidding `intake_chat.py` that P29 introduces) — a false stick at carve
  time, since the file exists by the phase's actual dispatch time. Fix: when a
  forbidden path is listed in the `scope.touch` of a `depends_on` handoff (or
  simply: forbid-non-existence is a WARNING, not an error, mirroring how L7
  already treats non-existent `touch` paths). Discovered dogfooding the B9/B10
  carve. Small `lint.py` change + test.
- **B11 — `doctor` crashes on already-BLOCKED tasks (`BLOCKED -> BLOCKED not
  allowed`).** Observed 2026-07-16 against dstdns while three tasks sat BLOCKED
  `interrupted-dead-end`: `exec-nyxloom.py doctor` exits with the TransitionError
  instead of a report — the one health surface an operator reaches for when tasks
  are blocked is the one that dies on them. Doctor must be read-only (never
  attempt transitions) or guard the re-block path the way reconcile's
  `!= BLOCKED` guard does. (Related TICK_ERROR spam existed in dstdns events
  seq 91-137 before the guard landed.)
- **B12 — lint L7 asymmetry: `scope.forbid` entries are checked as literal
  paths while `scope.touch` entries glob.** Observed 2026-07-16 (dstdns
  infra-P24): `forbid: ["infra/authentik/**"]` errored "path does not exist"
  though the dir exists and the same glob in `touch` passes. Either glob both
  or document that forbid takes literal prefixes; today authors discover it by
  lint error archaeology.
- **B13 — stale pause files are silently inert after the flag-format change.**
  dstdns carried an EMPTY `pause.agents` file (set 2026-07-15 by the pre-rename
  daemon); the current daemon reads the pause MODE from file CONTENT, treated it
  as no-pause, and dispatched — the operator believed the project was paused
  while agents ran. Migration/lint should flag (or refuse) a pause flag whose
  content is not a known mode; `status` should render the effective pause mode
  per project so operator belief and daemon behavior can't diverge silently.
- **B13 — carve-task exit re-scan gap (carved as P32).** The reconcile
  EmitAttemptExit re-scan finalizes an EXITED attempt of an ACTIVE task only for
  `role` IMPLEMENTER / FRONTIER_REVIEW — NOT CARVER. So a carve whose live
  exit-processing pass is missed (a daemon restart landing on the carver's exit)
  is never retired to SUPERSEDED and its synthetic task stays ACTIVE forever,
  permanently eating a wip slot. Observed 2026-07-16: `carve-nyxloom-1` stuck
  ACTIVE ~2h, throttling the factory to 2/3 capacity (manually superseded to
  unblock). Fix = add the CARVER branch to the trigger (reconcile.py); the
  daemon.py `_consume_carve_exit` handler already exists. Depends on P26
  (co-edits reconcile.py). Daemon-core: gate + rebuild after merge.
- **B14 — review verdict rubber-stamp (carved as P33).** The merge gate maps
  `receipt.result==DONE -> MERGE_READY`, but `result` reflects only PROCESS exit
  (wrapper: clean exit -> done), NOT the review verdict. The packet asks a
  rejecting reviewer to emit a `BLOCKED: rejected` FINAL line — a mechanical
  self-signal models miss. Live incident 2026-07-16: P26's reviewer wrote a
  correct REJECTED report but exited clean -> done -> MERGE_READY -> buggy
  daemon-core nearly merged (caught + reverted at merge review). Fix (P33):
  derive the verdict from the durable `<task>-REVIEW.md` and FAIL SAFE to
  REJECTED on any ambiguity; require a machine-readable `VERDICT:` line.
  Daemon-core. NOTE: P26 (resume-safety, B8) was reverted for a real defect
  (fresh-start dispatch bypasses every dispatch guard) — it needs re-carving
  with the guard-respecting contract (a P34).
