# thrashguard changes

Reverse-chronological, one section per release. Unreleased work is folded into
the next release's entry; the design is the normative document.

## [Unreleased]

### Added

- **P1 `observe` is complete.** `thrashguard serve` runs the tick loop: it reads
  the host's memory PSI `full` row, feeds the hysteresis machine (trigger 3.00
  held `hold_s`, release at half held `release_s`), asks the victim rule, and
  journals one row per **transition** — a `would-freeze` naming the unit and its
  scope, a `would-hold` naming the reason nothing was chosen, a `would-thaw` when
  the machine leaves parking mode, an `unknown-pressure` row, plus `registered`,
  `registration-refused`, `deregistered` and `expired`. Nothing is repeated every
  tick: the current decision is readable from `status`/`candidates` instead.
- **The daemon serves both sockets.** The registrant verbs `register`, `update`,
  `heartbeat`, `deregister` resolve a container to one scope, store it, answer,
  and journal the outcome; the reader verbs `status`, `candidates`, `events`
  and `version` answer from the daemon's own state and journal ring. A reader
  verb on the registrant socket is refused as `wrong-socket`; a peer that sends
  nothing is dropped after 30 s; at most 64 sessions run at once
  (`too-many-sessions`).
- **systemd socket activation is adopted.** `LISTEN_FDS`/`LISTEN_FDNAMES` are
  validated as a pure plan (exactly two descriptors, named `registrant` and
  `reader`, matched by name, not by position) and the descriptors are wrapped
  through this crate's one audited `unsafe` block. Without the variables — a
  foreground run — `--registrant-socket`/`--reader-socket` are bound instead, so
  a standalone host needs no systemd.
- **`deploy/systemd/` and `deploy/tmpfiles.d/`** ship the service, the two
  `.socket` units, the `thrashguard.slice` outside `dev.slice`, and the three
  `/run/thrashguard` entries with their groups and modes.

### Changed

- `unsafe_code` moves from `forbid` to `deny`: `src/activation.rs` needs one
  `unsafe` block to adopt a raw descriptor. `tools/check-lints.py` asserts one
  allowance per block, in that file only, so a second block anywhere fails the
  lint lane. The no-act-path checks are lexical scans for known targets and APIs;
  they document their limits and do not claim to prove absence of every act path.
- The judged set grows to twelve targets: `src/activation.rs` and `src/daemon.rs`
  join the ten modules P1 already had.
- `status` and `candidates` documents are built once in `src/daemon.rs` and used
  by both the socket and the local paths, so a standalone host and a socket
  client cannot drift apart.
- `update` answers with the `score` and `priority` the registration now carries
  instead of a bare `id`.

### Fixed

- **Socket activation and journaling:** activation now checks `LISTEN_PID`, the
  exact descriptor count and names, and listener type; journal sequence and
  monotonic timestamps stay boot-relative across process restarts. Static
  candidates do not expire, boot grace and optional registrant behavior are
  applied, socket reads/writes and JSON nesting are bounded, and the local
  events page merges both journal generations within row and byte limits.
- **Resolver and issuer boundaries:** static paths refuse symlinks, incomplete
  walks refuse rather than claim uniqueness, and shadowed matches appear in the
  registration event. Cross-issuer operations remain possible in P1; TG-12
  records the P2 enforce blocker because the available stable API has no safe
  peer credential support.
- **Docs and deploy facts:** documented TOML is tested from the files, the
  measured R1 provenance names the commit actually measured without lowering
  the 88.0 floor, and service write paths match boot tmpfiles. The example
  documentation URL was removed. Hostobs' observer socket, issuer, open event
  names, point-event keys and RAM-ring completeness rules are stated in the
  design; P1 itself emits no hostobs events.
- **Review details:** registration depth and config loading have named tests;
  consumer docs describe hundredths, `several`, foreground socket permissions,
  and stale socket cleanup. The gdb helper uses the selected profile cache.
