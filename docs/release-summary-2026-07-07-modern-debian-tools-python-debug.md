# Modern Debian Tools + Python Debug Release Summary

Summary of the `20260707-10` release batch and the repack work that followed.

## Where the findings live

- Run log: `cmru.release14-repack.log`
- Package doc indexes:
  - `modern-debian-tools-python-debug/package-manifests-versioned/README.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/README.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-php85/README.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-php85-vsc-devcontainer/README.md`
- Versioned package pages:
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.11-20260707-10.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-vsc-devcontainer/trixie-py3.14-20260707-10.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-php85/trixie-py3.14-20260707-10.md`
  - `modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug-php85-vsc-devcontainer/trixie-py3.14-20260707-10.md`

## What changed

- `scripts/release-bake.sh` now records build/repack timing markers.
- `scripts/release-repack.sh` now pushes the repacked OCI layout correctly with `oci:${DST_OCI}`.
- `/.ghcr-auth.json` is treated as workspace-local runtime auth and ignored by git.

## Release batch

Build date / release date: `20260707-10`

Targets covered:

- `trixie-py311-vsc`
- `trixie-py314-vsc`
- `trixie-py314-php85`
- `trixie-py314-php85-vsc`

## Confirmed pushes

The log shows these GHCR pushes:

- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.11-20260707-10`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.11-latest`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-20260707-10`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-latest`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:latest`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85:trixie-py3.14-20260707-10`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85:trixie-py3.14-latest`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85-vsc-devcontainer:trixie-py3.14-20260707-10`
- `ghcr.io/volkb79-2/modern-debian-tools-python-debug-php85-vsc-devcontainer:trixie-py3.14-latest`

## Repack results

The repack used `REPACK_TARGET_SIZE=2GB`.

| Target | Repacked size | Layers | Notes |
| --- | ---: | ---: | --- |
| `trixie-py311-vsc` | `1.8 GiB` | `3` | `123974` / `2695` / `18556` files across the three layers |
| `trixie-py314-vsc` | `1.8 GiB` | `3` | `123396` / `2830` / `18508` files across the three layers |
| `trixie-py314-php85` | `1.8 GiB` | `3` | `105408` / `3755` / `14086` files across the three layers |
| `trixie-py314-php85-vsc` | `1.9 GiB` | `3` | `124589` / `3622` / `17949` files across the three layers |

## Timing

From the timestamps in `cmru.release14-repack.log`:

- `trixie-py311-vsc` repack window: `2026-07-07T08:52:38Z` to `2026-07-07T09:15:55Z`
- `trixie-py314-vsc` repack window: `2026-07-07T09:26:34Z` to `2026-07-07T09:50:20Z`
- `trixie-py314-php85` repack window: `2026-07-07T10:03:12Z` to `2026-07-07T10:27:24Z`
- `trixie-py314-php85-vsc` repack window: `2026-07-07T10:40:23Z` to `2026-07-07T11:01:46Z`

The log snapshot ends immediately after the final `php85-vsc` push lines.

