# tnt changes

## [Unreleased]

- Completed vanilla TrueNAS v27.0.0-RC.1 live acceptance through the registered `live-v27` Run-Gate lane. Datasets,
  shares, snapshot tasks, user credentials, containers, pool import, read views, API keys, multi-host switching,
  interactive SSH hand-off, and private configuration export passed. VM, Apps lifecycle, replication/cloud-sync
  execution, reboot, shutdown, and update apply were deferred for the reasons recorded in the
  [live acceptance report](docs/LIVE-TESTING.md#live-acceptance-run-2026-10-29). Live findings and their regression
  fixes are listed in that report.
- The `r0-r1` lane now reports coverage to two decimal places (`[tool.coverage.report] precision = 2`), aligning
  pytest-cov's `--cov-fail-under=97` comparison with its two-digit report so a displayed 96.94% fails. The general
  pytest-cov/Run-Gate command-lane false green is filed as RG-110 in run-gate-project's backlog. The preceding complete gate run reported
  6 failed, 924 passed, and 2 deselected; combined coverage was 97.65%, but true branch coverage was 96.45%
  (2471/2562), below the requested branch target. The third and final authorized gate attempt stopped at test
  collection because of an indentation error in the new core-view test; the syntax was corrected afterward, but no
  green gate result is available under the three-run limit.
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
