# Distro Package Probe (Docker Compose)

This folder provides a temporary container harness to test package-name candidates for desktop-analysis dependencies across distro families corresponding to your target distributions:

- Nobara -> Fedora (`nobara_fedora`)
- Bazzite -> Fedora Atomic family approximation (`bazzite_fedora_atomic`)
- Pop!_OS -> Ubuntu (`popos_ubuntu`)
- Garuda -> Arch (`garuda_arch`)
- Regata -> openSUSE family approximation (`regata_opensuse`)

## Why approximations

Nobara/Bazzite/Garuda/Regata are derivatives and may not provide official OCI base images suitable for generic package-manager probing. This probe focuses on package-manager family compatibility, which is what `desktop-analysis.py` uses for package resolution.

## Files

- `compose.nobara_fedora.yml`: Fedora-family probe for Nobara mapping
- `compose.bazzite_fedora_atomic.yml`: Fedora-family probe for Bazzite mapping
- `compose.popos_ubuntu.yml`: Ubuntu-family probe for Pop!_OS mapping
- `compose.garuda_arch.yml`: Arch-family probe for Garuda mapping
- `compose.regata_opensuse.yml`: openSUSE-family probe for Regata mapping
- `probe-inside.sh`: package availability probe copied into each container at runtime
- `run-all.sh`: host-side orchestration script to run probes sequentially (up -> probe -> down)

## Usage

From this directory:

```bash
chmod +x probe-inside.sh run-all.sh
./run-all.sh
```

Output format:

```text
tool_key,first_match,candidates
```

where `first_match` is the first installable package candidate for that distro family.

The orchestration is intentionally sequential so only one distro container runs at a time.
`run-all.sh` starts each container, copies `probe-inside.sh` to `/tmp/`, executes it, then tears the container down.

## Notes

- The probe does not install packages; it only checks availability.
- If a container image has stale metadata, you may need to refresh indexes manually (e.g., `apt update`, `pacman -Sy`, `dnf makecache`, `zypper refresh`) before probing.
