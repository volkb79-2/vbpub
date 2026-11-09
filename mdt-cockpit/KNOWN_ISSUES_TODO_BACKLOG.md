# mdt - known issues, TODO, and backlog

## MDT-001 - requirements for `aider-chat`

starting `MDT_IMAGE_VERSION="20261002"`, `IMAGE_MANIFEST="/usr/local/share/mdt-cockpit/manifest.md"` gives this startup info

```
[INFO] Using requirements file: /workspaces/dstdns/requirements.txt
ERROR: pip's dependency resolver does not currently take into account all the packages that are installed. This behaviour is the source of the following dependency conflicts.
aider-chat 0.86.3.dev53+g5dc9490bb requires aiohttp==3.13.3, but you have aiohttp 3.14.3 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires charset-normalizer==3.4.6, but you have charset-normalizer 3.5.2 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires click==8.3.1, but you have click 8.5.0 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires fastapi==0.135.1, but you have fastapi 0.142.2 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires packaging==26.0, but you have packaging 26.3 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires pydantic==2.12.5, but you have pydantic 2.13.5 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires pydantic-core==2.41.5, but you have pydantic-core 2.46.5 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires pygments==2.19.2, but you have pygments 2.21.0 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires requests==2.32.5, but you have requests 2.34.2 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires rich==14.3.3, but you have rich 15.0.0 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires urllib3==2.6.3, but you have urllib3 2.8.0 which is incompatible.
[SUCCESS] Python packages installed from /workspaces/dstdns/requirements.txt
```

## MDT-002  - docker daemon default-address-pools

host-setup should configure
```json
{
  "default-address-pools": [
    { "base": "10.240.0.0/16", "size": 24 }
  ]
}
```

## MDT-003 - integrate `fs.inotify.max_user_watches`

**CLOSED 2026-10-26.** install.sh installs `etc/sysctl.d/99-inotify.conf` as `/etc/sysctl.d/99-mdt-inotify.conf` and
applies it; check.sh verifies the live value and the persisted file; the devcontainer templates and vbpub's own
`.devcontainer/devcontainer.json` set `files.watcherExclude`/`search.exclude` for `.worktrees`, `.run-gate` and
`pytest-of-*` (measured: ~312k of 524288 watches in use, ~284k directories in this repo, mostly worktrees and gate output).
See `host-setup/README.md`, "inotify watch budget".

## MDT-027 - terminal key profile: verify, pick the documented default, and add remaining image tools

Opened 2026-10-27. (1) `templates/vscode/` ships two opt-in key profiles and a client-side
`keybindings.json` snippet; whether `workbench.action.terminal.sendSequence` keybindings still fire with
`sendKeybindingsToShell` enabled, and whether `terminal.integrated.enableKittyKeyboardProtocol` exists in the
deployed VS Code, are UNVERIFIED. Run the README recipe on the Windows client, then name one profile the
documented default. (2) `ov` (.deb), `dry` (binary), `yazi` (glibc zip; sha256 from the GitHub release asset `digest` because upstream
publishes no checksum file) and `sss` (github.com/azlux/sss, an unreleased bash script) are now WIRED with NO hard
pins (operator 2026-10-27): each version is re-selected at build time as the newest release at least
`MDT_TOOL_MIN_AGE_DAYS` (default: the estate `age_window_days`, 14) old, and `sss` as the newest default-branch commit at least that old with its
sha256 recorded at staging (first-use trust). The GitHub API shapes (`assets[].digest`, `commits?until=`) were checked live
on 2026-10-27 (`gh api`). Staged and verified in
`scripts/stage_tool_artifacts.py`, installed and re-verified in the `Dockerfile`, listed in the manifest and
`tests/runtime/test_image_contract.py`. Status 2026-10-27 (later the same day): the tools were built for real in the
`source-1bc10c5a6b68` release build (the first real build found and fixed two defects: the manifest RUN did not source
`tool-versions.env`, and multi-line `--version` output was recorded verbatim). Item (1), the key-profile verification on a
client, and the runtime-check results of item (3) against the published image remain OPEN. `dive` was already installed. (3) `tmux` and `broot` come from Debian packages; the `tmux.conf`, `gitconfig`,
`inputrc` and `vimrc.local` defaults have runtime tests in `tests/runtime/test_system_defaults.py` that
have not been run against a built image.

## MDT-004 - move to a persisted `/home/vscode` in mdt-cockpit

**CLOSED 2026-10-18 (MDTC-01, Phase 1; runtime acceptance is MDT-010).** Redesigned as a
fresh-home layout instead of a migrated one:

- the home is `~/mdt-cockpit-home/` (`0700`), `/tmp` is `~/mdt-cockpit-tmp/` (`1777`), any further
  source is `~/mdt-cockpit-<purpose>`; a new home **starts empty**;
- **no migration**: `migrate_persistent_home.py`, its tests, the legacy-source refusal and the
  completion-marker logic are deleted. The bootstrap prints one informational line when the old
  `~/mdt--mounted-folders/` holds files and the new home is empty; users copy by hand
  (`docs/HOME-PERSISTENCE.md`, "Moving from modern-debian-tools-python-debug");
- image-managed trees (`/opt/mdt`, `/srv`) are group `mdt` (GID 2750, setgid dirs, group-writable
  files) so the re-numbered runtime user can update tools without `sudo`; the build asserts the
  contract (`mdt-perms check`);
- one `PATH` (Dockerfile `ENV`; `~/.local/bin` last), `NPM_CONFIG_PREFIX=/opt/mdt/npm`,
  `UV_PYTHON_INSTALL_DIR=/opt/mdt/python`;
- every default is wired system-wide (`/etc/bash.bashrc`, `/etc/zsh/zshrc`, `/etc/nanorc`,
  `/etc/htoprc`, `/etc/mc/mc.ini`, `/etc/xdg/nvim`, `LESSOPEN`); the finalizer's only home write is a
  comment-only `~/.zshrc` stub. The `~/.venv*`, `~/.oh-my-zsh`, `~/mdt-manifest.md` compatibility
  symlinks and the per-tool `.env` symlinks are gone;
- the namespace is `mdt-cockpit` (`/usr/local/share/mdt-cockpit`, `~/.config/mdt-cockpit`).

## MDT-011 - the shipped "NvChad" tree is the core repository, not a starter

`NVCHAD_VER` stages the `NvChad/NvChad` repository tag. Its `init.lua` is the placeholder
`print('use this along starter!!!')` - the real configuration is the separate `NvChad/starter`
repository. The previous layout seeded that tree as `~/.config/nvim` and the move to
`/etc/xdg/nvim` (system-wide) preserves the behaviour as it was. Decide: stage the starter (and let
`customization/nvim/sysinit.vim`'s `configs.lazy` lockfile redirection apply), or ship plain
defaults. Needs network on first run either way (lazy.nvim bootstraps its plugins).

## MDT-005 - adopt `cli-extended` for grammer, wizard, sematic check, ...

vbpub has a library to prohibit handrolling solutions every CLI needs. adopt it.

## MDT-006 — cgprofile observer directory setup must fail closed

**Status: implementation and behavioral oracles are in the contract-2
candidate; registered host-setup gate and live host verification pending.**

`install.sh` previously printed a warning and continued when
`systemd-tmpfiles --create` failed. It could therefore report a successful host
setup while `/run/cgprofile/observer` was missing or had the wrong owner, group,
or mode, causing later observer socket mounts to fail or expose the wrong
access boundary.

The installer now stops on tmpfiles failure and verifies the resulting
directory against the resolved `cgprofile-observer` group and exact
`0751 root:cgprofile-observer` contract before proceeding. Before writing, it
refuses symlinks, non-directories, a missing group identity for an existing
path, and drifted owner/group/mode on an existing observer directory. The
host-check also verifies the observer group differs from Docker, the live path
has the required metadata, and the complete active sysusers/tmpfiles rule sets
match the contract (comments and blank lines are ignored; extra active
directives fail closed). `test-render.sh` has behavioral cases for mismatch refusals,
idempotence, and preserving a live control socket; the registered host-setup
gate and live post-install `stat` plus `mdt-host-check.sh` are the runtime
oracles.

## MDT-007 — publish host setup as a self-contained, updateable adopter bundle

**Status: PLANNED (2026-10-13).** The current MDT release is an OCI image.
An adopter who does not clone the MDT repository cannot run the host-side
setup: `host-setup/install.sh` consumes adjacent scripts, unit templates,
configuration examples, and helper files. A Python wheel by itself would not
carry this operational tree or provide the right host installation/update
boundary.

**Proposed distribution:** add a CMRU `bundle` artifact alongside the image
and use CMRU's generated standalone `get.py` installer for install, update,
status, and rollback. The bundle should contain the complete supported host
setup tree (installer, wizard, checks, every referenced helper, systemd and
tmpfiles/sysusers templates, config examples, and adopter documentation),
plus the MDT CLI/runtime and its required dependency closure. As part of
MDT-005, include the `cli-extended` wheel in that closure if MDT's host CLI
adopts it. The installer should place immutable versioned content under an
MDT-owned data directory and update a stable launcher/pointer transactionally;
the privileged host-setup action remains an explicit operator command.

A wheel can be an additional artifact for Python-only integrations, but a
wheel alone is not the host-setup distribution: it must also carry or fetch the
shell scripts, templates, configs, and helper assets. The normal adopter path
is to install the verified bundle on the **host**, then invoke its explicit
`mdt` host command there. The OCI devcontainer image remains a separate
artifact and may include the same version of the CLI for cockpit diagnostics.
Today `scripts/mdt_cli.py` is a shared `argparse` helper imported by individual
scripts, not a public `mdt` executable or a wheel. Define that public entry
point as part of this work. `cli-extended` is not currently imported by
`mdt_cli.py`; it becomes a bundle dependency only if MDT-005 changes that.

Provide a pasteable install command through CMRU's generated `get.py`. The
direct `curl ... | python - install` form is currently documented by CMRU as
unverified: the bundle's signature does not verify the bootstrap script before
it runs. MDT's root-level setup must use the secure path CMRU supports; do not
present an unchecked pipe as a verified install.

**Required decisions and oracles:**

- Define supported host OS/Python versions and whether the bundle carries
  platform-independent or versioned wheel assets; prefer one bundle if its
  complete dependency closure is portable.
- Ensure no repository checkout, editable install, PyPI lookup at install
  time, or network access from `install.sh` is required after bundle
  verification.
- Preserve the host's existing `/etc/mdt/host-setup.env` and live state during
  update; include a tested rollback path and never replace an existing
  bind-mounted runtime directory inode during a service restart.
- On a clean supported Debian host with no MDT checkout, install from the
  generated `get.py`, run the host-side doctor/setup command, update to a
  second release, and roll back. Verify the installed file inventory against
  the signed manifest and verify the `mdt` CLI can import its bundled
  `cli-extended` dependency once MDT-005 is complete.

CMRU owns generic bundle/get.py lifecycle behavior; MDT owns the artifact
contents, host-setup entrypoint, compatibility contract, and live acceptance
for this adopter use case. Coordinate the bootstrap UX with CMRU KI-76.

## MDT-008 — preserve the BuildKit host bind-directory inode across service restarts

**Status: implemented on the current candidate; registered gate and live bind
verification pending (2026-10-13).** `mdt-buildkitd.service` used
`RuntimeDirectory=mdt-buildkitd` with the default stop cleanup. Restarting the
service removed `/run/mdt-buildkitd` and made systemd create a new directory
inode. Existing devcontainers retained a bind mount of the old, now-empty
directory, so the restarted daemon socket stayed invisible inside the cockpit
until its container was recreated.

**Required behavior:** preserve the runtime directory for the host boot, so
service stop/restart never replaces an existing bind source. Start-up may
repair its owner/mode and remove the stale `buildkitd.sock` entry; it must not
delete and recreate the directory. `/run` being tmpfs means a host reboot
still creates a fresh directory on the next service start.

**Oracle:** rendered-unit tests require `RuntimeDirectoryPreserve=yes`; a live
acceptance check records the host directory's device/inode and a running
devcontainer's bind source before and after `systemctl restart
mdt-buildkitd.service`, then proves the inode is unchanged and the socket is
visible through the existing container mount. Also verify required owner/mode
repair and stale-socket replacement without changing the directory inode.

## MDT-009 — keep test frameworks out of the cockpit and make `mdt doctor` detect drift

**Status: OPEN (filed 2026-10-13).** The devcontainer is the cockpit; registered
gates and test suites run in `tester-unified`. The current toolkit dependency
set installs pytest and pytest plugins into the cockpit's Python environment,
so an accidental `pytest` or `python -m pytest` can look like an acceptable
local gate even though it is running in the wrong container. There is no
reliable inverse `requirements.txt` syntax that prevents a user or another
installer from adding those packages later.

**Expected direction:** remove pytest, Hypothesis, mutation and coverage test
dependencies from the default cockpit environment when no cockpit feature
needs them; keep them in the dedicated tester image. Add an explicit runtime
role marker to the devcontainer so `mdt doctor` can identify the cockpit
without guessing from a path or package set. When invoked there, `mdt doctor`
checks the configured cockpit interpreter for the declared test-only package
set and reports any installed packages as an architecture-policy error. The
host-side `mdt doctor` remains usable for MDT host setup and does not apply the
cockpit-only rule. This detects later manual installs; it is not an OS-level
restriction on what a user can install.

**Oracles:** a clean cockpit image lacks the test-only packages, while
`tester-unified` retains the exact test dependency closure it needs; `mdt
doctor` reports a package installed into the cockpit interpreter and passes
for the same package in the dedicated tester image; running the host-side
doctor does not misclassify host packages as cockpit drift. A fresh cockpit
container can still inspect repositories and drive CIU/CMRU/Run-Gate without
pytest or Hypothesis in its venv.

<!-- INT-01 numbering (2026-10-19): the mdt-cockpit branch filed MDT-006..MDT-010 while the
cgprofile-v1.0.0 lineage (published) filed a different MDT-006..MDT-009 in the same project. The
published ids keep their numbers; the branch's items are MDT-010 (acceptance probes, was 006),
MDT-011 (NvChad tree, was 007), MDT-012 (image-runtime in release flow, was 008), MDT-013
(resolve-devcontainers via cmru versions, was 009) and MDT-014 (aider, was 010). -->

## MDT-010 - run acceptance probes against the built image artifacts

**CLOSED 2026-10-18 (MDTC-01, Phase 2).** The `image-runtime` lane (`run-gate image-runtime`,
driver `tests/runtime/`, design in `docs/DESIGN-GUIDE.md` "Built-image acceptance is a separate test
boundary") starts real containers from the exact native and VS Code image refs and checks the final
container's user, process environment and mounted-home behaviour: the image contract, one `PATH`
in six shells, group-`mdt` writes under a re-numbered uid (with a negative control), the finalizer
on a fresh home, silent interactive shells, every system-wide default with an empty home, and
local-dev provenance. Its first real run found two defects the unit tests could not (`/srv`
re-moded by Debian's base-files postinst; the finalizer calling `os.getlogin()` without a tty).

Not covered, deliberately: the Dockerfile's install-time version probes for the apt-installed tools
(still best-effort, recorded in the manifest), anything needing the network (updates, first-run
plugin bootstraps), and GUI/IDE behaviour. Filed as MDT-012: the lane is not yet part of the
release flow, so `cmru release` does not run it against the images it publishes.

## MDT-012 - run `image-runtime` as part of the release flow

**FIXED 2026-11-04 (fix-mdt-1-20261104).** New lane `image-runtime-built` (`scripts/run_image_runtime_lane.py`): it takes the native
and VS Code layouts the `prepare` step left (bound to the build by `transaction.json`, the `oci-push` rules), imports them under
private tags, runs `tests/runtime` on them and removes the tags by exact name. `cmru.toml` `run-tests` runs it after `oci-push`.
`image-runtime` itself stays the manual lane with explicit refs. Not yet exercised by a real release (needs a full build).

`image-runtime` needs the exact image refs (`MDT_RUNTIME_IMAGE_NATIVE`, `MDT_RUNTIME_IMAGE_VSC`).
A release builds and pushes images but never runs the lane against them, so a published image can
still skip the runtime contract. Decide where the refs come from (the build's saved state) and wire
the lane between build and push; until then it is run by hand against a local-dev or release build.

## MDT-013 - `resolve-devcontainers-release.py` consumes `cmru versions` JSON

Once cmru's `versions` has the OCI fixes in `handoff/CMRU-GAPS-2026-10-19.md` (G2 digest on semver
records, G3 no 5000-tag refusal, G6 Docker credential store), `scripts/resolve-devcontainers-release.py`
keeps only its devcontainers advisory (which devcontainers image release a base tag belongs to) and takes
the tag/digest selection from `cmru versions check --json` instead of its own `fetch_registry_tags` /
`select_latest_stable_tag` scanner. Until then the scanner is the design input for those cmru items. It also consumes the devcontainers OCI
record as the `FROM` digest, with an equality test as `tester-base/tests/test_pins.py` does; the eight
`oci.*` targets in `cmru.toml` are labelled ADVISORY until then, and `scripts/test_ai_cli_versions.py` fails
if a build input consumes them while the label stands (change label, docs and test together).

## MDT-014 - aider returns to a vetted release

`aider-main-pin.toml` pins an exact commit of upstream `main` because aider's newest release needs
Python <3.13 (MDTC-03). When an aider release supports Python 3.14: delete the file, declare
`pypi.aider-chat` in `cmru.toml`, read it in `_vetted_ai_cli_versions` with the other three, and drop the
documented exception from README / DESIGN-GUIDE.

## MDT-015 - a version waiver has no expiry (INT-01 B-06, 2026-10-19)

**FIXED 2026-11-04 (fix-mdt-1-20261104).** `_check_record` refuses a waiver once the overridden release is more than
`WAIVER_MAX_AGE_DAYS` (14, one age window) old; the error names `cmru versions resolve`. Test:
`test_a_waiver_expires_after_one_age_window`.

`_check_record` marks `<TOOL>_WAIVED` and the manifest row says so, but nothing makes a long-lived
waiver fail or age out: an override released after the age cutoff waives the 14-day window for that
tool's dependencies indefinitely. Give a waiver an expiry or a ceiling (for example refuse a waiver older
than the record age limit) and test it.


## MDT-016 - dev.slice IO governance was not in effect and certified itself (2026-10-19)

**CLOSED 2026-10-19 (MDT-HS-01).** Production game servers (`wings.slice`) lagged while dev gates ran.
Measured on the live host: (1) after a kernel update the reconcile rejected the baseline and ran its
clear path, `set-property --runtime dev.slice IOReadBandwidthMax= ...`; in systemd an empty assignment
resets the whole list including unit-file values, so `dev.slice` ended with empty runtime drop-ins and an
empty `io.max` (no IO cap) while the script logged "static unit fallback is authoritative"; the timer
re-applied this every 60 s. (2) The stored baseline (4.3 GB/s read, 2 GB/s write, 94k/68k IOPS; fio 120 s,
numjobs=1, 16 GiB file) was hypervisor-cache inflated: the virtio `vda` (write back, rotational=1) really
saturated at ~90% util, ~1,500-1,800 merged writes/s, ~20 MB/s, w_await 35-60 ms, so 60% of it never bound.
(3) The resolved device (`/dev/mapper/gstammtisch--vg-root` -> whole disk `vda`, 254:0) was invisible, and
nothing checked that the swap partitions (vda6, vda7) sat on a capped disk. (4) `DEV_SWAP_CASCADE_PCT=80`
gave dev.slice 59 GB and each child 47 GB of swap on a 16 GiB host; a gate that outgrew its memory thrashed
swap. (5) The operator's listing script tested `[ -s io.max ]`; cgroupfs files report size 0.

Fixed: no clear path (explicit fallback re-asserted, empty assignments only without a device); kernel
`io.max` read back after every apply, non-zero exit on mismatch; operator-pinned `DEV_IO_*_MAX` keys (min
with a baseline); baseline schema 3 + plausibility refusal; `UNCAPPED SWAP` warning in the reconcile and
`mdt-host-check.sh`; `mdt-cgroup-io-limits.sh` shipped. Design: `docs/DESIGN-GUIDE.md` D-HS-01-1..4.
Tests: `host-setup/tests/test_io_governance.py`.

## MDT-017 - measure a sustained, cache-defeating IO baseline (D-HS-01-2)

`mdt-io-baseline.py` only refuses an implausible baseline; it still measures through the hypervisor cache.
Redesign: O_DIRECT with `--fsync`/`--end_fsync` for writes, a working set larger than the cache the host
can see, and a steady-state tail measurement (last N seconds) recorded in the results. This changes the
shared `scripts/debian-install-v2/tools/iocost_coef_gen.py` (and its vendored patch series), which is
outside `mdt-cockpit`; coordinate with debian-install-v2 (same item filed there in `TODO.md`). The
plausibility refusal is shipped in both consumers (`mdt-io-baseline.py`, `iocost-calibrate.sh`). Until the
redesign, pinned `DEV_IO_*_MAX` are the recommended source on a virtual disk.

## MDT-018 - dev-tier swap sizing default (D-HS-01-3)

**CLOSED 2026-10-19 (decided by the operator, amendment A1).** Swap stays ample (the 80% cascade is kept).
The protection is a tight IO cap on the gate tier (`DEV_GATES_IO_{RBPS,WBPS,RIOPS,WIOPS}_MAX`, and the
same four keys per tier): swap-out IO is charged to the page owner's cgroup (per kernel source, not measured here: swap writeback bios are associated with the page owner's blkcg via `bio_associate_blkg_from_page` in `mm/page_io.c`). A throttled gate runs
slowly, an infrastructure-classified slowdown. `DEV_SWAP_MAX` / `DEV_GATES_MEMORY_SWAP_MAX` stay
available but are not the protection.

## MDT-019 - a transient findmnt failure strips per-container IO caps for one sweep (pre-existing)

**Status:** FIXED 2026-11-04 (fix-mdt-3): the sweep (`mdt-dev-governance-reconcile.sh`) keeps the caps when a valid baseline exists but no device was discovered; a missing baseline still clears. The events watcher's startup path is unchanged.

With no block device discovered (for example a transient `findmnt` failure), `_mdt_derive_watcher_caps`
sets `WATCHER_SKIP` and `_mdt_clear_container_caps` removes MDT's per-container transient IO caps for that
one run; the next sweep or the Docker-events watcher re-applies them. The dev.slice and tier paths are
guarded since MDT-016 (no write, ERROR when limits exist); the container sweep is not. Not fixed here: the
existing no-baseline sweep test depends on the strip, and a guard needs the same "carries limits" probe per
scope. Fix: skip `_mdt_clear_container_caps` when the device is merely undiscovered, keep it for a missing
baseline.

## MDT-020 - no lane reproduces the live LVM/device-mapper IO-cap defect (MDT-HS-04 review H-33)

**Incident record (MDT-HS-06, 2026-10-20 10:00:01 UTC, gstammtisch, reconcile pid 255787):** the `install.sh --wizard` rollout switched the
systemd spelling from `/dev/mapper/gstammtisch--vg-root` to `/dev/vda`. dev.slice hit the stale-entry mismatch and the self-heal reload
verified it; `dev-gates.slice` then showed an empty kernel `io.max`, "reload suppressed: last self-heal reload ... (1s ago)", "IO governance is
NOT verified", install exit 1, dev-gates uncapped. A manual `daemon-reload` did not restore dev-gates' kernel `io.max`; the next reconcile
(set-property again) verified all slices. Fixed by MDT-HS-06 (batch apply/verify, one reload, re-apply every unit). The VM lane now also runs a
two-spelling migration (partition node vs whole-disk node of the guest root, vm-governance-acceptance.sh step 9), real systemd; the
device-mapper root topology itself is still NOT covered, so this item stays open.

The vm-system guest's root is a plain partition, so nothing exercised the case that failed live
(2026-10-20, systemd 257: `IOReadBandwidthMax=/dev/mapper/<lv> N` on dev.slice left the kernel `io.max`
empty, and two spellings of one disk ended with the last entry's value). MDT-HS-04 fixes the spelling and
covers it with fakes only; the cause of the silent mapper drop is still unexplained. Add a VM-lane case INSIDE
THE GUEST ONLY (never on the host, never a privileged container: see the loop-device host-hang incident of
2026-09-09): stack a device-mapper linear device on a loop device or a second virtio disk, point
`IO_DEV_PATH` at the mapper node, run the reconcile, and assert that the whole disk's kernel `io.max` holds
the cap, plus a deliberately stale runtime entry under another spelling (`/dev/vda`) that must be gone
afterwards. Also assert in the guest, after a reboot and before the first reconcile, that the rendered
static `dev.slice` line produced an `io.max` entry. Cleanup must `dmsetup remove` and detach the loop in the
guest before the lane ends.

## MDT-021 - the wizard and host-check do not know the memory watermarks or watermark boost (DOCS-FOLD-01, 2026-10-20)

**Status:** OPEN, recorded only; not implemented.

`vm.watermark_boost_factor` was found stuck-boosting on gstammtisch (2026-10-20 14:09 UTC: ~860 MB held free while all
reclaim ran in the allocating threads, `node_unreclaimable = 1`); the operator set `vm.watermark_boost_factor = 0` and
`vm.watermark_scale_factor = 50` by hand (persisted 23:16 UTC). Mechanism, numbers and recovery:
`scripts/gstammtisch-guide/MEMORY-ARCHITECTURE.md`, "Watermark boost and the 14:09 UTC failure mode"; the decision and
the wizard follow-up: `docs/DESIGN-GUIDE.md`, "Memory watermarks and watermark boost are kernel settings this
host-setup does not own yet". Follow-up:
- the wizard (and `scripts/debian-install-v2`, which writes the same sysctls) should ask for or derive
  `watermark_boost_factor` next to `watermark_scale_factor`, suggesting 0 on swap-backed, memory-protected hosts, with
  that reasoning;
- `mdt-host-check.sh` should WARN when `node_unreclaimable` is 1, or `watermark_boost` is non-zero for a long time
  (per zone, `/proc/zoneinfo` and `/proc/vmstat`). Note: how `node_unreclaimable` was reached on 2026-10-20 is
  unexplained (v7.2 does not count a kswapd failure for a boosted pass, `vmscan.c:7237-7242`), so the WARN is a
  symptom check, not a proof of the boost mechanism.

## MDT-022 - a timed-out lister test left orphaned processes running for about 39 hours (2026-10-19 to 2026-10-20)

**Status:** FIXED 2026-11-04 (fix-mdt-3): the lister reads through `timeout` (`MDT_CG_READ_TIMEOUT`, default 5 s) and the tests run it with `start_new_session` plus `killpg`.

**What happened.** A test of `host-setup/scripts/mdt-cgroup-io-limits.sh` in worktree `mdt-hostsetup-io-20261019` left the
chain `bash -x mdt-cgroup-io-limits.sh dev.slice` → `cat …/io.max`, plus `column -t`, running from 2026-10-19 08:27 to
2026-10-20 ~23:30 UTC (about 39 h), all reparented to PID 1 after the test's timeout. In the fixture directory
`/tmp/tmpo1i6get1` the file `dev.slice/dev-gates.slice/io.max` was a named pipe (`prw-r--r--`), and `cat` on it blocked
forever. The controller killed the processes and removed the fixture on 2026-10-20 ~23:30 UTC.

**Where.** `host-setup/tests/test_io_governance.py:548` (`os.mkfifo(path)` in `ListingToolTests._fifo_file`, lines
546–560): each FIFO is served by a daemon thread of the test process, so once the test process dies or times out
nothing writes and every later `cat` blocks. The FIFO-backed tests run the lister with `subprocess.run(..., timeout=30)`
at lines 582–583 and 602–606; on `TimeoutExpired` Python kills only the direct child (`bash`), not its descendants
(`cat`, `column`). Line 632 (`test_unknown_cgroup_is_an_error`) runs the lister with no timeout at all (line 661 is
`iocost-calibrate.sh`, not the lister).

**Defects.**
1. The fixture uses a FIFO with no persistent writer, or the test's timeout does not kill its process group (start the
   child with `start_new_session=True` and `os.killpg` on timeout, or use `terminate`/`kill` on the whole group).
2. The script has no read timeout on cgroup files (`scripts/mdt-cgroup-io-limits.sh:66,108,110`: `cat "$cur/io.max"`,
   `cat "$cg/io.latency"`, `cat "$cg/io.max"`). On a real host a hung cgroup read would block the same way; a bounded
   read (for example `timeout 5 cat`) would turn that into a visible error.


## MDT-023 - IO governor: the io.cost model, QoS and dev caps go stale when the sustained ceiling moves (2026-10-23)

**Status:** DESIGN written, nothing implemented: `docs/IO-GOVERNOR-DESIGN.md`.

**Observed.** On gstammtisch the sustained write ceiling went 516 MB/s (2026-07-08) -> 14-18 MB/s (2026-10-20) -> 330-350 MB/s (2026-10-23)
with the same disk identity, so identity-based baselines never noticed. The `DEV_IO_WBPS_MAX=10M` pin made the mdt-cockpit build step take 4694 s
instead of about 1100-1200 s, and the 14.7 MB/s `IO_COST_MODEL` was 23x low (vrate compensated at 2000-2800). With the corrected model vrate sat on
the QoS floor (50) in 58 of 77 samples while latency was far under target.

**Next.** P0 observe-only service feeding `libraries/iobench` `ceilings.jsonl` (`source: governor`, IOB-CEI-01), then `recalibrate_recommended` in
`mdt doctor`, then enforce-down, then enforce with a quiet-window probe. Open: why vrate pins at the floor (A/B `nr_requests`, `min` 50 vs 100).
Also tracked from iobench `BACKLOG.md` (the optional IOGOV item).

## MDT-024 - move the mdt-buildkitd cache onto a compressed btrfs subvolume in `devcontainers-cow` (2026-10-23)

**Status:** IMPLEMENTED 2026-10-23 as host-setup key `DEV_BUILDKITD_CACHE` (a Docker volume name, the default, or an absolute host path); the cold-cache trial on `/srv/mdt-cockpit-cow/buildkitd-cache` passed the same day and the setting stays in use.

**Trial result (mdt-cockpit image, cold cache, 2026-10-23).**
* **Works:** rootless BuildKit with the `runc-overlayfs` snapshotter runs on btrfs; the build succeeded in 1152 s, the same as the warm-cache ext4 builds (1082-1090 s). Those earlier builds got little from their cache, so the cold run is not slower.
* **Compression:** 26 GB referenced, 22 GB logical, **13 GB on disk (60 %)**; zstd extents 12 GB -> 4.2 GB (32 %), 9.1 GB stored as is (already compressed layer blobs and incompressible data, as expected).
* **IO:** average write 30 MB/s and read 45 MB/s, peaks 123 / 197 MB/s, device busy at most 74 %, io pressure average 1.0 / max 9, the same as on ext4.
* **Attribution:** `dev-buildkitd.slice/io.stat` shows the btrfs LV (253:2) with 18.8 GB written and 40.6 GB read, so btrfs data IO is charged to the slice, not to the root cgroup. Metadata and log writes were not separated out.
* **Cleanup done:** the old `mdt-buildkitd-cache` volume (93 GB) and three stale `buildx_buildkit_*_state` volumes were removed.
* **Open:** the pool is shared with the VM harness (194 GB): set `gckeepstorage` in `buildkitd.toml` before the cache grows toward it. Today the cache is the Docker named volume `mdt-buildkitd-cache` (93 GB, snapshotter `runc-overlayfs`,
owner 1000:1000, no userns remap) on the root ext4, so BuildKit's write bursts share the root journal with everything else
(HOST-IO-INTERFERENCE: that shared journal is what stalled the game's fsync path). `devcontainers-cow` is a separate btrfs filesystem
(own transaction stream, 194 GB, nearly empty, mounted `noatime,discard=async` at `/srv/mdt-cockpit-cow`; the name `devcontainers-cow` in this entry is the 2026-10 host's LV/label, kept as host reality, the default mount path was renamed 2026-11-03).

**Steps (host, root).**
1. `btrfs subvolume create /srv/mdt-cockpit-cow/buildkitd-cache` (the subvolume name is a path below the top level, BTRFS-COW.md 2.1).
2. `btrfs property set /srv/mdt-cockpit-cow/buildkitd-cache compression zstd` (BTRFS-COW.md policy: no `compress=` mount option; never `chattr +C`, it disables compression).
3. `chown 1000:1000 /srv/mdt-cockpit-cow/buildkitd-cache && chmod 2755 ...` (the rootless worker is uid 1000).
4. host-setup: a key `DEV_BUILDKITD_CACHE` in `/etc/mdt/host-setup.env` on the HOST (value `/srv/mdt-cockpit-cow/buildkitd-cache`; empty = the Docker
   volume, the current behaviour). It is a host path, not a devcontainer path: `install.sh` renders it into `units/mdt-buildkitd.service.in` as
   `-v <host path>:/home/user/.local/share/buildkit` (the part after the colon is the path inside the buildkitd container). The key exists only so the
   unit does not hardcode a host layout (hosts without btrfs keep the volume), with an `ExecStartPre` that refuses unless a host path exists as a real directory (the mount is `nofail`, and a missing mount must not make Docker create the directory on the root ext4); install.sh also checks the owner is 1000:1000. btrfs is not required: any filesystem path works.
5. `buildkitd.toml`: `[worker.oci] gc = true` with `gckeepstorage` (MB) well below the pool, which the VM harness also uses (BTRFS-COW-OPTIONS A17).
6. Start with a cold cache (no migration; it is disposable and crosses filesystems). Afterwards remove the old volume and the stale `buildx_buildkit_*_state` volumes.

**Verify.** `findmnt -T`, `compsize` for the real ratio, one build with the IO sampler against the 2026-10-23 numbers (about 1100 s,
35 MB/s average writes), and that `dev-buildkitd.slice` `io.stat` still shows the writes (btrfs can charge metadata and log writes to the root cgroup).

**Risks.** overlayfs over btrfs inside the rootless user namespace is unproven here (needs a trial; fall back = leave the cache on ext4);
compression costs CPU (8 cores) and layer blobs are already compressed, so only unpacked snapshot trees gain; what stays on the root ext4 is everything the Docker
*daemon* stores: `docker --load` / `docker pull` / running containers (the daemon is separate from the remote buildkitd, so even a build that goes
through mdt-buildkitd hands its `--load` result to the daemon's overlay2 store), the oci-push gate's `regctl export | docker load`, and the OCI layouts
under each release worktree's `build/oci-layouts`. Only BuildKit's own cache and snapshots move; moving Docker's data-root is a separate decision (BTRFS-COW.md 'Not covered'). Not a reason to touch the `host-services` filesystem: it carries the game volumes.

## MDT-025 - 61 GB inside `vbpub/.git` are dead build caches plus a growing artifact cache (2026-10-23)

**Status:** DONE 2026-10-23. The three dead `mdt-buildkit-cache*` directories (54 GB) were deleted (`.git` is now 218 MB), and `mdt-artifact-cache` was moved to `mdt-cockpit/artifacts/cache/tool-artifacts` (gitignored). `stage_tool_artifacts.py` now defaults there: the main checkout's project `artifacts/cache/tool-artifacts`, shared by every worktree (test: `test_default_artifact_cache_is_the_main_checkouts_gitignored_artifacts_dir_shared_by_worktrees`). Still open: an age/LRU prune (`stage_tool_artifacts.py prune --older-than`).

| path under `.git/` | size | writer | state |
|---|---|---|---|
| `mdt-buildkit-cache` | 46 GB | `release-bake.sh` `--cache-to type=local` (added 7c9c61c75 2026-07-28 "reuse verified release build caches") | **dead**: last written 2026-09-18 |
| `mdt-buildkit-cache-zstd-true-3`, `-zstd-false-3` | 8 GB, small | same script, namespaced by compression policy (c2deb87f1 2026-09-17) | **dead**: last written 2026-09-18 |
| `mdt-artifact-cache` | 6.3 GB, 162 URL entries | `scripts/stage_tool_artifacts.py` (default `<git-common-dir>/mdt-artifact-cache`, override `MDT_ARTIFACT_CACHE_DIR`) | **live**, only grows |

98015f29a (2026-09-18, "keep release cache on managed BuildKit remote") removed the local cache exporter: the persistent cache is now the remote
buildkitd's, so nothing writes `mdt-buildkit-cache*` any more and about 54 GB can be deleted (a cache, regenerable). The artifact cache needs
an age/LRU prune, and its default should not be inside `.git` (set `MDT_ARTIFACT_CACHE_DIR`, or move it with the buildkitd cache to the btrfs area).
Fix direction: a `stage_tool_artifacts.py prune --older-than` and a unit test; no other code in the repo writes under the git common dir.

## MDT-026 - optional fsync-free BuildKit cache operations (2026-10-23)

**Status:** ANALYSED, not implemented. `libeatmydata` cannot affect `buildkitd` (static Go binary, raw syscalls); it can only help dynamic processes in `RUN` steps. Options and recommendation: `docs/BUILDKITD-FLOWS.md` section 7 (Dockerfile build ARG with `dpkg --force-unsafe-io` / `eatmydata`, default off, measure first; a seccomp no-op filter for the daemon is unverified). Architecture and flows of the whole BuildKit setup: `docs/BUILDKITD-FLOWS.md`.

<!-- MDT-028..MDT-031 are taken by the iogov review branch (iogov-fable-review-20261027); the entries below
     were filed by the mdt-cockpit deep review of 2026-10-27 and start at MDT-032 to avoid a collision. -->

## MDT-060 - `cmru release` never runs the smoke lane, and the release lane skips every pytest-style staging test (2026-10-27)

**FIXED 2026-11-04 (fix-mdt-1-20261104; the bugfix program calls this "MDT-032 (release)").** `cmru.toml` `[steps.run-tests]` now
runs `run-gate smoke` first; `test_release_flow` asserts the exact command list.

*(Renumbered from MDT-032 on 2026-11-04: the id was reused by the iogov fill-test entry; MDT-032 now means that entry.)*

`cmru.toml` `[steps.run-tests]` runs `run-gate release` and `run-gate oci-push` only. The `release` lane is
`python -m unittest scripts.test_release_flow scripts.test_oci_tool_staging scripts.test_oci_push_lane`, and
unittest collects only `TestCase` classes: the pytest-style functions in `scripts/test_oci_tool_staging.py` (the
aged-latest resolver, sss, yazi/dust digests, the manifest-RUN and age-policy pins) and the whole `smoke` lane
(`pytest scripts host-setup/tests`) are not part of a release. Measured: the smoke lane had been RED on `main`
since 5a0517dc0 / b3b1a0366 (five failures: the per-checkout home spelling vs the tests and docs, a date-bound
waiver test, the dpkg clean-up after the final assertions) and the 2026-10-27 release still passed. Fix: make
the `release` lane run `pytest` over those modules (tester-unified has `/opt/tester-venv/bin/pytest`) or add
`run-gate smoke` to `run-tests`; `test_release_flow.py` should assert the lane runs every test function.

## MDT-061 - build inputs COPYed to `/tmp` are baked into the image layers (2026-10-27)

*(Renumbered from MDT-033 on 2026-11-04: the id was reused by the capacity entry; MDT-033 now means that entry.)*

`COPY apt/packages.list`, `pip/wheels.list`, `pip/wheels/`, `requirements/`, `package-manifests-versioned/`,
`customization/`, `scripts/mdt_cli.py`, `apt/backports.sources.in` and `build/tool-artifacts-staging/tool-versions.env`
land under `/tmp` and are never removed (only the four installer scripts and `ai-tool-versions.env` are, Dockerfile
"RUN rm -f ..."). Small (about 350 KB plus the wheels) but they ship in every native image's `/tmp` (the VSC template
hides them under the host-backed `/tmp` mount), and the manifest RUN now relies on `/tmp/packages.list` still being
there. Fix: bind-mount the build inputs (`--mount=type=bind,source=...`) in the RUNs that read them, or end the build
with one `rm -rf` of the known list and an `image-runtime` assertion that the native image's `/tmp` is empty
(the whiteout still leaves the bytes in earlier layers; bind mounts are the real fix).

**Status 2026-11-04: FIXED (fix-mdt-2).** A throwaway `mdt-build-inputs` stage holds the inputs; every consuming RUN
bind-mounts it at `/tmp/mdt-inputs`. `metadata.json` has its own stage (manifest RUN only). Only the generated
`/tmp/ai-tool-versions.env` still passes between RUNs (removed afterwards). `package-manifests-versioned` is no longer
copied at all (nothing read it). Not yet verified by a real image build; the `image-runtime` assertion that the native
image's `/tmp` is empty is left to that lane (MDT-012).

## MDT-034 - the manifest's Version column records raw `--version` banners for most tools (2026-10-27)

Only ov, gdu, dust, yazi, regctl, nvim, lnav and shellcheck are normalised. `claude` records
`2.1.311 (Claude Code)`, `codex` `codex-cli 0.164.0`, `copilot` `GitHub Copilot CLI 1.0.92-4.`, `dive` `dive 0.13.1`,
`glances` `Glances version:<TAB>4.5.8`, `b2` `b2 command line tool, version 5.0.2 (b2sdk ...)`, `consul`/`vault`/`gh`/
`delta`/`fd`/`rga`/`ripgrep`/`htop`/`node` likewise; the 20261021 and 20261022 manifests recorded `copilot` as
`Package extraction took 18189ms` (a first-run log line). The aider row regressed to its usage banner on 20261027
(fixed: the manifest RUN no longer exports the staged `AIDER_*` variables and reads the distribution version).
**Status 2026-11-04: FIXED** (the normaliser is on main: `scripts/manifest_sections.py` `normalise_version` / `normalise_versions`);
the "still open" improvement below is an optional redesign, not part of this defect.
**Status 2026-10-27 (follow-up): FIXED in the review branch** — `manifest_sections.normalise_version` (first
version-looking token, `v` stripped, `sss`/`ksm-optin`/node/npm passed through) is applied when the custom-tooling
file is read; grype and lazydocker take their `Version:` line in the Dockerfile; `scripts/test_oci_tool_staging.py`
runs the real 20261027 banners through the Dockerfile's own extraction commands and the normaliser. Still open:
the staged version (`tool-versions.env`) would be the better source for every staged tool and is already present in
that RUN; a manifest built from it would not depend on banner parsing at all.

## MDT-035 - `INSTALL_COPILOT`, `INSTALL_CLAUDELINK`, `INSTALL_PI` and `PI_VERSION` are not wired through bake (2026-10-27)

**FIXED 2026-11-04 (fix-mdt-1-20261104).** The bake file declares and passes `INSTALL_COPILOT`, `INSTALL_CLAUDELINK`, `INSTALL_PI`,
`PI_VERSION` (and the equally missing `INSTALL_REASONIX`, `INSTALL_OPENCLAW`, `REASONIX_VERSION`); `cmru.toml` `build` and `push`
forward the four. `test_every_ai_cli_install_switch_and_pin_is_declared_in_bake_and_forwarded` pins it.

The Dockerfile declares them as ARGs and `scripts/install_ai_cli_tools.py` / `stage_tool_artifacts.py` honour the
switches, but `docker-bake.hcl` declares no variables for them and `cmru.toml` `bake_set_vars` does not forward
them (it forwards `INSTALL_REASONIX`/`INSTALL_OPENCLAW`, which the bake file does not declare either). An operator
`INSTALL_COPILOT=false ./build-push.py --build` skips copilot at staging (`_vetted_ai_cli_versions`), while the
image build still runs with the ARG default `true` and no `COPILOT_VER`. Fix: declare the four in the bake file
and `bake_set_vars`, and add a test that every `INSTALL_*`/`*_VERSION` ARG of the Dockerfile is forwarded
(`scripts/test_ai_cli_versions.py` already reads the Dockerfile).

## MDT-036 - the aged-latest resolvers inspect only the newest 30 releases / commits (2026-10-27)

`_resolve_latest_aged_release` requests `releases?per_page=30` and `_resolve_sss_commit` `commits?...&per_page=30`
without pagination. A repository that publishes more than 30 releases (or commits on the default branch) inside the
age window fails closed with "no stable release published on or before ...". Unlikely for the six tools, but the
failure message does not say why. Fix: follow the `Link: rel="next"` header until a qualifying entry is found (bounded
to a few pages), or say "newest 30 inspected" in the error.

**Status 2026-11-04: FIXED (fix-mdt-2).** Both resolvers page (`&page=N`, at most 5 pages of 30, stopping at the first
page with a qualifying entry or a short page) and the error says how many entries were inspected.

## MDT-037 - `mdt-host-check.sh` does not check the dedicated Docker API socket or a pending daemon restart (2026-10-27)

**Status:** FIXED 2026-11-04 (fix-mdt-3): drop-in and listener (`root:docker` 0660) FAIL, pending restart WARN.

install.sh installs the `docker.socket` drop-in (`/run/docker-api/docker.sock`, the directory the devcontainer
template bind-mounts) and merges `/etc/docker/daemon.json`, and reminds about the restart only at the end of its own
run. check.sh verifies neither: the listener present and `0660 root:docker`, the drop-in installed, and daemon.json
newer than `docker.service`'s main process (the same test install.sh uses). A host that applied setup but never
restarted Docker passes `mdt-host-check.sh` while every new devcontainer refuses to start on the missing mount
source. Fix: three checks, the restart one a WARN.

## MDT-038 - the inotify sysctl file can lower a higher host value at boot (2026-10-27)

**Status:** FIXED 2026-11-04 (fix-mdt-3): the file holds `max(live, shipped 1048576)`. Not done: check.sh does not yet detect a later sysctl file overriding it (the `_check_io_override` reuse suggested below).

install.sh raises the live `fs.inotify.max_user_watches` only when it is below 524288 but installs
`/etc/sysctl.d/99-mdt-inotify.conf` unconditionally; `sysctl --system` applies it at boot after any earlier-sorting
file, so a host that set 1048576 elsewhere is lowered at the next boot (the README now says so). Fix: write
`max(current, 524288)` into the file, and let check.sh reuse `_check_io_override` to notice a later file or
`/etc/sysctl.conf` overriding it (as it does for the writeback keys).

## MDT-039 - install.sh usage drift and a post-commit-point refusal (2026-10-27)

**Status:** SUPERSEDED 2026-11-08 (H1g'): the `--observer-only` mode and `provision_cgprofile_observer` were removed with host-setup's cgprofile ownership. The details below are historical; keep the remaining `install.sh` usage and option matrix current.

The header's one-line synopsis omits `--restart-buildkitd` and `--require-damon-disable` (which `usage()`
lists); `--observer-only` rejects `--wizard`/`--reset`/`--with-baseline`/`--restart-docker` but silently accepts
`--restart-buildkitd` and `--require-damon-disable`; `provision_cgprofile_observer /` runs after the units are
rendered but before `daemon-reload` and can return 2 (observer-directory drift, missing docker group), leaving a
half-installed host. Fix: complete the synopsis and the option matrix; move the observer drift checks into the
pre-flight section (before the COMMIT POINT) and keep only the write after it.

## MDT-040 - the PWMCP installer URL requires the source commit to be on GitHub when the image builds (2026-10-27)

`build-push.py pwmcp_build_inputs()` points the in-image `install_pwmcp_instance.py` at
`raw.githubusercontent.com/<owner>/<repo>/<HEAD>/pwmcp/get.py` with the sha256 of the local file. The release's
`prepare` step therefore needs that commit pushed before the image build fetches it; a build from an unpushed
worktree commit fails at the PWMCP layer with an HTTP 404, and the message does not say why. The 2026-10-27 release
worked, so cmru's transaction evidently satisfies this; document the ordering in `docs/BUILD-ARCHITECTURE.md` and
make `install_pwmcp_instance.py` say "is this commit pushed?" on a 404. Related: `docs/CONSUMERS.md` and
`local-dev-artifacts.toml.example` show `1.64.0-r4` while `pwmcp/pwmcp.contract.json` still names `1.64.0-r3`
(forward references until pwmcp-v1.64.0-r4 ships; the image installs the contract's release).

**Status 2026-11-04: FIXED (fix-mdt-2)** for the 404 message and the ordering note (`docs/BUILD-ARCHITECTURE.md`,
"Ordering"). The `r3`/`r4` forward-reference remark is unchanged and resolves when pwmcp ships.

## MDT-041 - host and project names in the design docs and build inputs (2026-10-27)

User-facing docs are host-agnostic since this review (README, CONSUMERS, templates, host-setup README, USAGE).
`docs/DESIGN-GUIDE.md` still names the reference host twelve times and a production slice, `apt/packages.list` and
`requirements/toolkit.txt` comments name a consumer project, and two host-setup README pointers now read
`scripts/<host>-guide/...` because the guide lives at the repository root under the host's name. Fix: genericise
the design doc ("the reference host"), move the measurements it cites into `handoff/` (exempt), and give the
host guide a host-neutral directory name or an in-repo alias.

**Status 2026-11-04: FIXED for the mdt-cockpit docs and build inputs (fix-mdt-h).** `docs/DESIGN-GUIDE.md` now says "the reference
host" and "the production slice", names the guide once as `scripts/<host>-guide/` (defined at its first use), and the two
consumer-project mentions in `apt/packages.list` and `requirements/toolkit.txt` are gone. Re-diff: the README, CONSUMERS,
templates and USAGE were already host-agnostic; the host-setup README keeps its `<host>` pointers. Not done, by decision: the
guide directory itself keeps its name (`scripts/gstammtisch-guide/`; renaming it touches other subprojects' paths, tests and the
wizard's printed example); the measurements stay in the design doc rather than moving to `handoff/`. Open as a follow-up only if
the directory is ever renamed.

## MDT-042 - the README's tool inventory is incomplete (2026-10-27)

The "Shell and CLI tooling" tables never name `bat`, `rg`, `rga`, `yq`, `gh`, `grpcurl`, `consul`, `vault`,
`awscli`, `b2`, `hadolint`, `grype`, `cdebug`, `shellcheck`, `ncdu`, `httpie`, `w3m`, `sshfs`, `skopeo`, `minisign`,
`sqlite3`, `strace`, `gdb`, the sysstat/iotop family or the QEMU/VM packages, all of which the image installs
(`zswap-status` in the README is an alias from `aliases.sh`, not a binary). Fix: one compact table generated from
`apt/packages.list` plus the staged-tool list, or a pointer to the generated manifest as the complete inventory,
with a docs-in-sync test that every staged tool name appears in the README.

**Status 2026-11-04: FIXED (fix-mdt-h).** Re-diff: only `skopeo` and the QEMU packages were named; the listed tools were all missing.
The README gained a compact "Everyday search, data and cloud tools" table plus a pointer to the generated manifest as the
complete inventory, and `scripts/test_docs_in_sync.py` `test_every_staged_tool_and_the_named_apt_utilities_appear_in_the_readme_inventory`
fails when a `tool="..."` entry in `stage_tool_artifacts.py` (or one of the named apt utilities) is not in the README.

## MDT-043 - decision record: `pwmcp-client` wheel in `/opt/mdt/venv` (2026-10-27)

`pip/wheels.list` has no `pwmcp-client`; the client ships inside the PWMCP bundle (`/srv/pwmcp-instance`, on `PATH`
first through `/srv/pwmcp-instance/bin`), so `pwmcp` resolves and the runtime lane checks it. Recommendation of the
2026-10-27 review: do NOT add it to the venv. A second copy would have to match the bundle's version exactly (the
bundle is pinned by `pwmcp.contract.json`, the wheel would follow `pwmcp-client-latest`), both would be on `PATH`,
and nothing in the cockpit imports it as a library. Revisit only if a cockpit tool needs `import pwmcp_client`;
then install the wheel that the SAME bundle release carries, never a separately resolved one. The iogovmon/iomon
tooling (IO governor, now the independent project `iogovmon/`; iomon scripts) stays host-side for the same reason the `topos` daemon does: it needs cgroup
and block-device access the devcontainer does not have; the image may carry read-only clients once they exist.

## MDT-028 - iogov `watch`: per-cgroup swap, refault and memory.events counters in episodes and ticks (2026-10-27)

**MOVED 2026-11-05 to `iogovmon/BACKLOG.md` IGM-04** (the IO governor is now the independent project `iogovmon/`; full text there). Status OPEN.

## MDT-029 - iogov: a protected-process-side canary ("what does the game wait on?") (2026-10-27)

**MOVED 2026-11-05 to `iogovmon/BACKLOG.md` IGM-05** (full text there). Status OPEN.

## MDT-030 - iogov: gate reachability on a never-calm host, and a bench gate on memory pressure (2026-10-27)

**MOVED 2026-11-05 to `iogovmon/BACKLOG.md` IGM-06** (full text there). Status OPEN.

## MDT-031 - iogov -> iobench ceilings bridge (`ceilings.jsonl`, `source: governor`) (2026-10-27)

**MOVED 2026-11-05 to `iogovmon/BACKLOG.md` IGM-07** (full text there; the interface is now fixed by the host-tuning design as
`iogovmonctl export ceilings`). Status OPEN. MDT-023's "Next" paragraph reads the export through host-tuning's adapter, not an mdt import.

## MDT-032 - iogov: `test_prevention_checker_is_quiet_when_healthy_and_says_unknown_when_blind` reads the real filesystem fill (2026-11-02)

*(The iogov code and tests moved to `iogovmon/` on 2026-11-05; this entry is kept as the record of the closed fix.)*

The test builds its collectors from the live host (`covs(world)`) and asserts that no `fill` finding is raised. On a host whose
root filesystem is above the fill threshold (observed at 74% used) the finding appears and the `smoke` lane fails although
nothing in the change under test is related.

**CLOSED 2026-11-03 (6f669fa26).** The test now injects an empty fill result before checking the unknown fstrim/provider facts, so verdicts do
not depend on the machine's disk usage. `test_covariates_record_fill_discard_provider_queue_and_prio` continues to exercise live
fill collection using the fixture's controlled filesystem.

## MDT-033 - host-setup: the capacity unavailable to MDT (RAM, disk swap, CPU cores) drives sizing, protection and PSI control (2026-11-03)

**Status:** IMPLEMENTED in the worktree (unreleased), live validation OPEN: a through e are built and tested on
fake trees (`host-setup/mdt_capacity.py`, `scripts/mdt-psi-guard.py`, `tests/test_excluded_capacity.py`,
`tests/test_psi_guard.py`). Still open: the live runs on r1002 (design §9 M6), the devcontainer template's
read-only mount of `/run/mdt/psi-guard`, the run-gate RG-144 reader, and switching `MDT_PSI_GUARD` to `enforce`
after M6. Final operator decisions of 2026-11-03 supersede every earlier wording of this entry, including the
"reserve" and "budget = RAM minus reserve" variants. Cross-tool design:
`docs/design/RESOURCE-PROFILES.md` §7.

**Problem.**

- Memory sizing uses fixed fractions of total RAM (`propose_memory_tiers`, `mdt-host-setup-wizard.py:456`). The
  shipped README table is sized for about 16 GB: dev.slice at 12G/16G exceeds an 8 GB host.
- CPU uses two integer-only `*_RESERVE_CORES` keys, whose word "reserve" reads as "reserved for MDT".
- Swap takes 80% of the host total, regardless of what production services already occupy.
- Nothing verifies that the production services are actually protected.
- Nothing reacts to pressure.

**Decision record (operator, 2026-11-03, binding).**

1. **Three numbers.** The operator gives three numbers naming capacity that is **unavailable to MDT** because other
   services on the host use it:
   - RAM;
   - disk swap only (swap space on the swap device);
   - CPU cores (fractions allowed; `0` on a host without other load).

   Everything else is MDT's.
2. **Overcommit and protection.** RAM and CPU are overcommitted by design. About 500 MB of disk swap is kept for the
   host. Protection comes from:
   - weights (`CPUWeight`, `IOWeight`);
   - `memory.min` on the named host or production cgroups, mirroring the RAM unavailable to MDT. The shared host's
     reference: `wings.slice` `memory.min=6500M`, `memory.high=15000M`, two scopes at 4000M + 3000M, about 13.8 GB
     of swap in use on average;
   - PSI as the control signal.
3. **Terminology.** No key name or prompt uses "reserve" or "reserved". The wording is "excluded from MDT",
   "withheld from MDT", or "unavailable to MDT".
4. **No backwards alias (operator update 2026-11-03).**
   - `DEV_CPU_RESERVE_CORES`, `DEV_SUBSLICE_CPU_RESERVE_CORES` and every other `*_RESERVE_*` key are renamed
     outright, with no mapping layer.
   - The installer and the wizard refuse the old names with a message naming the new key.
   - The migration is a one-time documented rename.
5. **Live probe.** An optional probe suggests the numbers from `memory.min` and the current swap use of the named
   services.

**Design** (sub-items; they ship together in one mdt-cockpit release unless noted).

- **MDT-033.a Keys and outright rename.**
  - New keys:

    | Key | Meaning | Default |
    |---|---|---|
    | `MDT_EXCLUDED_RAM` | size | `0` |
    | `MDT_EXCLUDED_SWAP` | size | `0` |
    | `MDT_EXCLUDED_CPU_CORES` | decimal, one decimal place | `0` |
    | `DEV_TIER_EXCLUDED_CPU_CORES` | decimal; cores no single tier may use on top of the above | `0` |
    | `MDT_PROTECTED_UNITS` | space-separated unit names | empty |
    | `MDT_HOST_SWAP_MARGIN` | size; disk swap kept for the host | `512M` |

  - Refused old keys. The rename table goes into the host-setup README section "Renamed keys (2026-11)":
    - `DEV_CPU_RESERVE_CORES=c` → `MDT_EXCLUDED_CPU_CORES=c`;
    - `DEV_SUBSLICE_CPU_RESERVE_CORES=k` → `DEV_TIER_EXCLUDED_CPU_CORES=max(0, k−c)`, which keeps today's tier quota (the parent's quota already caps a tier when k < c); `0`
      is recommended.
  - The refusal message text: `<OLD> is no longer read: rename it to <NEW> (<semantics>; your value <v> → <NEW>=<v'>).
    See host-setup/README.md "Renamed keys (2026-11)".`
  - Files to change:
    - `host-setup.env.example` §2.1 and §3;
    - the wizard validators `:829-830`, the CPU table `:2485-2512`, the help texts `:2566` and `:2603-2606`, and the
      docstrings `:1299-1317`;
    - `install.sh`, `units/dev-buildkitd.slice.in`, `check.sh`;
    - `README.md`, `cgroup-cpu-swap-redesign-HANDOFF.md`;
    - `tests/test_hs02.py`, `tests/test_buildkit_governance.py`.
  - Not changed: the deprecated pre-rename tree.
  - The operator renames the live `/etc/mdt/host-setup.env` with the table before the next install.
- **MDT-033.b Derived sizing (overcommit by design).**

  ```
  RAM_MDT  = MemTotal − MDT_EXCLUDED_RAM                           (refuse < 1G)
  SWAP_MDT = SwapTotal − MDT_EXCLUDED_SWAP − MDT_HOST_SWAP_MARGIN  (≤ 0: warn and require confirmation:
                                                                    MDT gets no disk swap)
  CPU_MDT  = nproc − MDT_EXCLUDED_CPU_CORES                        (refuse < 0.5)
  ```

  - **`dev.slice`:** MemoryMax = `RAM_MDT`, MemoryHigh = 92% of that, MemorySwapMax = `SWAP_MDT`, CPUQuota =
    `CPU_MDT` × 100%.
  - **Tiers:** MemoryMax/MemoryHigh as fractions of `RAM_MDT` (interactive 60/45%, background 70/50%, gates 70/50%,
    buildkitd 35/25%). Their sum exceeds `RAM_MDT` on purpose, and zswap and swap absorb the cold part.
    - Interactive MemoryLow is 15%.
    - Tier swap = `DEV_SWAP_CASCADE_PCT`% of `SWAP_MDT` (unchanged rule).
    - Tier CPUQuota = (`CPU_MDT` − `DEV_TIER_EXCLUDED_CPU_CORES`) × 100%.
  - **Explicit values always win** and are the prompt defaults. The fractions are initial values, validated by design
    §9 M6.
  - **Disk swap only.** The excluded swap is the swap space the other services use on the swap device, read as their
    `memory.swap.current`.
  - **Worked examples (README):**

    | Host | Inputs | Result |
    |---|---|---|
    | Shared host | 16 GiB RAM, 69 GiB swap, 6500M / 14G excluded | `RAM_MDT` ≈ 9.2 GiB, `SWAP_MDT` ≈ 54.5 GiB |
    | r1002 | 4 cores, 7.75 GB, all 0 | 7.75 GB, SwapTotal − 512M, 400% |

- **MDT-033.c Protection verification.** The production units are operator-owned, so this is verify-only by default.
  `check.sh` and the wizard summary WARN when:
  - the effective `memory.min` summed over `MDT_PROTECTED_UNITS` is below `MDT_EXCLUDED_RAM`;
  - the children's `memory.min` sum exceeds the parent's (shared host: 7000M of scopes under a 6500M slice, so the
    kernel scales the scopes down);
  - a protected unit's `CPUWeight` or `IOWeight` is below 4× that of `dev.slice`.

  `install.sh --apply-protection` (opt-in, interactive confirmation) writes `50-mdt-protection.conf` drop-ins with
  `MemoryMin=`, `CPUWeight=` and `IOWeight=` for the named units.
- **MDT-033.d Live probe.** `mdt-host-setup-wizard.py --probe-excluded [--units ...] [--seconds 600]`, read-only.
  - **It reads, per named unit and child cgroup:**
    - `memory.min/low/high/max/current`, `memory.swap.current`;
    - `memory.stat` (`anon`, `file`);
    - the `cpu.stat` usage delta;
    - `cpu.weight`, `io.weight`.
  - **It suggests:**
    - RAM = the effective `memory.min` sum, rounded up to 256M. If no `memory.min` is set, the p95 of
      `memory.current`, flagged "unprotected".
    - Swap = the maximum of `memory.swap.current` over the window, rounded up to 512M.
    - CPU = the p95 usage rate, rounded up to 0.5.
  - **It prints** the table and the §c warnings, and writes nothing unless accepted.
  - **Why it reads the files directly.** It is a one-shot configuration hint, not a resource profile, so the
    cgprofile single-source rule does not apply. It must also run before cgprofile is installed.
- **MDT-033.e PSI guard (`mdt-psi-guard`).** A stdlib service outside `dev.slice` that watches kernel PSI triggers:
  - the protected units' `memory.pressure` `some` (10%);
  - host `memory` `full` (5%);
  - the protected units' `cpu.pressure` `some` (25%);
  - with 10 s hold and 60 s release at half the threshold.

  Actions in `enforce`, each reverted on release, on stop and at boot:
  1. `CPUWeight` and `IOWeight` of `dev-gates`, `dev-background` and `dev-buildkitd` set to 1;
  2. after 30 s still above, `memory.high` of `dev-gates` and `dev-background` lowered to max(floor,
     0.9 × `memory.current`).

  - It never freezes or kills: wall-clock budgets would expire and change results.
  - `MDT_PSI_GUARD = off|report|enforce`, shipped as `report`. It switches to `enforce` after M6.
  - Events go to `/run/mdt/psi-guard/events.jsonl`, which run-gate RG-144 reads as a contamination source. The
    devcontainer template mounts that directory read-only.
  - The guard state file lists the keys the guard currently owns (the reconcile sweep writes none of them and only logs the list).
    The real overwriter of an active throttle is systemd (`daemon-reload`, `set-property`), documented as a known limit.
  - `events.jsonl` rotates at 1 MiB to `events.jsonl.1` (tmpfs); RG-144 reads both.
- **MDT-033.f Wizard prompt texts** (no "reserve"):
  - `RAM unavailable to MDT — used by other services on this host (0 if none) [suggested: …]:`
  - `Disk swap unavailable to MDT — swap space other services on this host occupy (0 if none) [suggested: …]:`
    Help line: "Swap space on the swap device that these services use."
  - `CPU cores unavailable to MDT — used by other services on this host, fractions allowed (0 on a host without other load) [suggested: …]:`
  - `Host/production units to verify and probe (space-separated, empty if none) [...]:`
  - The summary: `MDT may use: RAM <x> (overcommitted across tiers), swap <y>, CPU <z> cores. Everything else is
    unavailable to MDT.`
- **MDT-033.g Parallelism note (docs).** Lane workers and R2 jobs are sized by run-gate from measured per-worker
  profiles (run-gate RG-146 and RG-148), not from these limits. The tier `MemoryMax` is the safety cap, and the
  run-gate admission budget is the predictable need.

**Open design question: soft protection via `MemoryLow` on `dev.slice`.** The wizard proposes no `MemoryLow`
(neither on `dev.slice` nor on a tier). A `dev.slice` `memory.low` gives MDT reclaim priority over every unprotected
host service (system.slice, the docker daemon, the game's page cache beyond its `memory.min`), about 1.4 GB on the
shared host, so it needs an explicit operator decision before any proposal returns. A tier `MemoryLow` is ineffective
while the parent's is unset. Until decided, an operator may still set them by hand.

**Acceptance criteria.**

- **Keys:** every new key validates, including fractional cores (`0.5`, `1.5`; `1.55` refused).
- **Old keys:** each is refused by the installer, the wizard and `check.sh` with the exact rename message.
- **Wording:** a repository test finds no `\breserve` in any host-setup key name, prompt or help text, apart from the
  refusal message and the README rename table.
- **Sizing:** derived values equal the worked examples on fake `/proc/meminfo` and `nproc`.
- **Swap:** `SWAP_MDT ≤ 0` gives the no-disk-swap warning and requires confirmation.
- **Protection:** the §c warnings on a fake cgroup tree, including children-exceed-parent. `--apply-protection` writes
  only after confirmation and only for named units.
- **Probe:** suggestions on a fake tree (with and without `memory.min`); it writes nothing.
- **PSI guard:** staged actions and their revert on a fake cgroupfs with injected pressure; restore on stop and at
  boot; a vanished source releases and a counter is keyed by path; the event file is bounded; `report` mode writes events and changes nothing.
- **Docs:** README, `host-setup.env.example` and CONSUMERS agree (docs-in-sync test).

**Test plan.**

- The host-setup unit tests (`tests/test_hs02.py`, new `tests/test_excluded_capacity.py`,
  `tests/test_psi_guard.py`), plus `host-setup/tests/test-render.sh` with the new keys.
- **Live on r1002** (design §9 M6): the probe with all values 0 and with a simulated profile (2G / 1G / 1 core).
  - The guard in `report` during the M5 memory-hog run, then `enforce` for one repeat.
  - Lane verdicts and buckets must stay identical under `enforce`.
- **Afterwards:** a shared-host dry run of the probe (read-only) for the operator's numbers.

**Dependencies and order.**

- Independent of the other tools' releases.
- M6 needs r1002 host setup completed (the 2026-11-02 run stopped at the Docker restart).
- RG-144 consumes the guard events, but optionally.
- Flip the guard to `enforce`, and adjust the tier fractions, after M6.

## MDT-044 - host-setup: provision the CoW area and the checkout location, in a fresh-host order (2026-11-03)

**Status:** OPEN. Depends on `libraries/cowtree` COW-BL-01/-02 for the per-project declarations; this entry is the host half.

**Decision (operator, 2026-11-03, binding).**

- The checkout, with `.worktrees/`, lives on the CoW filesystem. Worktrees still come from git. Only the paths a project
  declares in the cowtree schema become writable snapshots of main's. There is no mount-over of a folder in the checkout and no
  `MDT_*` path override.
- `mdt host-setup` creates and mounts the btrfs CoW area (default `/srv/mdt-cockpit-cow/<user>/`), with the
  `user_subvol_rm_allowed` option and an fstab entry, and the per-user root inside it.
- Both must exist **before the devcontainer starts**: Docker refuses a missing bind source, and a source it creates itself is
  root-owned. The checkout is cloned inside the per-user root, and the devcontainer binds that path.
- `docs/BTRFS-COW.md` §6.3 still prescribes ISOs in `<user>/shared/upstream-iso` and golden disks in `shared/vm-disks/base`.
  Rewrite it to the project layout: VM files in `<project>/artifacts/vm-lab/`, ISOs in the repo-level `downloads/` (AGENTS.md
  "Directory conventions and VM files"), CoW through the declared paths.

**Fresh-host order (to document and to make `check.sh` verify).**

1. Host-setup: CoW area (mkfs or an existing btrfs, mount, `user_subvol_rm_allowed`, fstab), then the per-user root owned by
   the checkout user.
2. Clone the repository inside the per-user root.
3. Provision the declared paths in main (cowtree provision; subvolume per declared path, placeholders moved aside and back).
4. Start the devcontainer with the per-user root as the workspace bind.
5. Worktrees: `ciu worktree create` snapshots the declared paths (CIU-159).

**Open points.** What `check.sh` reports on a host with no btrfs (one INFO line, not a failure); whether the wizard offers to
create a loop-backed btrfs for a test host (no: a real device or an existing btrfs only, see the loop-device incident rule).

**Acceptance.** The installer and `check.sh` cover steps 1-2 with fake-filesystem tests; the README gives the order; BTRFS-COW
§6.3 matches the layout; no document names a user, host or fixed home path.

## MDT-045 - Python variants: only 3.14 builds today (2026-11-03)

**Status:** OPEN. `LATEST_KNOWN_PYTHON` is a release input (CHANGES, `[Unreleased]`), but only the default variant builds.

**Measured.**

- **3.14:** builds.
- **3.15:** fails at the pwmcp instance install (`greenlet==3.5.6` has no matching distribution for 3.15) and at `aider-chat`
  (requires Python below 3.15). `INSTALL_AIDER=false` skips the second; the first remains.
- **3.11:** fails at the same `greenlet` pin.

**Decision.** A Python variant needs per-Python pins; one global pin set cannot serve every variant. The pwmcp instance install
is the consumer of the `greenlet` pin (the contract in `pwmcp/pwmcp.contract.json` owns it), so the pin must come from a
per-interpreter constraint there, not from an mdt-cockpit override.

**Next steps.**

1. Per-Python constraint files (or markers) for the pwmcp instance install; file the greenlet finding in the pwmcp backlog.
2. Decide `aider-chat` for 3.15: skip with a build-log line when `INSTALL_AIDER` is false, or pin to the last release that
   supports it.
3. A smoke build per supported variant in the release checklist; an unsupported variant fails early with the reason, before the
   long layers.

**Acceptance.** `LATEST_KNOWN_PYTHON=<v> cmru release mdt-cockpit` either builds each variant in the supported list or stops at
the first step with a one-line reason. The supported list is in the README.

## MDT-046 - release docs omit the `LATEST_KNOWN_PYTHON` and `DEVCONTAINERS_IGNORE_NEW_RELEASES` environment inputs (2026-11-03)

**Status 2026-11-04: FIXED (fix-mdt-h).** `docs/BUILD-ARCHITECTURE.md` "Release inputs" documents both variables; the README
links it; `scripts/test_docs_in_sync.py` `test_the_release_environment_inputs_are_documented_where_the_release_flow_lives` pins it.
*(Original status: OPEN (docs).)* CHANGES (`[Unreleased]`) records both: `LATEST_KNOWN_PYTHON=3.15 cmru release mdt-cockpit` publishes a
second Python variant next to the default, and `DEVCONTAINERS_IGNORE_NEW_RELEASES=true` skips the newer-base stop. The README
mentions the bake default and `--ignore-new-releases`, but not the environment form.

**Fix.** Add a "Release inputs" subsection to `docs/BUILD-ARCHITECTURE.md` under "Canonical release path" (that file owns
the release flow): the two variables, that the environment wins over the `docker-bake.hcl` default, the published tag shape
(`trixie-py<ver>-php<ver>-<date>` and its `-latest`), and that a variant other than the default must be listed as supported
(MDT-045). Link it from the README sentence about `LATEST_KNOWN_*`. A docs-in-sync test asserts both variable names appear in
`docs/BUILD-ARCHITECTURE.md`.

## MDT-047 - host-setup `install.sh` restarts Docker and a running container was lost (2026-11-03)

**Status:** OPEN. Found on a scratch host: after `install.sh` restarted Docker, a running container did not come back, although
`live-restore` was configured.

**Next steps.**

1. Reproduce on a scratch host with one named container and `live-restore` on; record whether `docker.socket` and
   `docker.service` restart together, and what the daemon logged.
2. The installer must not restart Docker unless `--restart-docker` is passed (the flag exists); verify that no other path
   restarts it (units, drop-ins, the wizard's apply step).
3. Before any restart, list the running containers by name and require confirmation (never an all-container inventory beyond
   the names), and print the restart-free alternative (`systemctl reload` or a reboot window) when the changed keys allow it.
4. Document the chicken-and-egg rule: a folder inside the checkout is never mounted over by host-setup (the vm-lab mount-over
   was dropped); CoW paths are subvolumes of the checkout filesystem declared per project (MDT-044), so no container needs a
   restart to see them.

**Acceptance.** A test on fake `systemctl` shows `install.sh` without `--restart-docker` issues no Docker restart on any path;
the scratch-host reproduction is recorded with its cause (or "not reproducible with live-restore, restart was explicit").

## MDT-048 - cgprofile client is not in the image: cgprofile releases carry no wheel (2026-11-03)

**Status:** FIXED 2026-11-04 (branch `mdt-cgp-native-20261104`; verified by the wheel-set, staging, release-flow, local-dev and
finalize-skills tests). `cgprofile` is in `pip/wheels.list`, `FIRST_PARTY_WHEEL_NAMES`, `FIRST_PARTY_NO_AGE_WINDOW`, the Dockerfile
manifest RUN, the size-breakdown leaf, the docs tables and the `local-dev-artifacts.toml.example` names; `SKILLS_CLIS` gains `cgpctl`;
`cmru.orchestration.toml` declares `cgroup-profiler` in the cockpit's `depends_on`. The `report` extra is not installed (the image
installs only the files `check_wheel_set.py` emits, and the wheel's only runtime dependency is cli-extended); a release-flow test
pins that. The text below is the original problem statement.

`pip/wheels.list` resolves `<name>-v*` releases that carry a `.whl`. The `cgprofile-v*` releases
(`scripts/cgroup-profiler/cmru.toml`: `artifacts = ["oci-image"]`) publish only the daemon image to `ghcr.io/volkb79-2/cgprofile`
and a release note; the release has no assets, and `scripts/cgroup-profiler/pyproject.toml` (`cgroup-profiler`, stdlib-only
collector, optional `report` extra) is not built into a wheel. So the cgprofile client (wrap/attach CLI) cannot be added to the
image's wheel list without inventing a package.

**Next steps.**

1. Decide the client's distribution: add a `wheel` artifact to the cgroup-profiler release (client CLI entry point, stdlib-only
   core, `report` extra left out of the image closure), or keep it image-less and document running it from the checkout.
2. If a wheel is published, add `cgprofile` (the release prefix name; the wheel's distribution name must normalize to it or the
   resolver and `scripts/check_wheel_set.py` will not match) to `pip/wheels.list`, `FIRST_PARTY_WHEEL_NAMES`, the Dockerfile
   manifest RUN, and the tables that enumerate the wheel set (README, `docs/CONSUMERS.md`, `local-dev-artifacts.toml.example`,
   `FIRST_PARTY_NO_AGE_WINDOW`).
3. Any dependency of the new wheel must be in the set or `requirements/toolkit.txt`, because the image installs with `--no-index`.

**Acceptance.** `cgprofile` resolves from a published wheel and `check_wheel_set` plus the manifest test pass with it declared;
or the decision to stay image-less is recorded here with the checkout invocation.

## MDT-049 - the BuildKit shim rewrites an explicit `BUILDX_BUILDER=default` to `mdt-managed`; builds FROM a locally loaded image fail (2026-11-04)

**Status:** OPEN. Found while building `tester-unified` on a runner host on top of a locally loaded `tester-base:local`.

**Observed.** `docker-buildx-shim.sh` treats `BUILDX_BUILDER` unset **and** `default` the same (`case ... ""|default) BUILDX_BUILDER=$MDT_BUILDER`). The
managed builder is rootless BuildKit with its own image store; it cannot see an image that exists only in the Docker daemon's store, so
`FROM tester-base:local` fails with `pull access denied ... repository does not exist`. A caller who selects `default` explicitly (the
docker driver, which does see local images) is overridden without notice. The only escape is `MDT_BUILDX_PASSTHROUGH=1`, which also disables
the cgroup/registration handling for that call.

**Next steps.**

1. Honor an explicit `BUILDX_BUILDER=default` (rewrite only when it is unset), or keep the rewrite and print one stderr line naming
   `MDT_BUILDX_PASSTHROUGH=1` and `--builder` as the way to the docker driver.
2. Document the local-base-image limitation and the passthrough workaround in `tester-unified/README.md` ("Build") and
   `docs/BUILD-ARCHITECTURE.md`.
3. Alternative that removes the need: build `tester-base` with `--output type=image` into the registry used by the managed builder, or pass the base as a
   BuildKit `--build-context` of the local image.

**Acceptance.** A shim test (fake buildx plugin) shows `BUILDX_BUILDER=default` is passed through unchanged, or that the hint line is printed; the
docs name the workaround.

## MDT-050 - `host-setup.env.example` ships `DEV_INTERACTIVE_MEMORY_LOW=2.5G` while the README says no MemoryLow is proposed (2026-11-04)

**Status:** OPEN. The wizard warns on the shipped example: `DEV_INTERACTIVE_MEMORY_LOW` is set but `dev.slice` has no `MemoryLow` ancestor, so it is
inert. A first live `install.sh` run needed a manual edit to the empty value.

**Fix.** Set the example default to empty (line 265, `DEV_INTERACTIVE_MEMORY_LOW=`), keep the allowed-values comment, and add a test that the
example parses with no wizard warning on a host with no `DEV_MEMORY_LOW`.

## MDT-051 - `mdt-host-check.sh` warns on `auto` values it should resolve (2026-11-04)

**Status:** OPEN. On a freshly installed host 10 of 12 warnings were noise: `DEV_*_CPU_QUOTA=auto is not a recognized CPUQuota form (N%%)` (the message
also doubles the `%`) and `*_SWAP_MAX=auto is not byte-comparable`. The other two (no io baseline; IOWeight inert without io.cost) are real and expected.

**Fix.** Resolve `auto` to the computed value (the same function the installer uses) before comparing, or skip the comparison with an `info` line;
fix the doubled `%%` in the message. Test: a fake host-setup.env with `auto` for both keys yields no warning.

## MDT-052 - the docker-run form of the host-folder cockpit has no UID remap, so checkouts, CoW root and lane user disagree (2026-11-04)

**Status:** OPEN. Hosts without the devcontainer CLI create the cockpit with `docker run`, which skips `updateRemoteUserUID`. The container's `vscode`
(uid 1000) then owns nothing the 1003 `tester-unified` lane user can share: a checkout owned by one uid fails Git (`dubious ownership`) or the CoW root
check (`MDT_COW_ROOT must be owned by the non-root caller uid`) on the other side (Run-Gate RG-150).

**Fix.** Ship the docker-run equivalent as a supported helper (`mdt-cockpit run-host-folder`, or a documented script) that creates the container as root,
remaps `vscode` to the owner uid/gid of the checkouts directory (`groupmod`/`usermod`), then runs `finalize_container_environment.py` as `vscode`; document that
the runner account must be uid 1003 (the tester-unified lane uid). Test the helper with a fake docker.

**Rescope 2026-11-04 (operator decision, see MDT-055).** Step 1 is decided: the cgprofile release ships a wheel (client CLI plus
the stdlib-only collector, `host-unit` subcommand; CP-41). The wheel serves two consumers: this image's wheel list and host-setup's
native cgprofile service (MDT-055). The `report` extra stays out of both closures.

## MDT-053 - fixed estate group `mdt-docker` (gid 2751) on the host Docker relay socket (2026-11-04)

**Status:** OPEN. Packages in order: this entry first (host-setup), then MDT-054, CIU-160, NL-54 and the assay/run-gate entries.

**Problem.** The relay socket `/run/docker-api/docker.sock` is `root:docker 0660` (`units/docker-api-socket.conf:56-63`) and the
cockpit gets access through `--group-add ${localEnv:DOCKER_GID}`. That number is a host fact that must be exported in the host shell
before `docker run`, because nothing inside the container can look it up before the container exists; ciu then persists the
host's number into `ciu.env`, and ownership of `vol-*` directories follows it. A different host (or a reinstalled docker group)
silently changes access and ownership. `/run/cgprofile` (`root:docker 0770`) rides on the same gid.

**Decision record (operator, 2026-11-04, binding).** A new fixed estate group `mdt-docker`, gid **2751** (option A2 of the platform
memo; the image's `mdt` group, gid 2750, is NOT reused because "may write /opt/mdt" must not also mean "root-equivalent through
Docker"). The group lives on the host-setup relay socket. The host's own `/var/run/docker.sock` stays `root:docker`.

**Design.**

1. New `units/mdt-docker.sysusers`: `g mdt-docker 2751 - -`. `install.sh` runs `systemd-sysusers`, then verifies by `getent`
   that the name resolves to gid 2751; if the gid is taken by another name, or the name exists with another gid, refuse with a
   message naming both (no silent adoption, same pattern as the `cgprofile-observer` checks at `install.sh:155-201`).
2. `docker-api-socket.conf` sets `SocketGroup=mdt-docker` (socket mode stays 0660). The `/run/docker-api` directory is
   `root:mdt-docker 0750`/the existing mode with the new group.
3. `mdt-cgprofile.conf` (`d /run/cgprofile 0770 root mdt-docker -`): the cgprofile control-socket directory moves to the same
   group (the decision and the consequence for `cgprofile-observer` are recorded in CP-43). The observer group stays
   `cgprofile-observer` (0751) and must differ from both `mdt-docker` and the host `docker` gid; the existing check at
   `install.sh:201` gains the second comparison.
4. `check.sh` assertions: group exists with gid 2751; relay socket group is `mdt-docker`; `/run/cgprofile` group is `mdt-docker`;
   host socket still `docker`.
5. One-time migration, documented in the README (and batched with the next host-setup restart because restarting
   `docker.socket` is disruptive on the production-shared host): create the group, regroup the socket, restart `docker.socket`,
   rebuild the cockpit (MDT-054), regenerate `ciu env` (CIU-160), `chgrp` existing hostdirs per the README recipe.

**Acceptance.**

- Fake-root `install.sh` tests: fresh host creates the group at 2751; an occupied gid, or an existing `mdt-docker` with another
  gid, is refused with both identities named; rerun is a no-op with no docker.socket restart unless the drop-in changed.
- `check.sh` fails when any of the four assertions is violated.
- A live run on a scratch host (not the production-shared host first): a container whose image adds its user to `mdt-docker`
  reaches the relay socket with no `--group-add`.
- **Non-mdt path (operator criterion, 2026-11-04).** Nothing in repo tools requires the group: a test (or documented live check)
  on a host without host-setup shows `ciu`, `run-gate`, `cmru`, `assay` and `nyxloom` resolving the docker gid by `stat` of the
  socket they actually use and working for a user with only the normal docker access of that environment. This entry owns the
  CONSUMERS statement: "`mdt-docker` is a cockpit-side access group, never a prerequisite of any repo tool".

**Test plan.** Extend host-setup's existing fake-root install tests; add the check.sh cases; one scratch-host live run.

## MDT-054 - cockpit image and templates: `vscode` in `mdt-docker`, no `--group-add`; finalize derives the gid from `DOCKER_HOST` (2026-11-04)

**Status:** OPEN. Depends on MDT-053.

**Problem.** `.devcontainer/devcontainer.json:228-231` and both templates pass `--group-add ${localEnv:DOCKER_GID}`; the r1002
runner does the same in `start-cockpit-runner.sh:14`. `finalize_container_environment.py:143-146` stats only
`/var/run/docker{-host,}.sock`; under `DOCKER_HOST=unix:///run/docker-api/docker.sock` it likely prints `docker_gid=?` (`:702`).

**Decision record (operator, 2026-11-04).** The image creates group `mdt-docker` gid 2751 and adds `vscode` to it; Docker applies the
image's supplementary groups, so no runArg is needed. `--group-add ${localEnv:DOCKER_GID}` is removed everywhere (no alias, no
fallback): the devcontainer, both templates, the r1002 runner start script and its docs.

**Design.**

- Dockerfile: `groupadd -g 2751 mdt-docker` and `usermod -aG` for `vscode` (and for the runner remap path, which re-creates
  `vscode` with a new uid: the supplementary group membership must survive the remap; test it).
- Templates and `start-cockpit-runner.sh`: drop the runArg and the `DOCKER_GID` host-shell prerequisite from the docs.
- finalize: derive and print the gid by `stat` of the socket named by `DOCKER_HOST` when it is `unix://`, else the legacy paths;
  report both the socket gid and whether the current user is a member.
- Image check: a test asserts `getent group mdt-docker` is 2751 and `vscode` is a member.

**Acceptance.**

- Image build test pins the group and membership; template tests refuse `DOCKER_GID` anywhere under `.devcontainer/`.
- finalize prints the real gid on a fake socket path, and names `mdt-docker` as the expected group on a mismatch.
- Live: launching without exporting `DOCKER_GID` still reaches the relay socket.
- **Non-mdt path.** The same image used outside the mdt cockpit (plain Debian host or another devcontainer that mounts a docker
  socket of another gid) still works: tools read the gid by `stat` of the socket mounted, and the documentation says a user
  of another environment needs that environment's normal docker access (its own `--group-add` or group), nothing from `mdt-docker`.

**Test plan.** Template/dockerfile static tests, finalize unit tests with a fake socket, one live launch.

## MDT-055 - host-setup installs and pins the cgprofile host service from the released wheel (2026-11-04)

**Status:** SUPERSEDED 2026-11-08 (H1g'): cgprofile lifecycle moved to `cgprofiled install`; host-setup's installer, config keys, and check sections were removed. The implementation record below is historical. Use cgprofile's native deployment guide for current install behavior.

Historical implementation, completed 2026-11-04 on branch `mdt-cgp-native-20261104` (fake-root tests in `host-setup/tests/test_cgprofile_install.py`;
smoke and release lanes in the package report); the live installs are the ops package (NC-05, r1002 first, then the shared host).
Built against cgprofile 2.0.0 (`cgpd unit print|check`, `cgpd config check`, `cgpctl sessions|status|doctor --json`), not the
`cgprofile host-unit --print` command named in the design below, which became `cgpd unit print`. Deviations from the design text
below: the second pin `CGPROFILE_CLI_EXTENDED_WHEEL` (the venv is built `--no-index`), `CGPROFILE_WHEEL_SOURCE`, the drain
(`CGPROFILE_DRAIN_TIMEOUT`, `--force-cgprofile-restart`), and the fact that host-setup does NOT stop the 1.x ciu container (the 2.0 daemon's single-daemon guard refuses
a second daemon on the socket, so `ciu down` first, as the migration section of NATIVE-DEPLOYMENT.md says).
Original text follows. Depends on CP-41/CP-42 (wheel release and `host-unit`) and the MDT-048 distribution decision (now made). Interim
(RG-55 C option): until the wheel exists the ciu deployment of `cgprofile-host-daemon` stays, with the gates slice rendered from
host config instead of hand edits (NC-06).

**SUPERSEDED 2026-11-08 (H1g'): the Problem, Decision record and Design text below is the retired design.** `CGPROFILE_WHEEL`,
`CGPROFILE_ENABLE`, `--cgprofile-only` and `--force-cgprofile-restart` no longer exist in host-setup; `cgprofiled install` owns the
service. Kept as history only, not as current design text.

**Problem.** The daemon runs as a ciu-managed privileged container (`scripts/cgroup-profiler/ciu.compose.yml.j2`) and depends on a
repo checkout, ciu, and on r1002 two uncommitted hand edits (`RUNBOOK.md:52-90`). It restarts with dockerd, which is exactly the
event it should observe (MDT-047). host-setup owns the `/run/cgprofile` directory, the `cgprofile-observer` group and the slice
names, yet calls the daemon "out of scope" (`README.md:72-75,450-455`); `infra/cgprofile.slice` is installed by hand.

**Decision record (operator, 2026-11-04).** Option B of the platform memo: a native systemd service from a RELEASED WHEEL, installed
and pinned by host-setup the way `mdt-psi-guard` is; the ciu deployment of the host daemon is retired after one release with a
dated migration window (no alias).

**Design.**

- `host-setup.env`: `CGPROFILE_WHEEL` (pinned version plus sha256; refused when empty while the service is enabled) and
  `CGPROFILE_ENABLE`. `install.sh` installs the wheel into `/opt/cgprofile` (venv, `--no-index`, hash-checked), renders the unit
  from `cgprofile host-unit --print` (CP-42; the unit template ships inside the wheel so cgprofile keeps a single source) with
  host-setup's tier names (`dev-gates.slice` etc.), and restarts only on drift (wheel hash or rendered unit changed).
- Protection of the host from a misbehaving daemon is part of the rendered unit; the values and rationale are CP-42. host-setup
  owns `cgprofile.slice` (replacing the hand-installed `infra/cgprofile.slice`) and its env-tunable limits.
- The daemon keeps its write rule: `cgroup.kill` only on exact gates-slice container cgroups (PROTOCOL.md section 3). The unit must
  not widen this; no `Delegate=` beyond what the existing placement needs, reviewed in CP-42.
- Socket paths, groups (CP-43) and the observer layout stay unchanged, so run-gate, assay and cockpits need no change.
- README: the scope statement changes from "daemon out of scope" to "host-setup installs and pins the cgprofile service".
- `check.sh`: service active, version equals the pin, `ctl.sock` and the observer socket present with the expected groups.
- Retirement: after one release the ciu compose, `ciu.toml.j2` host profile and `infra/cgprofile.slice` hand-install steps are
  deleted from `scripts/cgroup-profiler` (CP-44) and RUNBOOK edits dropped.

**Acceptance.** Fake-root install tests (fresh, no-op rerun without restart, pin drift restarts once, empty pin refused);
`systemd-analyze verify` on the rendered unit; r1002 first, then the shared host in an idle window; run-gate and assay
`ctl`/observer probes unchanged after the swap.
**Non-mdt path.** run-gate, assay and tools stay usable without the service on a host that never ran host-setup (existing
`docker exec` fallback and the explicit infrastructure refusal when the observer is required): add a test per consumer, owned in
RG-152 / B212.

## MDT-056 - local base images through OCI-layout build contexts on `mdt-managed` (2026-11-04)

**Status:** OPEN. Packages (memo order): tester-chain pilot (TB-14), wheel-builder, srdm (SRDM entry), cmru `build_contexts`
(KI-113), pwmcp investigation (PWMCP-23), then docs here.

**Problem.** `mdt-managed` (rootless buildkitd, `mdt-buildkitd.service`) is a remote builder and cannot see dockerd's image store,
so `FROM <local-tag>` fails. Today the estate bypasses it with the docker driver: `cmru/build-initial-standalone.sh:312,320`,
`tester-unified/build-image.sh` with a local base, `wheel-builder/Dockerfile:35`, srdm `go-base.sh:39`. A bypass loses the
governance slice, the btrfs cache and GC (MDT-024), the fail-closed shim and the guard, and identifies bases by a mutable tag
(TB-11).

**Decision record (operator, 2026-11-04).** Keep `mdt-managed` as the ONLY builder. Local bases are exported as OCI layouts and passed
as named build contexts pinned by digest: `--build-context <ref>=oci-layout://<dir>@sha256:<digest>`
(`BUILDKITD-FLOWS.md:151` already names this; `release-repack.sh:101,113` is prior art). The bypass survives only for the fresh-host
bootstrap (`build-initial-standalone.sh`, before `mdt-managed` exists), spelled `--builder default`, never chosen through an
environment variable.

**Design.** Pilot first: confirm that a context key with a tag (`tester-base:local`) maps a tagged `FROM tester-base:local`. Then the
pattern rolls to each chain through its own entry. This entry owns the shim caveat (MDT-057), the docs (BUILDKITD-FLOWS section 6
and CONSUMERS; remove the documented bypasses) and a guard test that no committed script selects the default builder by
environment variable after the migration.

**Acceptance.** Pilot on tester-base to tester-unified measured against the bypass (wall time, cache hit, slice accounting);
digest of the base lands in the derived image's labels; docs list `--builder default` only under "fresh-host bootstrap".
**Non-mdt path.** The same build scripts run on a plain Debian host with an ordinary buildx builder or the classic docker driver:
the layout and context flags are accepted by both; test the script's argument construction without mdt.

## MDT-057 - live check: does the shim remap `BUILDX_BUILDER=default` back to `mdt-managed` on the host? (2026-11-04)

**Status:** OPEN (check only; no code change until it is run). Memo finding, derived from the code and not checked live.

**Problem.** `scripts/docker-buildx-shim.sh:71-76` forces `mdt-managed` when `BUILDX_BUILDER` is unset or `default`. The documented
bypass `BUILDX_BUILDER=default` may therefore work only inside the devcontainer; on the host it may need `--builder default` or
`MDT_BUILDX_PASSTHROUGH=1`. Several documents and scripts (pwmcp release, tester-base docs) spell the bypass by environment.

**Check steps (read-only except a throwaway build; use a trivial `FROM scratch` Dockerfile in a scratch directory; no
all-container inventories).**

1. On the host shell and inside the devcontainer: `docker buildx ls` and `docker buildx inspect` to record the effective default.
2. `BUILDX_BUILDER=default docker buildx build --progress=plain -t mdt-shim-probe:1 <scratch>`; record which builder ran (the
   plain output names the driver) in each location.
3. Repeat with `--builder default` and with `MDT_BUILDX_PASSTHROUGH=1`; repeat with plain `docker build`.
4. Record the table in `BUILDKITD-FLOWS.md` and list every committed script whose spelling does not work where it runs.

**Acceptance.** The table is committed; a failing spelling is either fixed to `--builder default` (MDT-056 rule) or the shim is
changed with a test; the documented bypass text matches measured behavior. Remove the probe image by exact name afterwards.

## MDT-058 - docs: attach to a remote runner (docker context over ssh, VS Code, restricted key) (2026-11-04)

**Status:** OPEN (docs). Companion of NC-07 (the key and the runner README recipe). Wait for `fix-r1002-findings-20261104` to merge.

**Decision record (operator, 2026-11-04).** r1002 access is `docker context create r1002 --docker host=ssh://runner@<host>` plus VS Code
"Attach to Running Container" for the IDE, and a restricted ssh key with a forced `command=` into the container shell for a
terminal. No sshd in any container (a supervisor, persistent host keys, a published port and a second auth surface, with no
isolation gained since the container already holds the Docker socket).

**Design.** A section in the devcontainer docs: prerequisites (the user's key is in `runner`'s authorized_keys), creating the
context, attaching, which uid the shell gets, and the security note that `runner` is in the docker group, so its keys belong only to
operators and the controller. Cross-link NC-07. No code.

**Acceptance.** A reader can attach from a laptop in the documented steps; the doc states the single-uid rule as it stands today and
points at RG-151 phase 4 for the relaxed form. A docs-sync test (if the cockpit docs have one) passes.

## MDT-059 - relax the "uid 1003" runner/cockpit rule after lane-uid phase 4 (MDT-052, NC-04) (2026-11-04)

**Status:** BLOCKED on RG-151 phase 4 (and TB-13). Operator decision 2026-11-04: lanes run as the checkout owner; the baked uid 1003 rule retires.

**Problem.** MDT-052 (a `docker run` cockpit has no automatic remap) and NC-04 (a runner account must be uid 1003) exist only
because tester-unified bakes 1003 and git refuses a differently-owned tree.

**Design / acceptance.** After phase 4, update MDT-052 and NC-04 to "any uid, as long as the checkout, the CoW root and the cockpit
user share it"; delete the 1003 wording from the README, the runner scripts and their tests, keeping the RG-150 owner preflight as the
safety net. Lanes that need Docker add the socket gid derived by `stat` (MDT-054). A test pins the new wording and that
the provisioning script no longer hardcodes 1003.

## MDT-062 - cgprofile cutover from the 1.x container: the installer cannot drain it and does not stop it (2026-11-05)

**Status:** SUPERSEDED 2026-11-08 (H1g'). `mdt-cockpit/host-setup` no longer owns or ships the cgprofile installer or `--cgprofile-only`; service migration and any remaining old-daemon cutover belong to cgprofile's installer. The observed rollout details below are historical context, not a current host-setup work item.

**Observed.** (1) On a host whose 1.x `cgprofile-host-daemon` container still serves `/run/cgprofile/ctl.sock`, `cgpd` 2.0.0 refuses to start
("another daemon already serves"), so `install.sh --cgprofile-only` only works after the container was stopped by hand; the stop, `docker update
--restart=no` and the later removal were manual steps. (2) The drain looks at the NEW venv's `cgpctl sessions`, which needs ctl contract 2, so
against a 1.x daemon it fails ("sessions needs ctl contract 2") and the drain cannot count live sessions; the operator used the container's own
`cgprofile ctl status` instead. (3) `mdt-host-check.sh` ends with a FAIL for `caller-group` when run as root (CP-57).

**Design.** A cutover step in `mdt-cgprofile-install.sh`, only when `/opt/cgprofile/current` is absent and a container named `cgprofile-host-daemon`
exists: list live sessions through `docker exec cgprofile-host-daemon cgprofile ctl status` (contract 1), wait/refuse exactly like the drain, then
`docker update --restart=no` + `docker stop` it, and print the rollback line (`docker start cgprofile-host-daemon` after `systemctl stop cgpd`). The
container removal stays an operator step. `check.sh` ignores `caller-group` for uid 0 until CP-57 ships. Tests with the fake docker.

## MDT-063 - debian-install-v2 must run the iobench calibration and persist `result.json` for host-setup (2026-11-05)

**Status:** OPEN (host-setup consumer shipped, producer deferred). Found on r1002/v1001: the static fallback (500 IOPS) throttled every gate lane on an
idle 8000-IOPS disk.

**Done (MDT-TESTHOST-IO).** `HOST_ROLE=dedicated-dev` makes `dev.slice` use `proposals.caps.dev_aggregate` of `/var/lib/iobench/result.json`
(identity-verified, `scripts/mdt_iobench_caps.py`), no static fallback; see host-setup/README.md "Host role".
**Open.** debian-install-v2 still only runs the older `iocost_coef_gen` (io.cost model, `io-benchmark.json`). Add, after that tool and before the
throwaway partition's teardown: unmount, `iobench target init <partition> --i-own-this-device --expect-size --expect-partuuid`, then
`iobench calibrate --target <partition> --work-dir <dir> --profile quick|standard --installer --confirm-quiet-window --protect-cgroup ... --abort-psi 40`
so `result.json` and `calibration.json` land in `/var/lib/iobench/`; new config `run_iobench_calibration` (default false), a `warned` (advisory) step on
failure, resume handling like `io_benchmark`. Needs the `iobench` wheel and `fio` in the target; fake-`iobench` unit tests in `test_io_benchmark.py`.
**Live verification** needs a vm-system run (a real disk with a tail partition): the result must show `caps.dev_aggregate` and `install.sh` on the
VM with `HOST_ROLE=dedicated-dev` must render those numbers. Until then the operator runs `iobench calibrate` on the test hosts by hand.

## MDT-064 - iobench `proposals.iocost_qos` is not consumed: latency targets for the responsiveness guard (2026-11-05)

**Status:** OPEN. The goal of the iogov/iobench work is high device utilisation WITH responsiveness. On `dedicated-dev` the absolute ceiling now
follows the measured capacity, so the guard is the gate/BuildKit sub-ceilings, IOWeight, the PSI guard and `IO_COST_QOS` (still operator-written, default `off`).
iobench emits only `min`/`max` QoS proposals (rule R6), no read/write latency target, so the wizard cannot propose `rlat`/`wlat`.
**Design.** Agree the latency target (e.g. p95 read latency a multiple of the measured idle latency) in iobench R6, extract it in the wizard like
`IO_COST_MODEL`, and on `dedicated-dev` propose `IO_COST_QOS` from it; validate live on a test host (PSI-io with and without) before defaulting.

## MDT-065 - btrfs/page-cache read amplification under an R2 campaign container (2026-11-05)

**Status:** OPEN, UNDER MEASUREMENT (no cause is claimed). See host-setup/README.md "Observation: read volume under a low IOPS cap".
**Observed.** One R2 campaign container read 15-30 GB within an hour on an 8 GB host while the IOPS cap was 300. Candidates are only hypotheses
until measured: page-cache churn with little RAM, btrfs read amplification on `/srv/cow`, the workload itself. Tracked by assay B217/B218 and
dstdns `docs/testing/PARALLELISM-AND-PERFORMANCE.md`.
**Acceptance.** A measurement (per-container `io.stat` rbytes vs. block-layer rbytes, cache hit ratio, `iostat`) on a dedicated host with the cap lifted;
the result is recorded in the README observation and decides whether host-setup needs a change.

## MDT-071 - host-setup cannot express "no IO limit"; dedicated-dev hosts need a pin above the device or a measurement (2026-11-05)

**Status:** OPEN. Found lifting the 8000/4000 IOPS, 300M/250M pins on r1002 and v1001 (both `/dev/vda`, virtio, 512G disk, sole tenant).
**Gap.** `DEV_IO_*_MAX` accepts only positive values ("0 is refused"), and on `HOST_ROLE=dedicated-dev` with no pin and no `/var/lib/iobench/result.json` the
installer refuses (exit 2). `DEV_SUBSLICE_IOPS_PCT` is 1-100, so the gate/BuildKit sub-ceiling can only equal `dev.slice`, not vanish. The hosts have no iobench
install and no result (MDT-063 is the producer), so the least-limiting expressible config is pins set above the measured device ceiling
(applied: 2G / 1G bytes/s, 60000 / 40000 IOPS, `DEV_SUBSLICE_IOPS_PCT=100`; a 20 s fio probe outside dev.slice read 98k randread IOPS / 4.3 GB/s and wrote
67k IOPS / 1.4 GB/s, burst-grade because of the hypervisor cache, so the pins are not sustained device facts).
**Design.** Accept an explicit `unlimited` (renders `max`, skips the reconcile read-back mismatch) for the `dev.slice` pins and `DEV_SUBSLICE_IOPS_PCT=off`,
only under `HOST_ROLE=dedicated-dev`; keep IOWeight and the PSI guard. Also: the installer reads `../../scripts/debian-install-v2/tools/` outside `host-setup/`, so a
`host-setup/`-only copy fails midway (after rendering units) with "cannot stat iocost_coef_gen.py"; either vendor those two files or check for them up front.
Also seen: the reconcile logs `Permission denied` writing `io.bfq.weight` on docker scopes (non-fatal, scheduler is not bfq).
## MDT-072 - dedicated-dev memory: follow-ups after MDT-TESTHOST-MEM (2026-11-05)

**Status:** PARTLY DONE. Done: `DEV_HOST_MEMORY_KEEP`, derived dev.slice limits, no tier ceilings, host protection, PSI watch of the host services, check.sh, wizard prompts,
tests (host-setup/README.md "Memory of the dedicated-dev role"). Open:
1. The wizard's memory step (step 3) still asks the tiers' MemoryHigh/MemoryMax on this role although they are ignored; skip those prompts when `HOST_ROLE=dedicated-dev`.
2. `memory.max` is RAM only, so on an overcommitted host the combined RAM+swap bound is not enforceable by one cgroup knob; a pressure-driven reclaim of the dev tier
   (psi-guard stage 2 on dev.slice itself, `memory.reclaim`) was not built. Measure whether MemoryHigh plus the OOM order is enough under 4-5 parallel mdt-cockpit gates.
3. psi-guard `enforce` was never validated live on the test hosts (it ships `report`); its CPU `some` 25% signal on docker.service may fire in a CPU-saturated lane burst.
4. Measure the zswap pool (`max_pool_percent` 25) against a real overcommit run before changing it.
5. Unmeasured: `io.max` on dev.slice throttles the swap-out of dev pages; reclaim waiting on a throttled device may stall other cgroups (priority inversion). Measure
   host memory PSI and `kswapd` behaviour in an overcommit run with the IO caps in force.
6. The memory.swap.max of dev.slice is `SwapTotal - 512M` (MDT_HOST_SWAP_MARGIN), not derived from `DEV_HOST_MEMORY_KEEP`; the two formulas differ (documented, not unified).
Review fixes (2026-11-05): ssh no longer gets an OOM drop-in (F1), default keep 1G with floors (F2/F5), bounds documented without a memory.max backstop claim (F3), `0` pins mean omit (F4), stale drop-ins removed and mode 0644 (F6).

## MDT-069 - `mdt-psi-guard.py` writes `cpu.weight` and thereby silently resets `cpu.idle` on the dev slices (2026-11-05)

**Status:** OPEN. Severity P3. Found by the Geekbench exploration on r1002/v1001 (`reports/geekbench-explore-REPORT.md`, finding 1, in the release-program reports dir).

**Observed.** `/usr/local/sbin/mdt-psi-guard.py` writes `cpu.weight` (1 under pressure, then restores the saved value) on `dev-gates`, `dev-background` and `dev-buildkitd`.
In the kernel a `cpu.weight` write resets `cpu.idle` to 0 (and leaving idle resets the weight to 100). An operator or benchmark that sets `cpu.idle=1` on
`dev.slice/dev-gates.slice` loses it within minutes; on r1002 the first Geekbench launch lost idle that way, and the run wrapper had to re-assert `cpu.idle=1` every second.
The guard's restore can also leave a wrong weight (dev-gates read 100 instead of 20 after a short run).

**Design.** The guard reads `cpu.idle` together with `cpu.weight` before acting; on a slice with `cpu.idle=1` it skips the weight write (idle is already the strongest
deprioritisation), and when it does write a weight it re-applies `cpu.idle` afterwards (write `cpu.idle` last, since it also resets the weight). Document the interaction in host-setup/README.md.

**Acceptance criteria.** Unit test with a fake cgroup tree: a slice with `cpu.idle=1` keeps it through a pressure cycle and its weight is not rewritten; a slice with `cpu.idle=0` still
gets the 1 -> original weight cycle and ends with the exact original values.

## MDT-070 - Geekbench result pages are Cloudflare-challenged; the host CPU benchmark's `--resolve` page-parsing fallback cannot work (2026-11-05)

**Status:** OPEN (documentation and tool change; related to MDT-066/067/068 on branch rigor-hb23-20261105, which are not on main yet). Severity P3.
Evidence: `reports/geekbench-explore-REPORT.md` (finding 3) and `reports/geekbench-pwmcp-REPORT.md`.

**Observed.** `browser.geekbench.com` and `www.geekbench.com` answer scripted clients (curl with the tool's User-Agent, from the devcontainer, r1002 and v1001) with the Cloudflare
"Just a moment" challenge (HTTP 403). The PWMCP browser (chrome-windows and edge-windows profiles) does not pass the managed challenge either (page never resolves within 100 s). So the HB-1
`--resolve` / result-page fallback can never retrieve a real result page; no result-page fixture could be captured. The stdout "Benchmark Summary" is the only reliable score source.
Also seen: GB 6.7.0 ended with `unknown error (internal code 35)` at upload on r1002 twice (no scores printed, so no stdout fallback either); GB 7.1.0 uploaded fine.

**Design.** Treat the result page as best-effort at most: the HB-1/HB-2/HB-3 docs and the tool's help state that scores come from stdout, that the `--resolve` fallback is unavailable behind the
challenge, and that an upload failure or blocked page is a `warned` step (re-measure later, never fabricate a score). Prefer GB7 for the installer step; either remove the fallback or keep it
documented as unreachable. When MDT-066..068 land, cross-reference this entry from them.

**Acceptance criteria.** The HB docs and the tool text carry the Cloudflare/stdout-only statement; a test feeds the tool a challenge page and a missing-summary stdout and expects `warned`
with no score, not a parse of the challenge HTML.
## MDT-066 - HB-2 live verification: debian-install-v2 `run_cpu_benchmark` in a vm-system run (2026-11-05)

**Status:** OPEN (hermetic tests done; nothing has run against a real guest).
**What exists.** Installer step `run_cpu_benchmark` (default on, advisory) with mocked-tool tests: success, parse failure (exit 4), refusal, timeout, unpinned architecture, tool tamper, resume, dry-run.
**Acceptance (needs a vm-system run on the `case-b` image).** (1) With the Browser egress allowlist (`scripts/debian-install-v2/docs/DESIGN-GUIDE.md`, VM section) the step ends `success`, `/var/lib/mdt/host-facts/cpu-benchmark.json` is 0644 and the claim file 0600, the guest reports `QEMU TCG CPU`, and the single `Install complete` milestone carries the scores and result URL but no claim link. (2) With egress locked the step ends `warned` and the install still completes. Free Geekbench under TCG can exceed 15 minutes; check the 1800 s tool timeout holds. (3) Record the real Geekbench 7 stdout from the run and replace `gb7-stdout-ASSUMED-SHAPE.txt` (HB-1 follow-up).

## MDT-067 - HB-3 live verification: `install.sh cpu-benchmark` on the dev host, r1002 and v1001 (2026-11-05)

**Status:** OPEN (operator hands; nothing has run on a real host).
**What exists.** `sudo ./host-setup/install.sh cpu-benchmark [--force|--resolve]`, `mdt-host-check.sh` section "per-host CPU benchmark", hermetic tests over fixture files.
**Acceptance.** Run once on the dev host in a quiet window (CPU `some avg10` below 5), then later on r1002 and v1001 (hosts already installed by debian-install-v2 before `run_cpu_benchmark` existed; NEVER nano1). For each: `cpu-benchmark.json` 0644 with both scores, claim file 0600, `mdt-host-check.sh` shows the OK line, a second run is refused without `--force`, and after a VM resize `mdt-host-check.sh` shows the STALE warning. Open the claim link once per host to attach it to the operator's Geekbench account.

## MDT-068 - Geekbench archive pin for aarch64 (2026-11-05)

**Status:** OPEN. `scripts/debian-install-v2/tools/cpu_benchmark.pins.json` holds only x86_64 (Geekbench 7.1.0, sha256 read from the real download in HB-1). An architecture without a pin is `warned` by the installer and refused by `install.sh cpu-benchmark`, never run unverified.
**Acceptance.** Download the `LinuxARMPreview` 7.1.0 archive on an aarch64-capable path, record its sha256 in the pins file, add a test, and bump the pins together with the tool's `.sha256` where needed.

## MDT-073 - VM-only leak test for the load-tier ceilings (AD2-0 gate) (2026-11-05)

**Status:** OPEN. AD2-0 (gates/background MemoryMax = dev.slice MemoryHigh, MemorySwapMax = dev swap minus the protected Lows) is covered by hermetic host-setup tests and verified live by `mdt-host-check.sh`; the behavioural gate of the addendum needs a real VM.

**Next steps.** In `testing/vm` (next to `vm-governance-acceptance.sh`): install host-setup on `HOST_ROLE=dedicated-dev` with swap, start a leaking process in a `dev-gates.slice` leaf, hold an interactive scope with a working set above its Low.
**Acceptance.** The leaf ends with `oom_kill` in `dev-gates.slice` `memory.events` once its swap is spent, while the interactive scope's memory PSI stays below 5 % and its allocations are not throttled. Never run on a live host.
## MDT-074 - dedicated-dev shared tree cache host plumbing (AD2-2) (2026-11-05)

**Status:** DONE on branch `mdt-cache-slice-20261105` (host half). `dev-cache.slice`, `/srv/tree-cache` and `/srv/tree-upper` tmpfs mount units, `tree_cache` block of `/etc/mdt/host-capacity.json`, `mdt-tree-cache-gc.timer`, env keys, host-check, tests (host-setup/README.md "Shared tree cache of the dedicated-dev role"). Applied live on v1001 then r1002.
**Open (other packages / follow-ups).**
1. AD2-3: cowtree `cache` (ensure, lease, last-used, gc) must publish exactly the directory/lease format of `mdt_tree_cache.py`; once it does, the timer may call `cowtree cache gc` instead of the built-in GC (the hosts have no cowtree wheel in `/opt/mdt`).
2. AD2-5: run-gate checks `CGROUP_PARENT_DEV_CACHE` (LoadState) and `MDT_TREE_CACHE_ROOT`/`MDT_TREE_UPPER_ROOT`; the host does not export these env keys itself (they travel the `containerEnv` path like `CGROUP_PARENT_DEV_GATES`): the devcontainer template and the runner env still need them, the values are in `host-capacity.json`.
3. The GC cannot see a lane that holds a key only through a bind in a mount namespace it cannot read; it relies on the lease (flock) plus every /proc/*/mountinfo, and keeps everything when one mountinfo is unreadable. Verify with a real lane in AD2-5.
4. `shared-prod` opt-in is not offered (design: opt-in on shared-prod); add a key only when a consumer needs it.
5. `DEV_CACHE_MEMORY_LOW` default is empty: size it from the first measured populate (M-6 / AD2-6).
6. The upper area (`/srv/tree-upper`, 10 % of RAM) is one shared tmpfs with no per-lane limit: one lane filling it gives ENOSPC to the others. run-gate (AD2-5) must classify ENOSPC in an upper as infrastructure/inconclusive, never a product failure.
7. On a host without swap the two tmpfs areas (up to 35 % of RAM by default, at most 50 %) are unevictable.

## MDT-075 - test-render.sh stopped on unresolved `@DEV_CACHE_*@` in dev-cache.slice.in (2026-11-05)

**Status:** DONE on branch `mdt-render-dev-cache-20261105`. Root cause: the AD2-2 templates (`dev-cache.slice.in`, tree mounts, GC units) are rendered live by `mdt_tree_cache.py`, but `tests/test-render.sh` pushed every `units/*.in` through install.sh's bash `render()`, whose `RENDER_VARS` does not (and must not) carry their keys. Fix: `mdt_tree_cache.OWNED_TEMPLATES` is the single list; `test-render.sh` skips those in the bash loop and renders them with `mdt_tree_cache.py render` (the installer's own code path) from the shipped example, failing on any `@X@`; `_render()` now raises on a token it was not given, so the live install also refuses to ship one. `tests/test_tree_cache.py::TemplateWiringTests` asserts every template is wired to exactly one renderer. No behaviour change on a host (empty `DEV_CACHE_MEMORY_LOW` still drops the `MemoryLow=` line, `DEV_CACHE_ZSWAP_WRITEBACK` defaults to yes).
**Open.** None. Hosts need no re-run for this fix (test-only plus an install-time guard).

## MDT-076 - template VS Code settings never reach a persisted home's machine settings (2026-11-05)

**Status:** OPEN; documented on branch `mdt-vscode-excludes-20261105` (template comments, `templates/vscode/README.md` "Template settings and the persisted home", host-setup README "inotify watch budget"); the same branch adds `.assay` to the excludes and `search.followSymlinks: false`.
**Observed.** Container `mdt-cockpit-vb-vbpub`, created 2026-11-03, carried `files.watcherExclude`, `search.exclude` and `git.repositoryScanIgnoredFolders` (added by `68a4cb487`, 2026-10-26) in its `devcontainer.metadata` label. `~/.vscode-server/data/Machine/settings.json`, last written 2026-10-23, held only `python.defaultInterpreterPath`, so none of the MDT-003 excludes were active: the VS Code file watcher used 920 MiB RSS, and Python Environments 1.42.0's per-event whole-workspace rescans piled up to 192 concurrent `rg` processes.
**Cause.** Since MDT-004 the home (with `~/.vscode-server`) persists across rebuilds, and a rebuild does not rewrite an existing machine settings file.
**Open.**
1. A supported refresh: for example a finalize step that merges the template's `customizations.vscode.settings` into the machine settings file (template keys win, user-only keys kept), or a `mdt` verb that does the same. It needs a source for the effective settings inside the container (the workspace `devcontainer.json` as JSONC, or the metadata label).
2. A check that warns when a template key is missing from the machine settings file.
## MDT-077 - nothing in host provisioning publishes the run-gate gates cap; a reinstalled runner host refuses every dstdns lane (2026-11-05)

**Status:** DONE on branch `mdt-admission-20261105` (found on v1001 after the 2026-11-05 reinstall; review findings addressed in the fix round). The finding text was first filed on `mdt-admission-cap-gap-20261105` (`5e37e9cf8`); this entry supersedes it.
**Symptom.** dstdns sets `[admission] enabled = true`, `unreadable_policy = "refuse"`. On a fresh Docker daemon no `ciu-admission-<g>` object exists, so every lane ends `NOT_RUN no-headroom` ("no gates limit is published and unreadable_policy=refuse"); the v1001 dstdns chain retried 11+ times at 3-minute intervals while the host sat ~65% idle.
**Root cause.** The cap is a Docker-daemon object (a never-started `ciu-admission-<g>` container carrying `ciu.admission.tiers.gates.max_concurrent`), published only by the operator verb `run-gate admission set --max-concurrent N --unreadable-policy refuse`. It was not part of `host-setup/install.sh`, `debian-install-v2` or the runner provisioning scripts (`provision-runner.sh`, `setup-cockpit-run-gate.sh`); `scripts/netcup/runner/remote-gate.py` only DETECTS the gap (`admission_check`, fail-closed) when a dstdns gate is submitted, and the value was never derived from host policy. A reinstall (new Docker daemon) loses the object. AGENTS.md says limits are host-owned and "a tool asks the host policy", but no host policy provided this number. The numbers actually published were hand-picked: v1001 2, r1002 first 1 then 4 (both 4 vCPU / 8 GB), so "about one gate per 2 vCPU on both hosts" in the first filing was only true of v1001.
**Interim (done on v1001, 2026-11-05).** `run-gate admission set --max-concurrent 2 --unreadable-policy refuse` run in the cockpit from a dstdns checkout.
**Fix.** `host-setup/mdt_admission.py` derives the cap from the effective minimum of `dev.slice`'s `DEV_CPU_QUOTA` and `dev-gates.slice`'s `DEV_GATES_CPU_QUOTA`; auto shares come from the same `mdt_capacity` derivation used by install.sh. The formula remains `max(1, min(floor(gates CPU cores / ADMISSION_CPUS_PER_GATE), floor(gates MemoryHigh / ADMISSION_MEMORY_PER_GATE)))`, defaulting to 2 CPUs and 2G per gate (4 vCPU / 8 GB dedicated-dev gives 2). Publishing uses `show`, `set` or the safety-verified `set --replace` path, then reads the cap back. The full install's default `ADMISSION_PUBLISH=auto` publishes when no readable cap exists and preserves a differing readable cap; explicit `install.sh --admission-only` reconciles differences. `missing` never replaces, `replace` lets full installs reconcile, and `off` disables management. `ADMISSION_MAX_CONCURRENT` always supplies the desired value when pinned. An explicit reconcile can repair an unparseable published limit with `--replace`. The verb runs with a minimal private `run-gate.toml` and `CGROUP_PARENT_DEV_GATES=dev-gates.slice`, on the host or inside the cockpit. Missing run-gate/cockpit/ticket image and an unreachable Docker daemon produce distinct NOTE reasons; attempted publish failures remain non-zero. `ADMISSION_RUN_GATE` rejects a leading dash. `mdt-host-check.sh` fails on a missing cap and pinned mismatch, warns on an unpinned difference, and warns when a prerequisite makes the cap unreadable. Tests cover the installer function with fake docker and run-gate commands in a temporary PATH. The registered `host-setup-admission` Run-Gate lane runs the requested subset in tester-unified. User-facing guidance is in the host-setup README, MDT DESIGN-GUIDE, and CONSUMERS.md.
**Open.** Live verification on a host is not done (no host access in this fix round). After a reinstall, operators must run `sudo ./host-setup/install.sh --admission-only` once run-gate and the ticket image exist; set `ADMISSION_COCKPIT` first when run-gate lives in a cockpit. r1002 should pin `ADMISSION_MAX_CONCURRENT=4` before an explicit reconciliation if the hand-raised limit is still required. The reinstall runbook is not tracked; the host README and consumer guide carry the adoption instruction.
## MDT-078 - retire modern-debian-tools-python-debug (2026-11-05)

**Status:** DONE on this branch. Removed the predecessor directory and its CMRU project registration; repointed live image, install, code, and documentation references to `mdt-cockpit`. Kept runtime state paths and historical records intact.

**Open items:**
1. Build-check nyxloomd against the new `mdt-cockpit` base image.
2. Existing GHCR packages under the retired name remain in place by operator decision.

## MDT-079 - OpenCode staging checks the launcher package only; the installer needs the platform package, and nothing verifies the installed result (2026-11-08)

**Status:** OPEN; priority should-fix. Source: `reports/opencode2-REVIEW.md` N1 (review of `53e191341`, merged in `1b5a212f5`).
**Finding.** `stage_tool_artifacts.py:_resolve_opencode_version` (line 623) checks that `@opencode/cli@X` exists on the registry, but `install_ai_cli_tools.py:461-487` installs `@opencode/cli-linux-<x64-baseline|arm64>[-musl]@X`. Registry timestamps show the platform packages land 1-5 minutes after the launcher (2.2.4: launcher 16:53:18, baseline 16:55:37; 2.2.6: launcher 23:33:40, baseline 23:35:14), and the launcher's own postinstall documents the race. A staging run in that window records X and the image build fails with ETARGET (loud, retry works, a flaky red build). After the install nothing checks that the installed package is X, and `require_file` (line 346) does not check the exec bit.
**Fix.** In `_resolve_opencode_version` also require `@opencode/cli-linux-x64-baseline@X` and `@opencode/cli-linux-arm64@X` (via `_fetch_json(registry/<pkg>/<ver>)`, as `_resolve_pinned_npm_version` does) and raise a StageError saying "platform package not published yet". Add `verify_opencode_install(prefix, version)` beside `verify_codex_install`: read `<prefix>/lib/node_modules/<pkg>/package.json`, require `version == OPENCODE_VER`, and use `require_executable_binary` instead of `require_file`.
**Close when.** A test with the launcher published and a platform package missing fails staging with the new message, and a fake tree with a wrong installed version or a non-executable binary fails the verifier.

## MDT-080 - the Dockerfile's opencode manifest probe never fails, and the staging fixture still reports 1.19.4 (2026-11-08)

**Status:** OPEN; priority should-fix. Source: `reports/opencode2-REVIEW.md` N2.
**Finding.** `Dockerfile:1066` records `opencode=$(opencode --version 2>&1 | head -1 || true)`: the never-fails pattern the codex-image work replaced for `claude` and `codex` with a guarded probe (`v="$(tool --version 2>&1)" || fail; test -n; echo ${v%%$'\n'*}`). A broken opencode (for example a wrong-libc ELF, "No such file or directory" from a missing `/lib/ld-musl-*`) is recorded as an error string and the build passes. `test_oci_tool_staging.py:1041` keeps `VERSION_BANNERS["opencode"] = ("1.19.4", "1.19.4")`, a 1.x banner for a 2.x tool.
**Fix.** Use the guarded probe for opencode (`v="$(opencode --version 2>&1)" || { ...; exit 1; }; test -n "${v}" || ...; echo "opencode=${v%%$'\n'*}"`), add `"opencode"` to the `@pytest.mark.parametrize("tool", ["claude", "codex"])` list of `test_a_broken_agent_cli_fails_the_manifest_step`, and set the fixture to `("opencode v2.2.6", "2.2.6")`; `normalise_version('opencode','opencode v2.2.6') == '2.2.6'` was verified.
**Close when.** A broken opencode shim fails the manifest step in the parametrized test.

## MDT-081 - musl detection uses `platform.libc_ver()`, which is likely empty on musl hosts (2026-11-08)

**Status:** OPEN; priority nit (the image is Debian trixie, so latent). Source: `reports/opencode2-REVIEW.md` N3.
**Finding.** `install_ai_cli_tools.py:470-472` selects the musl platform package from `platform.libc_ver()[0]`; on Alpine CPython's `libc_ver()` most likely returns `('', '')`, so the glibc package would be selected and install "successfully". The musl packages carry no `libc` field, so npm cannot catch it. Mutation M5 (musl suffix dropped, line 334) survived: no musl test exists.
**Fix.** Detect musl with `Path("/etc/alpine-release").exists()` or the `ldd --version` text (as OpenCode's own postinstall does) and add a musl case to `TestInstallOpencode`.
**Close when.** The musl test passes and M5 re-applied fails it.

## MDT-082 - docs for OpenCode 2: stale 1.x claims in DESIGN-GUIDE and no CHANGES entry for the 1.x to 2.x jump (2026-11-08)

**Status:** OPEN; priority nit. Source: `reports/opencode2-REVIEW.md` N4 and N6.
**Finding.** `docs/DESIGN-GUIDE.md:590` keeps "yes (1.19.3 to 1.19.2 and back, measured)", a measurement of the retired 1.x package, for a row that now describes 2.x, and cites `opencode upgrade --major`, which is not in the 2.2.6 `upgrade --help` (only `--method/-m`). The docstring "measured on 1.19.5 and 2.2.6 alike" in `tests/runtime/test_ai_cli_update.py` was not reproducible without running `upgrade`. `CHANGES.md` has no entry for the user-visible 1.x to 2.x jump (v2 reads `opencode.jsonc` with a `providers` schema and keeps sessions in `opencode.db`; persisted 1.x config and data may need migration).
**Fix.** Re-measure the update on the 2.x image with the runtime test and update the sentence, or mark the cell "1.x measurement; 2.x covered by tests/runtime/test_ai_cli_update.py". Add the CHANGES entry naming the config schema and data-file change.
**Close when.** The DESIGN-GUIDE row names no flag absent from `opencode upgrade --help`, and CHANGES carries the entry.

## MDT-083 - OpenCode staging and installer tests: surviving hand mutants (2026-11-08)

**Status:** OPEN; priority nit. Source: `reports/opencode2-REVIEW.md` N5 (11 hand mutants: 7 killed, 4 survived; M5 is MDT-081).
**Finding.** M4 (drop the explicit-pin early return, `stage_tool_artifacts.py:625-626`) survived: no test sends a pin through `_resolve_opencode_version`. M9 (size-report glob reverted to `opencode-linux-*`) survived: nothing tests the `LEAVES` globs. M10 (README update command reverted) survived: `test_docs_in_sync` pins only DESIGN-GUIDE for that string. M3 (tripwire `theirs > ours` to `>=`) is killed only incidentally by an unrelated test; no test names the equal-major case.
**Fix.** One test that an explicit pin (`"2.0.0"`) skips the tripwire even when the legacy fake says 3.0.0; a `LEAVES` glob check (or drop the globs); the README string in the `test_docs_in_sync` list; an explicit "same major does not raise" assertion.
**Close when.** M3, M4, M9 and M10 re-applied one at a time each fail a named test.

## MDT-084 - Codex launcher check: a timeout leaves a grandchild process (2026-11-08)

**Status:** OPEN; priority nit. Source: `reports/codex-image-REVIEW2.md` (B1 section).
**Finding.** `verify_codex_launcher` runs `subprocess.run([launcher, "--version"], timeout=CODEX_LAUNCHER_TIMEOUT_SECONDS)` (`install_ai_cli_tools.py`, around line 304). On timeout `subprocess.run` kills only the launcher process; the reviewer's `sleep 300 &` grandchild survived. The real native `--version` exits at once, so the effect is cosmetic.
**Fix.** Start the launcher in its own process group (`start_new_session=True`) and kill the group on timeout, with a test using a launcher that backgrounds a child.
**Close when.** The test shows no surviving child after the timeout.

## MDT-085 - phase-2 cutover: apply `h1g-cutover-pieces.patch` once the cgprofile, topos and BuildKit endpoints are delivered (2026-11-08)

**Status:** OPEN; priority to-do, blocked on the endpoint delivery in MDT-086. Source: `reports/h1g-REPORT.md` (B2 split), `reports/h1g-REVIEW.md` B2 and `reports/h1g-REVIEW2.md` section 3.
**Finding.** The H1g' branch was split so that only `mdt-cockpit/host-setup` merges now; the consumer side was saved as `/home/vscode/vbpub-release-program-20261027/reports/h1g-cutover-pieces.patch` (26 files): `.devcontainer/devcontainer.json` and both cockpit templates (`${localEnv:...}` mounts, `--group-add`, `containerEnv` including `CGPROFILE_*`/`TOPOS_*`), `cmru.orchestration.toml` (`${BUILDX_BUILDER}` and `${BUILDKIT_HOST}` without defaults, so an unset variable is a cmru load error), 15 `required_env` names on the `image-runtime-built` and `image-runtime` lanes of `mdt-cockpit/run-gate.toml`, `ensure-release-builder.sh`, `finalize_container_environment.py`, `mdt_buildkit_builder.py`, the consumer tests and docs. Applied on the controller before the endpoints exist, the next devcontainer rebuild cannot start (a Docker `--mount` with an empty source fails) and every cmru invocation without those variables fails to load.
**Check done (h1g-REVIEW2).** `git apply --check` passes on the branch head (26/26); on origin/main 23 apply, and `docs/DESIGN-GUIDE.md`, `host-setup/tests/test_buildkit_governance.py` and `scripts/test_devcontainer_audit.py` need the host-setup branch first. Applying it on the head reproduces the pre-split state byte for byte for 24 of 26 files; the two differing mixed docs carry intended later edits.
**Fix.** After the host-setup branch is merged and the endpoint record is published on the host (MDT-086), apply the patch, resolve the three mixed files by hand, run the consumer lanes (not run yet), and rebuild the controller devcontainer on a host that has published the facts.
**Close when.** The patch (or its reviewed successor) is merged, a devcontainer rebuild with the published record starts, and `cmru` loads in a context that has only the host environment.

## MDT-086 - design: who delivers `CGPROFILE_*` and `TOPOS_*` to the devcontainer and the cockpit (2026-11-08)

**Status:** OPEN; priority design decision (blocks MDT-085). Source: `reports/h1g-REVIEW.md` B2, `reports/host-endpoints-and-phase2-fastpath-DESIGN.md` (consumer table, section 5.4).
**Finding.** host-setup now publishes only the BuildKit and Docker-API facts (`BUILDX_BUILDER`, `BUILDKIT_HOST`, `MDT_HOST_DOCKER_GID`, `MDT_BUILDKIT_SOCKET`, `MDT_BUILDKIT_SOCKET_DIR`, `MDT_BUILDKIT_GID`, `MDT_DOCKER_API_SOCKET`, `MDT_DOCKER_API_SOCKET_DIR`) into `/etc/host-endpoints.d/mdt-host-setup.env`. The cutover pieces expect `CGPROFILE_CONTROL_SOCKET_DIR`, `CGPROFILE_OBSERVER_*`, `TOPOS_OBSERVE_*`, `CGPROFILE_CONTROL_GID` and `TOPOS_OBSERVE_GID` to exist in the host environment, but nothing writes them yet: `cgprofiled install` and `toposd install` are the natural publishers and are not installed on the live host. The design defers the devcontainer level (`${localEnv:VAR}` from a pam_env block, with an optional default) and delivers cockpit values by container `-e` from the record; neither path has an owner for the CGPROFILE/TOPOS keys.
**Decide.** Which installers publish which keys (each daemon's installer writes its own fragment, or host-setup reads the daemon sockets), how a VS Code Remote-SSH server or devcontainer CLI picks them up (pam_env block versus an `initializeCommand` that reads the record), and what a cockpit start does when a fragment is missing (refuse versus omit the mount).
**Close when.** The decision is recorded in `docs/DESIGN-GUIDE.md` and the fragments exist on a host, so MDT-085 can be applied.

## MDT-087 - the new `install.sh` refuses on the live controller host until the operator removes the retired provisioning by hand (2026-11-08)

**Status:** OPEN; priority operator action, part of the cutover. Source: `reports/h1g-REVIEW.md` section (1) and `reports/h1g-REVIEW2.md` section (4).
**Finding.** The live controller was provisioned by the retired host-setup paths, so `install.sh` refuses (before every mode: `--admission-only`, `cpu-benchmark`, `--apply-protection`) until these are removed: `/etc/tmpfiles.d/mdt-cgprofile.conf`, `/etc/tmpfiles.d/mdt-cgprofile-observer.conf`, `/etc/tmpfiles.d/mdt-topos.conf`, `/etc/sysusers.d/mdt-cgprofile.conf`, `/etc/systemd/system/topos.service.d/50-mdt-socket-group.conf`, `/usr/local/sbin/mdt-cgprofile-install.sh`, `/etc/profile.d/mdt-buildkit.sh` and the `# BEGIN mdt-buildkit ... # END mdt-buildkit` block in `/etc/environment`. Removal is non-destructive but the tmpfiles and drop-in files recreate `/run/cgprofile`, `/run/cgprofile/observer`, `/run/topos` and the topos socket group at boot: a reboot between removal and `cgprofiled install` / `toposd install` leaves the running old daemons without runtime dirs. Sessions started before the cutover keep the old environment (no `MDT_BUILDKIT_SOCKET`), so their `docker build` exits 2 with a clear message until they log in again.
**Operator order (the refusal prints it).** One sitting, no reboot in between: `sudo rm -f --` the six file paths, `sudo systemctl daemon-reload`, then `sudo rm -f -- /etc/profile.d/mdt-buildkit.sh` and `sudoedit /etc/environment` (the marker lines only), then `install.sh`, then the daemon installers (`cgprofile/docs/NATIVE-DEPLOYMENT.md` section 3, plan 5.3), then log in again and restart the VS Code server and long-lived units.
**Close when.** The operator has run the sequence on the controller, `mdt-host-check.sh` passes, and builds work from a fresh login.

## MDT-088 - H1g' review nits left open (2026-11-08)

**Status:** OPEN; priority nit. Source: `reports/h1g-REVIEW2.md` (carried-over nits), `reports/h1g-REPORT.md` (not done).
1. The `host-setup-admission` lane in `mdt-cockpit/run-gate.toml` runs `test_cgprofile_install.py`, whose content is now retirement and endpoint tests; rename the file (and the lane entry).
2. `docs/CONSUMERS.md` lost the sentence "The cockpit image carries the cgprofile wheel's clients (`cgprofile`, `cgprofilectl`; MDT-048)"; restore it.
3. The endpoint writer string is `mdt host-setup install` with no version; augeaswrap documents "verb and version" and accepts it. Add the version.
4. Optional: the Buildx shim has no fallback deriving `MDT_BUILDKIT_SOCKET` from `BUILDKIT_HOST` when only that is set, so long-lived pre-cutover sessions fail with exit 2. A design choice; the "log in again" documentation is the minimum and is in place.
5. `--dry-run-endpoints` needs root, systemd and a live BuildKit socket, so it cannot preview on a fresh host (documented in the usage line).
**Close when.** Items 1-3 are done and 4-5 are accepted or struck out with a reason.

## MDT-089 - devcontainer VS Code memory: Pylance and the file watcher dominate the cap; the templates ship no `python.analysis` limits and the multi-root workspace excludes nothing (2026-11-09)

**Status:** OPEN (filed 2026-11-09); priority should-fix. Source: `HANDOFF-STATE-20261107.md` (22:05Z memory entry and the 03:41Z root-cause entry, `dev-interactive.slice` OOM), memory note `feedback-devcontainer-8g-cap-oom-concurrency`; related MDT-003 (closed), MDT-076 (open).
**Measured after the 12 GiB relaxation requested at 22:05Z (the limits then were max 12 GiB / high max / swap.max 16 GiB).** cgroup anon 4.4 GB plus 5 GB swap (2.5 GB swapcached). Largest: Pylance 1.1 GB plus 1.0 GB swapped, the VS Code file watcher 0.73 GB, the extension host 0.85 GB plus 0.3 GB, Claude sessions 0.46 GB, opencode 0.27 GB, 13 Codex processes 0.23 GB each. Cause named in the log: the multi-root workspace (`/workspaces/vbpub/multi-root-volkb79-2.code-workspace`, untracked, `"settings": {}`) includes `/home/vscode` and, through `/workspaces/vbpub`, `.worktrees/` (41 worktrees, 8.4 GB, about 95k Python files). The earlier OOM kill at 03:41:16Z occurred while the previous `dev-interactive.slice` memory.max of 8 GiB was in effect; it was the event that motivated the relaxation request.
**What already exists (do not redo).** `.devcontainer/devcontainer.json:310-334` and `mdt-cockpit/templates/devcontainer.json:302` ship `files.watcherExclude` (`.worktrees`, `.run-gate`, `.assay`, `.venv`, `artifacts`, `downloads`, ...), `search.exclude`, `git.repositoryScanIgnoredFolders` and `search.followSymlinks: false`. MDT-076 records that a persisted home keeps its old `~/.vscode-server/data/Machine/settings.json`, so a long-lived container may run none of them.
**Gaps.**
1. No `python.analysis.exclude`, `python.analysis.diagnosticMode` or `python.analysis.indexing` setting in either file (`git grep python.analysis` finds nothing), so Pylance indexes `.worktrees/**`, venvs and `scripts/cgroup-profiler` leftovers.
2. `**/.venv/**` does not match `~/.venvs/` (the estate keeps gate and tool venvs there) or `.cache`; the `/home/vscode` workspace folder is therefore watched and indexed for them.
3. The multi-root workspace file is not a template: nothing says how its folders should be limited, and an `/home/vscode` root is the heaviest folder in it.
4. The untracked `scripts/cgroup-profiler/` directory in the shared vbpub checkout (it holds only ciu runtime files such as `ciu.env`, `ciu.compose.yml` and `artifacts/`; the project itself moved to `cgprofile/` in `e0655e35b`) is inside every watcher and index.
**Fix.** Ship in both templates and `.devcontainer/devcontainer.json`: `"python.analysis.exclude": ["**/.worktrees/**", "**/.venv*/**", "**/.venvs/**", "**/node_modules/**", "**/.run-gate/**", "**/.assay/**", "**/artifacts/**", "scripts/cgroup-profiler/**"]`, `"python.analysis.diagnosticMode": "openFilesOnly"`, and add `**/.venvs/**` and `**/.cache/**` to `files.watcherExclude` and `search.exclude`. Document in `templates/vscode/README.md` how to apply them to a multi-root workspace file (folder-level `settings`, drop the `~` folder or exclude it) and pair with the MDT-076 refresh so existing containers pick them up. Remove or ignore the stale `scripts/cgroup-profiler/` directory (operator action). Extend the template-settings test to assert the new keys.
**Close when.** A rebuilt devcontainer reports the new settings in its effective Machine settings, a before/after RSS sample of Pylance and the watcher on the same checkout shows the reduction (record it), and the template test covers the keys.

## MDT-090 - the cockpit image carries only `cgpctl` 2.1.0; rebuild it with the cgprofile 3.0.0 CLI (`cgprofilectl`) (2026-11-09)

**Status:** OPEN (filed 2026-11-09); priority should-fix. Source: `HANDOFF-STATE-20261107.md` (22:25Z "Backlog to file"), `reports/v1001-cockpit-EXEC-LOG.md` (21:46Z); runner-side counterpart NC-22 in `scripts/netcup/KNOWN_ISSUES_TODO_BACKLOG.md`.
**Finding.** `pip/wheels.list` (line 20) lists `cgprofile` and documents the clients `cgprofile` and `cgprofilectl` (MDT-048), but the image running on v1001 on 2026-11-08 held only the 2.1.0 client `cgpctl` (the cut-over agent installed 3.0.0 on the host and recreated the cockpit container from that old image, `v1001-cockpit-EXEC-LOG.md`). `SKILLS_CLIS` was extended with `cgpctl` (CHANGES.md line 107); check it against the client names the 3.0.0 wheel installs (`cgprofile/CHANGES.md`).
**Fix.** Rebuild the image with the 3.0.0 wheel (hash-pinned from the release), update `SKILLS_CLIS`, the docs tables and `scripts/test_release_flow.py` (line 101) to the names the wheel installs, and add an image check that `cgprofilectl --version` reports 3.0.x and `cgpctl` is absent or an explicit shim.
**Close when.** The rebuilt image reports `cgprofilectl` 3.0.x in a container, the image check is in the registered gate, and NC-22 is closed from it.

## MDT-091 - git worktree sprawl: no pruning policy or tool; 41 directories (~8.1 GB; 35 registered plus 6 unregistered) sit under `/workspaces/vbpub/.worktrees` (2026-11-09)

**Status:** OPEN (filed 2026-11-09); priority should-fix (feeds MDT-089: every worktree is watched, indexed and searched). Source: `HANDOFF-STATE-20261107.md` (22:05Z memory entry: "prune merged worktrees"), `worktree-inventory-20261027.md` and `worktree-audit/` in the release-program directory; operator standing rule "we can't have hundreds of worktrees/feature branches" (memory `worktree-sprawl-cleanup`).
**Finding.** `git worktree list` shows 35 registered worktrees on 2026-11-09. The directory audit has 41 directories totalling about 8.1 GB, so apart from the 35 registered worktrees it contains 6 unregistered directories. They include merged branches (for example `rg-selftest-fix-20261108`, merged at `c6eb38a04`) and release scratch (`cmru-release-*-ciu-*`, `rel-assay`). `ciu worktree` creates them; nothing removes them after the merge, and the safe-cleanup recipe (archive ignored evidence, skip active, secret-bearing or younger than 24 h, `cmru abandon` from source) lives in an agent memory, not in the repo.
**Fix.** Add the recipe as a documented, dry-run-first command (a `ciu worktree prune --merged --older-than 24h` verb, or a script under `mdt-cockpit/` if ciu owns no such verb): list merged-into-`origin/main` branches whose worktree is clean, archive ignored evidence, remove the worktree and delete the local branch, never touch a worktree with a running container or a lock, and print what it skipped and why. State a cap in AGENTS.md (for example 20 live worktrees, merged ones removed within a day). File the ciu half in `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` (CIU-NN) if the verb belongs there.
**Close when.** The verb or script exists with a test on a synthetic repository (merged, dirty, young and locked cases), the cap is in AGENTS.md, and the worktree count is back under it.
