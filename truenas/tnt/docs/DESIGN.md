# tnt design

Status: M0 scaffold, 2026-10-28. Inputs: the prior-art research (`truenas-tui-research.md`, release-program brief),
the canonical action matrix (`docs/ACTION-MATRIX.snapshot.md`, from branch `truenas-restore-iso`), the TrueNAS web UI
source (`truenas/webui` `navigation.service.ts`, read on master 2026-10-28) and the estate's cli-extended conventions.
Everything marked UNVERIFIED has not been confirmed against a running host; BACKLOG TNT-01 is the first spike.

## 1. Goals and non-goals

Goals: (1) one tool that does the storage jobs the web UI and the stock API cannot (disk free space and partitions,
a pool on a partition or free space, boot-pool data management, cache/log/special vdevs; reserving the free-tail
partition) using the API for everything it can and an explicit, gated fallback for the rest; (2) a TUI an experienced
TrueNAS user navigates without learning anything; (3) every TUI action available as a scriptable CLI verb; (4) safe by
construction on a host that may be production.

Non-goals: replacing the web UI wholesale; a REST client (removed in 26); managing hosts below v27 (the v27 RC is the
floor; v25.10/26 adapters are possible later, TNT-22 notes the `virt.*` difference); a daemon.

## 2. Architecture

One-way layers; each only imports the ones above it in this list.

```
profile.py   Profile, Secret, key-file checks            (no I/O beyond reading config and the key file)
transport.py RpcClient protocol, connect(), GuardedRpc   (the only module that touches truenas_api_client)
adapters/    Backend per API generation + method manifest
domain.py    Disk, Partition (free_bytes), human_bytes   (plain values)
actions.py   Action, Plan, apply_plan, refusal list      (plan/apply model)
session.py   Session = profile + guarded rpc + backend; the single query entry point (Session.disks())
cli.py       cli-extended registry: doctor, profile, disks, tui
tui/         Textual app: Tree navigation, content view per screen, command palette
```

The CLI and the TUI both call `Session` methods; the TUI holds no query logic. `build_cli(opener=...)` takes the
session opener so tests substitute a fake-server-backed one.

### 2.1 Transport

`truenas_api_client.Client` (LGPL-3.0, official; threaded, JSON-RPC 2.0 over websocket at
`wss://HOST/api/current`). Pinned by git sha of tag `TS-27.0.0-RC.1` (commit dated 2026-08-31, 58 days old on
2026-10-28, so past the 14-day third-party rule); it is not on PyPI. The pin is re-checked by TNT-31 at GA.

- Login: `Client.login_with_api_key(user, key, mechanism, channel_binding=...)`. This client helper sends
  `auth.login_ex` (`SCRAM` default, SCRAM-SHA-512 with tls-server-end-point channel binding over wss; or
  `API_KEY_PLAIN` when the profile says `auth = "plain"`). The deprecated middleware method
  `auth.login_with_api_key` is never called (a test asserts the wire never carries it). The client never
  auto-downgrades SCRAM to PLAIN; neither does tnt.
- One persistent session. A failed login is fatal and not retried (rate limit: 20 unauthenticated requests per 60 s,
  then 10 minutes of lockout). `tnt doctor --online` is one login.
- `ws://` is refused: TrueNAS revokes API keys sent without TLS. Profiles reject it at load time and `connect()`
  rejects it again. The only exception is the loopback hook the tests use for the fake server
  (`allow_loopback_ws`, honoured for 127.0.0.1/::1/localhost, not reachable from the CLI or from a profile).
- TLS verification is ON. `tls = "insecure"` in a profile turns it off only when the command line also carries
  `--insecure`, and the TUI then shows a red banner on every screen. Estate hosts that run with `verify_ssl = false`
  (the existing `scripts/truenas/truenas.toml` does for v1001) keep that explicit per profile.
- Errors from the client are wrapped into one `TransportError(kind, message)` with the API key scrubbed from the text.

### 2.2 Version adapters and the method manifest

API methods move between generations (v27.0 has no `virt.*`; containers are `container.*`; `pool.snapshot.*` coexists
with `zfs.resource.snapshot.*`; job result shapes changed in 25.10). So the UI never calls a method name directly:
screens call `Backend` operations and each `Backend` subclass knows its generation's names.

- `adapters.select(version)` parses the major from `system.version` (`TrueNAS-27.0.0-RC.1` -> 27; a nightly like
  `28.04-MASTER-...` -> 28) and picks `V27` or `V28`; any other major raises `UnsupportedVersion` (fail closed).
- Every adapter carries `manifest`: feature -> RPC methods it needs, and `verified`: how that list was obtained
  (`reference` = read from api.truenas.com/v27.0; `live` = confirmed against a host; `copied` = inherited, unconfirmed).
  `V27` is `reference`, `V28` is a `copied` stub of V27.
- `adapters.missing_methods(backend, core.get_methods result)` diffs the manifest against what a host lists; `doctor`
  will use it (the name `core.get_methods` as the method-list call is UNVERIFIED on v27 until TNT-01/22).
- Planned: a drift test that diffs manifests against the fetched reference index (TNT-23, opt-in, outside the gate).

### 2.3 Domain and the partition-data question

`Disk.partitions` is `None` when the API gave no partition data and `()` when the disk has none; `free_bytes` is `None`
for unknown, so "unknown" can never be mistaken for "full" or "empty". `free_bytes` is an upper bound (alignment and the
backup GPT are not subtracted) until TNT-02 computes exact extents. The M0 adapter asks `disk.query` with
`extra.pools` and `disk.details` with `{type: BOTH, join_partitions: true}`; both parameter shapes are UNVERIFIED on
a live v27 host (TNT-01). If the API cannot expose partitions, the source becomes an SSH `lsblk --json` / `sgdisk -p`
fallback behind the same `Backend` method.

### 2.4 Action model (plan/apply)

`Action(id, kind, title, calls, probe, confirm_target)`; `kind` is READ, MUTATE or DESTRUCTIVE. `id` is an
ACTION-MATRIX row id (A-xx/B-xx) or a TNT id. `make_plan` runs the read-only `probe` calls and stores a fingerprint;
`Plan.render()` prints the exact RPC calls in order. `apply_plan` checks, in this order, and stops at the first failure:

1. refusal list (section 8), never overridable;
2. write session (`--allow-write`), for MUTATE and DESTRUCTIVE;
3. typed confirmation, for DESTRUCTIVE: the operator types the target name exactly;
4. state drift: the probe is re-run and its fingerprint must equal the plan's, else "plan again";
5. execute the calls (jobs via `job=True`).

The TUI shows the plan in a modal (TNT-27); the CLI will expose `plan`/`apply` verbs with cli-extended's `--dry-run` /
`--yes` (TNT-26). Both use the same functions. No real action exists at M0; the model is implemented and tested so the
first one (TNT-04, import a pool) only supplies calls.

## 3. Action backlog from the matrix

Rows with verdict `API-TUI` or `pending decision` are the TUI's backlog (seeded into `BACKLOG.md`; a test keeps them
in step). The API-TUI-fallback rows come first, as asked:

| row | action | tnt |
|---|---|---|
| (input) | disk free space / partitions | TNT-01 spike, TNT-02 |
| B-01 | reserve the free-tail partition | TNT-03 (SSH `sgdisk` fallback, double-gated) |
| B-06 | adopt a hand-made pool (`pool.import_find`, `pool.import_pool`) | TNT-04 (API works; measured PASS) |
| B-02, B-03 | create a pool on a partition / free space | TNT-05, decision pending |
| (derived) | boot-pool data management | TNT-06 |
| A-09 | cache/log/special vdev | TNT-07 |
| B-07, A-08 | must stay refused | refusal list |
| A-01..A-07, B-04, B-05 | work in the (patched) web UI or the stock API | later screens (Datasets, Shares, Containers, VMs) |

## 4. Screens map (mirrors the web UI menu)

Top-level labels and order are taken from the web UI source (`navigation.service.ts`, master): Dashboard, Storage,
Datasets, Shares, Data Protection, Credentials (slide-out: Users, Groups, Privileges, Directory Services, Backup
Credentials, Certificates, S3 Access Keys, KMIP), Containers, Virtual Machines, Apps, Reporting, System (slide-out:
Update, General Settings, Advanced Settings, Network, Boot, Services, Shell, Alert Settings, Audit, Enclosure).
Findings that differ from the brief's guess: there is no "Virtualization/Instances" entry (v27 has Containers and
Virtual Machines as separate top-level items) and Network is under System. The Storage page's own sub-pages (Disks,
Import Pool, pool management) are reached from the Storage page, not from the menu; tnt shows them as children.

`tnt/menu.py` holds the tree; `implemented` screens get a view, the rest show a "planned, milestone Mx, matrix rows"
placeholder, so the whole map is navigable from day one. Deviations from the web UI (all marked `tnt_only`): under
Storage, "Free space and partitions" and "Boot pool data" (the gap features); Shell is omitted (a TUI is already a
shell; reconsidered at M3).

## 5. UX

- Left navigation is a Textual `Tree` (arrows, enter, expand/collapse), content pane on the right, a banner line with
  `profile host generation version read-only|WRITE ENABLED [TLS VERIFICATION OFF]`.
- Command palette: Textual's built-in `ctrl+p`; `get_system_commands` adds a "Go to A > B" entry for every leaf, so a
  user can type "net" and jump to System > Network. Planned: action entries per screen ("Import pool...").
- Keys: `ctrl+n` menu, `r` refresh (app-level so it works from the menu), `?` keys, `q` quit. Chords are not used.
- Per-screen action list (footer plus palette), each action opening the plan modal with the exact calls (TNT-27).
- Long reads run in worker threads (`@work(thread=True)`); the client is synchronous and thread-safe enough for one
  worker at a time (`exclusive=True`). Subscriptions come later (TNT-25).
- Target host is selected by profile only; no URL or key is ever typed into the TUI.

## 6. Version policy

`cli-extended>=0.4.0` (estate floor reasons as in iobench), `textual>=8.0` in the `tui` extra (Tree, DataTable,
SystemCommands, Pilot), `websockets>=15` in the test extra, `truenas_api_client` by sha. Python >= 3.11 (tested 3.14).

## 7. Secrets

Key only from a profile's `api_key_file` (must be a regular file, no group/other permission bits), then
`TNT_API_KEY_FILE`, then `TNT_API_KEY`. Never an argument, never in the profile file (the loader rejects `api_key`,
`password`, `token` keys), never printed: `Secret.__repr__/__str__` are redacted, `profile show` prints only the file
path and whether its mode is acceptable, every wrapped transport error is scrubbed, and cli-extended's `--debug-raw`
is the only way raw diagnostics appear (and it says so). `truenas.toml`-style files hold URI and user only.

## 8. Safety rules and the refusal list

- A session is read-only by default. `GuardedRpc` refuses any method that is not a read shape (`*.query`, `*.details`,
  `*.get_*` listed, `system.info|version`, ...) before it reaches the socket. `--allow-write` opens a write session;
  that alone changes nothing: every change still goes through plan/apply.
- DESTRUCTIVE actions (wipe, pool destroy/export, dataset delete, detach) need the typed target name.
- Never executed, with or without confirmation (`actions.ALWAYS_REFUSED`, `BOOT_POOL_REFUSED`): `system.reboot`,
  `system.shutdown`; `boot.attach|detach|replace` (matrix B-07: a partition has no disk size, internal TypeError);
  `pool.export|update|dataset.delete|snapshot.delete` that mention `boot-pool` (A-08); any `auth.*` except `auth.me`
  and `auth.mechanism_choices`; `disk.wipe` of a disk that belongs to a pool.
- Host-impacting operations keep the estate rules: a throwaway VM host only for mutating live tests, never production
  or nano1; loop-device/swap work is out of scope (the 2026-09-09 host-hang incident).
- The SSH fallback (TNT-03) is a second explicit switch (`--allow-ssh`), runs a fixed, listed set of commands, and each
  appears in the plan.

## 9. Test strategy

- Unit: pure modules (profile, domain, actions, matrix, menu) with direct tests; the refusal list, gate order and
  drift check each have a test that fails if the gate is removed.
- Fake server: `tnt.testing.FakeServer`, a real websocket server on loopback (the `websockets` sync server) that speaks
  JSON-RPC 2.0 with the middleware's error shape, `auth.login_ex` (`API_KEY_PLAIN` only), `system.version`,
  `core.get_methods`, `disk.query`, `disk.details`; it records requests and has error injection (`fail=`). The tests
  therefore drive the real `truenas_api_client` over a real socket, including login and failure paths. It does not
  implement SCRAM (TNT-21) or jobs yet.
- TUI: Textual `Pilot` (`run_test`) against a session on the fake: navigation, palette entries, load, failure, refresh,
  insecure banner.
- CLI: cli-extended `build_cli(opener=...)`, assertions on `--json` output and exit codes; `doctor` checks both ways.
- Live lane (TNT-20, not in the gate): the same read-only scenarios against the throwaway host, key from a file,
  opt-in marker; mutating live tests only on the designated throwaway VM.
- Drift lane (TNT-23): manifests vs the fetched API reference. Optional dependencies required by the suite fail, never skip.
- Gate: `run-gate.toml` lane `r0-r1` (pytest with branch coverage). The registered lane runs without a mutation lane at
  M0 (TNT-30). Tests are deterministic under host load: no timing assertions beyond bounded waits for a worker.

## 10. Milestones

| milestone | scope | size |
|---|---|---|
| M0 (this) | scaffold: transport, profile/secret handling, v27/v28 adapter and manifest, plan/apply model with refusal list, `doctor`/`profile`/`disks`/`tui`, Storage > Disks view, fake server, tests, docs | done (M) |
| M1 | spikes TNT-01 (partition data, params) and TNT-34; real exact free-space view (TNT-02 groundwork); Dashboard, Pools, Datasets read views, System > Boot with boot-environment actions; live lane; SCRAM in the fake; cli-extended surface review; real v28 manifest | L (about 8-10 days) |
| M2 | the gap features through plan/apply: Import Pool (TNT-04), reserve partition (TNT-03, SSH fallback), pool on partition/free space (TNT-05, after the decision), boot-pool data (TNT-06); confirm-gate modal; audit log; live job/subscription updates; plan/apply CLI verbs | L (about 10-15 days, live-host bound) |
| M3 | breadth: Shares, Containers, VMs, Apps, Data Protection, System pages, Credentials/Reporting read views; A-09 vdevs; first release `tnt-v0.1.0` via cmru | XL (open-ended, per screen M) |

## 11. Cross-version risks

The v28 nightly moved middleware code (`v28-nightly-20261014/middleware.patch` is a UI-affecting patch), so a v28
adapter cannot be trusted until TNT-22. A v26 or v25.10 host works only if an adapter is added (`virt.*`). The matrix's
v28 column is `pending` for every row.

## 12. Open design questions and estate registration

Open questions for the operator are listed in the hand-back report and repeated here:
Q1 package/pin policy (hard dependency on a git sha vs. a vendored wheel in the estate's artifact store);
Q2 the SSH fallback for partitioning (allowed at all? which user, key handling, which commands);
Q3 decision B-02/B-03 (pool on partition by `zpool create` + import, or a middleware patch);
Q4 whether v26/v25.10 hosts need adapters; Q5 the Shell menu entry; Q6 where the live-lane host and key live.

Estate registration is deliberately not done at M0: `cmru.orchestration.toml` and the root run-gate registry need an
entry for `truenas/tnt` (TNT-32), and the gate environment needs `tnt` installed for the cli-extended version lookup
(TNT-33). `cmru.toml` and `run-gate.toml` in this directory are ready for that step.
