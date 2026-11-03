# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

### Changed
- fix(calls): `[tls_edge.calls]` now defaults to `enabled = true, public_port = 8443` (was off / 8444), agreeing with the Mattermost project's default media port. New test `tests/test_calls_defaults_agree.py`.
- BREAKING: the alternate HTTPS port (9443, entrypoint `websecure-alt`, `https_alt`) is removed. Routing is by host name only; the public ports are exactly 80/tcp, 443/tcp and 8443 tcp+udp (Calls TCP and UDP both on 8443). Port 80 now defaults ON (`--disable-port-80`).
- feat: the release is an offline installer instance: `get.py install --variant py313|py314` builds a hash-locked venv from the bundled ciu and dependency wheels and creates `/srv/tls-edge-instance/stack` (stable ciu root, `./ciu` stub, `consumer.env`, seeded `ciu-stack/ciu.toml.j2` from the wizard answers). One bundle per interpreter (`tls-edge-v<ver>-py313.tar.xz`, `-py314`). See `docs/INSTALL.md`.
- BREAKING (ciu deployments): container names are `tls-edge-<id>-traefik` and `tls-edge-<id>-dockerproxy` (alias `traefik` kept); the plain-compose render keeps `edge-traefik`. New site switches `ingress_external` and `tls_edge.acme.volume_name` adopt an existing network and ACME volume (migration without re-issuance).
- fix(release): the per-interpreter bundles publish through `cmru handler tarball-publish --variant` (all variants under one tag, or none) instead of the project script `scripts/publish-variants.py`, which refused every retained build-output release; the standalone `get.py` is now a release asset beside the bundles (cmru KI-115).
- Consumer examples reduced to A (web), D (guarded) and E (media TCP+UDP) with `consumer-examples/README.md`; tests render and lint every example offline.

<!-- cmru: release history -->

## [2.0.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=85c236dd5dae4c212a7e5652798dfd69c61e2f9a -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)
