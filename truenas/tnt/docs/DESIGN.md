# tnt design

Status: M1, M2, It1, It2, and It3 are implemented. Vanilla-v27 live acceptance is recorded for 2026-10-29; remaining It4 work is listed in section 10.
Container image identity, configured CPU count and memory limits
are unavailable in the verified v27 container record; VM creation uses separately reviewed device plans because v27
does not accept devices in `vm.create`. Inputs: the prior-art research (`truenas-tui-research.md`, release-program brief),
the canonical action matrix (`docs/ACTION-MATRIX.snapshot.md`, from branch `truenas-restore-iso`), the TrueNAS web UI
source (`truenas/webui` `navigation.service.ts`, read on master 2026-10-28) and the estate's cli-extended conventions.
Remaining UNVERIFIED entries have not been confirmed against a running host; BACKLOG TNT-01 remains a deferred partition-inventory spike.

## 1. Goals and non-goals

Goals: run operator workflows against a vanilla TrueNAS v27 host: Datasets and Snapshots, SMB/NFS shares, data
protection tasks, network status, Apps, Containers, Virtual Machines, local Users, Audit, API Keys, System actions,
and read-only Dashboard, Alerts, Jobs, Services, and Storage health views.
Containers and Virtual Machines are separate menu items, matching the v27 web UI; users live under Credentials > Local
Users. The TUI and scriptable CLI share one query/action layer. Every write uses
plan/apply, displays the exact API calls, re-reads state before apply, and uses typed confirmation for destructive work.

Non-goals: boot-pool management, pool creation, a REST client, a daemon, and compatibility
with releases other than v27. tnt targets vanilla TrueNAS; it does not rely on any relaxation patch.

## 2. Architecture

One-way layers; each only imports the ones above it in this list.

```
profile.py   Profile, Secret, paired versioned host/secrets files and descriptor checks
host_store.py serialized paired-file edits, rename, defaults, and atomic replacement
ssh.py       interactive-only OpenSSH argv construction and pinned local file checks
transport.py RpcClient protocol, connect(), GuardedRpc   (the only module that touches truenas_api_client)
adapters/    Backend per API generation + method manifest
domain.py    Plain domain records and formatting
actions.py   Action, Plan, apply_plan, refusal list      (plan/apply model)
jobs.py      host-scoped job records, callbacks, handles, and reconnect reconciliation
readviews.py schema-checked read-only Dashboard/Alerts/Jobs/Services/Storage models
session.py   Session = host profile + guarded rpc + backend; shared workflow/read-view entry points
session_controller.py host generation, active session, per-host write mode, worker cancellation/drain
cli.py       cli-extended registry: hosts, overview reads, workflows, TUI
tui/         Textual app: host picker, session-bound workers, overview/workflow views, palette and plan dialogs
```

The CLI and the TUI both call `Session` methods; the TUI holds no query logic. `build_cli(opener=...)` takes the
session opener so tests substitute a fake-server-backed one.

### 2.1 Transport

`truenas_api_client.Client` (LGPL-3.0, official; threaded, JSON-RPC 2.0 over websocket at
`wss://HOST/api/current`). Pinned by git sha of tag `TS-27.0.0-RC.1` (commit dated 2026-08-31, 58 days old on
2026-10-28, so past the 14-day third-party rule); it is not on PyPI. The pin is re-checked by TNT-31 at GA.

- Login: API keys use `Client.login_with_api_key` with SCRAM-SHA-512 and TLS channel binding over wss. Password
  profiles use the pinned client's `Client.login_with_password`, which uses `auth.login_ex` with `PASSWORD_PLAIN` on
  v27. The pinned client source at the commit in `pyproject.toml` was checked for this method and mechanism. tnt never
  calls the deprecated `auth.login_with_api_key` middleware method or retries a failed login.
- One persistent session. A failed login is fatal and not retried (rate limit: 20 unauthenticated requests per 60 s,
  then 10 minutes of lockout). `tnt doctor --online` is one login.
- `ws://` is refused: TrueNAS revokes API keys sent without TLS. Profiles reject it at load time and `connect()`
  rejects it again. The only exception is the loopback hook the tests use for the fake server
  (`allow_loopback_ws`, honoured for 127.0.0.1/::1/localhost, not reachable from the CLI or from a profile).
- TLS verification is ON. `tls = "insecure"` in a profile turns it off only when the command line also carries
  `--insecure`, and the TUI then shows a red banner on every screen.
- Errors from the client are wrapped into one `TransportError(kind, message)` with configured credentials scrubbed.

### 2.2 Boundary with `scripts/truenas`

The `scripts/truenas` utility is a separate subproject, even though it also wraps the official `truenas_api_client`.
It has its own CLI lifecycle and currently targets a broader API-generation boundary. tnt is an installable product
targeting vanilla v27, with a guarded plan/apply surface, a multi-host TUI, and a paired config/secrets contract. It
does not import `scripts/truenas` or copy its environment-based credential handling. Both projects use the same
upstream transport implementation; that is the shared protocol boundary.

If a later change needs common code, first extract a small neutral package with its own tests and dependency contract.
Candidates are generic job tracking/progress helpers and the lazy loading pattern for the official client. Host
selection, config search, credential storage, and error policy stay owned by tnt because their user and security
contracts differ.

### 2.3 Version adapters and the method manifest

The adapter boundary remains extensible, but this release ships only v27. `adapters.select(version)` parses the major
from `system.version` and selects `V27`; every other major raises `UnsupportedVersion` (fail closed). The supported
entries in the adapter manifest are the exact callable allow-list: an unlisted method is not a read or write just
because its name ends in `.query`, `.details`, or a familiar verb. Every entry has an explicit read/write/destructive
classification; `supported = false` entries retain guard classifications for known unsafe or nonexistent names
without admitting them to plans.
- On every connection, `Session` calls `core.get_methods(service=None, target="WS")` and installs the returned method
  names in `GuardedRpc`. Calls absent from this live list fail as unavailable before they reach the client. The
  handshake proves method presence only; it does not prove the caller's RBAC roles or validate a response schema.
- `adapters.missing_methods(backend, core.get_methods result)` reports each feature's supported manifest methods that
  the host does not list. `tnt doctor --online` reports these gaps; a missing method stays unavailable for that
  session. Review the exact v27 method and schema evidence in [`docs/API-METHODS-V27.md`](API-METHODS-V27.md).
- v27 has no `container.restart` RPC. tnt composes the user-facing restart action as `container.stop` followed by
  `container.start`, so its plan is destructive and requires typing the container name. A successful stop job is not a
  stopped-state guarantee: stop uses v27 `force_after_timeout` and then polls `container.get_instance` until
  `status.state == STOPPED` or a bounded 30-second postcondition check fails. The rendered plan states this check.
- VM names are restricted by vanilla v27 to ASCII letters and digits; tnt validates this before planning a create.
  The live lane reads `vm.supports_virtualization` and defers VM lifecycle checks when the disposable host lacks
  hardware virtualization.
- Planned: a drift test that diffs manifests against the fetched reference index (TNT-23, opt-in, outside the gate).

### VM and container lifecycle contracts

TrueNAS v27 may complete a `container.stop` job before the container leaves `RUNNING`. TnT requests the documented
`force_after_timeout` behavior, then polls `container.get_instance(id)` for `status.state == STOPPED` for up to 30
seconds. Restart does not submit `container.start` until that postcondition is observed. A malformed response, missing
instance, or timeout fails the plan instead of treating job completion as proof of a stopped container. This matters
because the lifecycle job and the container state are separate observations.

The v27 VM name validator accepts ASCII letters and digits only. TnT applies that same constraint before planning
`vm.create`; punctuation and non-ASCII names fail locally. The optional live VM lifecycle probe reads
`vm.supports_virtualization()` first and reports a hardware-based deferral when the host returns `false`.

### 2.4 Domain and the partition-data question

`Disk.partitions` is `None` when the partition data is unknown and `()` only when it is corroborated; the free-space
figure is `free_upper_bound_bytes` (CLI column "FREE (UPPER BOUND)" shown as `<= 411.5 GiB`, JSON key
`free_upper_bound_bytes`): alignment gaps and the backup GPT are not subtracted until TNT-02 computes exact extents.
It is `None` ("?") whenever it cannot be known: no partition data, no usable disk size, or any partition without a
usable size. Unparseable, missing, negative or boolean sizes become `None`, never 0. An empty partition list is
believed only with evidence that the API really joins partition data (some disk shows partitions) and only for a disk
that is not a pool member; otherwise it reads unknown, because the matrix measured `disk.details` as empty for a disk
that does have a partition. The M0 adapter asks `disk.query` with `extra.pools` and `disk.details` with
`{type: BOTH, join_partitions: true}`; both parameter shapes are UNVERIFIED on a live v27 host (TNT-01). If the API
cannot expose partitions, the source becomes an SSH `lsblk --json` / `sgdisk -p` fallback behind the same `Backend`
method (section 8.1).

### 2.5 Action model (plan/apply)

`Action(id, kind, title, calls, probe, confirm_target)`; `kind` is READ, MUTATE or DESTRUCTIVE. `id` is an
ACTION-MATRIX row id (A-xx/B-xx) or a TNT id. Structural rules (`validate_action`, at plan and again at apply): an
action has calls; every probe call is an explicitly classified read method (also in a write session); a READ action's
calls are all read methods; every MUTATE and DESTRUCTIVE action has a non-empty target probe (else the drift check
would be vacuous); a DESTRUCTIVE action also has a non-blank
confirmation target; an action containing a method explicitly classified destructive by the v27 manifest (including
`disk.wipe`, pool and dataset deletion, snapshot rollback/deletion, container stop/deletion, VM stop/poweroff/deletion,
VM device deletion, app stop, and user deletion) is DESTRUCTIVE. A `user.set_password` plan is promoted only when its
target is the current login user, and then it also needs `--confirm-self`; shell or home changes to the current login
user follow the same destructive typed-confirmation rule. Every non-read call must appear as supported in the v27 adapter manifest;
`validate_action` refuses unlisted writes. `make_plan` runs the probe
and fingerprints the action id, kind, confirm target, exact calls, probe results, session id and nonce, so swapping the
calls, target or identity under a plan is detected. A plan carries the id of the session that made it and a nonce: applying it through
another `GuardedRpc` is refused (`foreign-session`) and applying it twice is refused (`plan-reused`; the nonce is spent
only once every gate has passed, so a wrong confirmation does not burn the plan). `GuardedRpc` registers each issued
nonce with its fingerprint, so replacing a plan's nonce or fingerprint does not create a valid plan. This protects
against accidental reuse and honest mistakes; code in the same Python process can reach internal state, so the guard
is not a security boundary against that code. `Plan.render()` prints the exact RPC calls in order. `apply_plan` (which accepts only
a `GuardedRpc`) checks, in this order, and stops at the first failure:

1. validity (above);
2. live safety state, read only when some call touches disks or pools (`needs_membership`: non-read `pool.`, `zfs.`,
   `boot.`, `disk.` methods; an action such as `service.stop` never depends on pool membership), and the refusal list
   for every call, never overridable;
3. write session (`--allow-write`), for MUTATE and DESTRUCTIVE;
4. typed confirmation, for DESTRUCTIVE: the operator types the target name exactly;
5. state drift: the fingerprint is recomputed and must equal the plan's, else "plan again";
6. execute inside the private `GuardedRpc._authorized(...)` path with a one-use grant issued only by
   `actions.apply_plan`; it lets exactly these calls through, in order (default-deny). The former public
   `authorized(...)` entry point refuses every call.

The CLI and TUI use the same plan/apply functions. CLI mutation verbs require `--allow-write`; they print the exact
plan and require `--confirm TARGET` for destructive work. The TUI action dialog shows the same calls and confirmation
target. Every mutation has a probe that re-reads its target before apply. Name-based updates and lifecycle calls probe
one exact query result and bind the write ID or username to that result; a broad or mismatched lookup is refused.

### Non-blocking jobs and core read views

The pinned `truenas_api_client` is synchronous for the RPC call but returns a v27 job handle when called with
`job="RETURN"`. The handle carries the remote ID, completion event, callback updates, and final result. `GuardedRpc`
holds its call lock only until the handle is returned; `apply_plan` then waits outside that lock in the TUI worker
thread. This lets independent reads proceed while a remote job runs. The private apply grant remains bound to its
original thread and exact ordered calls, so another caller still cannot consume or create a write.

`JobRegistry` belongs to a host, not a socket. It records each applied job's ID, method, session generation, state,
progress, bounded log/error excerpts, timestamps, and bounded event history. Callback fields and display strings are
sanitized before they reach CLI/TUI output. When a connection ends, nonterminal records become UNKNOWN and pending.
After reconnect, tnt queries at most 10,000 `core.get_jobs` rows and matches an integer remote ID plus method. A
matching row updates the record. A query error is unavailable; a missing row remains UNKNOWN because the current
session may not be allowed to see the old job. No absence is treated as completion.

The Dashboard validates `system.info.uptime_seconds` as a finite, non-negative JSON number. The v27 schema allows a
fractional value, so the read model preserves it instead of narrowing the contract to integer seconds. The published
`reporting.get_data` schema describes object samples, while vanilla 27.0.0-RC.1 returned ordered numeric arrays whose
first value is the sample timestamp. TnT accepts either documented object samples or non-empty numeric arrays with at
least two values, preserving the host data without inventing a mapping from positional series to labels.

An apply result contains one per-call outcome record, including job IDs and generations. If a later call fails, the
completed results and every planned call state remain attached to `PlanExecutionError.partial`; the CLI returns that
partial JSON with a nonzero status and the TUI keeps the plan review visible. It tells the operator to inspect current
host state before recovery, avoiding blind replay of work that may already have succeeded. Stopping a local wait only
detaches the waiter; it does not abort the TrueNAS job. Although v27 has `core.job_abort`, the Jobs screen is read-only
and does not expose remote abort.

Remote job rows stay in the caller-requested page. The first page separately includes up to 200 recent local ledger
records, with a truncation flag; subsequent pages do not repeat that process-local supplement. A remote row matched to
the local ledger carries its local record as an annotation. Thus the remote page's count and `possibly_more` flag retain
their meaning even when local and remote job visibility differs.

`ReadViews` is shared by CLI and TUI and validates the selected v27 response fields before formatting. The Dashboard
uses `system.info` plus one bounded hour/page of CPU and memory history; that history is not live utilization. Alerts,
Jobs, Services, and Storage health preserve permission errors as errors, and nullable/absent facts stay Unknown. The
Jobs screen labels host-wide results only after `auth.me` confirms `FULL_ADMIN`; otherwise it states the result is
current-session-only. Services has no lifecycle writes. Jobs pages in 200-row TUI slices and CLI accepts bounded
`--limit`/`--offset`; Services and each Storage query stop at 10,000 rows and disclose possible truncation. Polls are
single-flight: Dashboard every 30 seconds, Jobs every 5 seconds, and Alerts, Services, and Storage every 60 seconds.
Each view also offers status, search, refresh, help, copy, last-success retention, and a credential-scrubbed error
panel. See the exact method, schema, role, paging and unavailable-state contract in
[`docs/API-METHODS-V27.md`](API-METHODS-V27.md#iteration-2-read-view-contract-implemented-against-the-verified-v27-reference).

### User account updates

The official v27 [`user.update` schema](https://api.truenas.com/v27.0/api_methods_user.update.html) accepts more fields
than this client exposes and its result includes the password when one was specified or generated. tnt does not pass
arbitrary JSON through it. The basic `user update` workflow
allows only `full_name`, `shell`, and `home`; username rename and `random_password` are refused. Group membership
(`group`, `groups`) and authentication access (`password_disabled`, `locked`, `ssh_password_enabled`) use the separate
`user access` workflow. It accepts only those fields with validated values, rejects built-in, immutable, root, and
admin accounts, and always creates a DESTRUCTIVE plan with the exact username as its confirmation target. Its probe
contains both the named `user.query` row and `auth.me`; the row ID, mutable-local status, login identity, action kind,
and typed target are rechecked against fresh results at apply. The explicit `--confirm-self` value is included in the
plan fingerprint. If `auth.me` identifies the target at either planning or apply, that flag must be present. This keeps
account access changes inside the same fingerprinted review as the identity check. SSH-key updates remain on their protected-secret path; a
current-login SSH-key change is also destructive, asks for typed username confirmation, and fingerprints `auth.me`.
Changing the current login user's `shell` or `home` is lockout-shaped as well: the plan includes the named user and
`auth.me` probes, requires `--confirm-self` plus the typed username, and checks current identity again at apply. Changes
to another account's shell or home remain planned writes but still bind the `auth.me` result into the plan.

## 3. Action backlog from the matrix

The action matrix still tracks the separate storage work. M1 delivered the core workflows: Datasets, Snapshots, Users,
Containers, and Virtual Machines. M2 added the management workflows listed in section 10. Boot-pool datasets remain
refused; the remaining storage matrix work is Later unless section 10 assigns it an iteration.

| row | action | tnt |
|---|---|---|
| (input) | disk free space / partitions | TNT-01 spike, TNT-02 |
| B-01 | reserve the free-tail partition | TNT-03 (SSH `sgdisk` fallback, double-gated) |
| B-06 | adopt a hand-made pool (`pool.import_find`, `pool.import_pool`) | TNT-04 (API works; measured PASS) |
| B-02, B-03 | create a pool on a partition / free space | TNT-05, decision pending |
| (derived) | boot-pool data management | TNT-06 |
| A-09 | cache/log/special vdev | TNT-07 |
| B-07, A-08 | must stay refused | refusal list |
| A-01, A-02, A-03 | remain refused when they name boot-pool; regular-pool datasets are supported |
| A-04, A-05 | boot-pool targets remain refused; regular-pool workloads are supported |
| A-07, B-05 | SMB/NFS export records | TNT-15 (M2; does not create or remove filesystem data) |

## 4. Screens map (mirrors the web UI menu)

Top-level labels and order are taken from the v27 web UI source (`navigation.service.ts`): Dashboard, Storage,
Datasets, Shares, Data Protection, Credentials (slide-out: Local Users, Groups, Privileges, Directory Services, Backup
Credentials, Certificates, S3 Access Keys, KMIP), Containers, Virtual Machines, Apps, Reporting, System (slide-out:
Update, General Settings, Advanced Settings, Network, Boot, Services, Shell, Alert Settings, Audit, Enclosure).
Findings that differ from the brief's guess: there is no "Virtualization/Instances" entry (v27 has Containers and
Virtual Machines as separate top-level items) and Network is under System. The Storage page's own sub-pages (Disks,
Import Pool, pool management) are reached from the Storage page, not from the menu; tnt shows them as children.

`tnt/menu.py` holds the tree. TrueNAS places “My API Keys” in the top-right account menu; tnt nests it under Credentials
as a `tnt_only` tree leaf because the TUI has no account menu. Datasets, Shares, Data Protection tasks and Snapshots,
Credentials > Local Users/API Keys, Containers, Virtual Machines, Apps, Network, Audit, Update, Reboot, Shutdown, and
General Settings have workflow views. It2 adds Dashboard, Alerts, Jobs, Services, and Storage > Health. Reporting
beyond the Dashboard's bounded CPU/memory history and other unassigned entries remain Later placeholders. Multi-host/
session controls are It1; interactive SSH hand-off is It3. Containers and Virtual Machines are separate top-level
entries and are always shown. The TUI omits Shell because it is already a terminal.

### TUI navigation and small terminals

Keep the TrueNAS route order in the tree, then mark entries that are planned, conditional, or tnt-only in plain text.
These labels remain understandable without color and keep the web UI's names available for implemented pages. The
breadcrumb names the current page even when Jump opens it without selecting a tree row. `Ctrl+P` uses Textual's command
palette for screen/action search; `?` opens a persistent key guide, and `Escape` returns focus to navigation or cancels
the active dialog through that dialog's cancel path.

The sidebar narrows with the terminal so the content keeps usable width. Below 48 columns or 12 rows, tnt hides the
working layout and shows a resize instruction instead of presenting controls that cannot fit. Destructive dialogs show
the typed target as wrapped text above a neutral input placeholder; the action guard and exact typed comparison remain
unchanged. When no host is configured, the picker focuses Add, disables actions that need a selected host, and provides
Quit as its exit action. Pilot tests render the real screens to text at 80×24, 100×30, 120×40, 160×50, 200×60, and 60×40, then
exercise a resize to 40×10. The tmux UX checks complement those structural Pilot checks by driving the real app on a
pseudo-terminal, including color attributes and terminal resize behavior. Current Textual widgets expose terminal text
and keyboard focus but do not provide a semantic screen-reader tree; that limitation is tracked in `docs/UX-REVIEW.md`.
See [UX testing](UX-TESTING.md) for commands and coverage limits.

### Shares

`sharing.smb.create` and `sharing.nfs.create` accept only paths that `filesystem.stat(path)` confirms exist on the
TrueNAS host. The returned canonical `realpath` must equal the supplied path and must not resolve under boot-pool.
Missing-path errors are translated to a refusal; transport and permission errors remain visible as failures. SMB
purpose values are validated against the v27 enum. `guestok` exists only in the `LEGACY_SHARE` options schema; the
default share preset supports `hostsallow` and `hostsdeny`. Share deletion is destructive and requires typing the
share name (or NFS path); it deletes the export record and retains the filesystem data. `sharing.smb.getacl` is called
for each listed SMB share so ACL state is available in both clients.

### Data Protection tasks

Periodic snapshot tasks use the v27 `pool.snapshottask.*` methods. Creation requires a dataset that resolves through
`pool.dataset.get_instance`, a non-empty cron object, positive `lifetime_value`, a supported `lifetime_unit`, and a
`naming_schema` with the v27 date/time directives or `%s`. Snapshot task deletion is destructive and confirms the
dataset plus task ID. Replication and cloud-sync task writes are limited to enable/disable and run-now. Run-now plans
are destructive and typed: replication shows source dataset(s), target, and whether `allow_from_scratch` can remove
destination snapshots; cloud sync warns when `MOVE` deletes source files after transfer. Cloud-sync credentials and
encryption fields are masked before they reach CLI or TUI presentation. Although the published reference marks
`pool.snapshottask.run` as a job, the pinned client on vanilla 27.0.0-RC.1 returned `null` from tnt's ordinary call and
the generated snapshot appeared. The workflow therefore checks the snapshot list instead of requiring a job handle;
see the [live acceptance record](LIVE-TESTING.md#live-acceptance-run-2026-10-29).

### Network status

System > Network calls only the v27 reads `interface.query`, `route.system_routes`, and `network.general.summary`. The
summary supplies active interface addresses, DNS nameservers, and default gateways; the detailed reads add configured
interface state and installed IPv4/IPv6 routes. The display is read-only, and missing required summary fields are a
schema failure instead of an empty network state. See [API method evidence](API-METHODS-V27.md#network-status).

### Apps

The app list uses `app.query` without the option that retrieves app configuration, and displays the API's
`upgrade_available` value alongside state and version. v27 has `app.start` and `app.stop` jobs but no `app.restart`
method; `app.stop` is destructive, so stop and the ordered stop/start restart workflow require the typed app ID. tnt
does not use `app.redeploy`, whose v27 behavior also pulls images. Uninstall is `app.delete`, requires the typed app
ID, keeps images and TrueNAS-managed data volumes by default, and exposes an explicit opt-in for `remove_ix_volumes`.
See [API method evidence](API-METHODS-V27.md#apps).

### Audit

The Audit screen and `tnt audit` send one service per v27 `audit.query` call, always set a bounded limit, and sort by
descending `message_timestamp`. The filters cover an ISO-8601 time range (converted to Unix seconds), username, and
event. Event data is treated as untrusted: secret-named keys, inline credential assignments, bearer values, API-key
shapes, and PEM private-key blocks are scrubbed before CLI/TUI display. The v27 method index has no general middleware
or syslog tail API, so tnt does not read logs through shell commands or filesystem paths. See
[API method evidence](API-METHODS-V27.md#audit).

### API Keys

The API key list uses a v27 `select` projection and returns only ID, name, owner, timestamps, locality, and revocation
status; salt and SCRAM verifier fields never reach ordinary CLI/TUI display. Create uses `api_key.create` and places
the returned raw value only in a one-time non-JSON CLI warning output. The TUI requires a private output path and writes
the returned value directly to a new mode-0600 file without displaying it. For an API-key-authenticated host, the TUI
updates `tnt.secrets.toml` to point to that file for the next connection. The normal plan renderer redacts key/verifier
field names, and create results bypass generic action logging/formatting. CLI `--write-key` is optional, exclusive,
and never overwrites an existing path or edits either config file automatically.

Rename calls `api_key.update(id, {name, reset: false})`; tnt explicitly avoids the API's key-reset option because a
reset also returns new plaintext key material. v27 has no public revoke RPC. The user-facing revoke action is the
destructive `api_key.delete(id)` operation: middleware removes the record and regenerates its PAM state, immediately
invalidating the key. The plan probes the current ID/name, and apply requires typing the current key name.

The v27 web UI places “My API Keys” in the account settings menu at the top right. tnt places it under Credentials so
it can be reached from the left navigation tree; this is a documented navigation-only adaptation. See
[API method evidence](API-METHODS-V27.md#api-keys) and the upstream [TrueNAS API UI note](https://github.com/truenas/documentation/tree/master/content/SCALE/API).

### System actions

`system.reboot`, `system.shutdown`, and `update.run` are always destructive actions. Before creating a plan, tnt reads
`system.info`, `auth.me`, `core.get_jobs`, `vm.query`, `container.query`, and `smb.status`. It requires `FULL_ADMIN`
because v27 filters `core.get_jobs` to the caller's own jobs for less privileged sessions. The plan prints the host
name, every active job ID/method, running VM and container names, and the active SMB-session count. Any active job
blocks planning unless `--force-with-jobs` is explicit; the exact host name or target update version must still be
typed. The same reads are fingerprinted and checked again at apply time.

Update check reads `system.version` and `update.status`; because the latter's `new_version` has no train field, tnt
matches its version, manifest, release notes, and release-notes URL to exactly one `update.available_versions` row.
Ambiguous or incomplete records refuse the operation. Update apply binds that exact version and train into
`update.run`, sets `reboot=false`, and follows the returned job's progress. Reboot and shutdown accept a 0–86400 second
delay. After reboot, the TUI waits between fresh login attempts, checks `system.state`, and stops after five bounded
attempts; authentication errors stop retries immediately to respect the middleware login cooldown. If the reboot RPC
loses its connection before returning, tnt treats submission as uncertain and checks the host without resending the
power action. It reports success only after observing a boot-state transition; an immediate READY response remains
uncertain and requires the operator to inspect host state before retrying. A lost shutdown response is reported as
uncertain and is never resubmitted.

General Settings offers configuration export through `core.download("config.save", [{secretseed: true, ...}],
filename, false)`. The one-time download URL is not displayed. tnt requires a READY host, validates that the URL is
same-origin and names the returned job, disables redirects and proxies, and creates a new local `.tar` file
exclusively with mode `0600`. It validates the tar before keeping it. The archive includes TrueNAS's secret seed and
must be stored as a credential. The drift fingerprint binds the hostname from `system.info` while ignoring its
volatile uptime and load metrics; version, state, and boot ID remain separate probes. See [v27 method evidence](API-METHODS-V27.md#system-actions) and the
[system action examples](CONSUMERS.md#system-actions).

## 5. UX

- Left navigation is a Textual `Tree` (arrows, enter, expand/collapse), content pane on the right, a banner line with
  selected host name, API generation, read-only/write-enabled mode, session generation, and insecure-TLS warning.
- Command palette: Textual's built-in `ctrl+p`; `get_system_commands` adds a "Go to A > B" entry for every leaf and
  an entry for each available workflow action, plus host selection and write-mode switching.
- Keys: `ctrl+n` menu, `h` host picker, `w` toggle the selected host's write mode, `r` refresh, `?` keys, `q` quit.
- Per-screen action list (footer plus palette), each action opening the plan modal with the exact calls.
- Reads, action construction, plan probes (including system preflight and detail lookups), and applies run in worker
  threads. Busy messages and disabled plan buttons show progress while the event loop remains available. Each worker
  captures a session identity and generation. A host switch increments the generation before cancellation and
  invalidates open plans. It waits up to five seconds for workers to drain before closing the old session; if an RPC is
  still pending, a dialog offers Keep waiting or Cancel switch. Quit uses the same bounded drain and offers Cancel quit.
  Canceling a transition restores the current session, and any later transition still drains old leases bound to it.
  Results posted after a switch are rejected on the UI thread by generation and session identity. Workers stop between
  RPC calls when cancellation is requested; an RPC already in flight can finish under the client's call timeout.
- Connection testing opens a temporary read-only session and closes it without replacing the selected session.
  Write mode is stored independently per host; changing it reopens that host session in the chosen mode.
- The picker shows host name, URL, description, and last connection status. Password and API-key inputs are hidden;
  a newly created API key is written directly to a mode-0600 file and is never shown in the TUI. SSH key paths are not
  displayed. Container reads subscribe once to v27 `container.metrics`, wait up to 2.5 seconds, then unsubscribe.
  Missing metrics remain an explicit status, never a zero value. Core read views poll on bounded, single-flight timers
  and retain their last successful sample after an error.

## 6. Version policy

CMRU uses the tracked `VERSION` file as the package version source, seeded at `0.1.0`. A release transaction commits the
next version before tagging `tnt-vX.Y.Z`; the wheel and SHA256 are published to the matching GitHub Release, and CMRU
maintains the moving `tnt-latest` release. Wheels are not published to PyPI.
The project metadata declares `cli-extended>=0.4.0` and the official `truenas_api_client` at a pinned Git commit.
`textual>=8.0` is in the optional `tui` extra; `pytest`, `pytest-cov`, Textual, and `websockets>=15` are test extras.
Python >= 3.11 (tested 3.14). See the [consumer install steps](CONSUMERS.md#install-from-github-release-assets).

## 7. Secrets

`tnt.toml` and `tnt.secrets.toml` are schema-versioned independently at version `1`. The former contains only
`default_host` and `[hosts.NAME]` connection facts; any credential key or credential-bearing nested value is refused.
Each new secrets entry contains exactly one of `api_key`, `api_key_file`, or `password`, plus the exact `host_url` the
credential was entered for. Before authentication, tnt requires that binding to equal the selected profile URL.
Unbound legacy entries remain readable for host-file repair but cannot connect until their credential is replaced with
`tnt hosts edit NAME --set-secret`. `api_key_file` is an absolute path stored only in the secrets file. Environment
credential fallbacks are not used.

Config and secrets are resolved independently with the same precedence: the explicit `--config`/`--secrets` path,
the matching `$TNT_CONFIG`/`$TNT_SECRETS`, an existing current-directory file, then
`$XDG_CONFIG_HOME/tnt/` (or `~/.config/tnt/`). The files may live in different directories and resolve independently.
Matching host names alone do not authorize pairing: the secret's `host_url` must match the profile URL, which prevents
a cwd config from presenting an XDG credential to a different appliance with the same alias. Supply an explicit path
or matching environment variable for whichever file is outside its own search path.

Both TOML files and any API-key file must be regular files owned by the current user with exact mode `0600` and are
opened with no-follow semantics. Ownership, type, and mode are checked with `fstat` on the descriptor that is read.
The config store serializes updates with file locks, stages mode-0600 replacements, fsyncs them, and restores prior
contents if a paired replacement fails. Host rename updates the config and matching secret table together.

Credential values are wrapped in `Secret`, whose `repr` and `str` redact. `Profile` repr omits SSH key and known-host
paths. Host list/show and JSON output only report whether credentials or SSH files are configured. Transport, host
connection status, and RPC errors scrub credential material. Passwords and API keys are never accepted in argv.
Password and API-key prompts do not echo; API-key creation in the TUI writes directly to a private file rather than
showing the one-time key. The CLI's one-time key output is disabled in JSON mode.

```toml
schema_version = 1
default_host = "lab"

[hosts.lab]
url = "wss://truenas.example.invalid/api/current"
username = "admin"
auth = "api_key"
tls = "verify"
ssh_user = "admin"
ssh_key_files = ["/home/me/.ssh/id_ed25519"]
ssh_known_hosts = "/home/me/.ssh/known_hosts"
description = "Primary TrueNAS host"
tags = ["primary"]

[hosts.password-auth]
url = "wss://nas2.example.invalid/api/current"
username = "operator"
auth = "password"
tls = "verify"
```

```toml
schema_version = 1

[hosts.lab]
host_url = "wss://truenas.example.invalid/api/current"
api_key = "REPLACE_WITH_API_KEY"
# Or use api_key_file = "/home/me/.config/tnt/lab.key".

[hosts.password-auth]
host_url = "wss://nas2.example.invalid/api/current"
password = "REPLACE_WITH_PASSWORD"
```

## 8. Safety rules and the refusal list

Default-deny, enforced in the client object itself, not only in the action layer:

- `GuardedRpc` is the only object a screen or verb receives (`Session.rpc`, also with `--allow-write`). Method names are
  normalised (strip, lower-case) and must be plain dotted words. Only exact read methods in the v27 manifest, plus
  the small named bootstrap reads, are read methods. After the `core.get_methods` handshake, a method must also be
  present in that session's live WebSocket method list.
- A non-read method is refused in a read-only session. In a write session it is refused unless it is the next call of the
  plan currently executing in `apply_plan` (exact method, parameters and job flag, in order). Outside a plan a write
  session can call nothing but reads. Every granted call must still pass `refusal_for`.
- `apply_plan` receives a private execution capability only after the plan identity, refusal list, write flag,
  destructive confirmation and fresh probe fingerprint all pass. A direct call to the former public grant API is
  refused. This prevents accidental bypass through the transport API; because application code shares one Python
  process, it is not a sandbox against hostile code that can import private implementation details.
- The grant is bound to the executing thread and asyncio task, so another thread or task cannot consume a plan call
  (`foreign-caller`). RPC calls share a lock: a concurrent read waits while an RPC is in flight, then can run between
  plan calls. Job calls release the lock after TrueNAS returns a handle; the plan worker waits on that handle outside
  the lock. Another thread may read during the wait, but cannot consume the plan grant or make a write. Plans are
  session-bound and single-use (section 2.5).
- `--allow-write` therefore changes nothing by itself: every change goes through plan/apply with its gates.
- DESTRUCTIVE actions need the typed target name; the kind is tied to the methods (section 2.5).
- `refusal_for` (second wall, for calls an action declares by mistake):
  - all `core.*` except `core.get_methods` (`core.call`/`core.bulk` run any method); `system.reboot|shutdown`; any
    `auth.*` except `auth.me` and `auth.mechanism_choices`;
  - the boot family is default-deny with an explicit read allow-list: only manifested read methods run
    (`boot.get_state`, `boot.environment.query`); `boot.format`, `install_loader`, `scrub`, `set_scrub_interval`,
    `attach|detach|replace` (B-07) and `environment.*` writes are refused until a later decision adds them to
    `BOOT_WRITE_ALLOWED` (TNT-13);
  - for every non-read method, any string parameter (nested, dict keys included) that names boot-pool is refused.
    This includes dataset and path strings in `sharing.`, `vm.`, `container.`, `app.`, and `user.` payloads. A name
    counts by whole path component after collapsing `//` and `.`,
    then stripping `/mnt/`, relative `mnt/` or `/dev/zvol/`, with `@snapshot` and `#bookmark` suffixes:
    `boot-pool`, `boot-pool/ROOT`, `/mnt/boot-pool/x`, `mnt/boot-pool/x`, `boot-pool@s` are refused;
    `boot-pool-data` and `/mnt/boot-pool-data/x` are legal names. A path with a `..` component is ambiguous and refused;
  - pool references in mappings are recognized recursively for `pool`, `pools`, `pool_id`, `pool_name`, `pool_ids`,
    and `pool_names`, including camel-case spellings such as `poolId`. Integer IDs resolve through live `pool.query`;
    names must exactly match a listed pool. Unknown, null, digit-string, list and mapping forms are ambiguous and
    refused. For positional methods, only known pool-id methods are resolved as IDs; `pool.scrub.delete(id_)` deletes
    a scrub-task ID, while `pool.scrub.run(name, ...)` and `pool.scrub.scrub(name, ...)` take pool-name strings.
    Positional references use method-specific semantics so an unrelated integer ID is not mistaken for a pool;
  - `disk.wipe` is refused for any disk in a pool (boot included) or reported with `exported_zpool`, any disk with a
    mounted partition, any disk the host does not list, any disk without a usable positive integer size, and whenever
    any pool member could not be mapped to a disk or a pool has no usable topology. There is no override by probe
    evidence yet. A non-string or missing parameter raises `Refused`.
- Pool membership (`policy.compute_state`) is read from `pool.query`, `boot.get_state` and `disk.query`, and only for
  actions that touch disks or pools. A vdev is resolved by its `disk` key first, then `device`/`devname`, its `name` (for
  `DISK` nodes), then its `path`: `/dev/` stripped, partition suffixes (`sda3`, `nvme0n1p2`, `mmcblk0p1`) mapped to the
  disk, and `/dev/disk/by-*` links mapped through exact identifiers from `disk.query` (serial, wwn, identifier, lunid and
  each partition's name/partuuid/uuid/id); a link also matches by a whole dash/underscore-separated token, never by
  substring, and an identifier shared by two disks resolves to neither. Mirror/raidz/draid nesting and the
  cache/log/spare/special/dedup groups are walked. All resolvable references on a vdev are included in membership. A
  vdev that cannot be resolved or a pool with no usable topology is recorded and blocks `disk.wipe`
  only; it does not block `pool.export` of another pool or any unrelated action. If the reads fail or return an
  unexpected shape, the action is refused (`safety-state`).
- Limit: mounted non-ZFS partitions are visible only if `disk.query` reports a mountpoint for them; this is UNVERIFIED
  (TNT-01). The typed confirmation and the plan text remain the control for that case.
- Host-impacting operations keep the estate rules: a throwaway VM host only for mutating live tests, never a production
  host; loop-device/swap work is out of scope (the 2026-09-09 host-hang incident).

### 8.1 SSH fallback for partitioning (planned, not implemented)

The API has no partitioning method (matrix B-01), so reserving the free-tail partition needs `sgdisk` on the host. Design
(controller decision 2026-10-28): this storage fallback is separate from It3's interactive SSH hand-off. If implemented,
it is a planned, guarded Action for B-01 with a second explicit
switch (`--allow-ssh`) on top of `--allow-write`; it runs a fixed, listed set of commands (a `sgdisk` create of one
partition in a named free extent, and read-only `sgdisk -p`/`lsblk --json`), each of which appears in the plan text; it
uses the repo's known-hosts policy (`StrictHostKeyChecking=yes`, a key file from the profile, no password, no agent
forwarding); the target disk must pass the same `compute_state` checks as `disk.wipe` (never a pool member or mounted
disk) and the plan is DESTRUCTIVE (typed disk name, probe = the partition table read just before). Until implemented,
B-01 is a backlog item (TNT-03); no storage SSH fallback exists in the current product.

### 8.2 Interactive SSH hand-off

`tnt ssh [HOST]` and the TUI's `s` action hand the terminal to OpenSSH for an interactive session on the configured
host. The host is derived from the `wss://` URL, the login must be explicitly configured, and at least one identity
file plus one known-hosts file are required. The CLI reads only the non-secret host file and does not open an API
session. The TUI wraps the child process in Textual's `App.suspend()` context so the terminal is restored when the
process returns, including non-zero exits.

The subprocess receives a list of arguments with `shell=False` and no remote command. `-F /dev/null` excludes both
user and system SSH configuration. `UserKnownHostsFile` names only the configured file; `GlobalKnownHostsFile` is
`/dev/null`, `StrictHostKeyChecking=yes`, `VerifyHostKeyDNS=no`, and `UpdateHostKeys=no`. `IdentityFile=none` clears
OpenSSH's built-in identity filenames, while `IdentitiesOnly=yes`, `IdentityAgent=none`, and explicit `-i` values limit
authentication to the configured identities. This follows the documented `IdentityFile none` behavior in the
[OpenSSH client configuration reference](https://man.openbsd.org/ssh_config#IdentityFile). Password,
keyboard-interactive, and host-based authentication are disabled. `-tt` requests a terminal. No `ssh-keyscan` runs and
the application never rewrites the host-key file.

Before launch, the identity files are opened with no-follow semantics and checked as regular, current-user-owned
files with owner-only read permissions (mode `0600` or stricter). The configured known-hosts file must be a non-empty
regular file owned by the current user, owner-readable, and not writable by group or others. Its absolute path is
limited to unambiguous path characters because OpenSSH parses `-o` values as configuration syntax. Failure messages
do not include local key paths. OpenSSH owns the terminal streams directly; tnt does not capture or log the session.
The CLI preserves the child status, and the TUI reports a non-zero status after restoring its screen.

Arbitrary remote commands are deferred. OpenSSH sends command strings through the remote user's shell; a future
command mode needs a separately reviewed remote quoting contract and hostile-argument acceptance tests. Passing
additional arguments after `HOST` is not supported.

## 9. Test strategy

- Unit: pure modules (profile, domain, actions, matrix, menu) with direct tests; the refusal list, gate order and
  drift check each have a test that fails if the gate is removed.
- Fake server: `tnt.testing.FakeServer`, a real websocket server on loopback (the `websockets` sync server) that speaks
  JSON-RPC 2.0 with the middleware's error shape, API-key SCRAM and `PASSWORD_PLAIN` login, `system.version`,
  `core.get_methods`, and the workflow methods. It records requests and has error injection (`fail=`). Tests drive the
  pinned `truenas_api_client` over a real socket; job-returning methods use realistic job replies.
- TUI: Textual `Pilot` (`run_test`) against a session on the fake: navigation, palette entries, load, failure, refresh,
  insecure banner, and an SSH hand-off whose stub executable proves suspend/restore and process-status handling. The
  tmux UX test starts the same `TntApp` in a real pseudo-terminal to inspect rendered attributes, dimensions, and quit.
- CLI: cli-extended `build_cli(opener=...)`, assertions on `--json` output and exit codes; `doctor` checks both ways.
  SSH tests use a stub executable and assert exact argv, no shell, refusal paths, and no accepted remote command.
- Live lane (TNT-20, It4 acceptance): the registered `live-v27` Run-Gate lane runs in `tester-unified`; explicit
  `TNT_LIVE=1` and `TNT_LIVE_PROFILE` select the host, while pool, group, image and VM sizing prerequisites are
  mandatory. The image name and version must match a pair listed by the selected host's container image registry. The
  caller mounts private configuration and credential files from an out-of-repository scratch directory
  through Run-Gate's declared extra-mount input. The acceptance test creates only `tnt-live-`-prefixed test resources
  and cleans each up through plans. The M2 portion also checks read-only views, SMB/NFS share lifecycle, a periodic
  snapshot task, API-key file handling, TUI Pilot navigation and host switching, CLI readers, and an interactive SSH
  hand-off. Host switching requires a second same-endpoint alias; the exact profile and credential setup is in the
  [consumer guide](CONSUMERS.md#explicit-live-lane). VM lifecycle is conditional on the host capability read.
  Acceptance results are recorded in
  [`docs/LIVE-TESTING.md`](LIVE-TESTING.md), including the [ACTION-MATRIX B-06 import check](ACTION-MATRIX.snapshot.md#actions).
  The live lane is separate from the ordinary FakeServer gate.
- Drift lane (TNT-23, It4): manifests vs the fetched API reference. Optional dependencies required by the suite fail, never skip.
- Gate: CMRU release runs `run-gate.toml` lanes `r0-r1` (full pytest suite with branch coverage) and `ux-focused`
  (Pilot, host-picker, and tmux terminal checks); `live-v27` is opt-in host acceptance. The tmux cases report a clear
  skip while tester-unified lacks that package. The full unit lane retains its 97% floor; the separate mutation lane is
  It4 work (TNT-30). Tests are deterministic under host load: they poll for screen and worker conditions until bounded
  deadlines instead of asserting fixed response times.

## 10. Milestones

| milestone | scope | size |
|---|---|---|
| M0 | Historical scaffold: transport, profile, v27 adapter, plan/apply model, `doctor`/`profile`/`disks`/`tui`, Storage > Disks, fake server | complete |
| M1 | Core workflows: configuration and credentials, v27 default-deny manifest, datasets, snapshots, users, containers, and VMs in CLI/TUI | implemented; container metadata is limited to v27 fields and VM creation is staged |
| M2 | Management workflows: SMB/NFS shares, data protection tasks, Network, Apps, Audit, API Keys, and guarded System actions | implemented; generic middleware/syslog tail and scrub/SMART task management are absent from v27 or deferred |
| It1 | Paired multi-host configuration/secrets and the session controller: host generation, plan invalidation, worker draining, stale-callback rejection, connection testing, and per-host write mode | implemented |
| It2 | Non-blocking job engine and read-only core views: Dashboard, Alerts, Jobs, Services, Storage health, and global view UX | implemented |
| It3 | Hardened interactive SSH hand-off with pinned host-key sources and no arbitrary remote command mode | implemented |
| It4 | Vanilla-v27 live acceptance, remaining hardening and consistency review, estate registration, and release readiness | live acceptance and CMRU registration recorded 2026-10-29; first 0.1.0 release not published |
| Later | Storage partitioning, pool creation, boot-pool management, and other TrueNAS screens not assigned above | deferred |

## 11. Supported host

tnt accepts only a host whose reported API generation is v27. The adapter package stays extensible for a future
generation, but no other generation is accepted or represented in the shipped manifest.

## 12. Decisions, open questions and estate registration

Decided by the controller on 2026-10-28: the pinned git-sha dependency on `truenas_api_client` is accepted for now
(documented in `pyproject.toml`); no relaxation patches; no other API generation; no Shell menu entry.

Still open: Q7 how conditional menu entries (Apps, Containers, Virtual Machines, KMIP, Enclosure; flagged
`conditional` in `menu.py`) are detected per host (TNT-35). Live-lane profile selection and required variables are documented in
[`docs/CONSUMERS.md`](CONSUMERS.md#explicit-live-lane); the operator supplies the test host and its disposable pool.

`tnt.testing` ships inside the wheel but imports `websockets` at module level; `websockets` is a test-extra dependency
only, so importing `tnt.testing` without the test extra fails, and nothing in the runtime imports it.

CMRU estate registration is complete (TNT-32): project id `tnt` points to `truenas/tnt/cmru.toml`, declares its
first-party `cli-extended` dependency, and publishes a wheel plus SHA256 under tag and GitHub Release `tnt-vX.Y.Z`.
The `VERSION` file is the version source used by both setuptools and CMRU. CMRU's release gate runs the registered
`r0-r1` and `ux-focused` Run-Gate lanes. The `ux-focused` lane includes real tmux terminal checks, but those cases
skip while `tester-unified` lacks tmux; TNT-54 tracks that image capability. No tester image was changed for this
registration. The `live-v27` lane remains opt-in and is not a release gate; the separately reported `live-inspect`
lane belongs to estate gate tooling elsewhere. The first tnt release remains unpublished.
