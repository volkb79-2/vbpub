# cgprofile-P6-FOLLOWUPS — adversarial review handoff (RG-55 wave, package P6)

**Reviewer:** FRESH session (Opus, xhigh), never a fork of any implementer or
the controller. **Your job is to BREAK this before merge.** 3-round cap;
fix-verification rounds resume YOUR session (the controller messages you the
repair commit). Records: `scripts/cgroup-profiler/nyxloom-trove/reports/
cgprofile-P6-FOLLOWUPS-REVIEW-round<n>.md`. Edit tool only for that file; no
commits to the branch — repairs are the implementer's.

Branch `rg55-followups-cgprofile`, worktree
`/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`, project dir
`scripts/cgroup-profiler/`. Base: the P1 tip named in the dispatch message
(the branch merged it before the final gates). The tip hash is in the
dispatch message. Review the FULL diff `<base>...<tip>` — every file, every
type (lib, cgprofile.py, tests, goldens under `tests/fixtures/rg55/` AND the
frozen cross-package copy `run-gate-project/nyxloom-trove/fixtures/rg55/`,
`infra/`, ciu templates, Dockerfile, docs, CHANGES, backlog rows, the eight
session briefs, LOG/REPORT). Use absolute paths; ignore whatever primary
working directory the environment reminder names.

## Phase 1 — BLIND (before any LOG/REPORT/BRIEF)

Read, in this order: contract `run-gate-project/nyxloom-trove/
RG55-INTERFACE-CONTRACT.md` §1–§7 (v1) and **§8 (v1.1 — the spec)** on
`main`; design of record `DESIGN-2026-09-12-liveness-placement-admission.md`
§2 (D-17..D-26), A1 (D-27..D-29), A2 (D-30); controller rulings RW-30,
RW-31, RW-34, RW-35, RW-37, RW-39, RW-42, RW-44, RW-45; the implementer
handoff `cgprofile-P6-FOLLOWUPS-HANDOFF.md` (C1–C9); backlog rows CP-2,
CP-4..CP-11; then the diff itself — `lib/serve.py`, `lib/liveness.py`,
`lib/placement.py`, `lib/events.py` use, `lib/analyze.py`, `lib/store.py`,
`cgprofile.py`, `docs/PROTOCOL.md`, the ciu templates, `infra/`. Form your
own view of correctness, safety and contract conformance BEFORE the
narratives. Run your OWN sweeps.

## Phase 2 — RECONCILE against the implementers' claims

Read `cgprofile-P6-FOLLOWUPS-LOG.md` (incl. every "Decision asks" block),
`-REPORT.md`, `-BRIEF-1..8.md`; check each claim; list what you could not
verify. Eight sessions built this — hunt the seams between sessions.

## Attack surface (minimum; add your own)

1. **D-15 stays a whitelist.** Enumerate EVERY write the daemon can issue
   (grep `open(.*"w"`, `write_text`, `os.replace`, `os.rmdir`, `os.mkdir`,
   `shutil`, `subprocess`, `os.kill`, `killpg`, `chmod`, `chown` across
   `lib/` and `cgprofile.py`). The only cgroup writes allowed:
   `<gates slice>/cgroup.subtree_control` (`+` values only),
   `<gates slice>/rg-*/{cgroup.procs,memory.high,memory.max,cpu.weight,
   cgroup.kill}`, the original scope's `cgroup.procs` (move-back), `rmdir`
   of `<gates slice>/rg-*`; plus the sessions dir, `/sys/kernel/mm/damon/
   admin`, and the socket dir/socket perms. Plant a write outside the
   whitelist through the guard and prove refusal; plant a `-` value; plant a
   leaf path outside the gates slice (symlink). Does `_enforce_stall_kill`
   ever signal a pid outside the token subtree (a shared-scope session with
   no token must REFUSE the kill)? Can `cgroup.kill` land on a leaf that is
   not `rg-<this token>`?
2. **Socket carrier trust boundary (D-30, §8.1, §8.6).** `SO_PEERCRED` on
   every connection; uid 0 always allowed; `CGPROFILE_ALLOW_UIDS` fail-closed
   (a malformed list refuses daemon start — prove); `peer-refused` shape and
   close; socket `root:<dir gid> 0660`, dir `0770`, a root:root dir → INFO
   and root-only (prove no widening); the bind never follows a symlinked
   socket path; the wire check refuses v1's flat request BY NAME
   (`bad-argument`), no compatibility branch; one request per connection
   except `watch`; the 25 s server-side timeout; `transports` truthful
   (`listening` reflects the real bind). Parity: every verb over both
   carriers diffed (the test AND your own probe).
3. **Watch role (D-27, §8.2, §8.4).** Every state transition on a fake
   clock; the PSI pause reads the right PSI (session 7 found the host-PSI
   seam was reading ambient host PSI in tests — is the PRODUCTION pause
   keyed on gates-slice PSI first, host PSI second, exactly D-22?); `hung`
   = terminal stream event + alive 30 s; `runaway` only with a stream;
   precedence `over_ceiling > hung > throttled > stalled/runaway > ok`;
   `--on-stall kill` really kills (real `sleep` subtree → `-SIGKILL`);
   `report` never kills; a kill does not finalize the session (D-28); the
   stream reader is bounded (64 KiB tail, torn line tolerant) and reads via
   `/proc/<pid>/root/` — what if the pid vanishes mid-read, or the path is a
   FIFO/device? `ctl watch` streaming: exactly one `end`, verdict lines only
   on change, `--watch-interval` clamp, unknown session one line exit 2,
   the accept loop still serves other verbs while streaming; over exec
   `ctl watch` flushes per line and exits 0.
4. **Placement (D-20/D-25, §8.3).** Leaf created only under the gates slice;
   `+memory +cpu +pids` written only when absent; caps read back (a
   rounding-write fake must be reported as read, never echoed); late pids
   migrated on every discovery tick; move-back on stop reads the leaf's own
   `cgroup.procs`; `rmdir` retry 3× / 3 s; every refusal code
   (`no-token`, `no-gates-slice`, `over-slice`, `parent-not-gates-slice`,
   `write-failed:<file>`) on a session that STILL starts/samples/stops;
   `placement` null only when never requested; `throttled` reachable only
   with a leaf; a malformed cap value is `bad-argument` (no session). CP-11
   (orphaned leaf after daemon restart) is filed open — confirm nothing
   worse happens (a restart with a live placed session must not kill or
   strand pids).
5. **Goldens and byte identity.** v1 goldens byte-identical except the
   version-string bump the implementer made in the FROZEN cross-package copy
   `run-gate-project/nyxloom-trove/fixtures/rg55/` (RW-45 accepts a version
   string change ONLY): diff the two copies, then run run-gate's own tests
   that read `fixtures/rg55/` against this tip's fixtures
   (`git -C /workspaces/vbpub show main:run-gate-project/run-gate.py` +
   tests — a throwaway copy of `run-gate-project/` from `main` with the
   branch's fixtures dropped in; `nice -n 19 ionice -c 3 python3 -m pytest
   tests -q -k 'fixture or golden or rg55'`), and say whether anything
   breaks. New goldens (`socket/*`, `watch-*`, `start-placed-v1.1`,
   `start-refused-v1.1`, `host-v1.1`, `status-v1.1`, `summary-v1.1`)
   generated by the real code and byte-checked by tests.
6. **CP-4..CP-7, CP-10.** The run-id widening (session ids untouched);
   `events.jsonl` rows real and the Summary counters unchanged (goldens);
   manifest limits resolved read-only; the DAMON series reader; CP-10's
   corrected root cause (`host_proc_root` seam) — was session 6's
   "peer-cred leak" hypothesis actually wrong, or are BOTH real? Run the two
   classes in both orders with three seeds yourself.
7. **`cgprofile.slice` + `ctl host` §8.5.** Unit values (D-29), authored
   `cgroup_parent: cgprofile.slice` survives ciu governance (render from the
   standalone root; unset the env vars the old template needed), `gates_slice`
   / `daemon_slice` shapes incl. `present: false`, the three `_host_snapshot`
   call sites.
8. **Hollow tests / coverage.** 100% line+branch is necessary, not
   sufficient: mutate the subject, watch the test, for every cluster; the
   session mutant tables are the implementers' — plant your own.
9. **Docs.** `docs/PROTOCOL.md` accurate to the code for every verb and arg;
   README/ATTACH-GUIDE/DESIGN honest (not aspirational); CHANGES per CP;
   version `1.1.0` everywhere (`grep -rn '1\.0\.0'`); backlog rows FIXED with
   real hashes; contract mirror byte-identical to `main`'s after the merge
   the implementer did.
10. **r2 survivors.** Read the r2 verdict and every survivor's justification
    in the REPORT; re-run one killing test per survivor claim.

## Live probes (you run them yourself; host rule below)

The singleton `cgprofile-host-daemon` is the controller's (it may be up from
`main` at 1.0.0 or down) — never start, stop or `ciu down` it. Build
`cgprofile:local` from the tip (`python3 build-push.py --build`, once, under
PSI) and run YOUR OWN instance `cgprofile-p6-review-probe` with the compose
template's flags (privileged, private PID/cgroup namespaces, `--network none`,
`--cgroup-parent cgprofile.slice`, read-only host `/proc` at `/hostproc`,
read-only host cgroup v2 at `/sys/fs/cgroup`, `CGPROFILE_PROC_ROOT=/hostproc`,
`-v /tmp/cgprofile-p6-review:/run/cgprofile`), removed in a `finally`:
- every verb over BOTH carriers from a throwaway `cmru-enroll-fixture:local`
  container that mounts the same scratch dir (`--group-add <dir gid>`) —
  diff the JSON; `peer-refused` with an allowlist that excludes your uid;
- a placed exec-mode probe (80 MiB lane, token set, `--place --memory-high
  64M --memory-max 96M`) → leaf exists during, `applied` read back, pids in
  the leaf, `throttled` readings when the lane exceeds `memory.high`, leaf
  gone after `stop`;
- a watch probe (`sleep` subtree, `--idle-bound 20 --on-stall kill`) →
  `stalled` then `killed` in ≤ 60 s on `ctl watch` over the socket, then
  the same with `--on-stall report`;
- `ctl host` showing `gates_slice.present` (true only if the operator has
  installed mdt host-setup; report what you see) and `daemon_slice`.

## Verdict

`ACCEPT` / `ACCEPT-conditional` / `REJECT` with numbered blockers (B1..),
each with file:line evidence and a concrete prescription; non-blocking
findings (S1..) separately; product calls named as decision asks for the
controller, never improvised. Claims you could not verify listed as such.
Write the round file, then return the verdict line first in your message.

## HOST LOAD (binding)

8 cores shared with a production game server; PSI is the signal
(`cat /proc/pressure/memory`; back off while `full avg10` > 5). pytest
SERIAL only, `nice -n 19 ionice -c 3`; targeted files while iterating, the
whole suite at most once. ≤ 2 gate containers estate-wide (`docker ps` for
`run-gate-`/`tester-unified`/`cgprofile-` first; other packages' mutation
runs may be live); one image build, under PSI; `docker update --cpus=3`
after any launch; remove in a `finally`. No container may use host
PID/cgroup/network namespace modes. Never touch `run-gate-project/run-gate.py`,
`ciu/src/`, `/workspaces/dstdns`, other worktrees.
