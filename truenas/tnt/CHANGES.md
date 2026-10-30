# tnt changes

## [Unreleased]

<!-- cmru: release history -->

## [0.1.0] - 2026-10-29
<!-- cmru: generated -->
<!-- cmru: source-end=c0421e29c4a03b05de49455b4c93f92161cb813d -->

### Added
- feat(tnt): add generation-safe multi-host TUI sessions (052b192f5)
- feat(tnt): add secure paired multi-host configuration (2408c32bf)
- feat(tnt): truenas-tui scaffold (M0): design, transport, v27/v28 adapters, plan/apply model, CLI, Storage > Disks TUI, fake server, tests (accff5e8c)

### Fixed
- fix(tnt): correct the final two TUI test assumptions (7ae4fa9d2)
- fix(tnt): close bounded review findings (725313854)
- fix(tnt): lazy realistic pool membership, thread-bound plan grant, kind tied to destructive methods, strict paths and pool ids, single-use session-bound plans (fa45ad118)
- fix(tnt): default-deny guarded client, live disk-wipe protection, upper-bound free space, review hardening (730ed56ef)

### Changed
- Merge tnt 0.1.0 candidate: TrueNAS v27 TUI+CLI and cmru registration (luna-reviewed x3, r0-r1 943 passed 98.30%); pytest-cov backlog entry renumbered RG-110 -> RG-122 against the dev-wheel entry (c0421e29c)
- Register TNT as a CMRU release project (a4d3f5cd9)
- Add tmux-based TUI UX checks (c12038cad)
- Fix host add picker lifecycle (f8adfb940)
- Handle host picker mount ordering (06c899d65)
- Fix literal rendering for untrusted TUI text (cdd32193e)
- Align tnt UX assertions with merged menu and wide layout (80030dd69)
- Merge tnt UX work (small-terminal handling, discoverability, Pilot UX harness) into the tnt branch (1a75bff5e)
- Improve TUI discoverability and small-terminal UX (60ae8d13c)
- tnt: validate vanilla v27 live workflows (bb3359041)
- fix tnt findings 1 and 3: move TUI RPC planning into workers (860b36d90)
- fix tnt findings 2 and 3: bind credentials and bound worker drains (a821d21a1)
- fix tnt findings 4 and 5: lockout action safety (11aa64b7f)
- Add hardened interactive SSH hand-off (b046947bf)
- Add read-only core views and CLI commands (0ce219e14)
- Add non-blocking job registry and read models (9e99b4af6)
- tnt: reconcile post-M2 milestones (c751edd91)
- tnt: enforce the verified v27 method contract (c00d157e0)
- tnt: restore unit coverage gate (24744191b)
- tnt: guard local user access changes (042c58a77)
- tnt: handle uncertain system-action replies (d3ab34b71)
- tnt: add guarded system actions and config export (caee1f18c)
- tnt: add v27 API key management (704787b6b)
- tnt: add v27 audit viewer (9f11b961f)
- tnt: add v27 Apps workflows (0064b51d3)
- tnt: add read-only v27 network status (d8964f5d9)
- tnt: add v27 data protection task workflows (e957d4d8b)
- tnt: add v27 SMB and NFS share workflows (814bcf1ef)
- tnt: define opt-in live acceptance lane (af530c206)
- tnt: model v27 workflows in fake server (faa3138a5)
- tnt: add v27 workflow screens (394e8469c)
- tnt: add v27 core workflow CLI (9209d24ab)
- tnt: harden v27 manifest and boot-pool guards (563c00fdb)
- tnt: version profiles and support v27 password login (797a5bdd6)
- truenas/tnt: fix round-three guard findings (ee19a8d93)

### Documentation
- docs: record tnt milestone review fixes (9415769ab)
- docs(tnt): document multi-host credentials and host switching (93d49ce40)

### Testing
- test(tnt): synchronize tmux resize waits (0990a89af)
- test(tnt): cover branch gaps and record pytest-cov false green (bacb3c503)

<!-- cmru: pending release notes begin -->
- Registered the first CMRU release for this TUI and scriptable CLI for vanilla TrueNAS v27: initial version `0.1.0`,
  tag `tnt-v0.1.0`, and a wheel plus SHA256 in the matching GitHub Release; CMRU maintains `tnt-latest` as the moving
  release. No tnt release has been published yet.
  The release gate runs the existing `r0-r1` and `ux-focused` lanes; real-tmux cases still skip because
  `tester-unified` does not include tmux (TNT-54). The wheel's dependencies remain those in `pyproject.toml`.
  See the [release design](docs/DESIGN.md#12-decisions-open-questions-and-estate-registration) and
  [installation instructions](docs/CONSUMERS.md#install-from-github-release-assets).
- Completed vanilla TrueNAS v27.0.0-RC.1 live acceptance through the registered `live-v27` Run-Gate lane. Datasets,
  shares, snapshot tasks, user credentials, containers, pool import, read views, API keys, multi-host switching,
  interactive SSH hand-off, and private configuration export passed. VM, Apps lifecycle, replication/cloud-sync
  execution, reboot, shutdown, and update apply were deferred for the reasons recorded in the
  [live acceptance report](docs/LIVE-TESTING.md#live-acceptance-run-2026-10-29). Live findings and their regression
  fixes are listed in that report.
- The `r0-r1` lane now reports coverage to two decimal places (`[tool.coverage.report] precision = 2`), aligning
  pytest-cov's `--cov-fail-under=97` comparison with its two-digit report so a displayed 96.94% fails. The general
  pytest-cov/Run-Gate command-lane false green is filed as RG-122 in run-gate-project's backlog. The current registered
  `r0-r1` run passed 943 tests and reported 98.30% coverage. Earlier setup failures in the core-view and reboot-refusal
  tests were corrected; the tests locate the mounted view and wait for it to attach while retaining their behavior checks.
- Restored the typed `api-key-lookup` refusal for a missing API key, added visible worker-start messages when no host
  lease is available, and routed TUI action-builder tests through `_build_action_from_input`. Bound the copied
  password-auth example to its configured endpoint. Live-test docs now list the required `PROFILE-alt` alias and
  distinguish the local `r0-r1`/`live-v27` lanes from the separate estate `live-inspect` report.
- Closed the It4 milestone-review fixes for the vanilla v27 UI and guard paths. TUI lookup/action-construction and system
  preflight RPCs now run in cancellable workers with visible progress; host switching and quit bound the drain wait and
  offer Keep waiting or Cancel. Credentials are bound to their saved host URL, so independently resolved config and
  secrets cannot pair the same alias with another appliance; unbound legacy credentials must be replaced before use.
  App stop/restart and current-login shell/home changes now use destructive typed confirmation, and the boot-pool guard
  test asserts the actual refusal. The registered FakeServer gate remains the repeatable code gate. At that stage no
  live host had been contacted; the later vanilla-v27 acceptance is recorded in the
  [live acceptance report](docs/LIVE-TESTING.md#live-acceptance-run-2026-10-29).
  See the [worker/session design](docs/DESIGN.md#5-ux), [secret binding rules](docs/DESIGN.md#7-secrets),
  [user update design](docs/DESIGN.md#user-account-updates), and [Apps guide](docs/CONSUMERS.md#apps).
- Completed It3 interactive SSH hand-off. Added `tnt ssh [HOST]` and TUI `s`/command-palette actions with direct argv
  execution, no remote-command mode, no user/system SSH config, and a pinned known-hosts file with strict checking.
  Identity paths are checked before use; only configured identities are offered, agent/password authentication is
  disabled, and SSH session streams are not captured by tnt. TUI suspension restores the screen on exit and the CLI
  preserves the child exit status. Added stub-executable tests for hostile arguments, refusal paths, and suspend/restore.
  Reconciled It4 live-test guidance to cover interactive SSH only. See the
  [SSH consumer examples](docs/CONSUMERS.md#interactive-ssh-hand-off) and
  [SSH design](docs/DESIGN.md#82-interactive-ssh-hand-off).
- Completed It2. Job calls now return generation-bound handles so TUI workers can wait outside the guarded RPC call lock;
  callbacks capture sanitized progress, logs and errors, applied call results include remote job IDs, and reconnect
  reconciliation leaves invisible outcomes UNKNOWN. Partial multi-call applies preserve completed results and per-call
  states; CLI output carries the partial record with a nonzero exit, and TUI review directs the operator to inspect
  before recovery. Added schema-checked, bounded read-only Dashboard, Alerts, Jobs, Services, and Storage health views
  plus `tnt dashboard`, `tnt alerts list`, `tnt jobs list`, `tnt services`, and `tnt storage health` (`--json` on each).
  Job pages keep remote rows separate from a bounded first-page local ledger supplement. Job visibility is labelled
  host-wide only after `FULL_ADMIN` is verified; service lifecycle writes and remote job abort are not offered from
  these views. See the [It2 consumer examples](docs/CONSUMERS.md#read-only-overview-and-jobs),
  [job/view decisions](docs/DESIGN.md#non-blocking-jobs-and-core-read-views), and
  [v27 contracts](docs/API-METHODS-V27.md#iteration-2-read-view-contract-implemented-against-the-verified-v27-reference).
- Completed It1 multi-host support. `tnt.toml` now contains host facts only; per-host credentials are resolved from the
  paired mode-0600 `tnt.secrets.toml`. Host files are securely opened, validated, edited under serialized locks, and
  replaced with rollback on caught transaction failures. Added `tnt hosts` list/show/add/edit/rename/remove/test/default
  workflows, config/secrets examples, picker and active-host status, host switching, connection testing, and per-host
  read-only/write mode. Session generations invalidate plans and callbacks, request worker cancellation, and drain
  work before the old connection closes. TUI API-key creation writes directly to a new private file without showing
  the key. See the [multi-host adoption guide](docs/CONSUMERS.md#configure-multiple-hosts) and
  [session/config design](docs/DESIGN.md#5-ux).
- Added SMB and NFS share list/show/create/delete workflows to CLI and TUI. Creation verifies the remote canonical path,
  refuses missing and boot-pool paths, and share deletion requires the typed target while retaining data.
- Added periodic snapshot task create/list/show/run/enable/disable/delete and replication/cloud-sync list/show/run/enable/disable.
  Runs use job plans with source/target context and typed confirmation; cloud-sync credentials and encryption values are masked.
- Added read-only `tnt network show` and System > Network views for v27 interfaces, active IPs, installed routes,
  DNS nameservers, and default gateways, with explicit schema-shape refusal for incomplete summaries.
- Added Apps list/show/start/stop/restart/uninstall workflows. Restart is an ordered stop/start job pair; uninstall is
  destructive, requires the typed app ID, and preserves TrueNAS-managed data volumes unless explicitly enabled.
- Added filtered and paginated audit CLI/TUI queries with time, user, event, and service filters; audit fields and
  known inline secret patterns are scrubbed before display. Generic middleware/syslog tailing is unavailable in v27.
- Added API key list/create/rename/revoke management. The CLI can show a one-time key in non-JSON terminal output;
  TUI creation writes directly to an exclusive mode-`0600` file without displaying it. Verifier fields stay out of
  ordinary listings and plans.
- Added destructive system update, reboot, and shutdown workflows with a FULL_ADMIN job-visibility preflight, running
  VM/container and SMB-session summary, typed confirmation, update train binding/progress, and bounded TUI reconnect.
  A lost power-action reply now triggers bounded state checks without resubmitting the action; unconfirmed outcomes
  remain explicit.
  Added configuration tar export to a new local mode-0600 file; the export contains the secret seed and its download
  URL is never displayed. Live export passed with a valid mode-0600 tar; reboot, shutdown, and update apply were
  deferred. See the [live acceptance report](docs/LIVE-TESTING.md#live-acceptance-run-2026-10-29).
- Added the initial schema-versioned host and credential handling; It1 replaces inline credentials with paired
  secrets storage and multi-host records.
- Added v27 password login through the pinned client's `PASSWORD_PLAIN` mechanism; API keys continue to use SCRAM.
- Removed the postponed generation adapter. Only v27 is accepted; the adapter boundary remains extensible.
- Added committed `tnt.toml.example` and `tnt.secrets.toml.example`, and ignored local host/secrets files and locks.
- Added explicit v27 read/write/destructive method classifications and source-linked request signatures in
  `docs/API-METHODS-V27.md`; known non-RPC or unavailable destructive names remain classified but unsupported.
- Extended boot-pool and ambiguous-pool-reference refusals to nested string values and pool mapping spellings across
  every non-read method family. Pool/task ID checks now follow each method's v27 parameter meaning.
- Added shared query/action workflows for datasets, snapshots, local users, containers, and virtual machines to the
  CLI and Textual screens. Mutations use exact API plans, target re-reads, drift checks, and typed confirmation for
  destructive actions; dataset deletion also checks children, mount state, and running workload attachments.
- Restricted basic `user.update` to `full_name`, `shell`, and `home`; group membership and password/lock/SSH-password
  access changes now use a separate typed-confirmation workflow that binds and rechecks `auth.me`, including the
  explicit current-user confirmation bit in each plan. `random_password`,
  username rename, and built-in/immutable account access changes are refused. Current-user SSH-key changes also require
  typed confirmation and an apply-time identity probe; password/authentication flags remain visible while secret values
  stay masked.
- Added guarded local-user create/update/delete, SSH public-key set/add/remove, and password changes. Current-login
  password changes require the extra self-confirmation and typed username; other-account password changes remain
  ordinary planned writes. Secrets stay masked in plans and returned JSON.
- Added v27 VM lifecycle/device/console workflows. VM creation and each device use separate plans because the v27
  `vm.create` request excludes devices; a device failure can leave the VM record for operator inspection.
- Added one bounded `container.metrics` subscription per container read. Missing samples are reported explicitly;
  current image identity, configured CPU count, and memory limit are unavailable in the v27 container CRUD schema.
- Extended `FakeServer` and end-to-end workflow tests through the pinned `truenas_api_client`; added a default-deselected
  `live` test marker and a namespaced `tnt-live-` acceptance lane, exercised against vanilla v27. See the
  [live acceptance report](docs/LIVE-TESTING.md#live-acceptance-run-2026-10-29) for its passes and deferrals.
- M0 scaffold remains: `tnt --version`, `tnt doctor`, `tnt hosts list|show`, `tnt disks`, `tnt tui` (Storage > Disks).
- Fixed the registered `r0-r1` coverage suite environment and added focused safety-branch cases without lowering the
  97% combined coverage floor. At that earlier checkpoint, the gate reported 97.18% combined line/branch coverage
  and 95.30% branch-only coverage; see the current run status above for It4.
- Transport over `truenas_api_client` (pinned by sha of tag TS-27.0.0-RC.1): wss only and TLS verification on by
  default. Authentication failures are fatal and not retried.
- Default-deny `GuardedRpc`: write sessions run only the exact calls of the plan being applied; refusal list with live pool/disk state; disk.wipe protection computed from live membership.
- Re-review hardening: lazy membership with real topologies (disk key, partuuid, nvme, nested/aux vdevs), thread/task-bound grant and call lock, kind tied to destructive methods, normalised paths and strict pool-id forms, default-deny boot family, single-use session-bound plans.
- Guard review round 3: normalise boot-pool paths before prefix removal; refuse unmanifested writes and require destructive kind for additional data-changing methods; resolve boot-pool ids in nested references; block disk wipes when pool topology is unknown or disks report `exported_zpool`; union vdev references; register issued plan nonces against their fingerprints; document concurrent-read locking and the same-process trust boundary.
- Enforced the v27 manifest as the exact read/write allow-list, check the live WebSocket method list on session open, reject unsupported pool method assumptions, and require a target probe for every MUTATE action. Plan execution now needs a one-use internal grant bound to the exact call list; the former public authorization path refuses.
- Recorded v27 method/result/role evidence for the It2 overview views and updated the capability-check guidance. The roadmap is M1, M2, It1, It2, and It3 implemented, followed by It4 acceptance.
- Free space is an upper bound (`free_upper_bound_bytes`, `<= x` in the CLI/TUI) and unknown whenever a size or the partition data is missing.
- Plan/apply action model with confirm gates and a refusal list.
- Fake JSON-RPC websocket server (`tnt.testing`) used by the tests.
<!-- cmru: pending release notes end -->
