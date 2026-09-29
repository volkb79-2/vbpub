# cgprofile-P6-FOLLOWUPS — adversarial review handoff (RG-55 wave, package P6)

**Reviewer:** a genuinely fresh GPT-6-Sol xhigh session, never a fork of any
implementer or the controller. The caller configures and verifies the route;
the reviewer is not required to self-identify or attest model/effort metadata.
Proceed with the technical review if that metadata is not exposed. **Your job
is to BREAK this before provisional integration.** The previous review series
is preserved in rounds 1–5; round 5 rejected blockers B1–B6 and its report is
the repair baseline. The next verification is round 6; round 7 is the final
round under the three-round cap (rounds 5–7). Resume the round-5 reviewer for
fix verification if that session is still available; otherwise start a fresh
Sol xhigh session seeded with this handoff and rounds 1–5. The caller configures
the model route and effort; never ask the reviewer to attest its own metadata.
Record each verdict at
`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P6-FOLLOWUPS-REVIEW-round<n>.md`.
You may make and commit scoped fixes in the isolated P6 worktree, but may not
merge, tag, publish, install, or start/stop the main daemon.

Branch `rg55-followups-cgprofile-final`, worktree
`/workspaces/vbpub/.worktrees/rg55-followups-cgprofile-final`, project dir
`scripts/cgroup-profiler/`. At dispatch the controller supplies the exact
current `main` base and committed candidate tip; review the FULL diff
`<base>...<tip>` and verify the worktree state. The candidate includes the P1
daemon plus P6 follow-ups and their reconciliation with current main. Review
every changed file: library and CLI code, tests, both fixture copies, `infra/`,
CIU templates, Dockerfile, README, DESIGN-GUIDE, CONSUMERS, CHANGES, backlog,
controller log, P6 LOG/REPORT/briefs. The root and mirrored interface
contracts must remain byte-identical. Use absolute paths; ignore any different
primary working directory in the environment reminder.

### Controller integration boundary (RW-296 and RW-381; binding)

This is the final adversarial code review for a **provisional `--no-ff`
integration only**. The controller will dispatch this fresh review series only after the
exact candidate has green registered `r0-r1` and `r3` gates, full changed-line
and branch coverage, and the live probes required below. The current-tree R2
and full gate are intentionally allowed to finish asynchronously after
provisional integration so other RG-55 packages can proceed under RW-381.
The latest prior
P6 R2 receipt at `6540f87761a66ff933c8bb45f81d8ac9117f407b` is
`BUDGET_EXCEEDED/CANDIDATE_HUNG` and is not evidence for this candidate.

Do not reject solely because the exact-tree R2 or full gate is still pending.
Do reject any code, safety, contract, documentation, or oracle defect you find.
On `ACCEPT`, only the controller may provisionally merge this reviewed tree;
that verdict does **not** authorize a release, tag, install, or `ciu up`.
Those remain blocked until a fresh exact-tree R2 has a complete acceptable
verdict and survivor disposition, the registered full gate is green, and the
remaining RG-55 release/close-out conditions are satisfied. Keep the merged
candidate's CIU mutation worktree quiet and separate from fixes. Any repair
commit must be reviewed in this same live Sol session; the controller reruns
the affected short gates before provisional integration. If the review adds
or commits its round artifact, the exact reviewed tip changes and both short
lanes must be rerun on that tip before merge.

## Phase 1 — BLIND (before any LOG/REPORT/BRIEF)

Read, in this order: contract `run-gate-project/nyxloom-trove/
RG55-INTERFACE-CONTRACT.md` §1–§7 (v1) and **§8 (v1.1 — the spec)** on
`main`; design of record `DESIGN-2026-09-12-liveness-placement-admission.md`
§2 (D-17..D-26), A1 (D-27..D-29), A2 (D-30); controller rulings RW-30,
RW-31, RW-34, RW-35, RW-37, RW-39, RW-42, RW-44, RW-45, RW-319..RW-381
(especially RW-379..RW-381);
the implementer handoff `cgprofile-P6-FOLLOWUPS-HANDOFF.md` (C1–C9);
backlog rows CP-2, CP-4..CP-11; then the diff itself — `lib/serve.py`,
`lib/placement.py`, `lib/events.py` use, `lib/analyze.py`, `lib/store.py`,
`cgprofile.py`, `docs/PROTOCOL.md`, the ciu templates, `infra/`. Form your
own view of correctness, safety and contract conformance BEFORE the
narratives. Run your OWN sweeps.

## Phase 2 — RECONCILE against the implementers' claims

Read `cgprofile-P6-FOLLOWUPS-LOG.md` (incl. every "Decision asks" block),
`-REPORT.md`, every existing P6 brief through `-BRIEF-13.md`, and prior review
rounds 1–5; check each claim; list what you could not verify. Multiple
sessions built this — hunt the seams between sessions.

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
3. **Watch role (D-27, §8.2, §8.4; CP-12; RW-379/RW-380).** Every state
   transition on a fake clock; the PSI pause reads the right PSI (session 7
   found the host-PSI seam was reading ambient host PSI in tests — is the
   PRODUCTION pause
   keyed on gates-slice PSI first, host PSI second, exactly D-22?); `hung`
   = terminal stream event + alive 30 s; `runaway` only with a stream;
   precedence `over_ceiling > hung > throttled > stalled/runaway > ok`;
   no host-PID signaling. For `scope=container`, kill writes only to the
   exact verified container-ID cgroup's `cgroup.kill`; for
   `scope=container-shared`, kill requires a token and an explicitly
   requested, verified placement leaf. Never invent placement just to enable
   kill; `report` remains non-killing. Verify real enforcement, refusal stays
   `reported`, and CP-12's terminal `end` behavior; the old D-28
   non-finalizing-kill behavior is superseded. The
   stream reader is bounded (64 KiB tail, torn line tolerant) and reads via
   `/proc/<pid>/root/` — what if the pid vanishes mid-read, or the path is a
   FIFO/device? `ctl watch` streaming: exactly one `end`, verdict lines only
   on change, `--watch-interval` clamp, unknown session one line exit 2,
   the accept loop still serves other verbs while streaming; over exec
   `ctl watch` flushes per line and exits 0.
4. **Placement (D-20/D-25, §8.3).** Leaf created only under the gates slice;
   `+memory +cpu +pids` written only when absent; caps read back (a
   rounding-write fake must be reported as read, never echoed); late pids
   migrated on every discovery tick; stop enumerates host-visible survivors
   from the exact leaf when private-namespace `cgroup.procs` exposes PID 0;
   verify each survivor is still in that leaf before moving. Try the original
   scope's guarded `cgroup.procs` first; on `ESRCH`, use
   `AttachProcessesToUnit` only for the nearest verified systemd unit and
   subgroup derived from the absolute original cgroup path, then verify
   membership through the explicit host-proc view and record the successful
   move in the D-25 event sink. If a survivor cannot be identified/restored,
   the placement must remain unreleased, the leaf must remain intact, and the
   response must expose `write-failed:<origin-cgroup.procs>`; it must never
   claim cleanup succeeded. Cover vanished PIDs, stale membership, missing
   host-proc view, unsafe/relative origin paths, direct write success, and
   systemd fallback refusal. `rmdir` retries 3× / 3 s; every refusal code
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
11. **R-36h non-blocking behavior.** Any daemon start, sampling, socket,
    placement, or stop refusal must be disclosed without blocking the lane or
    changing its verdict; the lane-local watchdog remains verdict authority.

## Live probes (you run them yourself; host rule below)

The singleton `cgprofile-host-daemon` is the controller's (it may be up from
`main` at 1.0.0 or down) — never start, stop or `ciu down` it. Build
`cgprofile:local` from the tip (`python3 build-push.py --build`, once, under
PSI) and run YOUR OWN instance `cgprofile-p6-review-probe` with the compose
template's flags (privileged, private PID/cgroup namespaces, `--network none`,
  `--cgroup-parent cgprofile.slice`, read-only host `/proc` at `/hostproc`,
  host cgroup v2 at `/sys/fs/cgroup` with the D-25 placement whitelist,
  `CGPROFILE_PROC_ROOT=/hostproc`,
`-v /tmp/cgprofile-p6-review:/run/cgprofile`), removed in a `finally`:
- every verb over BOTH carriers from a throwaway `cmru-enroll-fixture:local`
  container that mounts the same scratch dir (`--group-add <dir gid>`) —
  diff the JSON; `peer-refused` with an allowlist that excludes your uid;
- a placed exec-mode probe (80 MiB lane, token set, `--place --memory-high
  64M --memory-max 96M`) → leaf exists during, `applied` read back, pids in
  the leaf, `throttled` readings when the lane exceeds `memory.high`; on stop,
  verify the surviving host PID is back in the original systemd scope via
  `/hostproc/<pid>/cgroup`, a successful `cgroup_write` event names the origin
  path, and only then is the leaf removed. Include a fail-closed probe where
  mapping or attachment is refused: the leaf remains and stop does not claim
  success;
- a watch probe with a tagged sleeper in a throwaway `dev-gates.slice`
  target. For `container-shared`, pass the token and explicitly request the
  verified placement leaf; prove the selected lane is killed while a second
  lane and the daemon remain alive. `ctl watch` over the socket must report
  `stalled`, then `killed` and one terminal `end` in ≤ 60 s. Repeat with
  `--on-stall report` and no placement; prove the sleeper survives and the
  session reports without claiming a kill. Do not invent placement for a
  normal consumer that did not request a resource plan.
- `ctl host` showing `gates_slice.present` (true only if the operator has
  installed mdt host-setup; report what you see) and `daemon_slice`.

Before binding the scratch directory, resolve the Docker host path from the
current cockpit's mount map. In this environment `/tmp` inside the cockpit is
not the host `/tmp`; the P6 report documents the exact trap. Use `mktemp`
under the resolved host-backed temporary root, set only that probe directory
to the socket's required group/mode, and remove only that exact directory at
cleanup. Never let Docker auto-create a missing host bind source.

## Verdict

`ACCEPT` / `ACCEPT-conditional` / `REJECT` with numbered blockers (B1..),
each with file:line evidence and a concrete prescription; non-blocking
findings (S1..) separately; product calls named as decision asks for the
controller, never improvised. Claims you could not verify listed as such.
Write `cgprofile-P6-FOLLOWUPS-REVIEW-round6.md`, then return the verdict line
first in your message. If round 6 rejects and its reviewer session ends,
round 7 must be fresh and seeded with all prior round files; round 7 is the
series cap.

## HOST LOAD (binding)

8 cores shared with a production game server; PSI is the signal
(`cat /proc/pressure/memory`; launch nothing while `full avg10` > 5). pytest
SERIAL only, `nice -n 19 ionice -c 3`; targeted files while iterating, the
whole suite at most once. At most 3 mutation containers estate-wide per
RW-319; each has an exact unique name, a verified loaded `dev-gates.slice`
parent, `--cpus=3` at creation, and immediate verified
`docker update --cpus=3` (`docker ps` for `run-gate-`/`tester-unified`/
`cgprofile-` first; other packages' mutation runs may be live). The review's
own daemon probe belongs only under its explicitly verified daemon slice;
workload/test containers belong under loaded `dev-gates.slice`. Apply and
verify the 3-CPU cap on every reviewer-owned container. Build one image under
PSI. Remove only your exact named containers and dedicated scratch you
created in a `finally`.

### Shared-host isolation (binding)

- Do not run `ciu up` or `ciu down` for review probes. Do not issue Docker
  network `create`, `connect`, `disconnect`, or `rm` commands. A probe may
  not attach to or otherwise alter an existing container's network membership.
- Use only uniquely named reviewer-owned probe containers with
  `--network=none`. Never inspect internals, update, stop, remove, or alter an
  existing agent-owned gate/container (including a cockpit); the controller
  may report its exact name as an exclusion.
- Never use host PID/cgroup/network namespace modes. If required live evidence
  cannot be obtained within these constraints, state exactly what is
  unavailable; do not weaken the constraints or improvise cleanup.

Never touch `run-gate-project/run-gate.py`, `ciu/src/`,
`/workspaces/dstdns`, or other worktrees. Read-only access to the interface
contract and controller-owned review packet is allowed; do not edit them as a
reviewer.
