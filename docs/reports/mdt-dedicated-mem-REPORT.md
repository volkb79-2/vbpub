<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/mdt-dedicated-mem-REPORT.md -->

# mdt-dedicated-mem report (2026-11-05): HOST_ROLE=dedicated-dev memory, applied on v1001 then r1002

Branch `mdt-dedicated-mem-20261105` (worktree `<local-path>`), commit `de9507821`. Not pushed, merged or tagged.
Hermetic suite: `host-setup/tests` minus `test_buildkit_governance.py` (needs `hypothesis`, not installed here): 587 passed, 75 subtests (new file `test_testhost_mem.py`, 26 tests).

## What changed (shared-prod and empty role are unchanged)
- New keys (runtime-optional, validated, documented in `host-setup.env.example` 4.0b): `DEV_HOST_MEMORY_KEEP` (default 512M) and `DEV_HOST_PROTECTED_UNITS`
  (empty = ssh, docker, containerd, cgpd, mdt-psi-guard, journald, oomd; `-` = none). Deviation: not called `DEV_HOST_RESERVE`, because `mdt_capacity.py` refuses every `*_RESERVE_*` key (2026-11 rename guard).
- dev.slice (dedicated-dev only; a non-empty `DEV_MEMORY_HIGH/MAX/LOW` is a pin and wins, `-` omits):
  MemoryMax = MemTotal + SwapTotal - MDT_EXCLUDED_RAM - MDT_EXCLUDED_SWAP - keep; MemoryHigh = MemTotal - MDT_EXCLUDED_RAM - keep; MemoryLow = sum of the tiers' MemoryLow.
  Caveat documented: `memory.max` counts RAM only, so the combined RAM+swap value is the leak backstop; the limit that keeps RAM for the host is MemoryHigh (reclaim into zswap/swap) plus system.slice MemoryLow.
- Tiers: `DEV_<TIER>_MEMORY_HIGH/MAX` ignored (install names the ignored values), MemorySwapMax = dev.slice's, MemoryLow/Min, weights and oomd untouched.
- Host protection: `system.slice` MemoryLow = keep; `OOMScoreAdjust=-900` drop-in `50-mdt-dedicated-dev.conf` on the protected units (skipped when a unit already has a lower value: containerd -999, oomd -900), running services lowered in place via /proc (nothing restarted); leaving the role removes exactly those files.
- mdt-psi-guard: on this role it also watches the protected units. Thresholds reviewed, not changed (stall fractions 10/5/25 %, stage 2 = 90 % of the tier's live `memory.current`, only a 256M floor; with no tier `memory.high` the original `max` is restored on release). Still `report`.
- zswap read and left unchanged (zstd, max_pool_percent 25, accept_threshold 90, shrinker on, writeback yes everywhere).
- `mdt-host-check.sh`: verifies the derived dev.slice values, "tier memory.high/max = max" and system.slice memory.low. Wizard: asks the two keys under the host role. README section "Memory of the dedicated-dev role", CHANGES [Unreleased] (one heading), backlog MDT-072 (open follow-ups).
- OOM order documented: reclaim takes dev pages first, oomd kills inside gates/background, the kernel OOM killer prefers dev processes (host services -900); the dev tier is killed first.

## Before / after (cgroup files)
| | v1001 before | v1001 after | r1002 before | r1002 after |
|---|---|---|---|---|
| host | 8 GB RAM, 8 GB swap | | 8 GB RAM, 16 GB swap | |
| dev.slice memory.high | 7516192768 (7G) | 7783813120 | 7516192768 | 7783809024 |
| dev.slice memory.max | 8053063680 (7.5G) | 16373739520 (15.25G) | 8053063680 | 24963661824 (23.25G) |
| dev.slice memory.low | 0 | 805306368 | 0 | 805306368 |
| dev.slice memory.swap.max | 8053055488 | unchanged | 16642981888 | unchanged |
| gates high / max | 4G / 5.5G | max / max | 4G / 5.5G | max / max |
| background high / max | 4G / 5.5G | max / max | 4G / 5.5G | max / max |
| interactive high / max / low | 3.5G / 4.5G / 0 | max / max / 512M | same | max / max / 512M |
| buildkitd high / max / low | 2G / 2.5G / 0 | max / max / 256M | same | max / max / 256M |
| tier swap.max | 80 % of dev | = dev.slice | 80 % | = dev.slice |
| system.slice memory.low | 0 | 536870912 | 0 | 536870912 |
| oom_score_adj docker / cgpd / psi-guard / journald | -500 / 0 / 0 / -250 | -900 each (containerd -999, ssh -1000 kept) | same | -900 each |
`systemctl show dev.slice` agrees with the cgroup files (v1001). Note the directive's "8+15 = 23 GB" holds for r1002 (16 GB swap); v1001 has only 8 GB swap (two 4 GiB partitions visible), so 15.25G.

## Commands (per host, v1001 first; controller-only identity helpers were used for `*_api_check` before every write and remain outside the repo; see the [persistence inventory](../persistence/PERSISTENCE-INVENTORY-2026-11-12.md#purge-readiness))
1. Memory PSI was 0.0 on both (some avg10), no campaign touched.
2. `cp -p /etc/mdt/host-setup.env /etc/mdt/host-setup.env.pre-memdedicated-20261105`
3. Ship `git archive HEAD mdt-cockpit/host-setup mdt-cockpit/scripts scripts/debian-install-v2/tools` to `<local-path>` (`v1001_put_dir`; same tar over `r1002_ssh` on r1002).
4. `sed` the env: clear `DEV_MEMORY_HIGH/MAX` and the eight tier HIGH/MAX values, `DEV_INTERACTIVE_MEMORY_LOW=512M`, `DEV_BUILDKITD_MEMORY_LOW=256M`, append `DEV_HOST_MEMORY_KEEP=512M` and `DEV_HOST_PROTECTED_UNITS=`.
5. `cd <local-path> && ./install.sh` (rc 0 on both; log `<local-path>`), `systemctl start mdt-dev-governance-reconcile.service` (rc 0), `systemctl restart mdt-psi-guard.service` (reads the env once at start).
6. Verify: cgroup files above, `systemctl show`, `/usr/local/sbin/mdt-host-check.sh`.

## Verification
- v1001: campaign container (assay r2, up 6 h, dev-gates at 2.8 GiB, now unconstrained), mdt-cockpit-runner and mdt-buildkitd up and not restarted; cgpd, mdt-psi-guard, mdt-buildkitd active; memory PSI some/full avg10 0.0 after; `dmesg` shows 0 oom-kill lines; host-check: the new checks OK.
- r1002: campaign container (3 h), runner, buildkitd up; services active; PSI 0.0; 0 oom-kill; host-check new checks OK.
- Both: `mdt-host-check.sh` still reports `FAIL cgpctl doctor reports failing checks: caller-group` (pre-existing, CP-57; not caused by this change). psi-guard `events.jsonl` has old `unreadable` entries from before the restart and none new.
- Not done: no real overcommit/stress run (the hosts carry live campaigns); the OOM order is documented and unit-tested, not provoked. docker and the hosts were not restarted. <host-address> untouched.

## Rollback (each host)
```
cp -p /etc/mdt/host-setup.env.pre-memdedicated-20261105 /etc/mdt/host-setup.env
cd <local-path> && ./install.sh     # old env: tier values return, pins 7G/7.5G, dedicated-dev memory block still runs unless HOST_ROLE changes
systemctl start mdt-dev-governance-reconcile.service; systemctl restart mdt-psi-guard.service
```
Caution: the restored env still says `HOST_ROLE=dedicated-dev`, so the NEW installer derives the memory again and ignores the restored tier values. To get the previous behaviour exactly, also set `HOST_ROLE=shared-prod` before running install.sh (the installer then removes its system.slice/unit drop-ins itself), or run the previous installer copy (`<local-path>`, which does not know the memory keys and will remove nothing: delete the `50-mdt-dedicated-dev.conf` drop-ins by exact path and `systemctl daemon-reload`). Running processes keep their lowered oom_score_adj until restarted.

## Open (MDT-072)
Wizard memory step still asks the ignored tier ceilings; no pressure-driven reclaim of dev.slice itself; psi-guard `enforce` not validated live; zswap pool size not measured under real overcommit.

---
# Review fixes (2026-11-05, commit 4e663da8c on mdt-dedicated-mem-20261105; not pushed/merged/tagged)

| finding | status | what |
|---|---|---|
| F1 ssh drop-in | FIXED | `ssh.service` removed from the default list (installer, `mdt_capacity.DEDICATED_PROTECTED_UNITS_DEFAULT`, psi-guard copy); `DEV_HOST_PROTECTED_UNITS` refuses ssh/sshd/ssh.socket names (validator + `dedicated-memory` exit 2); install.sh removes any owned drop-in of a unit not in the effective list, so the stale ssh drop-in of the earlier version is removed. Tests: refusal, stale-ssh removal keeps operator files and the system.slice file. |
| F2 keep sizing | FIXED | default `DEV_HOST_MEMORY_KEEP` = plain 1G (measured system.slice 0.94-1.1 GB); no hidden derivation. `mdt-host-check.sh` WARNs when system.slice memory.current exceeds the keep (OK line otherwise) and FAILs on a stale ssh drop-in. |
| F3 docs | FIXED | README/env example/capacity docstring/CHANGES: memory.max is unreachable and NOT a leak backstop; bounds in order = MemoryHigh throttling (thrash first) -> memory.swap.max -> oomd -> global OOM kills the largest dev process; io.max vs swap-out priority inversion listed as unmeasured (MDT-072 item 5); swap.max = SwapTotal-512M vs keep inconsistency noted (item 6). A test forbids affirmative "leak backstop" text. |
| F4 zero | FIXED (decision) | a `0` pin for DEV_MEMORY_HIGH/MAX/LOW = omitted, like `-` (same meaning as the shared `validate_size_or_zero`); keep 0 stays refused. |
| F5 floors | FIXED | keep >= 256M; HIGH/MAX pins >= 256M (LOW any positive); junk pins named "not a size". |
| F6 | FIXED | (a) narrowing the list removes the drop-ins of units that left (also `-`); (b) wizard `memory_review_warnings` no longer warns "no ancestor dev.slice MemoryLow" on dedicated-dev when `DEV_MEMORY_LOW` is empty (derived sum); (c) all drop-ins chmod 0644. |
| F8 tests | FIXED | shared-prod inert test uses the installer's real `_mdt_host_role` detection (extracted block), no flag set by the test; new rendered-units identity test (every `units/*.in`, install.sh's render semantics, shared-prod and empty role, with vs without the dedicated pre-flight block, plus a non-vacuity test); real-process test of the in-place oom_score_adj loop; check.sh block + wizard warning tests; README tautologies replaced by content assertions. |

Suites: `test_testhost_mem.py` + `test_testhost_io.py` 53 passed, 28 subtests; whole `host-setup/tests` minus `test_buildkit_governance.py` (hypothesis): 599 passed, 88 subtests (single process, memory PSI < 25). Deviation: first source edits of this round were done with a python replace script, not the Edit tool.

## Live re-apply (v1001 then r1002; api check before every write; memory PSI 0.0-0.5; no docker/container restart)
1. `cp -p /etc/mdt/host-setup.env /etc/mdt/host-setup.env.pre-memfix-20261105`; `sed -i 's/^DEV_HOST_MEMORY_KEEP=.*/DEV_HOST_MEMORY_KEEP=1G/'`.
2. Ship `git archive HEAD mdt-cockpit/host-setup mdt-cockpit/scripts scripts/debian-install-v2/tools` to `<local-path>` (r1002: ssh to root@<host-address>; the hostname is not in the pinned <ssh-material-path>).
3. `./install.sh` rc 0 on both (log `<local-path>`), `systemctl start mdt-dev-governance-reconcile.service` rc 0, `systemctl restart mdt-psi-guard.service`.
Result (identical policy, new keep): v1001 dev.slice high 7246942208 / max 15836868608 / low 805306368 / swap.max 8053055488; r1002 high 7246938112 / max 24426790912 / low 805306368 / swap.max 16642981888; both: system.slice memory.low 1073741824 (current 0.94-0.96 GB, inside the keep), tiers high/max = max/max, no ssh.service.d, ssh listener -1000, docker/cgpd/psi-guard/journald/oomd -900, containerd -999, drop-ins 0644; host-check new checks all OK (only the pre-existing `cgpctl doctor caller-group` FAIL). Campaign containers, runner and buildkitd stayed up; memory PSI ~0.

## Reproducibility after a reinstall (repo + /etc/mdt/host-setup.env keys)
Needed keys: `HOST_ROLE=dedicated-dev`; `DEV_HOST_MEMORY_KEEP=1G`; `DEV_HOST_PROTECTED_UNITS=` (empty = default list); `DEV_INTERACTIVE_MEMORY_LOW=512M`, `DEV_BUILDKITD_MEMORY_LOW=256M` (sum becomes dev.slice MemoryLow 768M); `DEV_MEMORY_HIGH=`, `DEV_MEMORY_MAX=`, `DEV_MEMORY_LOW=` empty (derived), and the eight tier `DEV_{INTERACTIVE,BACKGROUND,GATES,BUILDKITD}_MEMORY_{HIGH,MAX}` empty or `-` (ignored anyway); `MDT_EXCLUDED_RAM=0`, `MDT_EXCLUDED_SWAP=0`; IO keys per the MDT-TESTHOST-IO report (HOST_ROLE=dedicated-dev needs an iobench measurement or `DEV_IO_*_MAX` pins). Then `install.sh` + reconcile service start + psi-guard restart. Nothing else is host state.

## Rollback
`cp -p /etc/mdt/host-setup.env.pre-memfix-20261105 /etc/mdt/host-setup.env` returns keep to 512M (not recommended: below the host working set); the previous installer (`<local-path>`) would re-render the ssh drop-in: do not use it.
