# tester-base: known issues and backlog

## TB-01 - Debian `docker-compose` is installed unpinned on top of tester-base (INT-01 A-11, 2026-10-19)

tester-base T2 keeps the compose plugin out. tester-unified's capability step requires
`docker compose version` to answer, so on this base it installs Debian's `docker-compose` (2.26.1 when
measured): an unpinned apt package outside the 14-day age window and outside tester-base's version
management, in a gate image that is handed the host Docker socket. Decide: add a pinned compose to
tester-base (and amend T2), or drop the compose requirement from tester-unified after checking what
needs it. Until then both are documented as contradicting (tester-unified DESIGN-GUIDE, report D-006).

## TB-02 - the release graph has no tester-base gate-prerequisite edge (INT-01 A-12, 2026-10-19)

`cmru.orchestration.toml` lists `tester-base <- none` and nothing depends on it, although every gate image is
`FROM tester-base`. Harmless while the default is the local tag. When the defaults switch to the published
digest (tester-base/docs/CONSUMERS.md "After the first release"), the release order must put tester-base
before run-gate-project and cmru; add the edge with that switch.
