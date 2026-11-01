# M3 continuation

Branch `tnt-m3-20261101` contains the remaining M3 v27 work. M1, M2, It1-It3,
and release 0.1.0 were already complete before this branch.

## Implemented

- Pool scrub schedules use v27 `pool.scrub.query`, `get_instance`, `create`,
  `update`, and `delete`; run-now submits the tracked `zpool.scrub.run` job.
  Typed delete confirmation binds the pool name and schedule ID. Pool references
  are re-read, boot-pool schedules are refused, and run-now checks pool health
  and current scan state. The API can still return a successful no-op if its
  validation facts change after tnt's preflight.
- Added read-only Storage > Pools inventory and `tnt storage pools`, including
  health, space, scan state, and vdev topology.
- Added per-session-generation read probes for Apps, Containers, Virtual
  Machines, KMIP, and Enclosure. Unsupported entries are hidden; permission,
  transport, and malformed-response failures remain visible as unavailable
  without actions. Cache state resets when the host generation changes.
- Vanilla v27 has no SMART test-task method or generic middleware/syslog tail
  method. The existing audit reader remains the only log view and keeps its
  secret scrubbing.
- The pinned `truenas_api_client` is transport/authentication only. The v27
  container schema still has no image identity or configured memory-limit
  field, so TNT-36/TNT-37 remain deferred.
- The only additional Later screen selected was read-only pool inventory.
  Storage partitioning/free-space, pool creation/import, and boot-pool actions
  remain deferred; credentials, certificates, KMIP workflows, full Enclosure,
  broad reporting, and system-wide settings did not meet this scope's low-risk
  bar. See `DESIGN.md` and TNT-19 in `BACKLOG.md` for the candidate review.

The v27 signatures, classifications, and method evidence are in
`API-METHODS-V27.md`. Consumer commands and examples are in `CONSUMERS.md`.

## Verification and follow-up

The serial focused pytest selection passed **383 tests, 0 skipped**. It covered
`test_management_scrub.py`, `test_conditional_menu.py`, `test_management_tasks.py`,
`test_management_core_views.py`, `test_tui_workflow_branches.py`, `test_tui.py`,
`test_tui_hosts.py`, `test_tui_ux.py`, `test_docs_contract.py`,
`test_guard_v27_manifest.py`, `test_workflow_cli.py`, `test_domain_actions.py`,
`test_policy_edge_forms.py`, `test_fake_server_support.py`,
`test_fake_workflows.py`, `test_guard_rereview.py`, `test_guard_round3.py`, and
`test_workflow_precondition_edges.py`, with package and branch coverage enabled.
Coverage for that selection was **74.37% package-wide**; **309/309 (100%)** of
executable source lines added in M3 were covered. This partial selection does
not establish the project's 97% full-suite threshold.

The registered full `r0-r1` gate was not run because the task explicitly
prohibited `run-gate` and Docker. Run that gate in `tester-unified` when allowed
and record the resulting full-suite coverage against the 97% threshold.

No request was sent to any host. The local config required by the documented
live lane was unavailable, and `TNT_LIVE`/`TNT_LIVE_PROFILE` were not set, so no
read-only v1001 probe could be made. If the operator provisions the documented
profile, perform any M3 live reads only against v1001 while holding the stated
lock; never use another host. The live acceptance scope itself remains
opt-in.
