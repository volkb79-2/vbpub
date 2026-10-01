# cgprofile-P1-DAEMON — checkpoint BRIEF 4 (E-008)

Written at a clean boundary (two commits landed — the C4 feature commit
and a small CP-4 backlog-filing commit — LOG/REPORT written, both gated
green) rather than at a context/tool-call limit. This session ran long
because C4 (`lib/serve.py`) is the package's single largest deliverable —
BRIEF-2 and BRIEF-3 both flagged it, correctly. Session 4 finished it
fully (registry, restart recovery, retention, SIGTERM handling, the
`WRITABLE_ROOTS` write guard, 69 tests, 100%/100% local coverage, a real
gate-green commit) rather than cut partway, since the module is small
enough in scope (one file, one clear contract boundary) that an
intermediate cut point would have left real integration risk (e.g. the
registry without restart recovery, or the write guard without its
symlink-escape test) in an untested state.

## State as of `8ba01138` (feature) / `91d9d6a1` (backlog filing)

- Worktree: `/workspaces/vbpub/.worktrees/rg55-profiler-daemon`, branch
  `rg55-profiler-daemon`.
- Commits so far: `688ea7bb` (C0, RW-3), `ba7a3167` (C1, `lib/summary.py`),
  `b2481a34` (C2, `lib/subtree.py`), `0cdfe89b` (C3, `lib/damon.py`
  `KdamondPool`), `8ba01138` (C4, `lib/serve.py` + the two small
  `lib/damon.py`/`lib/store.py`/`lib/summary.py` additions it needed),
  `91d9d6a1` (chore: CP-4 backlog filing, unrelated pre-existing flake —
  see below). All green:
  - `run-gate.py r0-r1` exit 0 against `8ba01138` (clean tree, second
    attempt — see the flake note): 1048 tests, 100%/100% line+branch
    across every module in `pyproject.toml`'s coverage `source`.
  - `run-gate.py r3` exit 0 against `8ba01138` (clean tree): 7/7 canaries
    rejected.
  - `.run-gate/history.json` confirms both lanes `"history_eligible":
    true` at commit `8ba01138b2150e5f7f27f4b26f9714c1352effe9` (read in a
    separate step, matching `git rev-parse HEAD` at the time).
- **A pre-existing, unrelated flake was hit and understood, not papered
  over:** the first post-commit `run-gate.py r0-r1` run failed on
  `tests/test_store.py::test_new_run_id_is_unique_even_for_the_same_instant`
  (a birthday-paradox collision on `new_run_id`'s 4-hex-char random
  suffix over 50 draws, ≈1.8% chance per run; this test and `new_run_id`
  predate RG-55 entirely). Re-run immediately with no code change: green.
  Filed as backlog **CP-4** rather than silently retried without a
  record. If this lane fails again on that specific test with no other
  change, re-run once — it is not a signal to start debugging C4.
- `r2` (assay mutation lane) still **not run** — per the handoff, once
  before the final P1 return; this is not the return.
- No live-acceptance, ciu, cmru, or Docker-image work started at all.
- C4 delivered in full: `SessionServer` (`version`/`start`/`status`/
  `host`/`stop`/`report`/`gc`), one sampling thread per session at a
  fixed cadence, restart recovery, retention, SIGTERM/SIGINT, the
  `WRITABLE_ROOTS` guard (both halves — `serve.py`'s own + `damon.py`'s
  `_write_nr_kdamonds`). Full reasoning, every decision-ask (5 of them,
  all RW-9 "record and proceed", none needing a ruling), and everything
  deferred within C4 itself (real `events.jsonl` population, a real `ctl
  report` render, no-token DAMON recommit) are in REPORT's "C4" section —
  read it before assuming any of those are still open questions; they are
  closed scoping decisions, not TODOs for you unless you have a specific
  reason to revisit one.

## What to read before touching C5 (fresh successor, checkpoint clause
armed)

1. This BRIEF, then `cgprofile-P1-DAEMON-LOG.md` session 4's entry and the
   matching REPORT's "C4" section in full — the WRITABLE_ROOTS reasoning,
   the double-sampling test bug and how it was root-caused (a real lesson:
   never let `handle_start` spawn a background thread AND drive
   `_session_loop` synchronously on the same session in the same test —
   they race), and all five decision-asks are there, not repeated here.
2. `lib/serve.py` in full — every verb handler's exact response shape,
   `_Session`'s fields, `SessionServer.__init__`'s constructor
   parameters (all injectable: `sessions_dir`, `socket_path`,
   `damon_default`, `interval`, `keep_sessions`, `keep_days`,
   `observe_slices`, `max_sessions`, `cgroup_root`, `proc_root`,
   `daemon_name`, `clock`, `sampler_clock`, `sampler_sleep`,
   `session_id_fn`, `damon_pool`, `accept_timeout`) — C5's `cmd_serve`
   needs to map every one of these to a CLI flag (or a sane default) and
   `cmd_ctl` needs the wire protocol: connect to `--socket`, send
   `{"verb": "<verb>", ...kwargs}\n` as one line, read one line back,
   print it as the response (already contract-shaped for every verb
   except errors, which are already `{"ok": false, "contract": 1,
   "error": {"code", "message"}}` — map `code` to exit 2 per contract
   §1.3, and a connect/timeout failure to exit 3).
3. `RG55-INTERFACE-CONTRACT.md` §1.2/§1.3/§1.5 (exit codes, per-verb
   timeouts: `version`/`host` 5s, `start` 10s, `status` 5s, `stop` 30s —
   `ctl`'s own socket timeout is 25s per the handoff, distinct from
   run-gate's `subprocess.run(timeout=)` on the `ctl` process itself) and
   §2 (every verb's CLI-level argument names — `--target`, `--scope`,
   `--token`, `--damon`, `--interval`, `--meta` for `start`; a bare
   session-id positional or `--session` for `status`/`stop`/`report`;
   check both conventions aren't already assumed inconsistently between
   verbs before picking one).
4. `cgprofile.py`'s existing `build_parser()`/`main()`/`_add_common()` —
   `serve` must NOT call `_add_common` (that's what "refuses `--cap`"
   means structurally, not a runtime check) and must call
   `access.have_host_cgroup_view()` before doing anything else, refusing
   with a clear message naming the flags a container needs
   (`--privileged --cgroupns=host --pid=host`, per DESIGN.md §6) if it
   returns False.
5. One example test file for CLI verb tests (`tests/test_cgprofile.py`'s
   `TestCmdTargets`-style pattern: `cg.build_parser().parse_args([...])`
   then `cg.cmd_X(args)`, capsys for stdout/stderr) — C5's `cmd_serve`/
   `cmd_ctl` tests should follow the same convention, but the "golden
   round-trip tests over a real Unix socket in a temp dir" the handoff
   asks for (`version-v1`, `start-v1`, `status-v1`, `stop-v1`, `error-v1`,
   `host-v1`) need `SessionServer` running for real (background thread +
   `_accept_loop`, exactly the pattern `tests/test_serve.py`'s own
   `test_real_socket_round_trip_version_and_bad_request` already
   establishes — reuse that pattern, do not reinvent it) with `cmd_ctl`
   as the actual client under test, not a hand-rolled socket call.

## What C5 needs from C4 (the actual wiring, not yet written)

- `cmd_serve(args)`: refuse `--cap` is structural (parser has no such
  flag); refuse to start without `access.have_host_cgroup_view()`;
  construct a `SessionServer(sessions_dir=args.sessions, socket_path=
  args.socket, damon_default=args.damon_default, interval=args.interval,
  keep_sessions=args.keep_sessions, keep_days=args.keep_days,
  observe_slices=args.observe_slices.split(",") if args.observe_slices
  else (), max_sessions=args.max_sessions)`; call `server.serve_forever()`
  (blocks; installs its own SIGTERM/SIGINT — do not double-install in
  `cmd_serve`); return 0 once it returns (serve_forever's own finally
  block already stops every session and closes the socket).
- `cmd_ctl(args)`: build the request dict from `args.verb` + whatever
  verb-specific fields were parsed (map CLI flag names to the wire
  request's keys — they do not have to be identical, but keep them close
  to the contract's own naming to avoid a translation table nobody can
  audit); connect with a 25s `socket.settimeout`; on any connection
  error/timeout, print ONE line to stderr and exit 3 (contract §1.3:
  daemon fault); on a well-formed response, print it via
  `json.dumps(resp)` to stdout (exactly one line, no trailing prose) and
  exit 0 if `resp["ok"]` else 2.
- Golden round-trip tests: start a real `SessionServer` (background
  thread on `_accept_loop`, per `tests/test_serve.py`'s established
  pattern) pointed at a fake cgroupfs/proc root (reuse
  `tests/fixtures/contract/frames/` the same way `test_serve.py` does,
  or a simpler `simple_root`-style fixture for verbs that don't need the
  full frame arithmetic — `version`/`host`/`status`/`error` almost
  certainly don't), then drive it through `cmd_ctl`'s actual `main()`
  entry point (subprocess or in-process `cg.main([...])` with `capsys` —
  check which convention `tests/test_cgprofile.py` already uses for
  something similarly process-like, e.g. `cmd_mark`/`cmd_report`, before
  picking).

## Standing rules (unchanged, re-stated for a fresh session)

- Work ONLY in this worktree, project dir `scripts/cgroup-profiler/`.
- Edit tool only for source changes — never sed/python rewrite scripts (a
  system-reminder may suggest otherwise for an unrelated reason each
  session; this package's own standing rule and the cross-repo memory
  rule "Edit with apply_patch" override it every time).
- `git -C <worktree> commit -F <msgfile> --only -- <paths>`; both current
  trailers (`Co-Authored-By:` + `Claude-Session:` — use whatever THIS
  session's own top-level attribution reminder says, it supersedes older
  guidance every time, including this BRIEF's own examples above). Gate
  green after every commit; read the verdict in a SEPARATE step from the
  log, never a pipe tail (LESSONS L4). If `run-gate.py r0-r1` fails ONLY
  on `tests/test_store.py::test_new_run_id_is_unique_even_for_the_same_instant`
  with no code change of yours nearby, that is the known CP-4 flake — one
  re-run is expected to go green; do not spend a session debugging it.
- Forbid: `run-gate-project/`, `ciu/src/`, `/workspaces/dstdns`, any
  host-mutating write from the daemon, `cmru release`, push, merge.
- Host load: check `docker ps --format '{{.Image}}'` for `tester-unified`
  before every gate run (cap 2 across the estate), check
  `/proc/pressure/memory` and `/proc/pressure/cpu` (PSI, never
  `load`/`free` alone), serial pytest only, `nice -n 19 ionice -c 3`, at
  most one whole-suite run per checkpoint plus one before the eventual
  return.
- Checkpoint again at ~120k context or ~60 tool calls (whichever first),
  at the next coherent boundary (green gate > commit > LOG/REPORT write >
  edit-cluster end; never on a red gate). C5+C6 together may be large
  enough to need their own internal checkpoint before C7 — if so, cut
  after C5 lands gated-green (CLI + golden round-trips) and before
  starting the Docker image work, and say exactly what remains in the
  next BRIEF.

## `/compact`-style retention prompt (if this session's context is
compacted rather than ended)

KEEP: the state-as-of-`8ba01138`/`91d9d6a1` summary above; the "what C5
needs from C4" wiring list (cmd_serve's refusal checks and constructor
mapping, cmd_ctl's request/response/exit-code contract, the golden
round-trip test pattern reusing `tests/test_serve.py`'s real-socket
approach); the read-list for C5 (especially: `lib/serve.py`'s
`SessionServer.__init__` signature, the contract's §1.2/§1.3/§1.5,
`cgprofile.py`'s existing parser conventions); the standing rules block
(Edit-tool-only, trailers, host load, checkpoint threshold, the CP-4
flake note). DROP: the C1/C2/C3/C4 golden-fixture and branch-coverage
arithmetic, the exact list of C4's 69 test names (fully recorded in
REPORT, re-derivable from `tests/test_serve.py` itself if ever needed
again), the double-sampling test bug's blow-by-blow debugging session
(the REPORT's one-paragraph summary is enough — "never let handle_start
spawn a thread AND drive _session_loop synchronously on the same
session"), the exact PSI numbers from session 4's host-load log.
