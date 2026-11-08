# debian-install-v2 — TODO / backlog

## DONE in code, OPEN live (div2-data-area-v2, 2026-11-05): explicit `partitioning` plan

Replaces the predecessor rule-sized layout and separate root-cap setting with
one whole-disk `partitioning` plan. It reserves an 8 GiB raw `iobench-raw`
partition by default, numbers partitions by physical order, and refuses
unaccounted plans. See README "Disk layout" and
`docs/DESIGN-data-area-v2.md`. Unit tests cover the current planner; the
`r1-vm-real-commit` lane remains the real-disk-image validation and was not run
in this fix round. Deferred: `fs: zfs` as a managed filesystem, btrfs `subvol`.

## DONE (root-cap follow-ups, 2026-11-02; superseded by `partitioning`)

Stage one installs cloud-init and initramfs growth guards after the plan validates and before any initramfs
shrink hook is installed; stage two repeats them. They are installed whenever the plan fixes root (anything but `"*"`).

## DONE (requirements naming, 2026-11-02): name the optional wizard dependency by use case

Renamed the ad-hoc wizard manifest to `requirements-wizard.txt` and updated
the installer, tests, generated CLI surface, and adoption/design docs. Future
tool manifests are inventoried in the release-program requirements naming
report and will migrate in separate tool-scoped changes.

## OPEN (MDT-016 / D-HS-01-2, 2026-10-19): standalone io.cost calibration can measure through the hypervisor cache

`tools/iocost_coef_gen.py` (patched copy of the Linux generator, built by `tools/build-iocost-generator.py`)
runs `fio --direct=1 ... --time_based` and reports the mean. O_DIRECT does not defeat a hypervisor
write-back cache: one virtio host measured 4.3 GB/s read / 2 GB/s write / 94k IOPS from a disk that
saturated at ~20 MB/s with 35-60 ms write latency, so the numbers (and any io.cost model or io.max cap
derived from them) are cache readings. Shipped now for the standalone calibration consumers,
mdt-cockpit `mdt-io-baseline.py` and
`tools/iocost-calibrate.sh`): a plausibility REFUSAL for a rotational-flagged device above 1 GiB/s sequential or
20,000 random 4-KiB IOPS (one shared definition, `tools/iocost_plausibility.py`; override
`IO_BASELINE_ROTATIONAL_FLAG=ignore` for a fast virtio disk). NOT done (too large, touches the vendored patch series and the sha256): redesign the
generator to use `--fsync`/`--end_fsync` for writes, a working set larger than the cache the host can
see, and a steady-state tail measurement (ramp then measure only the last N seconds), recorded in the
output. Installer benchmarking now uses iobench `veryquick` against the planned raw partition and
does not use this generator; its measurements are also advisory and may reflect the guest-visible
virtual-disk cache. Tracked in mdt-cockpit as MDT-017.

## OPEN (operator note, 2026-10-06): test Augeas as an alternative for in-place config edits

Test Augeas as an alternative for in-place config edits (see
[`docs/AUGEAS-EVALUATION.md`](docs/AUGEAS-EVALUATION.md)); current approach is
drop-ins plus whole-file templates. Evaluated 2026-10-06 and NOT adopted: the
only true in-place edit in v2 is the fstab swap filter, and the lens catalog has
no deb822 lens. The evaluation's code sketches were never executed; proving
them in a throwaway container with the packages is the first step of any trial.

## IMPLEMENTED (LT-IOB, updated by div2-data-area-v2): io.cost benchmark step

`Installer._run_io_benchmark()` runs iobench `veryquick` on the planned
`iobench-raw` partition. It checks the PARTLABEL and geometry, refuses a
mounted or active-swap device, wipes signatures before the run, and leaves the
partition in place and raw. The default reservation is 8 GiB; config can set
2–10240 GiB or `false` when the benchmark is disabled. The temporary-tail
partition path, scratch filesystem, and delete-table transaction are removed.
See `IO-BENCHMARK-DESIGN.md`; unit coverage is in
`debian_install_v2/tests/test_io_benchmark.py`. The VM lane remains the real
partition-table validation and was not run in this fix round. Writing
`io.cost.model`/`io.cost.qos` remains a separate feature.

## Feature request: a zswap fill-watermark governor

There is no kernel-native way to keep the zswap compressed pool hovering
around a target fill level (e.g. "~80%, evict the excess to disk, but never
fully drain any one cgroup's pool"). The three runtime knobs under
`/sys/module/zswap/parameters/` are the entire menu, and none of them do
this:

- `max_pool_percent` — a hard ceiling on pool size, not a target.
- `accept_threshold_percent` — only matters *after* the ceiling is already
  hit; decides when to resume accepting pages, not a general fill target.
- `shrinker_enabled` — a generic, kernel-pressure-driven, cross-memcg LRU
  shrinker with no per-cgroup floor and no target percentage.

Confirmed live, twice, on the gstammtisch game host (2026-09-08, soulmask
periodic-stall investigation): enabling `shrinker_enabled` under real memory
pressure does not trim proportionally — it fully evacuates whichever
cgroup's pool it currently judges coldest, down to **0%**, with no
in-between state, and it does not recover on its own once off. First
occurrence drained one Soulmask instance's ~650-680M pool to 0 over ~7
minutes with the sibling instance untouched; re-enabled later to see if the
untouched instance would eventually go the same way, it drained a much
larger ~2.7G pool to 0 in **~2.5 minutes**, this time with a directly
observed severe stall (in-game FPS crashed to 8.3, disk-refault rate peaked
past 45,000/s). Turning `shrinker_enabled` back off stops the drain
immediately but does not restore what already moved to disk. Full
measurement detail: `scripts/gstammtisch-guide/OBSERVATION.md` §1.

**Ask:** a small userspace watcher/governor — poll
`/sys/kernel/debug/zswap/pool_total_size` (or the per-cgroup
`memory.zswap.current` for the specific cgroups that matter) against a
configured target band (e.g. 70-85% of the `max_pool_percent` ceiling), and
toggle `shrinker_enabled` on just long enough to trim back into the band,
then off again — instead of leaving it either permanently off (pool bursts
straight to disk once full, the original problem) or permanently on
(runaway full drain, the newly-discovered problem). This is host-tuning
scope, not game-specific, hence filed here rather than in gstammtisch-guide
— natural home given `debian_install_v2/README.md` §"Host tuning
(incorporated from gstammtisch-guide)" already owns `vm_swappiness`/KSM/oomd
config the same way.

Not designed or scoped beyond the above; no code exists yet.

_Captured 2026-09-08 from the live gstammtisch soulmask-stall investigation;
filed by Claude per operator request. debian-install-v2 has no CHANGES.md or
managed nyxloom backlog yet (checked `nyxloom.toml` for `[backlog_entries]`
— absent), so this file is the tracker. The host-escape helper request filed
earlier that session has since been implemented in `mdt-cockpit`.

_2026-09-08: feasibility report + implementation plan now exist —
[`zswap-shrinker-threshold-feasibility.md`](zswap-shrinker-threshold-feasibility.md)._

## DONE (LT-PREP, 2026-10-06): adopt nyxloom's Mattermost notification pattern (not the package itself)

Implemented as `debian_install_v2/notify.py` + `notify_backend` config; historical investigation below.

Operator asked (2026-09-08, netcup live-test session) whether the scp-api/
debian-install-v2 tooling could adopt nyxloom's Telegram/Mattermost
notification adapter (`nyxloom/src/nyxloom/notify.py`) instead of
maintaining its own separate Telegram-only sender, since nyxloom now has a
running Mattermost stack on main (`voice-screen-share/mattermost-server/`).

**Investigated. Findings:**

- `notify.py` is NOT a generic, importable library — it's nyxloom's own P06
  package, ~800 lines, tightly coupled to nyxloom's typed `Event`/
  `EventType`/`NotifyConfig` model plus its `storage`/`snapshot`/`log`
  modules (`from . import snapshot, storage`). Importing it would drag in
  most of nyxloom's core, not a small utility.
- Notably, nyxloom itself does **not** have a Telegram backend yet either —
  only `NtfyBackend`, the legacy `WebhookBackend`, and the new
  `MattermostBackend` are implemented (`notify.py`'s own docstring: "Telegram/
  Discord are additional subclasses later").
- What IS genuinely small and reusable: `mattermost_payload()` — folds a
  `{title, body, click, priority, tags}` note into Mattermost's incoming-
  webhook `{"text": ...}` contract (Mattermost ignores every other key),
  with priority folded into a fixed `:rotating_light:`/`:warning:` Markdown
  prefix since incoming webhooks have no real priority field. ~25 lines,
  zero third-party dependencies.
- **Why not import nyxloom as a dependency**: both `scp-api-install-host.py`
  and `debian-install-v2/bootstrap-remote.py` are deliberately single-file,
  dependency-free scripts — `bootstrap-remote.py` in particular runs on a
  freshly-imaged host with zero pip packages available, before the installer
  tree or shared CLI library exists, so pulling in a whole separate product's
  package there is a non-starter. (Same reasoning already applied the same session when
  `scp-api-install-host.py`'s `_load_env_file`/`_write_env_file` were
  duplicated rather than imported from the sibling `telegram_setup.py`.)
- **Where the real integration point is**: `debian_install_v2/installer.py`
  already has its own hand-rolled Telegram sender (`_split_for_telegram`,
  direct `api.telegram.org` calls) tightly coupled to
  `Config.telegram_bot_token`/`telegram_chat_id`/`telegram_verbose_progress`/
  `telegram_thread_id` and its own credential-file plumbing (systemd
  `LoadCredential`, `/etc/vbpub/credentials/telegram_*`).
  `scp-api-install-host.py` itself sends no notifications directly — it only
  passes `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` through into the
  customScript for `debian_install_v2` to use on the host.

**Recommendation:** borrow the pattern, don't import the package. Port a
small, adapted Mattermost-payload translator into `installer.py`, and
generalize its currently Telegram-only notifier into a pluggable backend
(new `Config` field, e.g. `notify_backend`/`webhook_url`, selecting
Telegram vs. Mattermost) — mirroring nyxloom's `NotifyBackend` shape without
depending on nyxloom itself. This is real, moderate-scope work (new Config
field(s), a new backend class, credential-file handling for a webhook URL
instead of bot-token/chat-id, a new customScript env var, tests) — scoping
it as its own follow-on task rather than folding it into the in-flight
live-test session, which is using the existing Telegram sender unchanged
for this round.

Not designed or scoped beyond the above; no code written yet.

_Captured 2026-09-08 from the netcup live-test session per operator request._

## DONE (root cap, 2026-11-02): write the boot growth guards only after the cap is validated

Stage one now installs the cloud-init and initramfs growth guards only after the planner accepts the capped tail-swap
layout, just before stage two is installed and the first reboot. The stage-one guard test covers capped, uncapped, and
refused-cap installs, asserting that refusal writes neither guard file.

## DONE (root cap, 2026-11-03): install growth guards before the initramfs shrink hook

Stage one resolves the root-shrink plan before writing anything that could
change boot behavior, then writes the cap guards before `_plan_root_shrink`
installs the hook and rebuilds initramfs images. An unplanned reboot during
`update-initramfs -k all` therefore has the guards already in place. The
recorded dry-run test asserts both guard writes precede the hook write and
initramfs rebuild; refused and uncapped paths remain covered.

## OPEN (operator decision 2026-11-04): the TrueNAS part of the seed-ISO recipe becomes a pointer to tnt

debian-install-v2 stays the seed-ISO builder only; no TrueNAS API code goes into it. The TrueNAS/Proxmox mention in
`docs/CONSUMERS.md` ("Build a private VM seed ISO", the paragraph naming TrueNAS SCALE, and the VM attach step) does not
say how the cloud image reaches the NAS. Change it so that the TrueNAS steps are a pointer to tnt (TNT-61 imports the cloud image
into a zvol, TNT-60 manages the devices), as:

1. Put the Debian cloud image and the seed ISO (and their `.sha256` files) in the repo-level `downloads/`.
2. `tnt files put <name> <dataset-dir>` (API upload from `downloads/` only, verified against the sha256; TNT-61 stage 1).
3. `tnt vm disk import <dataset-path> --zvol <pool/zvol>` (TNT-61 stage 2, with its capability probe).
4. `tnt vm create --iso <seed>`.
5. `tnt vm device add` (and `tnt vm device list/update/delete`, TNT-60, to inspect or fix the devices).

The Proxmox and Hyper-V mentions stay as they are. Acceptance: the section links tnt's CONSUMERS and names no TrueNAS API method; a
docs test (if the file has one) still passes; the existing recipe is kept as the manual fallback until tnt TNT-61 ships. Depends on tnt
TNT-60 and TNT-61 (`truenas/tnt/BACKLOG.md`).
