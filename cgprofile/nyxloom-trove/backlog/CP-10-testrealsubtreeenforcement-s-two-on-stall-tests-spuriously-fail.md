---
kind: backlog-entry
schema_version: 1
id: CP-10
title: "TestRealSubtreeEnforcement's two on_stall tests spuriously failed under real host memory PSI -- the daemon's liveness pause check shared proc_root with real subtree/pid resolution, so a loaded host (not TestPeerCredentials test order, session 6's original hypothesis) paused the idle clock indefinitely"
status: fixed
type: "bugfix"
severity: "medium"
provenance: "RG-55 wave, cgprofile-P6-FOLLOWUPS session 6 LOG (bisection) + RW-44, root-caused and fixed session 7, 2026-09-12"
filed_date: "2026-09-12"
closed_date: "2026-09-12"
closed_reason: "lib/serve.py host_proc_root seam + TestRealSubtreeEnforcement decoupling, b50163e9"
---

## Observed mechanism and reproduction

`tests/test_serve_watch.py::TestRealSubtreeEnforcement` spawns a REAL
`sleep 30` subprocess and resolves it through the REAL `/proc`
(`proc_root="/proc"`), because it needs the same `SubtreeResolver` the
daemon uses in production to find an actual pid. Two of its three tests
(`test_a_silent_subtree_is_stalled_and_killed_under_kill`,
`test_the_same_lane_under_report_is_only_reported`) additionally depend on
the liveness idle clock reaching `idle_bound=0.5s` within a 30s polling
window.

`lib/serve.py`'s `SessionServer` had exactly ONE `proc_root` knob, used for
BOTH real pid/subtree resolution (`lib.subtree.SubtreeResolver`,
`subtree_cpu_seconds`) AND `lib.metrics.sample_host(proc_root=...)` — the
host-wide meminfo/loadavg/PSI reads that feed §8.4's pause condition
(`lib/liveness.py`'s `_pause`: `host_psi_full_avg10 > 5.0` pauses the idle
clock). Because the test passes `proc_root="/proc"` for the (necessary)
real pid walk, it ALSO makes the daemon's liveness pause check read this
HOST's real, ambient `full avg10` at whatever level the rest of the estate
happens to be putting it at (concurrent mutation runs, gate lanes, …). When
that's above 5.0 for the test's own 30s budget, the idle clock never
advances and the two tests' `state == "stalled"` assertion fails with
`state == "ok"`.

Session 6 bisected two forward/reverse trial runs and attributed the
failure to `TestPeerCredentials` (in `test_serve_socket_carrier.py`)
leaking state when it runs first. **That correlation does not hold under
repeated trials**: re-running the identical two-class combo at
`e4be5111`, forward order failed once (host `full avg10` 8–16 at the time)
and passed on an immediate retry (host `full avg10` <1 at the time);
running the classes in REVERSE order also failed once host PSI happened to
be elevated during that trial. The two-trial bisection session 6 ran
coincided with different ambient-load windows, not a deterministic order
effect — `TestPeerCredentials`'s own real-socket test just adds enough
wall-clock time (thread start/accept/join) to plausibly land in a
different PSI window than running the enforcement tests first, which is
what made a load-driven flake look order-dependent across two data points.

Reproduction (bare, from the project root):
```
python3 -m pytest tests/test_serve_socket_carrier.py::TestPeerCredentials \
    tests/test_serve_watch.py::TestRealSubtreeEnforcement -v -p no:randomly
```
Correlate with `/proc/pressure/memory`'s `full avg10` sampled immediately
before the run — RED when it is materially above 5.0 through the run, GREEN
when it is not, in EITHER class order.

## Fix

`lib/serve.py`'s `SessionServer.__init__` gains `host_proc_root: Optional[str]
= None` (defaults to `proc_root` — every real caller and every other
existing test that already points one root at both stays byte-identical);
the four `metrics.sample_host(proc_root=self.proc_root)` call sites now
read `self.host_proc_root`. `TestRealSubtreeEnforcement`'s three server
constructions pass `host_proc_root=str(_fake_proc(tmp_path))` (a
zero-pressure fake) alongside the real `proc_root="/proc"` needed for pid
resolution — decoupling "which process do I watch" from "how loaded is
this host right now" the same way `cgroup_root`/`proc_root` were already
test seams for every OTHER reader in the package (see the class's own
updated docstring).

A new regression test,
`TestRealSubtreeEnforcement::test_host_pressure_is_read_from_host_proc_root_not_the_real_proc`,
proves the decoupling directly: `host_proc_root` claims `full avg10=99`
(the real host's own `/proc` is not, at the moment the assertion runs, in
the same ballpark) and the session pauses (`pause_reason == "host-psi"`,
`paused_for_seconds > 0.0`, `state` stays `"ok"`) regardless of what real
ambient host pressure is doing — proven at real host `full avg10` ranging
from <1 to >20 across repeated runs while landing this fix.

## Proof

`tests/test_serve_watch.py::TestRealSubtreeEnforcement` and
`tests/test_serve_socket_carrier.py::TestPeerCredentials` run green in BOTH
declaration orders, across three `pytest-randomly` seeds, at real host
`full avg10` observed between <1 and ~20 during the runs (full transcript
in `cgprofile-P6-FOLLOWUPS-REPORT.md`, session 7's CP-10 section).

## Provenance

RG-55 wave, session 6 (`cgprofile-P6-FOLLOWUPS-LOG.md`, "Finding: the r0/r1
lane is order-dependent"), RW-44; root-caused and fixed by session 7.
