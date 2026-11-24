# Changelog

All notable changes to vm-lab are recorded here.

## [Unreleased]

### Changed
- **Cockpit cgroup inspection without the host PID namespace.** Self-inspection now requires read-only host cgroup-v2 and system D-Bus mounts, a loaded and active Docker systemd scope with a readable nonzero `TasksCurrent`, and a well-formed 64-character container ID. It resolves the scope's cgroup through D-Bus and verifies that the scope lies within the selected slice. The Docker-scope path no longer needs a host PID namespace; `host_pid_cgroup` (used by the TrueNAS live gate) still needs `/hostproc` from the inspector. Mounts at or beneath the required paths must all be read-only, and the scope's cgroup path must end in `docker-<id>.scope`.
- **vmscreen floor (VML-BL-03):** `dependencies = ["vmscreen>=0.1.0"]`, the first vmscreen release (`vmscreen-v0.1.0`, 2026-11-18), which has the `--qemu-dump-dir` option that `vm_lab.text` requires.
- **vm-lab is a top-level project (RP-02, mdt-cockpit MDT-044).** The tool moved from `scripts/vm-lab/` (`vm_lab.py`, `vm_lab_console.py`, `vm_lab_text.py`) to `vm-lab/` with the package `vm_lab` in `src/` (`vm_lab.core`, `vm_lab.console`, `vm_lab.text`, `vm_lab.cli`), a `pyproject.toml` (name `vm-lab`, Python >= 3.11, dependency `vmscreen`, console script `vm-lab = vm_lab.cli:main`) and `python -m vm_lab`. History follows (`git mv`). Importers: `from vm_lab import core as vm_lab` for the functions tests patch; `from vm_lab import <name>` still works for the names `scripts/truenas/tcg_vm.py` re-exports. The TrueNAS front only changed its `sys.path` entry (`<repo>/vm-lab/src`) and its import lines.
- `python3 scripts/vm-lab/vm_lab.py <verb>` is gone; use `vm-lab/vm-lab <verb>` (a wrapper that sets `PYTHONPATH` to `vm-lab/src` relative to itself, so a bare checkout needs no install) or the installed `vm-lab` command.
- The start refusal for an unset `DOCKER_NETWORK_INTERNAL` now says the value comes from the ciu stack environment or the devcontainer (`ciu env print`).
- The `vm-lab` unit lane moved with the project (`vm-lab/run-gate.toml`, `cd {worktree}/vm-lab`; `pytest.ini` puts `src` and `tests` on the path).

- **The QEMU image build context ships with the wheel (VML-BL-02).** `qemu-image/` moved to `src/vm_lab/qemu-image` and is declared package data (`vm_lab/qemu-image/*`); `QEMU_IMAGE_CONTEXT` resolves next to the module, so `vm-lab image build` works from a checkout, an editable install and an installed wheel without `--context`. `--context DIR` stays the override; an empty value counts as not given. The "no Dockerfile" refusal no longer suggests running from a checkout.
- The unit lane is now `r0-r1` (`vm-lab/run-gate.toml`: coverage floor 72 %, measured 74.0 %), next to the new `r2` (assay mutation lane), `wheel` and `gate` lanes. A test builds a wheel from a copy of the project, so the lane first installs the pinned build tools (`setuptools==82.0.1`, `wheel==0.47.0`, `setuptools_scm==10.0.5`).

### Added
- **Release registration (VML-BL-01):** `cmru.toml` (tag `vm-lab-v*`, wheel only to GitHub Releases, notes in `VM_LAB_RELEASE_NOTES`), `assay.toml` (judged lane `vm-lab`) and an entry in `cmru.orchestration.toml` that releases `vmscreen` first. The `vmscreen>=0.1.0` floor is set (VML-BL-03).
- `wheel` gate lane: builds the vmscreen and vm-lab wheels from copies of the sources, installs both into a throwaway venv, runs `vm-lab --help` and `vmscreen --help` and checks the packaged Dockerfile. No Docker, no QEMU.
- Admission lock from an installed wheel: when the Git probe next to the module finds no repository (or no git), the lock directory is `--lock-dir` (on `start` and `disk promote`), else `VM_LAB_ADMISSION_LOCK_DIR` (absolute, existing, writable); neither: refusal naming both. The lock file name is unchanged; checkout, wrapper, editable and TrueNAS use are unchanged.
- `vm-lab/qemu-image/Dockerfile`: the QEMU container image (`debian:trixie-slim`, `qemu-system-x86`, `qemu-utils`, `procps`; no OVMF, no USER, no privileges), and the verb `vm-lab image build [--tag TAG] [--context DIR]` (default tag `vm-lab-qemu:local`; runs `docker build`).
- Image resolution for `start` with the generic profile: `--image`, else `VM_LAB_QEMU_IMAGE`, else the local tag `vm-lab-qemu:local` when `docker image inspect` finds it, else a refusal that says to run `vm-lab image build`. A Docker that cannot be asked is reported as such. The TrueNAS front has no tag fallback (`VmProfile.default_image` is unset there).
- Documented path recipes in `docs/CONSUMERS.md` (`libraries/cowtree/repo-path vm-lab --project <project>` for state, `repo-path vm-images` for bases) and the install forms (wrapper, editable, wheel, image build, vmscreen dependency, PATH note).
