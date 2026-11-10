<!-- origin: 2026-10-28; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: truenas-tui-research.md -->
<!-- relation: TrueNAS TUI prior-art research -->

# TrueNAS remote TUI: prior-art research and recommendation

Date: 2026-10-28. Method: WebSearch (standard) plus WebFetch of primary pages.
Everything marked UNVERIFIED could not be confirmed from a fetched primary source.
Nothing was installed. No code in vbpub was changed.

## 1. Facts about the target API (primary sources)

- Transport: versioned JSON-RPC 2.0 over WebSocket. Endpoint `wss://<host>/api/current`
  (also `/api/v25.10` style versioned paths; versioned-path form UNVERIFIED for 27).
  Existing estate config already uses it: `scripts/truenas/truenas.toml`
  (`wss://v1001/api/current`, `verify_ssl = false`).
- REST: deprecated 25.04, removed in 26 (official deprecations page:
  https://www.truenas.com/docs/scale/26/gettingstarted/deprecations/). A REST-based tool is a dead end.
- Auth (important, moving target), from the same page and the api_client README:
  - `auth.login_with_api_key` is deprecated in 26 with planned removal in 27.
    Replacement: `auth.login_ex` with `mechanism="API_KEY_PLAIN"` (stays supported past 27)
    or `mechanism="SCRAM"` (SCRAM-SHA-512 mutual auth, default in the client for 26+, with
    SCRAM-PLUS TLS channel binding on over wss).
  - A forum report says `login_with_api_key` failed on 25.04-BETA1 / 25.10.1 while `login_ex`
    worked (https://forums.truenas.com/t/fangtooth-api-authentication-changed/36030);
    cause not confirmed. Conclusion: a tool must use `auth.login_ex` and never rely on
    `login_with_api_key`.
  - Keys sent over plain HTTP/ws are auto-revoked by TrueNAS: never allow `ws://`.
  - Rate limit: 20 auth/unauthenticated requests per 60 s, then a 10 min cooldown
    (api_client README). A TUI must reuse one persistent session and not retry-loop on auth failure.
- 27 status: RC.1 released (renamed from 26; GA planned early Dec 2026) per
  https://www.virtualizationhowto.com/2026/10/truenas-27-rc1-is-here-5-changes-that-matter-for-home-labs/
  and StorageReview. Official 27 docs list "improved authentication methods incl. SCRAM-SHA-512".
  Method reference exists at https://api.truenas.com/v27.0 (fetched; list below).
- Methods available in the v27.0 reference (fetched index):
  - pool.*: create, update, query, export, import_find/import_pool, attach/detach/replace/remove/offline/online,
    expand, scrub, get_disks, validate_name, filesystem_choices
  - pool.dataset.*: create/update/delete/query/details/rename/promote/set_quota/lock/unlock/..., pool.snapshot.*
  - zfs.resource.* and zfs.resource.snapshot.* (new-style ZFS API alongside pool.snapshot), zpool.query, zpool.scrub.run,
    zfs.tier.*
  - disk.*: query, details, get_used, temperatures, update, wipe, SED ops
  - boot.*: attach, detach, replace, get_disks, get_state, scrub, set_scrub_interval; boot.environment.*:
    query, clone, activate, keep, destroy
  - container.* and container.device.* (new in the 26/27 line), vm.* and vm.device.*, app.*, app.image.*,
    app.registry.*; NO `virt.*` namespace in v27.0 (it existed in 25.10/26; containers moved to `container.*`).
    This is a concrete cross-version difference an adapter layer must absorb.
  - system.info, auth.* (login_ex, login_ex_continue, login_with_api_key, mechanism_choices, me, ...)
- The GAP, confirmed from the schema pages:
  - `pool.create` `topology.*.disks` is "array of disk names" (strings like "da1"/"sda"); the visible schema says nothing
    about partitions or sizes (the page was truncated at 100000 of 105618 chars; the last 5.6k chars unread).
    So creating a pool on a partition or on free space of a disk is NOT expressible through the documented
    pool.create schema. UNVERIFIED on the live host whether a partition name (e.g. `sda3`) is silently
    accepted; must be probed with a dry validation (pool.create has no documented dry-run; use
    `pool.validate_name` plus a throwaway disk on the test host) before designing around it.
  - `boot.attach(dev, {expand})` only attaches whole devices to the boot pool. No documented API to create
    a data partition on boot disks or manage a "boot-pool-data" pool. `disk.*` has wipe/update but no partition
    listing or creation method in the index.
  - Therefore partition-level actions cannot be API-only. Likely design: API for discovery and for everything
    else; for partition work either (a) an explicit SSH fallback (sgdisk/zpool create then `pool.import_find` +
    `pool.import_pool` to adopt the pool into middleware; HYPOTHESIS, UNVERIFIED that middleware adopts such
    pools cleanly or tolerates partition vdevs), or (b) a middleware-side helper. Needs a live-host spike first.

## 2. Candidates

| Candidate | URL | License | Maintenance | API/version support | Features | Library or app | Verdict |
|---|---|---|---|---|---|---|---|
| truenas/api_client (provides `truenas_api_client` + `midclt`) | https://github.com/truenas/api_client | LGPL-3.0 | Official; 113 commits; tags per release (TS-25.10.3, TS-26.0.0-BETA.1); master = 27.0-ALPHA "unstable". Last-commit date not shown on fetched page (UNVERIFIED) | 24.10 legacy only (EOL); 25.04+ JSON-RPC 2.0; 25.10 adds new-style jobs + `verify_ssl`; 26 adds SCRAM API keys; no 27-RC tag listed on the fetched page (UNVERIFIED) | Generic `call(method,...)`, jobs, subscriptions, API key via string/JSON/INI, password, SCRAM(+channel binding); stdin payloads in midclt | Library (sync `Client`) and CLI | REUSE as the transport. Caveats: sync API (wrap in threads or use for Textual workers); LGPL is fine for use as a dependency; install is git-tag based (`pip install git+https://...@<tag>`), not PyPI, so pin a tag per CIU version policy; verify a 27-RC compatible tag |
| midclt / TrueNAS's "new CLI" | same repo; old interactive CLI is https://github.com/truenas/midcli | midcli: unverified here; midclt: LGPL-3.0 | midclt maintained; midcli is the deprecated interactive CLI (docs: API Client "is not the deprecated TrueNAS CLI") | midclt follows api_client | One-shot calls, `-j` jobs, `-K` key file, stdin payloads | App | USE for ad-hoc debugging only. Searched for `truenas/truenas-cli`: GitHub returns 404; I found NO separate "new truenas-cli" project. The forum "Introducing the new TrueNAS CLI" hit is the 2021 midcli post. UNVERIFIED if one exists under another name |
| TrueNASScale-TUI (SoumyaPurani) | https://github.com/SoumyaPurani/TrueNASScale-TUI ; forum https://forums.truenas.com/t/project-truenasscale-tui-a-real-time-terminal-dashboard-built-for-scale-25-04/65437 | MIT | Early stage; 10 commits, 7 stars; last commit date not shown (UNVERIFIED) | 25.04+; uses `auth.login_with_api_key` (deprecated 26, removal 27) | Dashboard, pools/disks/temps/scrub, service start/stop/restart, live updates via `core.subscribe`; Python 3.13+, Textual, uv | Application | DO NOT BUILD ON. Useful as a Textual reference for subscription-driven views only. No partition/pool-create/boot-env features; uses the auth call that is going away |
| pytruenas | https://pypi.org/project/pytruenas/ (GitHub jose-pr/pytruenas per listing) | UNVERIFIED | 0.0.0 to 0.2.2, small; later releases lean on a separate "hostctl" | JSON-RPC 2.0, ws(s) or local unix socket | Typed client plus scripting CLI | Library/CLI | Skip: tiny, single maintainer, typed models would lag nightlies |
| aiotruenas | https://pypi.org/project/aiotruenas/ (kayl-codes/aiotruenas per listing) | UNVERIFIED (page failed to load) | v1.0.0, 1.1.0 | `/api/current`, 25.04+; generic `call()`, job polling | asyncio-native | Library | Interesting for Textual (asyncio), but unvetted: auth mechanism (login_ex/SCRAM?) unverified, small project. Fallback option only; reading its source is cheap if we want an async transport |
| aiotruenas-client | https://pypi.org/project/aiotruenas-client/ | UNVERIFIED | Old (0.1.0) | Older websocket API | disks/pools/VMs state | Library | Skip: legacy protocol |
| truenas/terraform-provider-truenas (official) | https://github.com/truenas/terraform-provider-truenas | MPL-2.0 | Official, 394 commits, Go 1.25 | 26.0 and 25.10 fully tested, 25.04 untested; nothing older; 27 not stated | 85 resources (storage, shares, ACLs, iSCSI, NVMe-oF, apps, VMs, replication, ...); own JSON-RPC client, API key | Terraform provider (Go) | Not reusable by a Python TUI; useful as a reference list of which methods/params the vendor treats as stable, and for schema-drift patterns. Also suggests vendor does not do partition-level pool creation either |
| dariusbakunas/terraform-provider-truenas | (via forum search result) | UNVERIFIED | REST `/api/v2.0` | REST: broken on 26+ | - | - | Dead (REST) |
| Ansible collection / Prometheus exporter | none found in one search | - | - | - | - | - | UNVERIFIED absence; one search only. Not pursued |
| truenas-aiops (PyPI) | https://pypi.org/project/truenas-aiops/ | UNVERIFIED | 0.5 to 0.9 | Notes 26 deprecates login_with_api_key | LLM-driven ops with audit/risk tiers/undo | App | Not a TUI; the "risk tiers + audit + undo" idea is worth borrowing for confirm gates. Not evaluated further |
| Homepage dashboard fork with WebSocket support | forum thread 61645 | - | - | - | widget only | - | Irrelevant |

No Textual/Rich/urwid TrueNAS dashboard other than TrueNASScale-TUI turned up. A k9s-style TrueNAS tool: none found. Search coverage is shallow (about 10 queries); GitHub-side search was not possible directly, so smaller projects may exist.

## 3. Recommendation

Build a new tool; reuse only `truenas_api_client` as the transport and, if desired, read aiotruenas and
TrueNASScale-TUI for patterns. Reasons: the requested value is exactly the gap area (partitions, free space,
pool-on-partition, boot-pool-data) that no existing tool touches; existing TUIs are read-mostly dashboards on a
soon-removed auth call.

### Architecture sketch

- Layers (strict one-way dependencies):
  1. `transport`: wrapper around `truenas_api_client.Client` (threaded) behind a small `RpcClient` protocol:
     `call(method, *params)`, `job(method, *params) -> JobHandle`, `subscribe(collection)`, `close()`.
     Auth only via `auth.login_ex` (SCRAM default; `API_KEY_PLAIN` selectable by flag), TLS channel binding
     retained. Refuse `ws://`. One persistent session; auth failures are fatal, never retried (rate limit).
  2. `adapters` (per API generation): a `Backend` interface with domain operations
     (`list_pools`, `list_disks`, `list_free_space`, `create_pool_plan`, `boot_status`, `list_containers`, `list_vms`,
     `list_apps`, `snapshots`, ...). Implementations selected from `system.version`/`system.info` at connect:
     `V25_10`, `V26`, `V27`, plus a `Nightly` that probes with `core.get_methods` (or the method listing exposed by
     the target; UNVERIFIED name) and fails closed on unknown required methods. Known divergences to encode:
     `virt.*` vs `container.*`; `pool.snapshot.*` vs `zfs.resource.snapshot.*`; `login_with_api_key` vs `login_ex`;
     job result shapes (new-style jobs from 25.10). Each adapter ships a golden method manifest so CI can diff against
     `https://api.truenas.com/v27.0` method pages and a live host.
  3. `domain`: plain dataclasses (Pool, Disk, Partition, FreeExtent, Dataset, Snapshot, BootEnv, Container, Vm, App) and
     an `Action` model: `Action(kind=READ|MUTATE|DESTRUCTIVE, summary, plan_steps[], rpc_calls[])`.
  4. `ui`: Textual app (async workers call the sync client in threads; subscriptions feed reactive tables), plus a
     non-interactive CLI (`list`, `plan`, `apply`) via cli-extended so everything the TUI does is scriptable and
     testable. TUI is a thin view over the same domain functions.
- Mutation safety: default session is read-only; `--allow-write` (or an in-TUI unlock) enables MUTATE; DESTRUCTIVE
  (wipe, pool destroy/export, boot detach, dataset delete) always shows the exact planned RPC calls/commands and
  requires typing the target name; honour cli-extended `--dry-run` and `--yes` semantics. Plan then apply: every
  mutating feature first renders a plan (derived from `disk.details`, `pool.query`, `zpool.query`) and refuses if
  state changed between plan and apply. Optional audit log (no secrets) under XDG state dir.
- Secrets: API key only from `TRUENAS_API_KEY_FILE` (preferred, mode check 0600), `TRUENAS_API_KEY` env, or the
  api_client key-file formats; never a CLI argument, never logged, redacted via cli-extended redaction; the existing
  `truenas.toml` holds uri/username only and must stay secret-free. Python `repr` of client objects must be scrubbed.
- TLS: verify on by default (`verify_ssl=True`, channel binding on); `--insecure` / `verify_ssl=false` requires an
  explicit flag AND prints a banner on every screen; restrict via config to hosts listed as throwaway.
  (The estate's `truenas.toml` currently sets `verify_ssl = false` for v1001; keep that explicit per-host.)
- Testability: (a) fake JSON-RPC 2.0 WebSocket server (stdlib `asyncio` + `websockets`) that serves recorded fixtures per
  adapter, implements `auth.login_ex` handshake incl. a SCRAM stub, jobs (`core.job` updates), and error injection;
  Textual tested with its `Pilot`; (b) contract tests that replay the same domain scenarios against the fake and
  the live test host (marked, opt-in, read-only by default; mutating live tests only on the designated throwaway
  VM host, never production/nano-class hosts); (c) manifest drift test against fetched API docs.
  Respect the estate rule that gates must not depend on host load.

### First milestone (focus on gaps)

1. Connect/auth/version detect, adapter selection, read-only mode (system.info, alerts).
2. Disk inventory: `disk.query`/`disk.details`/`get_used` joined with pool membership; free-space and partition view
   (needs a source of partition data: spike to see what `disk.details`/`pool.get_disks`/`zpool.query` expose;
   otherwise SSH `lsblk --json`/`sgdisk -p`, UNVERIFIED availability).
3. Pool/dataset/zvol/snapshot read views; boot pool state and boot environments (`boot.get_state`, `boot.environment.*`),
   with safe BE actions (clone, activate, keep) behind confirm gates.
4. SPIKE (gate for the headline feature): on the live throwaway host determine whether `pool.create` accepts
   partitions; if not, prototype partition + `zpool create` + `pool.import_pool` adoption and check what the UI/middleware
   do afterward (export/reimport, reboot persistence, boot-pool-data layout). Only then implement
   "create pool on free space/partition" and "boot-pool-data management" as plan/apply actions.
5. Later: shares, VMs/containers (`vm.*`, `container.*`), apps (`app.*`), replication, subscriptions-driven live views.

### Placement in vbpub

`scripts/truenas/` already exists (`truenas.toml`, `fix_virt_global.py`, `golden-iso/`, `tests/`) and holds the
estate's TrueNAS tooling and host config; propose `scripts/truenas/tui/` (or a sibling `scripts/truenas-tui/` if it
needs its own release stream). It is a user-facing Python CLI, so it adopts `cli-extended` as every estate CLI does
(`libraries/cli-extended`, import `cli_extended`, Python >= 3.11): `CliIdentity.resolve`, `CliRegistry` with
`VerbSpec/OptionSpec`, `unexpected_exceptions="report"`, `CliFailure` for domain errors, `--dry-run/--yes/--json`
owned by the library, and run `cli-extended audit` against `docs/ADOPTION-CHECKLIST.md`. Pin
`truenas_api_client` by git tag with a documented floor reason (version-policy rule); Textual as a `>=` floor in the
root `requirements.txt` style. Release via cmru like other subprojects. Note the Textual UI layer is new
territory relative to the other cli-extended consumers (argparse/stdlib-first); keep it an optional extra
so the scriptable CLI has no heavy deps.

## 4. Unverified / open items

- Last-commit dates for api_client, TrueNASScale-TUI, terraform provider (fetched pages omit them; check `git log`).
- Whether an api_client tag supports 27.0-RC.1 (README lists up to 26.0.0-BETA.1 and master=27.0-ALPHA).
- Whether any official `truenas-cli` exists (GitHub 404 for truenas/truenas-cli; none found).
- aiotruenas/pytruenas licenses and auth mechanisms.
- pool.create schema tail (last ~5.6k chars unread), and live behaviour with partition names.
- Exact method for listing available RPC methods per version on a nightly.
- Official 27 migration guide contents (not fetched).

## Sources

- https://github.com/truenas/api_client
- https://www.truenas.com/docs/scale/26/gettingstarted/deprecations/
- https://api.truenas.com/v27.0 and pages api_methods_pool.create.html, api_methods_boot.attach.html, api_methods_auth.login_ex.html
- https://forums.truenas.com/t/fangtooth-api-authentication-changed/36030
- https://forums.truenas.com/t/project-truenasscale-tui-a-real-time-terminal-dashboard-built-for-scale-25-04/65437
- https://github.com/SoumyaPurani/TrueNASScale-TUI
- https://github.com/truenas/terraform-provider-truenas
- https://pypi.org/project/aiotruenas/ , https://pypi.org/project/pytruenas/ , https://pypi.org/project/truenas-aiops/
- https://www.virtualizationhowto.com/2026/10/truenas-27-rc1-is-here-5-changes-that-matter-for-home-labs/
- https://forums.truenas.com/t/homepage-dashboard-truenas-websocket-support-fixes-rest-api-deprecation-warnings/61645
- Local: /workspaces/vbpub/libraries/cli-extended/README.md, docs/ADOPTION-CHECKLIST.md, /workspaces/vbpub/scripts/truenas/truenas.toml
