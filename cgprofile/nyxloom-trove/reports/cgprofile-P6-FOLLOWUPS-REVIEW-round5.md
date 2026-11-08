REJECT

# cgprofile P6 final adversarial review — round 5 (RG-55)

Reviewed 2026-09-29 UTC. This is a rejection for current code defects, not for
the still-pending exact-tree R2 or full gate. The source tree remains at
`0eb2686c9e0f1635cecc38bdc5fe85376d728a7a`; this report is the only
worktree addition. No production-code repair was attempted in this round.

## Numbered blockers

**B1 — `--on-stall kill` cannot enforce an ordinary unplaced lane from the
required private PID namespace.** `lib/serve.py:1288-1387` reads host PIDs
through the explicit host-proc view, then requires `samefile` with the daemon's
local `/proc/<pid>` before calling `os.kill`. For a live sibling container's
host PID under the daemon's private PID namespace, that test fails. The path
records `kill-refused:signalled-0-of-N-pids`, sets the watch verdict to
`reported` and `enforced=True` (`lib/liveness.py:658-665`), and leaves the
lane alive. The committed oracle
`tests/test_serve_watch.py:281-323` explicitly demonstrates that refusal;
its successful real-subtree kill oracle runs the lane in the daemon's own PID
namespace, so it does not cover deployment. The contract §8.4 promises
SIGKILL of an unplaced token subtree; §8.9 says ephemeral container lanes
remain Docker-capped rather than placed. A live private-namespace acceptance
oracle must prove the requested lane actually exits while an unrelated lane
survives. Supply a safe enforcement mechanism within D-15 and private
namespaces, or seek a controller product decision that changes the public
policy and consumer contract. Do not switch to a host namespace or certify a
failed signal as a kill. Round 4 correctly contained the false `killed`
claim, but it did not implement the promised enforcement.

**B2 — a cgroup directory is mistaken for an installed, bounded gates
slice.** `lib/placement.py:472-492` admits placement when
`os.path.isdir(gates_abs)` is true. If `memory.max` is `max`, its parsed value
is `None` and the over-slice check is skipped. `lib/serve.py:1528-1555`
reports `gates_slice.present:true` on the same directory-only evidence.
Neither path verifies a loaded, authored host unit or its expected capacity.
An unknown slice name can yield a transient, unlimited cgroup, precisely the
host configuration failure that the repo's AGENTS.md forbids accepting by
default. Add a verified host-unit/capacity fact to both paths, refuse
placement when it is absent, and report `present:false` or an explicit
unavailable state. Test an existing transient/unbounded directory as well as
an authored loaded unit; checking only the directory cannot prove this claim.

**B3 — the frozen cross-package v1 goldens are no longer byte-identical and
run-gate's own test fails.** The candidate changed the version string in
`run-gate-project/nyxloom-trove/fixtures/rg55/{summary,summary-container,
stop,version}-v1.json` to `1.1.0`, as RW-45 permits, but the corresponding
`run-gate-project/tests/fixtures/rg55/` copies remain at `1.0.0`.
`run-gate-project/tests/test_run_gate.py:12930-12947` compares those bytes.
In a throwaway archive of main's `run-gate-project/` with **both** candidate
fixture directories overlaid, the prescribed serial command
`nice -n 19 ionice -c 3 python3 -m pytest tests -q -k 'fixture or golden or rg55'`
returned **1 failed, 21 passed, 1198 deselected**; the failure was
`TestRG55FixtureByteIdentity.test_golden_json_files_are_byte_identical` at
`summary-v1.json`. Apply the same version-only changes to the test fixture
copy and rerun this cross-package oracle. The cgprofile contract mirror itself
is byte-identical; this blocker concerns the third fixture copy.

**B4 — a malformed UID allowlist fails open.** `lib/serve.py:122-147`
silently skips empty comma fields. `CGPROFILE_ALLOW_UIDS=,,` parses to
`None`, which means **no allowlist**; `1000,` and `1000,,1001` are silently
accepted. The current test even asserts the comma-only case is a valid unset
list (`tests/test_serve_socket_carrier.py:530-541`). The documented and
handoff-required behavior is that malformed configured lists refuse daemon
start. Reject every empty field in a nonblank configured list; retain
`None`/blank as the only unset cases, and test the daemon entry point with
comma-only and partial-empty values.

**B5 — malformed wire values can silently change caps or close the socket
without `bad-argument`.** `lib/placement.py:219-254` accepts fractional
`memory_high=1.5` and `cpu_weight=1.5`, truncating each to 1. `memory_max=inf`
raises raw `OverflowError`; `cpu_weight=nan` raises raw `ValueError`. The
protocol declares integer cap values and `bad-argument` without a session
for malformed caps (`docs/PROTOCOL.md:115-123`). Further, `lib/serve.py:648`
passes a non-string token to a regex, and `_validate_wire` at
`lib/serve.py:1880-1908` leaves a non-string `verb` for the hash lookup at
`lib/serve.py:1849`. Standard JSON integers or lists can therefore trigger
uncaught `TypeError`; `_handle_connection` at `lib/serve.py:2064-2086`
logs and closes without a protocol error for bad caps or token. The list-valued
verb is worse: its hash lookup at `lib/serve.py:2061` is outside that catch,
and `lib/serve.py:2221-2239` does not catch handler exceptions, so the accept
loop exits and stops all sessions. Validate finite integral caps and wire
field types before use; return `bad-argument` with no session for each
malformed value. Exercise the actual socket carrier, not only direct parser
calls. In a local Unix socketpair probe, a list-valued verb raised uncaught
`TypeError` from `_handle_connection`; an integer token produced an empty
reply and logged a `TypeError`.

**B6 — the 25-second connection timeout is an idle-recv timeout, allowing
one client to monopolize the serial accept loop indefinitely.**
`lib/serve.py:2026-2046` calls `conn.settimeout(25.0)` once and then keeps
appending to an unbounded `data` buffer until newline. A peer that sends one
byte within each 25-second interval can keep every `recv` successful forever;
the serial accept loop cannot serve `stop`, `version`, or other requests while
that partial line is held. This violates the documented 25-second server-side
connection limit (`docs/PROTOCOL.md:57-59`) and permits unbounded request
memory. Enforce a wall-clock deadline and a bounded line size, then test a
trickle client while a second client requests `stop`.

## Independent evidence and reconciliation

- Initial branch was `rg55-followups-cgprofile-final`; HEAD was the exact
  supplied `0eb2686c9e0f1635cecc38bdc5fe85376d728a7a`; worktree was
  clean. Reviewed the full `02b9ac648d259d77e5dd8b9a83a12e28123d7f82`
  to candidate diff (102 changed files), canonical §1–§8 contract and mirror,
  design/rulings, handoff and backlog, then LOG/REPORT, briefs through 13,
  and review rounds 1–4. `git diff --check` passed and the interface contract
  copies compare byte-for-byte. The intended root v1 fixture changes are
  limited to the version string; B3 is the omitted vendored test copy.
- Independently parsed `scripts/cgroup-profiler/.run-gate/history.json`:
  latest `r0-r1` and `r3` rows each name this exact commit, `dirty:false`,
  `history_eligible:true`, `outcome:pass`, exit 0. The controller reports
  r0-r1's 1,705 passing tests and 100% statements/branches and r3's seven
  rejected canaries. Those gates do not constitute live daemon/DAMON evidence.
  The latest local R2 history row names an older commit; the reported P6 R2
  at `6540f877` was `BUDGET_EXCEEDED/CANDIDATE_HUNG` and is not current-tree
  evidence. Current-tree R2 and the full gate may remain pending for a later
  provisional ACCEPT, but B1–B6 prevent one now.
- An independent temporary-filesystem D-15 guard probe refused a write outside
  the gates slice, a `-memory` subtree-control write, and a symlink from an
  `rg-*` entry to an outside directory. This supports those guarded-write
  cases; it does not establish live cgroup or systemd behavior.
- Repeated the CP-10 peer-credential and real-subtree enforcement test classes
  in both orders with `PYTHONHASHSEED=1,2,3`: six serial runs, 15 tests each,
  all passed. This supports the documented host-proc seam correction; it
  does not revive the former speculative peer-credential-leak claim. Five
  targeted tests corresponding to reported R2 survivor justifications also
  passed; a complete current-tree mutation verdict remains unverified.
- The older LOG statement that an enforced kill does not finalize a session
  was superseded by CP-12 and the current contract's terminal watch behavior;
  it is not a finding here.

## Live-probe gap and host safety

The required live daemon/workload/client probes were **not run**. The host
daemon was down. Inside this cockpit, `systemctl show` reports that systemd
is not running, `/run/dbus/system_bus_socket` is absent, and neither the host
`cgprofile.slice` nor `dev-gates.slice` cgroup path is available for a loaded
unit/capacity readback. Round 4's host installation record is historical;
it cannot verify the current host state. A reviewer-owned diagnostic container
would itself require verified loaded slice placement before launch. Host
namespace entry would violate the handoff. Consequently the required
`cgprofile-p6-review-probe` private-namespace daemon, separate socket client,
80 MiB placed-lane/restore and fail-closed probes, kill/report watch probe,
and live `ctl host` slice shapes are unavailable under the binding rules.
No claim of live daemon, DAMON, migration, throttling, carrier parity, or
actual kill acceptance is made.

The image was built once successfully as `cgprofile:local`; its revision
label matched the candidate. At the first build attempt the PSI guard read
`full avg10=17.47` and refused launch, but a shell sequencing mistake let the
following build command begin. It was interrupted immediately (exit 130)
before an image was produced. The corrected invocation invoked the build
only inside a PSI-guarded conditional at `full avg10=0.20` and exited 0.
No reviewer probe container or network was created. The only visible
`run-gate-` containers were the two controller-excluded mutation runs; their
internals and lifecycle were untouched. No singleton, main daemon, existing
container/network, or other worktree was changed.

## Disposition

Repair B1–B6 and rerun affected registered short gates on the repaired tip.
The same reviewer session should verify those repairs, including the
cross-package fixture oracle. A current host loaded-slice proof is required
before any bounded private-namespace live launch; if that proof remains
unavailable, the missing live claims remain explicit rather than being
substituted by the green fake-cgroup gate. The controller owns any product
decision on B1 and all integration/release actions.
