# tnt changes

## [Unreleased]

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
- Added API key list/create/rename/revoke management. Creation reveals the raw key once in a dedicated warning display;
  optional file storage is exclusive mode `0600`, and verifier fields stay out of ordinary listings and plans.
- Added destructive system update, reboot, and shutdown workflows with a FULL_ADMIN job-visibility preflight, running
  VM/container and SMB-session summary, typed confirmation, update train binding/progress, and bounded TUI reconnect.
  A lost power-action reply now triggers bounded state checks without resubmitting the action; unconfirmed outcomes
  remain explicit.
  Added configuration tar export to a new local mode-0600 file; the export contains the secret seed and its download
  URL is never displayed. System and config-export live checks are documented but were not run.
- Added schema-versioned `tnt.toml` profiles with explicit config search order, current-user ownership and exact mode-0600 checks,
  inline API-key or password credentials, optional API-key files, and masked profile inspection.
- Added v27 password login through the pinned client's `PASSWORD_PLAIN` mechanism; API keys continue to use SCRAM.
- Removed the postponed generation adapter. Only v27 is accepted; the adapter boundary remains extensible.
- Added the committed `tnt.toml.example` and ignored local `tnt.toml` configuration.
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
  `live` test marker and a namespaced `tnt-live-` acceptance lane that is authored but not run.
- M0 scaffold remains: `tnt --version`, `tnt doctor`, `tnt profile list|show`, `tnt disks`, `tnt tui` (Storage > Disks).
- Fixed the registered `r0-r1` coverage suite environment and added focused safety-branch cases without lowering the
  97% branch-coverage floor.
- Transport over `truenas_api_client` (pinned by sha of tag TS-27.0.0-RC.1): wss only and TLS verification on by
  default. Authentication failures are fatal and not retried.
- Default-deny `GuardedRpc`: write sessions run only the exact calls of the plan being applied; refusal list with live pool/disk state; disk.wipe protection computed from live membership.
- Re-review hardening: lazy membership with real topologies (disk key, partuuid, nvme, nested/aux vdevs), thread/task-bound grant and call lock, kind tied to destructive methods, normalised paths and strict pool-id forms, default-deny boot family, single-use session-bound plans.
- Guard review round 3: normalise boot-pool paths before prefix removal; refuse unmanifested writes and require destructive kind for additional data-changing methods; resolve boot-pool ids in nested references; block disk wipes when pool topology is unknown or disks report `exported_zpool`; union vdev references; register issued plan nonces against their fingerprints; document concurrent-read locking and the same-process trust boundary.
- Free space is an upper bound (`free_upper_bound_bytes`, `<= x` in the CLI/TUI) and unknown whenever a size or the partition data is missing.
- Plan/apply action model with confirm gates and a refusal list.
- Fake JSON-RPC websocket server (`tnt.testing`) used by the tests.
