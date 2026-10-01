# RG-55 P1 daemon adversarial review — round 4

REJECT

## Target and disposition

I began on the clean `rg55-p1-release-review-20260930` tree
`148481e4af504fc416679ed2fd8dffe380709de6`, with merge base
`8730098d0a8205bb398e60028e799b4ef7b18835`. I read the plan, interface
contract and fixtures, binding rulings, and implementer handoff before the
full 17-file diff. I formed the findings below before reading the implementer
LOG, REPORT, and BRIEF records. The reviewed diff includes source, tests,
adopter docs, controller log, handoff, LOG, and REPORT. One source-code
contract violation remains after the two scoped review commits below.

The verdict refuses provisional merge because of B1 below. B2 and B3 are
separate evidence barriers. Current-tree R2 and the registered full gate
are a separate **release/shipping hold** under RW-381; neither is claimed as
current evidence here. Earlier R2 on `4e5ff2d2` (125/125 killed) and the
`1908316b` hung outcome apply only to their own trees. This checkout has no
`.assay/verdict-r2.json` to read.

## Review findings and repairs

1. **F1, repaired — socket tests had a machine-speed oracle.** New tests at
   `tests/test_serve.py:1948-2095` polled for a socket for only five seconds,
   used two-second client deadlines, and joined with a five-second assertion.
   A slow but correct host could fail. Commit `0c361de93b6afc608e2bc3fc57781e4a94154c40`
   now synchronizes on an event published after the actual bind, and uses
   60-second timeouts solely as failure safeguards. The tests still assert
   the JSON contract behavior. Focused local run: 4 passed. The broader
   local source tests: 346 passed, 1 skipped. This is a bounded cockpit
   check, not the registered container gate.
2. **F2, repaired — privileged-daemon authority was understated.** The
   README's former `only ... writable` claim and `docs/DESIGN-GUIDE.md`'s
   former `writes only` claim confused intended Python writes with a security
   boundary. The same `0c361de9` commit updates README, DESIGN-GUIDE, and
   CONSUMERS: the P1 stack has no host bus or writable host cgroup mount,
   but it is privileged and its normal write paths do not confine a
   compromised process. The v1.1 placement bridge's extra bus/cgroupfs
   authority and D-32's no-broker choice are stated explicitly. The docs
   loader, vocabulary, anchor and mirrored-contract tests passed locally.
3. **F3, repaired — the new helper PID fixture was hollow for the prefix
   guard.** Temporarily disabling `lib/targets.py:284`'s PID-prefix check
   left `test_helper_pid_refuses_missing_or_invalid_nspid_facts` green:
   another identity check supplied the refusal. Commit
   `d11adc4122a5bbc645d38763e1073d23795dde16` adds a direct stat reader
   test with a valid start-time field under the wrong PID. It failed under
   that controlled mutant and passed restored (2 focused tests passed).

Three other changed behaviors were checked against controlled wrong
implementations in isolated temporary copies: removing the socket's
non-object guard failed the malformed-request test; serializing an omitted
`--damon` as JSON null failed the real-socket default test; swallowing an
`os.walk` error failed the absent-versus-unreadable target test. Each mutant
failed its named behavioral test and no mutation was made in the worktree.

Seven separate temporary `lib/summary.py` mutants were caught by the golden
tests: max→min, nearest-rank floor, PSI `/1e6`→`/1e3`, `cores_max` max→min,
limit-drift changed→equal, container `memory.peak`→sampled max, and
last-successful→first read. The shared and container golden tests caught
the applicable arithmetic errors. The normal targeted local run passed
141 summary/target tests before the review repairs. The fixture identity
test also passes against the canonical fixture tree in this worktree.

## Independent code assessment

- `cgprofile.py` omits unspecified `token`/`damon`/`interval` keys, allowing
  the server's configured DAMON and interval defaults to apply; explicit
  invalid values remain bad requests. The server rejects non-object JSON,
  non-string verbs and tokens, preserves `contract: 1` on declared errors,
  and distinguishes a complete empty cgroup search (exit 2) from an
  unreadable tree (exit 3). The proc stat reader verifies the requested PID.
- `lib/summary.py` produces the expected values on the frozen frames, using
  nearest rank, last-successful absolute counters for scope `container`, start/end deltas
  for shared scope, actual adjacent monotonic times for `cores_max`, nullable
  deltas, and the contract's memory sources. Seven arithmetic mutants above
  demonstrate the golden tests' discrimination. The values are computed at
  stop from retained raw samples, which is B1 below.
- `lib/serve.py` guards session paths; `lib/damon.py` guards its direct
  `nr_kdamonds` write. The normal daemon path has no `TempCaps` import or
  `--cap` option. DAMON sessions use one pool, release the low index without
  shrinking under a live high index, and restore the baseline after the last
  owned index. The code's restart path writes a partial aborted summary and
  retention excludes live manifests. SIGTERM requests orderly finalization.
  These are code/test findings; live behavior on this tip is unverified.
- The Compose template authors the interactive parent, keeps private PID
  and cgroup namespaces and `network_mode: none`, mounts host proc/cgroup
  read-only, has no Docker socket, mounts DAMON separately, and declares
  `1g` memory plus `2g` combined memory/swap. The Dockerfile installs at
  build time and carries version/revision OCI labels; the wrapper routes
  collector/report interpreters. The R2/R3 declarations say `cpus = "3"`;
  live `NanoCpus` on this tip is unverified. `build-push.py --push` uses the
  release version resolver before login or push.
- The canonical and package contracts are byte-identical. CP-4..CP-7 are
  recorded follow-ups, not claimed completed capabilities. The v1.1
  `PROTOCOL.md` named in the handoff is absent from this P1 tree; it is a P6
  surface, so there is no P1 PROTOCOL file to judge.

## Conditional barriers

**B1 — summary is recomputed at stop, contrary to contract §1.5.**
`lib/summary.py:298-319` appends every raw cgroup, host, slice, and DAMON
sample. `finalize` at `:349-381` calls `_memory_block`, `_cpu_block`,
`_pressure_block`, `_faults_block`, `_damon_block`, `_host_block`, and
`_events_block`, which scan or sort those retained collections at
`:385-553`; `lib/serve.py:1005-1020` invokes `finalize` synchronously from
`ctl stop`. This is O(samples) work at stop, whereas the frozen interface
contract §1.5 explicitly requires the summary to be maintained at sampling
time and never recomputed from the series at stop so `stop` answers within
30 seconds for a session of any length. A deterministic one-hour/one-second
probe fed 3,601 samples, then changed the first and last input dictionaries
after `add_sample`; `finalize` reported the post-capture value
`memory.peak_bytes=999999999` and `baseline_bytes=999999999`. That proves the
summary was still derived from raw input at stop. The probe did not make a
wall-time claim: it establishes the architectural violation, not a measured
30-second failure on this machine. Prescription: update running counters,
first/last successful reads, per-pair rates/drift, and ordered percentile
state during `add_sample`; make `finalize` assemble the response from that
state without scanning raw samples. Preserve the §7 arithmetic and null
rules, and add a deterministic long-session behavioral oracle. Re-run the
registered short gates, current-tree R2 and full gate after repair.

**B2 — BLOCKED: host unit state cannot be established from this cockpit
without the prohibited host-namespace carrier.** The authored parent is
`CGROUP_PARENT_DEV_INTERACTIVE=dev-interactive.slice`, and the gate parent is
`CGROUP_PARENT_DEV_GATES=dev-gates.slice` (`ciu.compose.yml.j2:74-80`). The
read-only `systemctl show dev-gates.slice --property=LoadState,ControlGroup,
CPUQuotaPerSecUSec` returned exit 0 but only the local stub text `"systemd"
is not running in this container`; it returned **no** `LoadState` or
`ControlGroup`. `/sys/fs/cgroup/dev.slice/dev-gates.slice` is absent in this
private cgroup view; `/run/dbus/system_bus_socket` and `/run/systemd/private`
are absent. The existing cockpit container's Docker config names
`dev-interactive.slice`, but a name is not proof of a loaded host unit.
RW-384 records an earlier host read-only preflight, but it is not this
reviewer's current read. The missing observable is current loaded unit and
host ControlGroup state for both authored parents. Per the handoff's
mechanical rule, I stopped the live probe path here: no reviewer-owned
daemon, workload, helper, or gate container was launched, and I did not use
`host-escape` (which calls `mdt host-exec`, entering the host and running a
cgroup self-heal). Prescription: the controller provides an authorized
read-only host-unit preflight that does not join a host namespace, or reruns
the required reviewer-owned live probes from a carrier with that unit view.

**B3 — short-gate receipts must be refreshed on the repaired tip.** The
`.run-gate/history.json` records separate clean PASS/exit 0 for R0/R1 and
R3 at `148481e4af504fc416679ed2fd8dffe380709de6`, matching the
caller-supplied evidence. Review commits `0c361de9` and `d11adc41` change
tests/docs, and this round record changes the tree again. Those receipts
therefore do not certify the final tip. Run registered `./run-gate.py r0-r1`
and `./run-gate.py r3`, plus doctor, on the final quiet commit after B2's
placement preflight. The devcontainer venv lacks pandas, so its local tests
are not a substitute: collecting `tests/test_cgprofile.py` there failed on
`ModuleNotFoundError: pandas`.

## Other findings and unverified claims

- **S1 — crash can leave an owned kdamond running.** `_recover_orphans` at
  `lib/serve.py:278-350` replays summaries but does not identify or stop the
  former process's DAMON slots; a new `KdamondPool` reads the current
  `nr_kdamonds` as foreign baseline (`lib/damon.py:255-266`). An abrupt
  process death can therefore retain DAMON activity until an operator or
  host cleanup intervenes. The code cannot safely infer which slots were
  foreign after restart. Record this limitation for the controller's daemon
  lifecycle policy; no unproven automatic shrink is prescribed.
- **S2 — v1.1 contract wording needs a controller-owned trust clarification.**
  Contract §5 calls the host bus `read-only` and says the bridge is not a
  general systemd control surface. That describes the intended protocol,
  not what a compromised privileged process can call through a mounted bus
  socket. D-32/RW-382 explicitly retains that authority. The canonical
  contract is read-only to this reviewer, so both mirrors remain
  byte-identical; the controller should clarify both before P6 shipping.
- `ciu check` passed but `ciu render` selected zero stacks, so it did not
  demonstrate a governed rendered service. Unsetting
  `CGROUP_PARENT_DEV_INTERACTIVE` did make render refuse (exit 1). CIU
  also attempted to create `cgroup-profiler-bb7638-network` during these
  commands despite `auto_connect_network=false`; Docker refused because
  address pools were exhausted. A read-only network inspect confirmed no
  such network exists. No CIU or Docker cleanup was issued.
- The handoff's `cmru status --project cgroup-profiler` is not supported by
  the installed CMRU CLI: it exited 2 with `unrecognized arguments:
  --project`. No status claim is made from that command.
- Local `cgprofile:local` has OCI revision `66d33e05a2861908faa909df7ed5c85e29a72882`,
  not the reviewed tip. A current image build, `ctl version --json`, the
  ephemeral/shared/two-session/helper PID probes, DAMON assertion, HTML
  report opening, actual daemon/container 3-CPU caps, and current host
  overhead remain unverified because of B2. Historical probe results in
  earlier rounds are not transferred to this tree.
- The current-tree R2 and full gate are deliberately pending under RW-381.
  They remain mandatory before release, publish, install, or daemon
  activation, with any fixes backported and rejudged. They are not reported
  as a code-review failure or as a PASS.

No product decision ask remains: D-32 already chooses the no-broker trust
model. No pre-existing daemon, workload, network, or attachment was stopped
or removed.
