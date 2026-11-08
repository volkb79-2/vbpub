---
kind: backlog-entry
schema_version: 1
id: CP-41
title: "Release a cgprofile wheel (client CLI and stdlib-only collector/daemon) so the image and host-setup can install it"
status: open
type: "feature"
component: "release"
context_estimate: "medium"
provenance: "Operator decision 2026-11-04 (cgprofile native service from a released wheel); platform design memo DESIGN-PLATFORM-OPTIONS-2026-11-04 section 4; mdt-cockpit MDT-048, MDT-055"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler release"
priority: 2
filed_date: "2026-11-04"
---

## Problem

`cmru.toml` declares `artifacts = ["oci-image"]`, so `cgprofile-v*` releases carry only the daemon image and a release note. The
package (`pyproject.toml`, distribution `cgroup-profiler`, stdlib-only collector, optional `report` extra) is never built into a wheel.
Consequences: the cockpit image cannot carry the client CLI (MDT-048), and a native host service (MDT-055) has nothing to install.

## Decision record

Operator decision 2026-11-04: the host daemon becomes a native systemd service installed and pinned by `mdt host-setup` from a RELEASED
WHEEL (like `mdt-psi-guard`); this needs a cgprofile wheel release (MDT-048 rescoped).

## Design

- Add a `wheel` artifact to `cmru.toml` (`artifacts = ["oci-image", "wheel"]`), built from `pyproject.toml`, with a sha256 sidecar
  and the existing release-note flow. The wheel's distribution name must normalize to the release prefix name `cgprofile` or the
  cockpit's resolver and `check_wheel_set.py` will not match it: decide between renaming the distribution and mapping, and record it.
- The wheel carries `lib/`, `cgprofile.py` and a `cgprofile` console entry point (today the `cgprofile` shell shim and
  `setup.sh` venv are checkout-only). The core is stdlib-only; the `report` extra (pandas, plotly, ruptures) is NOT in the default
  closure and is not installed by the cockpit image or host-setup.
- `cgprofile --version` identity is unchanged (CGPROFILE_VERSION embedded at build, `docs/CONSUMERS.md`); wheel and image of one release
  report the same version.
- Version target: 1.2.0 (together with CP-39/CP-40) or 1.3.0 if `host-unit` (CP-42) rides the same release.

## Acceptance criteria

- `cmru release` for cgprofile produces the wheel next to the image; `pip install --no-index` of the wheel into a clean venv gives a
  working `cgprofile --version`, `doctor`, `ctl` and `serve --help`.
- No dependency outside the stdlib in the default closure (test on the wheel metadata).
- The wheel contains the observer, window and ancestor-event modules (CP-39/CP-40) and the package data host-unit needs (CP-42).
- Image and wheel of the same release print identical versions.

## Test plan

Wheel build and clean-venv install test in the gate; a metadata test for the dependency closure; release dry-run lists both artifacts.

## Dependencies and order

First of the cgprofile packages: CP-41, then CP-42 (host-unit), then mdt-cockpit MDT-055 (install and pin), then CP-44 (retire ciu).
Unblocks MDT-048 step 2 (adding the wheel to the cockpit image list).

## 2.0 plan amendment (2026-11-04, operator-accepted defaults)

The open design choices above are decided; the contract is `docs/NATIVE-DEPLOYMENT.md` section 2 and `docs/CGPCTL-SPEC.md`.

- Distribution is renamed to `cgprofile` (no mapping table); console scripts are `cgpctl` and `cgpd`, there is no `cgprofile` command.
- The Python package is `src/cgprofile/`; DAMON is vendored as `cgprofile._damon` with a sync test (Q7).
- The default closure is cli-extended only (`cli-extended>=0.5.1`); the `report` extra stays out of the cockpit image (Q6).
- Version target is 2.0.0 for the whole breaking set (Q11); there is no 1.3.0, and the earlier "1.2.0 or 1.3.0" line is void. 1.2.0 ships the
  delta fixes only.
- Implementation moves to CP-46 (package 1); this entry keeps the release-level acceptance (wheel next to the artifact set, clean-venv install,
  identical versions) and closes when CP-46 is merged and 2.0.0 is released.
