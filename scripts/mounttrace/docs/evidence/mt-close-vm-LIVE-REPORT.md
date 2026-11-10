<!-- origin: 2026-11-09; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/mt-close-vm-LIVE-REPORT.md -->
<!-- relation: final MT-11 VM acceptance -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

**Status: PASS**

# mounttrace VM lane at 04a224fab326d8728792b9a737f32a0bd7563ef5 on r1002 (2026-11-09)

Commit under test: `04a224fab326d8728792b9a737f32a0bd7563ef5` (branch mt-close-20261109, "Merge fresh origin/main into mt-close-20261109"). Shipped by `git archive 04a224fab... scripts/mounttrace scripts/debian-install-v2/testing downloads` (tar sha256 `86d2e6cf...6303f8`, identical on both sides) plus the repo-local `downloads/debian-13-genericcloud-amd64.qcow2` (sha512 prefix `d25999de...` matches `downloads/SHA512SUMS`). The guest-side `mounttrace.py` sha256 `af990993...c6114` equals `git show 04a224fab:scripts/mounttrace/mounttrace.py`.
Host r1002 (4 cores / 8 GB, user `runner`, identity-checked on every call), work dir `/var<temp-path>` (removed). Guest: Debian 13 cloud under QEMU/TCG, 4 vCPU, 3072 MB; no loop devices, privileged containers or host namespaces on the host itself. Method: as in `mt11-rerun-LIVE-REPORT.md` (env `CGROUP_PARENT_DEV_GATES=dev-gates.slice CGROUP_PARENT_DEV_INTERACTIVE=dev-interactive.slice BUILDX_BUILDER=mdt-managed`; `mkdir -p repo/scripts/mounttrace/artifacts/vm-lab`; `nohup bash ./run-vm-tests.sh`).

## Results

| Item | Result |
|---|---|
| Manifest preflight (`run-vm-tests.sh --check-inputs-only`, rc=0) | `== test inputs present (6 files): mounttrace.py README.md mounttrace.md docs/CONSUMERS.md docs/DESIGN-GUIDE.md testing/vm/run-vm-tests.sh` |
| Unprivileged phase | **PASS**: `Ran 67 tests in 57.262s`, `OK (skipped=5)`; the 5 skips are the `VmRootTests` (btrfs x3, Docker x2: "needs root inside the test VM"), as expected |
| Root phase (`== root in the guest: btrfs + Docker scenarios REQUIRED (a skip is a failure)`) | **PASS**: `Ran 67 tests in 105.621s`, `OK`, 0 skips, 0 failures, 0 errors |
| Whole lane | **PASS**: final line `== PASS: all mounttrace tests passed in the guest (mt-test-2609122)` |
| Wall time | 536 s (8 min 56 s): start 21:36:48Z, end 21:45:44Z, including the sealed-base build (log lines 1-173) |
| Failures | none |

Log timeline: base build line 1, `sealed base ready` 173, `test round mt-test-2609122` 174, `unprivileged` 192, unprivileged result 294-296, root header 297, root result 399-401, `== PASS` 402. Tests went 64 -> 67 against the previous re-run (cc16de7f2), as expected for this commit.

Footprint: disk fell to 5.4 GiB free (86 percent used) at the peak, never near the 3 GiB limit; memory used stayed under about 2.5 GB.

## Queue handling

Flag `host-sat-queue.disabled.r1002` touched 21:29Z. A queue lane (`sat-r1-20261109T212817-r1002`, pwmcp:r1) was still running; I waited for `r1002.current` to disappear AND the last log line to be `2026-11-09T21:36:18Z [r1002] disabled flag present: not launching`, and only then shipped and started (21:36:48Z). No overlap. The queue loop was not stopped or killed. Flag removed 21:46Z.

## Cleanup (verified)

Removed by exact name: container `debian-install-vm-harness-20f3b8beb1e9` (running, mine), image `debian-install-vm:20f3b8beb1e9` (imported by my run), and `/var<temp-path>`. The guest VM was already destroyed by the lane (no qemu process besides `qemu-ga`). After cleanup `/` is at 78 percent with 8.6 GiB free (same as at start); other containers on the host (`mdt-buildkitd`, `mdt-cockpit-runner`, old exited ones) were untouched. `<host-name>` and v1001 not touched; repo not edited.

## Artifacts

`docs/evidence/mt-close-vm-LIVE-artifacts/`: `mt-close-vm-lane.log` (402 lines, raw), `meta.txt` (preflight line, start/end times, seconds, post-run host state).

## Unverified

Nothing beyond the VM lane was run (no stand-in Wings or `--watch` checks; those were the previous re-run's scope).
