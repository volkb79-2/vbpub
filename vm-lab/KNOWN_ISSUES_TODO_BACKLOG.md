# vm-lab known issues and TODO backlog

## VML-BL-01 Release registration (CLOSED 2026-11-18, P-A2)

Registered in the iobench shape: `vm-lab/cmru.toml` (tag `vm-lab-v*`, wheel only, notes in `VM_LAB_RELEASE_NOTES`), `vm-lab/assay.toml` (judged lane `vm-lab`, coverage floor 72 against a measured 74.0 %), `vm-lab/run-gate.toml` (lanes `r0-r1`, `r2`, `wheel`, `gate`), and two entries in `cmru.orchestration.toml` (`vmscreen` before `vm-lab` in `project_order`, vm-lab `depends_on = ["vmscreen"]`; `cmru dependencies` preflight PASS, `cmru status` lists both with next tags `vmscreen-v0.1.0` / `vm-lab-v0.1.0`). Evidence: vbpub branch `vm-pa2-20261118`, commits `caf11e2c6` and `beea8b8a2`.

The release itself, and the R2 mutation lane (never run; core.py is 62.8 % covered), remain the controller's.

## VML-BL-02 Wheel build not exercised (CLOSED 2026-11-18, P-A2)

Decision: the Dockerfile ships as package data. `qemu-image/` moved to `src/vm_lab/qemu-image`, `pyproject.toml` declares `[tool.setuptools.package-data] vm_lab = ["qemu-image/*"]`, `QEMU_IMAGE_CONTEXT` resolves next to the module (checkout, editable install and wheel alike) and `--context DIR` stays the override (`resolve_image_context`). Evidence: commit `5b5377b59` (tests: resolver with and without `--context`, package-data declaration, a wheel built from a project copy that lists `vm_lab/qemu-image/Dockerfile`) and the `wheel` lane of `vm-lab/run-gate.toml` (builds both wheels, installs them into a throwaway venv, runs `vm-lab --help` and `vmscreen --help`, finds the packaged Dockerfile). The same commands were run by hand with the devcontainer Python on 2026-11-18 and all passed; the lane itself has not been run under `run-gate` yet.

The mdt-cockpit image recipe can rely on the wheel carrying the build context (see VML-BL-04 for the lock directory).

## VML-BL-03 vmscreen floor or pin

`pyproject.toml` depends on `vmscreen>=0.1.0`. The functional floor is the `--qemu-dump-dir` option (also checked at run time, `VMSCREEN_REQUIRED_OPTION`). The dependency has no public index to resolve from: install the vmscreen wheel from its GitHub Release (or from `libraries/vmscreen`) or use `--no-deps` for the vm-lab wheel.

**Resolved question (review RP-02 finding 6):** the floor is 0.1.0, the first vmscreen release. The dependency-confusion risk of the bare name (an installer pointed at a public index could resolve a foreign `vmscreen`) is reduced by the floor but not removed.

**Floor set (controller, 2026-11-18):** `pyproject.toml` now declares `vmscreen>=0.1.0` with its reason (the `--qemu-dump-dir` option); `vmscreen-v0.1.0` is released as a GitHub Release wheel. The dependency-confusion risk of the bare name remains in one respect: no public index carries the estate's vmscreen, so install the vmscreen wheel first (or use `--no-deps` for the vm-lab wheel) until the estate has a resolver for first-party wheels. Close this entry when vm-lab's first release notes state that install order.

**Close-when:** vm-lab's first release notes state the install order (vmscreen wheel first, or `--no-deps`). The floor itself is done.

## VML-BL-04 Start admission lock path in an installed wheel (FIXED in RP-02 fix round 1; shared-lock caveat open)

`vm_start_lock_path()` first runs `git -C <directory of core.py> rev-parse --git-common-dir` (unchanged for a checkout, the wrapper, an editable install and the TrueNAS front). When that finds no repository (an installed wheel) or no git, the lock directory comes from `--lock-dir DIR` on `start` and `disk promote`, else `VM_LAB_ADMISSION_LOCK_DIR`; an unset or unusable value is a refusal naming both. The lock file name stays the shared historical one.

**Caveat:** a wheel start and a checkout start share one lock only when both name the same directory (the repository's common Git directory); a wheel in a different place takes its own lock. A venv inside a repository resolves that repository's Git directory through the probe, as before.

**Close-when:** the mdt-cockpit image recipe sets `VM_LAB_ADMISSION_LOCK_DIR` to the directory every starter on that host shares, and one live start from the installed wheel has taken the lock.

## VML-BL-05 Image build placement

`vm-lab image build` runs a plain `docker build`; BuildKit ignores `--cgroup-parent`, so the build steps are not placed under `$CGROUP_PARENT_DEV_BACKGROUND` (AGENTS.md "Host cgroup placement"). The image is small (one apt layer), but the placement rule is not met.

**Close-when:** the build is placed on the background slice (or the rule is waived for this build in AGENTS.md), and the image is built live once on a test host.

## VML-BL-06 Image never built; program name in messages

The Dockerfile was not built in RP-02 (a later step builds it; the package set mirrors `scripts/debian-install-v2/testing/vm/Dockerfile`). The profile `program` is still `vm_lab.py`, so messages read `vm_lab.py: ...` also from the `vm-lab` command; tests pin the text and the admission lock message.

**Close-when:** the image builds and one guest boots from it; the program name is `vm-lab` in messages, with the tests updated.

## RP-07 ciu service for the image (idea)

Offer the QEMU image through a ciu service instead of a local `docker build`, so every host pulls one built image. Not started; depends on the release registration (VML-BL-01).
