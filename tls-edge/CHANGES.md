# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

<!-- cmru: pending release notes begin -->
### Added
- Site overrides now record `[tls_edge] schema = 1`; absent means schema 1 and newer schemas are refused.
- `tls-edge configure`, `tls-edge validate`, the standalone renderer, `ciu check`, and `ciu up` use the same
  tls-edge validation, including the same refusal messages.

### Changed
- Site overrides are plain TOML: Jinja markers and unknown keys are refused, while
  `[tls_edge.acme.dns.environment]`, `[tls_edge.secrets]`, and `[tls_edge.hooks]` remain open tables.

<!-- cmru: pending release notes end -->

<!-- cmru: release history -->

## [3.1.1] - 2026-11-21
<!-- cmru: generated -->
<!-- cmru: source-end=5e6a00842ddfc7c02c4b8333eb4919566c01d81e -->

### Fixed
- fix(tls-edge): correct release notes and port contract (6b15d0c3e)

### Testing
- test(vss): check Jitsi against tls-edge defaults (47022cffc)
- test(tls-edge): cover the repository Jitsi consumer (27a65b73b)

<!-- cmru: pending release notes begin -->
### Added
- Jitsi's registered consumer suite checks its real `websecure` route against tls-edge's rendered HTTPS/TLS defaults and optional HTTP/3 listener. JVB media stays direct on UDP 10000.
<!-- cmru: pending release notes end -->

## [3.1.0] - 2026-11-21
<!-- cmru: generated -->
<!-- cmru: source-end=b5984c7f0d4e3b2ff2d16ec44e67d88d84ce02c1 -->

### Added
- feat(tls-edge): add instance configuration CLI and HTTP/3 (5794004bc)

### Fixed
- fix(tls-edge): filter fresh bundle wheels at resolution (b5984c7f0)
- fix(tls-edge): share pytest source import config (60366f564)
- fix(tls-edge): keep ACME data outside config mounts (2d8a0b825)

### Testing
- test(tls-edge): restore validator after show branch (c2fbf2a59)
- test(tls-edge): cover config CLI edge cases (7e5ea473e)
- test(tls-edge): include CLI dependencies in tester image (febc2c16b)
- test(tls-edge): add release rigor gate for configuration CLI (ec4f857ef)

### Operator notes
- BREAKING: the Calls media preset is disabled by default in 3.1.0; it was enabled on 8443/tcp+udp in 3.0.0. Existing Calls deployments must set `[tls_edge.calls] enabled = true` to retain those listeners.
- HTTP/3 over QUIC/UDP is new in 3.1.0 and remains opt-in. It shares the HTTPS port and keeps the TCP listener for HTTP/1.1 and HTTP/2.
- `get.py install` installs the offline bundle and CLI without site answers; run `tls-edge configure` afterward to write the instance's site config and `consumer.env`.
- The CIU ACME startup fix mounts state outside the read-only configfile parents. The volume name and `acme.json` file at its root are preserved, so upgrades reuse certificates. Inside the container the file moved from `/etc/traefik/acme/acme.json` to `/var/lib/traefik/acme.json`.

## [3.0.0] - 2026-11-04
<!-- cmru: generated -->
<!-- cmru: source-end=9278e19d2cb75ff664bae4074722548af555c74b -->

### Added
- feat(tls-edge): run-gate.toml lane tests (tester-unified) and requirements-test.txt (794478658)
- feat(cmru): tarball-publish --variant publishes a multi-variant release all-or-nothing (KI-115); tls-edge uses it (489bdb8c8)
- feat(tls-edge): installer packaging - bundled ciu wheels per interpreter, stable stack root, wizard, adapter; file CIU-163 and KI-115 (c247cc5cb)
- feat(tls-edge): tested consumer examples (web, media tcp+udp, guarded), render/lint tests, opt-in Traefik config validation (b43abdf55)
- feat(tls-edge): host-name routing only - drop the alternate HTTPS port, publish 80/443/8443; standalone ciu root; adopt-existing network/volume switches (848245f68)
- feat(cmru): W1-INSTALLER finish: INS-18, rollback message, signature docs, authoring guide, plant table, report (c664861b3)
- feat(cmru,ciu): installer extensions mechanism; move get.py enroll into ciu (W1-CIU-ENROLL, O4) (3aada2d08)
- feat(cmru): authenticate transactional Git operations (7c2d7a8be)

### Fixed
- fix(tls-edge): stage-wheels can pin the bundled ciu and logs its sha256 (7c733a7aa)
- fix(tls-edge): Calls on by default at 8443 TCP+UDP, alt HTTPS default 9443; add Calls defaults agreement tests (5cbeef22c)
- fix(tls-edge): re-render get.py with the non-over-claiming 404 message (C-03a) (89f001b17)
- fix(tls-edge): re-render get.py with the clearer missing-asset refusal (R6-03) (953914481)
- fix(tls-edge): re-render get.py from the template without the legacy-name fallback (PWMCP-08) (85060ee95)
- fix(tls-edge): re-render get.py from the updated cmru installer template (KI-89 fallback) (5d91d11a4)
- fix(cmru,ciu): review fixes for W1-CIU-ENROLL (checker gaps, hard-fail drift guard, CIU-131) (2ac2fe2ea)

### Changed
- Merge branch 'fix-cmruki115-20261104c' (5979e3e3c)
- chore(tls-edge): bump docker-socket-proxy v0.4.2 -> v0.5.0 (f087d8c1d)
- Merge branch 'vss-20261103_175855-voice-screen-share' (f80080ed5)
- refactor: move mattermost-server/ to voice-screen-share/mattermost-server/ and update references (24c216156)
- chore(ciu,tls-edge): re-render the committed get.py with the new template; point refs at cmru installer render (e28bc5015)
- backlog: file A-06 (Calls defaults), A-11/A-12 (tester-base TB-01/TB-02), B-06 (MDT-015) from INT-01 review (9eaf6f960)
- Enable Mattermost Calls through tls-edge (93c6a39aa)
- W1-INSTALLER round2: re-render tls-edge/get.py for the manifest key grammar (a96905ff7)
- W1-INSTALLER round1: bundle-manifest verb (--name), SPEC inventory, REPORT section (9c519aded)
- W1-INSTALLER round1: template/producer fixes (WIP, tests next) (7193017cf)
- wip(cmru-w1-installer): generic get.py installer rewrite (fail-closed, per-release dirs, rollback, offline hash-locked wheels, signature policy); checkpoint (c15d5d873)

### Documentation
- docs(tls-edge): explicit 3.0.0 release intent, unsigned-release trust model, tag-pinned get.py, release notes (78c6a6511)
- docs(tls-edge): INSTALL.md with the 8-step migration plan, consumer-examples README, 80/443/8443 everywhere; cut examples b and c, drop wrapper.sh; instance gitignore covers ciu artifacts (76a7d7329)
- docs: formalize version target schema and backlog name (99d460ec1)
- docs(pwmcp): centralize modernization and disable legacy SSE (afe9f7a9b)

### Testing
- test(tls-edge): installer test fails instead of skipping; tamper and --overwrite-local cases (85c700e66)
- test(tls-edge): offline installer test - install, update, rollback, adapter validation against the committed get.py (58b8a8b89)

### Release details
- chore(deps): pinned `tecnativa/docker-socket-proxy` v0.4.2 -> v0.5.0 (haproxy 3.2.4 -> 3.4.2 base, optional `ALLOW_PAUSE`/`ALLOW_UNPAUSE` and `BIND_CONFIG` override; no default or env-name changes, tls-edge config unaffected). Multi-arch index digest sha256:1f5038b54f06c3e18422902cf00ba21803d1c97805aae032e5e6673d532d3459; images stay tag-pinned like traefik.
- Calls media defaults to enabled on 8443/tcp+udp, agreeing with Mattermost's default media port.
- BREAKING: the alternate HTTPS port (9443, entrypoint `websecure-alt`, `https_alt`) is removed. Routing is by host name only; the public ports are exactly 80/tcp, 443/tcp and 8443 tcp+udp (Calls TCP and UDP both on 8443). Port 80 defaults on.
- The offline installer creates `/srv/tls-edge-instance/stack` with the bundled ciu, stable root, and `consumer.env`; it publishes one bundle per interpreter. See `docs/INSTALL.md`.
- BREAKING (CIU deployments): container names are `tls-edge-<id>-traefik` and `tls-edge-<id>-dockerproxy` (alias `traefik` kept); plain Compose keeps `edge-traefik`. Site switches `ingress_external` and `tls_edge.acme.volume_name` adopt an existing network and ACME volume.
- Releases are unsigned (SHA-256 plus an in-bundle manifest); the trust model and tag-pinned `get.py` guidance are documented in `docs/INSTALL.md`.
- The installer test fails rather than skips when cmru is unavailable unless `TLS_EDGE_ALLOW_INSTALLER_SKIP=1`; it covers bundle tampering and `--overwrite-local`.
- `run-gate.toml` adds the `tests` lane in tester-unified; wheel staging can pin the bundled ciu with `TLS_EDGE_CIU_VERSION`.
- The per-interpreter bundles publish through `cmru handler tarball-publish --variant` as one all-or-none release; standalone `get.py` ships beside the bundles (CMRU KI-115).
- Consumer examples cover public-only web, guarded web, and TCP+UDP media routes.

## [2.0.0] - 2026-09-13
<!-- cmru: generated -->
<!-- cmru: source-end=85c236dd5dae4c212a7e5652798dfd69c61e2f9a -->

### Added
- feat(cmru)!: adopt strict portable project contracts (6abbc2e8)
