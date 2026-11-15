<!-- Evidence scrub 2026-11-10: environment-specific host identities, addresses, controller paths, and key material were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
# debian-install-v2 — TODO / backlog

## IMPLEMENTED (DIV2-BL-06, unreleased): bound KSM advisor work and scanner wakeups

**Status:** IMPLEMENTED in this unreleased change.

The KSM tmpfiles drop-in sets `advisor_max_cpu=10` percent of one core,
`advisor_target_scan_time=300` seconds, and `sleep_millisecs=100`. Host
measurements showed the advisor cap alone left ksmd near 15% of a core under
load, while the 100 ms sleep reduced it to about 4%. All three settings are
installer-configurable with validated `ksm_advisor_max_cpu_percent`,
`ksm_target_scan_time_seconds`, and `ksm_sleep_millisecs` values. The existing
best-effort behavior remains: a kernel missing one of the sysfs knobs gets a
warning for that tmpfiles entry and installation continues. README,
DESIGN-GUIDE, CONSUMERS, and CHANGES document the policy and the required
existing-host refresh command. Regression coverage pins rendered content,
config bounds, and missing-knob handling.

## OPEN (DIV2-BL-07, 2026-11-12): bound the KSM scanner sleep value to the kernel range

**Status:** OPEN; follow-up to the configurable `ksm_sleep_millisecs` setting
in the KSM tmpfiles work. Source: `debian_install_v2/config.py:479-480`,
`debian_install_v2/templates.py:186`; `reports/di2-ksm-sleep-REVIEW.md:25-30`.

**Finding.** The setting rejects values below 1 ms and malformed types, but
has no upper bound. Values above 4294967295 are accepted by configuration and
then rejected by the kernel's unsigned-int sysfs parser. Since the tmpfiles
write is best-effort, installation only warns and the kernel keeps its
previous sleep interval, which can differ from the requested configuration.

**Minimal solution.** Reject values above 4294967295 before rendering the
tmpfiles entry. A smaller documented ceiling is acceptable if the supported
policy does not need the full kernel range; never accept a value the kernel
cannot apply.

**Close when.** Configuration tests accept the selected maximum and reject
maximum plus one before any tmpfiles write; the rendered maximum reaches the
expected sysfs entry; existing zero, negative, bool, float, string, null, and
injection cases remain rejected; and the registered `r0-r1` lane passes.

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

## OPEN (operator note, 2026-10-06): evaluate Augeas for installer-managed config edits

Test Augeas as an alternative for installer-managed in-place config edits
(see [`docs/AUGEAS-EVALUATION.md`](docs/AUGEAS-EVALUATION.md)); the installer
still uses drop-ins plus whole-file templates. `augeas-tools` is now installed
with the base packages on every host for `augeaswrap`; this does not adopt
Augeas for edits performed by the installer. The only true in-place edit in v2
is the fstab swap filter, and the lens catalog has no deb822 lens. The
evaluation's code sketches were never executed; proving them in a throwaway
container remains the first step of any installer-side trial.

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

## FIXED d951f417d (DIV2-BL-01, 2026-11-08): swap partitions carry no PARTLABEL; `vbpub-swapN` is the filesystem label

**Status:** FIXED 2026-11-09. The <host> reinstall runbook now distinguishes GPT PARTLABEL from filesystem LABEL, states that managed swap PARTLABELs are empty, and records `LABEL=vbpub-swapN` with fstab `PARTUUID`; the stale external runbook patch is retired. The preview, consumer guide, README, and wrapper use the same identifiers.

**Priority:** minor. Source: [div2-reinstall-host-RESULT.md](docs/evidence/div2-reinstall-host-RESULT.md) (layout section).

**Finding.** On the reinstalled <host> the swap partitions have an empty PARTLABEL; the names `vbpub-swap1..4` are swap filesystem labels set by `mkswap -L` (`debian_install_v2/installer.py:3405`, vda6..vda9). The plan preview prints the name under a `PARTLABEL` column (`plan_preview.py:204`, `installer.py:752`), and [div2-reinstall-RUNBOOK.md](docs/evidence/div2-reinstall-RUNBOOK.md) lists "swap `vbpub-swap6..9`" as PARTLABELs. The swap-benchmark wrapper (see DIV2-BL-04) checks `PARTLABEL ^vbpub-swap[0-9]+$` and would refuse on this host. Only the raw iobench window depends on it, but the preview and runbook mislead.

**Fix.** Either write the PARTLABELs when partitioning (and keep the filesystem label), or key the wrapper and runbook on the fstab PARTUUID plus the filesystem label and rename the preview column. Decide one rule and state it in README "Disk layout".

**Close when.** The preview, runbook and wrapper name the same identifier and a test pins it against a planned layout.

## FIXED d951f417d (DIV2-BL-02, 2026-11-08): a failed advisory step keeps only the first line of the error

**Status:** FIXED 2026-11-09 in `d951f417d`. Failed advisory CPU and I/O steps write their complete already-redacted output under `<state_dir>` and name the log in the warned detail. Focused tests cover full text, notification, and an unwritable-log fallback.

**Priority:** minor. Source: [div2-reinstall-host-RESULT.md](docs/evidence/div2-reinstall-host-RESULT.md) (post-install check 5.2), [host-reinstall-EXEC-LOG.md](docs/evidence/host-reinstall-EXEC-LOG.md) (17:25Z).

**Finding.** An advisory step that fails is recorded as `warned` with a one-line detail: for iobench `str(exc).splitlines()[0][:400]` (`installer.py:3290`), for the CPU benchmark `_cpu_benchmark_failure_text` (`installer.py:3105`). On <host> the recorded state was only `iobench failed (advisory): action failed (1): run iobench veryquick on /dev/vda5`; the actual cause (`fio_option_unsupported`, DIV2-BL-03) was found only by re-running the runner by hand. The tool's stderr is not kept anywhere on the host.

**Fix.** Write the full (already redacted) stderr of a failed advisory action to a per-step log under `/var/lib/vbpub/bootstrap/` and put its path in the step detail and the notification.

**Close when.** A test with a failing advisory command shows the log file with the stderr and the path in the `warned` detail.

## OPEN — KNOWN ISSUE (DIV2-BL-03, 2026-11-08; operator decision 2026-11-10): io.cost remains unconfigured until a safe benchmark target exists, and the installer must record a named refusal

**Priority:** major for the installer's measured io.cost path. Source: [div2-reinstall-host-RESULT.md](docs/evidence/div2-reinstall-host-RESULT.md) (benchmark step), [host-reinstall-EXEC-LOG.md](docs/evidence/host-reinstall-EXEC-LOG.md) (17:25Z diagnostic; logs `<host>/iobench-diag-20261108/`).

**Finding.** `tools/iobench-install-runner --target /dev/vda5 ...` on the reinstalled <host> exited 1 in under a second with `Refusal: fio_option_unsupported: the installed fio does not document the option 'exclusive_open'`. Debian 13 ships fio 3.39-1: `fio --cmdhelp=exclusive_open` answers "No such command" and a `--exclusive_open=1` job is "unrecognized option". iobench hard-requires it (`devio.require_fio_option` from `stages.s0`) and its docs claim fio >= 3.30, so the installer's veryquick step can never pass on this OS image: `steps.io_benchmark = warned` and `steps.iocost = skipped: "io.cost not configured: no valid io benchmark result"` on every Debian 13 install. The root cause is an iobench item (`libraries/iobench/BACKLOG.md` IOB-BL-28); this entry tracks the installer's side.

**Fix.** After iobench fixes the exclusive-open mechanism, add a Debian 13 VM acceptance case (the `r1-vm-real-commit` style lane) where the step must reach `success` or fail with a named, recorded cause. Until then document that io.cost stays unconfigured on Debian 13 installs and must be measured later.

**Operator decision 2026-11-10.** io.cost stays unconfigured on both <host> and <host> as an explicit KNOWN ISSUE until a safe benchmark target exists. The installer benchmark must record a named refusal; it must not substitute generic earlier output, cache-inflated values, or a guessed model.

**Minimal solution.** Keep the raw-write refusal as the default. In the persisted install result and step detail, record a stable named reason such as `io.cost_unconfigured_no_safe_benchmark_target`, together with the exact runner refusal or missing prerequisite that produced it. Do not emit a vague "no valid result" or silently mark io.cost configured. Once WP-10 provides the held-claim writer for the `iobench-raw` target and DIV2-BL-05 supplies the revised `veryquick`/`quick`/`standard` ladder, revisit the decision in a separate reviewed change; until then the hosts are explicitly known-unconfigured.

**Close when.** An install on a Debian 13 image completes the `io_benchmark` step or records a documented, named refusal in the README and persisted result, and both designated hosts show that named known-unconfigured state rather than an inferred io.cost setup.

## FIXED d951f417d and follow-up (DIV2-BL-04, 2026-11-08): swap-benchmark window wrapper hardening (review 3 nits N1-N5)

**Status:** FIXED 2026-11-09. The original N1-N5 hardening remains in place. Review blocker B2 is fixed: `end` reads the selected device's fstab options, restores only that device, and verifies its identity and active state. A fake-root test proves a second configured but inactive swap remains inactive.

**Priority:** recommended (N1), nit (N2-N5). Source: [swap-bench-window-REVIEW3.md](docs/evidence/swap-bench-window-REVIEW3.md) (verdict READY-FOR-<host>, no blocker). The wrapper is maintained in `tools/`; its former unmerged external runbook patch is retired because it described an older draft and unsafe restore scope.

**Finding and fix.**
1. N1 (script lines 78-85): the match `swapoff $dev ` misses a foreign swapoff given as `swapoff -v DEV`, `swapoff -a`, `swapoff A DEV`, or a by-partuuid/by-uuid/by-label name (systemd's own swapoff uses a by-partuuid path). `end` can then say "nothing to restore" and archive the state while that swapoff still runs (no wipe or mkswap risk; they need `active` false). Treat any live non-zombie `swapoff` process as relevant (scan `/proc/[0-9]*`, `comm = swapoff`, non-empty cmdline) and reword the "nothing to restore" text (line 196) so it does not claim as fact that no swapoff is running.
2. N2 (line 68): add `cat tr ls basename id date mv rm` to the required-tool loop (a missing `cat`/`tr`/`ls` makes `swapoff_running` and the holders check fail open); optionally pin `PATH=/usr/sbin:/usr/bin:/sbin:/bin` after line 24.
3. N3 (lines 170-173): add `lsblk -dno PARTLABEL` to the F5 identity re-check and use the low-level probe `blkid -p -s PART_ENTRY_UUID` instead of the cache-capable `blkid -s PARTUUID`.
4. N4 (runbook): run `begin --commit` inside `tmux`/`screen` (or `nohup`) so an SSH drop cannot SIGHUP the script between swapoff and wipe; a kill leaves a safe state but needs a manual `end`.
5. N5: the STILL RUNNING message says "the orphan holds the lock", true only for begin's own swapoff; a foreign swapoff does not hold `.lock`.

**Close when.** Each item is applied to the adopted wrapper or runbook, or struck out with a reason; the wrapper is in the repo with its rehearsal evidence referenced.

## OPEN (DIV2-BL-05, 2026-11-10): the install benchmark needs the rev 4.1 profile ladder and a safe target/fallback boundary

**Priority:** major; blocks the default install benchmark and the live raw-target acceptance tracked by IOB-BL-39. Source: operator decisions 2026-11-10 and `reports/iobench-iogovmon-DESIGN-2026-11.md` rev 4.1, sections 3, 5, 6, 8 and 9.

**Problem.** The installer benchmark is currently advisory and does not implement one selectable rung family. The current raw writer correctly refuses unless it can retain its exclusive claim through I/O (`Host.write_range`), so raw-target runs cannot begin safely until WP-10 closes that gap. The earlier target proposal is superseded by design rev 4.1: install-time measurement must use a selectable profile ladder and provision the model conservatively, while iogovmon handles the moving target after install.

**Operator decision 2026-11-10 (design rev 4.1, binding).**

1. `run_io_benchmark` defaults to true. Every install selects one profile from `veryquick` (about 3 min), `quick` (about 7.5 min, the DEFAULT), or `standard` (about 30-35 min), with outer timeouts of 5, 12 and 45 min. All rungs use the same nested cell family and measure the same quantities at increasing length.
2. After WP-10, the benchmark target is the reserved `iobench-raw` partition, with the writer holding the `O_EXCL` claim for the complete raw stages. <host> sets `iobench_raw_gb: 32`.
3. Before WP-10, an ext4-root file may supply only the derived numbers whose ext4 verdict in the bounded study table is `✓` live-with or `⚠` iogovmon-must-correct. A btrfs file is never used for the model. If no permitted target exists for a number, keep the stable named `io.cost_unconfigured_no_safe_benchmark_target` refusal; never substitute cache-inflated output, generic earlier output, or a guessed model.
4. During every run, `io.cost` is unconfigured and every cgroup IO control is off, with the state asserted by WP-3. The interim io.cost model is coarse and QoS stays off.
5. The bounded host study determines the verdict table and each rung's measured error by number and target type. It runs on <host> after the planned D2 install with host safety and identity checks; it is capped at 500 GB written, about 3 h active IO and 12 h elapsed, and uses adaptive repeats. No install or host change proceeds without the required operator GO.
6. iogovmon supplies continuous adjustment after install: it re-benches the configured rung on `iobench-raw` (default `quick`) weekly and on events, at most once per 24 h and only in a quiet window. Adoption is bounded to ±25 % per step unless two confirming runs agree; `capability_shift` freezes, re-benches, re-baselines, and only unfreezes after `stable_days`. Distress-gated adjustments retain their bounds, cooldown and rollback.

**Minimal solution.** Implement the installer side in coordination with its owning dependencies:

1. Add `io_benchmark_profile = "quick"` with the allowed `veryquick` and `standard` values and the design's timeout table. Preserve explicit operator selection; no install silently changes rungs.
2. Route `quick`, or the selected rung, through the iobench runner on `iobench-raw` once WP-10 exists. Until then choose the ext4 fallback per derived number from the study verdict table, or record the named refusal when the number has no permitted target.
3. Assert `io.cost` and every cgroup IO control are off before measurement, keep QoS off for the interim model, and retain the complete redacted result and refusal in the persisted install result.
4. Expose the rung summary line from the study table in the wizard. Add the final `fstrim -av` and keep `autodefrag` off.
5. Depend on the bounded study and WP-20's implementation of the nested cell family; do not infer rung accuracy or fallback eligibility from old file-target results. IOB-BL-39 owns the live rung and raw-target evidence; WP-21 owns the iogovmon scheduler and bounded adoption implementation.

**Where.**

- `scripts/debian-install-v2/debian_install_v2/config.py`, `installer.py`, `wizard.py`, and `tools/iobench-install-runner`: profile selection, target/fallback choice, guards, and persisted result.
- `scripts/debian-install-v2/debian_install_v2/tests/`: default/selection, timeout, target, refusal, IO-control-off, fallback-verdict and no-I/O-mutation tests.
- `scripts/debian-install-v2/README.md`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md` and `CHANGES.md`: rung meanings, default, safe-target boundary, allowed fallback numbers and operator sequence.

**Acceptance idea.** Fake-root tests prove `quick` is the default, each named rung is selectable with its timeout, `iobench-raw` is preferred through WP-10, the ext4 fallback is offered only for numbers whose study verdict permits it, and every other number records the named refusal. They assert `io.cost` and all cgroup IO controls are off for the full measurement and that the step changes no host IO policy. Removing the default, bypassing WP-10 when available, allowing an unlisted fallback, or skipping an IO-control assertion must fail a focused test. After WP-10, a live <host> install completes the selected rung on `iobench-raw`; before then, <host> records either the allowed fallback for its exact permitted results or the named refusal.

**Close when.** The default and selectable ladder, WP-10 target, study-gated ext4 fallback, IO-control-off assertions, named refusal, final fstrim and iogovmon handoff are covered by focused tests; live evidence on both designated hosts completes the selected rung or records the named refusal; and the operator decision is reflected in README, CONSUMERS, DESIGN-GUIDE and CHANGES `## [Unreleased]`.
