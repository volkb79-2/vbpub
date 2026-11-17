# vm-lab known issues and TODO backlog

## VML-BL-01 Release registration

vm-lab is not registered in the release machinery: no `cmru.toml` (the tag `vm-lab-v*` is already named in `pyproject.toml`, and `setuptools_scm` has no fallback version, so an untagged build is a dev version), no cmru orchestration entry, no `assay.toml` mutation lane. Copy the iobench trio when the project is adopted, under the controller's release program together with VMS-BL-01 of vmscreen.

**Close-when:** `cmru status` lists vm-lab, a release builds a wheel from a tag, and an assay lane exists.

## VML-BL-02 Wheel build not exercised

The package is meant to be wheel-buildable (`python -m pip wheel --no-deps vm-lab`), but no wheel was built in RP-02 (no build in the implementer session). The wheel does not carry `qemu-image/` (no package-data; `QEMU_IMAGE_CONTEXT` points at the checkout's `vm-lab/qemu-image`), so from an installed wheel `image build` needs `--context DIR` and fails with a message naming it otherwise.

**Close-when:** a wheel builds in a gate lane, installs next to vmscreen, and `vm-lab --help` runs from it; decide whether the Dockerfile ships as package data (`vm_lab/qemu-image`) or the image is built from the checkout only (then say so in the mdt-cockpit image recipe).

## VML-BL-03 vmscreen floor or pin

`pyproject.toml` depends on `vmscreen` without a version: vmscreen has no release yet. The functional floor is the `--qemu-dump-dir` option (checked at run time, `VMSCREEN_REQUIRED_OPTION`). The dependency also has no index to resolve from; install it from `libraries/vmscreen` or use `--no-deps`.

**Open question (review RP-02 finding 6):** what floor, and from which release? Until then the bare name has a dependency-confusion risk: an installer pointed at a public index could resolve a foreign `vmscreen`, and `pip install vm-lab` cannot resolve it from the estate's own wheels. Install vmscreen from `libraries/vmscreen` (or the estate wheel list) first, and use `--no-deps` for the vm-lab wheel, until the floor exists. It must be resolved before any vm-lab release.

**Close-when:** vmscreen is released and this line carries a `>=` floor with its reason.

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
