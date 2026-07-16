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
- **B5 — migrate groop + dstdns to nyxloom-trove.** One deliberate pass each
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
