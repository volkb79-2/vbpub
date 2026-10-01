# cgprofile-P1-DAEMON — checkpoint BRIEF 1 (E-008)

Written at a clean boundary (two green-gate commits, LOG/REPORT just
written) rather than at a context/tool-call limit — a deliberate early
checkpoint given the size of the remaining package (C2-C9 is realistically
several more sessions: a new stateful module, DAMON pool management, a
Unix-socket server with threads and signal handling, a Dockerfile+image
build, a ciu stack with `privileged`/`pid: host`/`cgroupns: host`, and a
cmru registration). Landing C0+C1 solidly, gated and documented, is a
better handoff than pushing further into C2 with less budget left to gate
and document it properly.

## State as of `ba7a3167`

- Worktree: `/workspaces/vbpub/.worktrees/rg55-profiler-daemon`, branch
  `rg55-profiler-daemon`.
- Commits: `688ea7bb` (C0, RW-3), `ba7a3167` (C1, `lib/summary.py`). Both
  green: `tools/gate.sh coverage` exit 0, 933/933 tests, 100%/100%
  line+branch; `run-gate.py r3` exit 0, 7/7 canaries rejected.
- `r2` (assay mutation) not yet run — intentionally, per the handoff ("once
  before your return"); this is not the return.
- No live-acceptance, ciu, cmru, or Docker-image work started.

## What to read first (successor: fresh Sonnet, checkpoint clause armed)

1. This BRIEF, then `cgprofile-P1-DAEMON-LOG.md` and `-REPORT.md` (session
   1) in full — they record exactly what was and was not read, and why.
2. **Close the read-list gap before touching C2**: `DESIGN.md` §1/§3/§4.4/
   §4.7/§4.8/§6-§6a, `ATTACH-GUIDE.md`, `README.md`, `lib/sampler.py`,
   `lib/targets.py`, `lib/store.py`, `lib/events.py`, `lib/limits.py`,
   `lib/access.py` (whole), `lib/caps.py`, one of `tests/test_sampler.py`/
   `tests/test_damon.py`. None of these were read in session 1 — do not
   assume their contents from this BRIEF.
3. **Read the actual plan of record**,
   `run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`
   §1-3, 5"P1", 6-10 — session 1 never opened this file and relied on the
   handoff's "every decision below is DECIDED" framing plus the frozen
   contract. This is the biggest real gap; C4 (`serve.py`) and C7 (ciu
   stack) both likely depend on D-numbered decisions in there that are
   currently unknown to this package's implementer lineage.
4. Already read and safe to trust without re-reading:
   `RG55-INTERFACE-CONTRACT.md` (all), `fixtures/rg55/README.md`,
   `lib/util.py`, `lib/metrics.py`, `lib/damon.py`, `tests/conftest.py`,
   `pyproject.toml`, `run-gate.toml`, `tools/gate.sh`, the P0 controller
   log, and — obviously — the new `lib/summary.py` + `tests/test_summary.py`
   this session wrote.

## Next deliverable: C2 — `lib/subtree.py`

Per the handoff: given a cgroup path and a token, resolve every pid whose
`/proc/<pid>/environ` carries `RUN_GATE_PROFILE_SESSION=<token>` plus every
descendant (walk `/proc/<pid>/task/*/children`, else a ppid map from
`/proc/*/stat` parsed past the last `)` — `lib/metrics.py`'s
`_proc_cpu_usec` already has a worked example of the "parse past the last
`)`" trick, copy that convention rather than reinventing it). Re-resolve
every discovery interval (2s, per the handoff — confirm this against the
plan of record per point 3 above, since session 1 never verified it there).
`targets_seen` = distinct pids ever attributed (mirrors
`SummaryAccumulator`'s own `_pids_seen` union — `lib/subtree.py`'s resolver
is the thing that should be feeding `add_sample(pids=...)` in the
token-bearing case; session 1's tests only exercised the no-token
`read_cgroup_pids` path). Proc root injectable; unreadable environ = skip,
never fail.

**Suggested shape** (not binding — the handoff leaves the internal API
design to the implementer, same as `SummaryAccumulator`): a
`SubtreeResolver` class holding the discovered pid set across calls (so
`targets_seen` can be a running union like `SummaryAccumulator`'s), with a
`refresh()` method the session server's sampling loop calls once per
discovery interval, and a `current_pids` property/method the server passes
straight into `SummaryAccumulator.add_sample(pids=...)`.

**Test conventions**: fake `/proc` roots via `tmp_path`, same style as
`tests/conftest.py`'s `proc_root` fixture and this session's own
`tests/test_summary.py` (`TestSamplingHelpers`). No sleeping — inject
whatever "now"/interval concept the resolver needs.

## Then C3 (DAMON `KdamondPool`) → C4 (`serve.py`) → C5 (CLI) → ... in the
handoff's own order. Do not reorder; C4 depends on C2+C3, C5 depends on C4,
C6 depends on C5, C7 depends on C6, C8 depends on C7.

## Standing rules (unchanged, re-stated for a fresh session)

- Work ONLY in this worktree, project dir `scripts/cgroup-profiler/`.
- Edit tool only for source changes (this session's one exception: a
  disposable, non-committed mutation-probe script run from the session
  scratchpad against a scratch copy of `lib/summary.py`, restored and
  `diff`-verified identical before every commit — never apply that pattern
  to a file you intend to commit).
- `git -C <worktree> commit -F <msgfile> --only -- <paths>`; both trailers;
  gate green after every commit.
- Forbid: `run-gate-project/`, `ciu/src/`, `/workspaces/dstdns`, any
  host-mutating write from the daemon, `cmru release`, push, merge.
- Host load: check `docker ps --format '{{.Image}}'` for `tester-unified`
  before every gate run (cap 2 across the estate), serial pytest only,
  `nice -n 19 ionice -c 3`, at most one whole-suite run per checkpoint plus
  one before the eventual return.
- Checkpoint again at ~120k context or ~60 tool calls (whichever first),
  at the next coherent boundary (green gate > commit > LOG/REPORT write >
  edit-cluster end; never on a red gate).

## `/compact`-style retention prompt (if this session's context is
compacted rather than ended)

KEEP: the state-as-of-`ba7a3167` summary above; the "what to read first"
list (especially the plan-of-record gap); the C2 suggested shape; the
standing rules block; the exact gate commands that are known-green
(`tools/gate.sh <worktree> coverage`, `run-gate.py --worktree <worktree>
r3`) and their results, so they are not re-run speculatively without
reason. DROP: the full text of the golden-fixture arithmetic
cross-checking (already encoded in `lib/summary.py`'s tests, re-derivable
from `tests/fixtures/contract/README.md` if ever needed again); the
mutation-probe script's exact mutation strings (the outcomes table in the
REPORT is what matters, not the sed-substitution text); the blow-by-blow of
fixing the M8 test bug (recorded in the REPORT for transparency, not
operationally needed going forward).
