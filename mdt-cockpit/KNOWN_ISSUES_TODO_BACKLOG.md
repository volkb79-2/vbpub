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
`MDT_TOOL_MIN_AGE_DAYS` (default 14) old, and `sss` as the newest default-branch commit at least that old with its
sha256 recorded at staging (first-use trust). The GitHub API shapes (`assets[].digest`, `commits?until=`) are from memory
and UNVERIFIED live. Staged and verified in
`scripts/stage_tool_artifacts.py`, installed and re-verified in the `Dockerfile`, listed in the manifest and
`tests/runtime/test_image_contract.py`. Still UNBUILT and UNVERIFIED in a real image: no download was run,
the `ov` `.deb` has not been `dpkg -i`'d, and the runtime checks have not run. `dive` was already installed. (3) `tmux` and `broot` come from Debian packages; the `tmux.conf`, `gitconfig`,
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

**Status:** OPEN, severity low (it leaks processes on every timeout); recorded only; not fixed.

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

**Status:** IMPLEMENTED 2026-10-23 as host-setup key `DEV_BUILDKITD_CACHE` (a Docker volume name, the default, or an absolute host path); the cold-cache trial on `/srv/devcontainers-cow/buildkitd-cache` passed the same day and the setting stays in use.

**Trial result (mdt-cockpit image, cold cache, 2026-10-23).**
* **Works:** rootless BuildKit with the `runc-overlayfs` snapshotter runs on btrfs; the build succeeded in 1152 s, the same as the warm-cache ext4 builds (1082-1090 s). Those earlier builds got little from their cache, so the cold run is not slower.
* **Compression:** 26 GB referenced, 22 GB logical, **13 GB on disk (60 %)**; zstd extents 12 GB -> 4.2 GB (32 %), 9.1 GB stored as is (already compressed layer blobs and incompressible data, as expected).
* **IO:** average write 30 MB/s and read 45 MB/s, peaks 123 / 197 MB/s, device busy at most 74 %, io pressure average 1.0 / max 9, the same as on ext4.
* **Attribution:** `dev-buildkitd.slice/io.stat` shows the btrfs LV (253:2) with 18.8 GB written and 40.6 GB read, so btrfs data IO is charged to the slice, not to the root cgroup. Metadata and log writes were not separated out.
* **Cleanup done:** the old `mdt-buildkitd-cache` volume (93 GB) and three stale `buildx_buildkit_*_state` volumes were removed.
* **Open:** the pool is shared with the VM harness (194 GB): set `gckeepstorage` in `buildkitd.toml` before the cache grows toward it. Today the cache is the Docker named volume `mdt-buildkitd-cache` (93 GB, snapshotter `runc-overlayfs`,
owner 1000:1000, no userns remap) on the root ext4, so BuildKit's write bursts share the root journal with everything else
(HOST-IO-INTERFERENCE: that shared journal is what stalled the game's fsync path). `devcontainers-cow` is a separate btrfs filesystem
(own transaction stream, 194 GB, nearly empty, mounted `noatime,discard=async` at `/srv/devcontainers-cow`).

**Steps (host, root).**
1. `btrfs subvolume create /srv/devcontainers-cow/buildkitd-cache` (the subvolume name is a path below the top level, BTRFS-COW.md 2.1).
2. `btrfs property set /srv/devcontainers-cow/buildkitd-cache compression zstd` (BTRFS-COW.md policy: no `compress=` mount option; never `chattr +C`, it disables compression).
3. `chown 1000:1000 /srv/devcontainers-cow/buildkitd-cache && chmod 2755 ...` (the rootless worker is uid 1000).
4. host-setup: a key `DEV_BUILDKITD_CACHE` in `/etc/mdt/host-setup.env` on the HOST (value `/srv/devcontainers-cow/buildkitd-cache`; empty = the Docker
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
