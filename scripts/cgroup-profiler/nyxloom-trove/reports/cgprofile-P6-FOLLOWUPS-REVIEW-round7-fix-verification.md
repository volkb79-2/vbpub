# cgprofile P6 — round-7 fix-verification addendum

**Disposition: ACCEPT-conditional for B1–B3 fix verification only.** All three
round-7 blockers are verified on repair commit `d48d1ed8` (parent candidate
`07489a5e18bf7f8056e51efacb96c4580d766d1c`, current-main base
`10a344e2f4685ac033f586fcb846b99e5fc2c8d5`). This is not round 8 and
does not alter `cgprofile-P6-FOLLOWUPS-REVIEW-round7.md`. The controller must
rerun registered `r0-r1` and `r3` plus doctor on the final tip after this
addendum commit before provisional integration. Caller-provided PASS and
coverage counts at `07489a5e` are prior-tip evidence. Current-tip R2 and
full gates remain pending under RW-381. No merge, release, install, or
main-daemon lifecycle action is authorized by this addendum.

## B1 — verified after an additional repair

The first live image from `07489a5e` reproduced round 7: `start` reported
one placed PID and both caps, but `stop` returned
`place-refused:write-failed:...scope/cgroup.procs` after the PID returned to
its original Docker scope and the leaf and systemd unit disappeared. The
host's `busctl GetUnit` rendered absence as exactly `Call failed: Unit
<unit>.scope not loaded.`; the old checker expected a printed D-Bus error
name. Accepting that exact response alone did not close B1: systemd removed
the scope **during** the final PID move, before `_restore_owned_processes`
finished its membership read.

`lib/placement.py` now accepts either exact known `busctl` absence rendering
for the requested unit. If a source membership read becomes unavailable
during restore, it accepts auto-retirement only after the exact unit and
both owned paths are absent and every journaled same-start survivor is at
its recorded origin (or its original identity exited). It persists that
proof before final cleanup. An unknown manager, path, PID, or journal state
still refuses recovery. New tests exercise retirement during the last
restore and refusal when unit absence is indeterminate.

Live repeat: session `s-20261001T133941Z-c554` placed host PID `2281220`
in `/dev.slice/dev-gates.slice/rg-profile-p6round7place551eeaa1.scope/rg-p6round7place551eeaa1`.
Host systemd read back `LoadState=loaded`, `Slice=dev-gates.slice`,
`Delegate=true`, and the exact parent `ControlGroup`. At `stop`, that PID
was back in its original `docker-...scope`; one `cgroup_write` event named
the origin; `summary.placement.error` was `null`; both owned paths were
absent; and `busctl GetUnit` said that exact unit was not loaded. A second
live run, `s-20261001T133632Z-cb48`, had the same successful final result.

## B2 — verified

The repaired `apply()` refuses an unreadable requested cap and verifies
leaf membership and PID identity before claiming success. The fake-cgroup
regression removes `memory.high` after the write and asserts refusal with
no success claim. The live placed session read `memory.high=67108864` and
`memory.max=100663296` independently from the leaf; both matched
`start.placement.applied`, with `pids_moved=1` and the verified host PID
in that exact leaf. A live unreadable kernel cap fault was not induced.

## B3 — verified

A live AF_UNIX client sent a complete, state-changing `start` JSON object
without its newline and half-closed its write side. It received zero bytes;
the session count stayed at zero; a subsequent newline-terminated `version`
on a new connection succeeded. The real-socket regression likewise sends
a complete unterminated `stop` and asserts no handler dispatch. The serve
loop returns on EOF before parsing when no newline has arrived.

## Additional P6 socket finding closed in the scoped repair

With `CGPROFILE_ALLOW_UIDS=0`, a live UID 1000 client initially got a reset
instead of `peer-refused`: the daemon sent and closed on accept before the
client's write reached the socket. A deterministic real-socket test now
connects, waits until accept, then sends its request. The server still reads
`SO_PEERCRED` on every connection; it waits for the existing bounded request
line before sending refusal and never parses or dispatches denied requests.
The rebuilt image returned shaped `peer-refused` JSON to UID 1000 while
root exec `version` succeeded. The socket was root:gid 994 mode `0660` and
`version.transports.socket.listening=true` matched the bind.

## Other live evidence and limits

The reviewer daemon used `cgprofile.slice`, private PID/cgroup/network
namespaces, no network, read-only host `/proc`, the host system bus socket,
and the template's **writable full host cgroupfs bind**. That broad bind is
the settled D-32 application-guard design, not OS containment. Reviewer
workloads used loaded `dev-gates.slice`; every reviewer-owned container was
launched and updated at 3 CPUs. The scratch directory was resolved from
the cockpit's `/tmp` mount map to the host-backed path. Memory PSI `full
avg10` was below 5 at launches. All reviewer-owned containers and scratch
directories were removed. The two named unrelated R2 containers and the
singleton main daemon were neither inspected internally nor altered.

`ctl host` reported `gates_slice.present=true` at
`/dev.slice/dev-gates.slice` and `daemon_slice.cgroup=/cgprofile.slice`.
Exec and socket results were identical for `version`, `gc`, `report`, an
idempotent `stop`, and unknown-session `watch`. `host` and `status` had the
same top-level keys, with live readings changing between calls. A placed
shared-scope watch emitted `stalled/killed` and exactly one `end:killed`
in 21.6 seconds; the selected lane was killed while its container, a
sibling lane, and the daemon stayed alive. A separate unplaced
`--on-stall report` watch emitted `stalled/reported`, then one `end:stopped`
after explicit stop; its tagged sleeper remained alive.

Serial, load-niced targeted tests on the repaired code tree passed: **531
passed in 33.03 s** across `test_placement_systemd_helpers.py`,
`test_serve_placement.py`, and `test_serve_socket_carrier.py`. `git diff
--check` passed before the repair commit; root and mirrored RG-55
contracts remained byte-identical. These local tests are not the registered
tester-unified gate.

Not independently closed here: a live forced mapping/attachment refusal
that leaves an owned recovery leaf, a live unreadable cap fault, byte
equality of volatile host/status snapshots, malformed-allowlist startup on
the final repaired image, current-tip R2/full gates, and the controller's
final exact-tip short-gate and doctor receipts. The original review record
retains its broader findings; this addendum verifies B1–B3 and the
additional socket refusal repair only.
