# Design: `requires_contained_cgroup` (TP-13)

Status: design, 2026-11-24 (package P-core-02). Implementation: P-core-04. Supersedes RG-293 and cmru P7.0a
(the run-gate lane-cap fact and the `capped_container` lane allowlist).

## Decision summary

* `@pytest.mark.requires_contained_cgroup` marks a test that signals, kills or floods processes. Before the body
  runs, `estate_test_policy` checks one LOCAL fact: this process is contained. Contained means a finite
  effective cgroup v2 `memory.max`, a finite effective `pids.max`, and a private PID namespace. The marker carries
  no lane identity, no image identity and no run-gate protocol (operator Q4).
* The PID-namespace part is the protection; the caps part is a portability guard. On this host every Docker
  scope, the devcontainer included, gets finite caps (see Measurements). Caps alone pass in the devcontainer, where
  a test signalling PID 1's child killed it twice (risk R1, PROGRAM rule 3).
* Under `fail` a refused test FAILS at setup; under `warn` it is SKIPPED. In both modes the body never runs. Under
  `off` the marker is inert. The entry point defaults to `off` (TP-10a); a project opts in via `pytest.ini`.
* The marker lives in `estate_test_policy` (it needs pytest), not in cli-extended (stdlib only).

## Measurements (2026-11-24, read-only probes)

The gate-shaped containers ran `tester-unified:local` (sha256:a99d0d60) with
`docker run --rm --init --cpus 1 --cgroup-parent dev-gates.slice … bash -c '<python probe>'`. The daemon
accepted `--cgroup-parent dev-gates.slice`.

| fact | devcontainer | capped (`--memory 1g --pids-limit 512`) | memory only (`--memory 512m`) | uncapped |
|---|---|---|---|---|
| `/proc/self/cgroup` | `0::/` | `0::/` | `0::/` | `0::/` |
| effective `memory.max` | 12884901888 | 1073741824 | 536870912 | **12884901888** |
| effective `pids.max` | 19116 | 512 | **19116** | **19116** |
| PID 1 | `sh` (`/usr/bin/dash`) | `docker-init` | `docker-init` | `docker-init` |
| `/proc/self/ns/pid` equal to PID 1's | yes | yes | yes | yes |
| `NSpid` entries | 1 | 1 | 1 | 1 |
| pids in `/proc` | 239-253 | 2 | 2 | 2 |
| foreign pids (rule below) | 205-221 | 0 | 0 | 0 |
| PID 1's children | tmux, 2 codex, gpg-agent, dirmngr, … | the lane command | same | same |
| `ppid 0` processes besides PID 1 | many `sh` (docker exec sessions) | none | none | none |

Two facts the review did not expect:

* An uncapped container never shows `max` on this host. host-setup installs
  `/etc/systemd/system/docker-.scope.d/50-default-limits.conf`, a `MemoryMax` backstop of 12 GiB on every
  `docker-<id>.scope`. systemd's `DefaultTasksMax=15%` gives `pids.max` = 15% of `threads-max` 127442 = 19116.
  The cli-extended `r0-r1` lane declares `memory` but no `pids`, so it reads `pids.max` 19116, which is finite.
  So the caps checks never refuse on this host. They do refuse on a host without these backstops.
* Inside a private cgroup namespace the walk ends at the container's own cgroup, so the caps of
  `dev-gates.slice` are invisible. The marker therefore requires the container's own caps, which is intended.

The lane-shaped run was bash (not exec'd), then a python parent, then a child "worker". Both processes saw 0
foreign pids. After `(sleep 20 &)` in the lane's bash, the orphan was reparented to `docker-init` and counted as
foreign (pid 12, `sleep`, ppid 1). run-gate starts ephemeral lanes as
`docker run -d --init --cgroup-parent <slice> [--memory] [--pids-limit] … bash -c <inner>`
(`run_gate.py` lane start). It adds `--memory` and `--pids-limit` only when the lane declares them.

## The predicate

Inputs: `proc_root` (default `/proc`) and `cgroup_root` (default `/sys/fs/cgroup`), both injectable. Every fact
is evaluated and every failed one is reported. An unreadable or malformed file is a refusal; nothing fails open.

```
cg     = lines(proc_root/self/cgroup); refuse "cgroup-v2" unless cg == ["0::<path>"] and cgroup_root/cgroup.controllers exists
for k in memory.max, pids.max: walk cgroup_root/<path> up to cgroup_root; absent file = no limit at that level;
       effective = min(finite values); refuse k when none is finite
mount  = the fstype-proc line with mount point /proc in proc_root/self/mountinfo; refuse "proc-hidepid" if absent or hidepid!=0 / subset=pid
me     = pid of proc_root/self/stat; top = me; while ppid(top) not in {0, 1}: top = ppid(top)
refuse "pid-namespace-entry" if me != 1 and ppid(top) == 0          # entered by docker exec / nsenter
foreign = {p in proc_root/<digits> : p != 1 and p not in subtree(top)}   # vanished pid = skipped
refuse "pid-namespace-shared" if foreign stays non-empty for estate_policy_settle_seconds (re-scan; pass on the first empty scan)
```

Reasons for the PID rule:

* A process may live under PID 1 only inside the test's own top-level tree: the child of PID 1 that the test
  descends from. Within that tree, xdist workers, the assay supervisor, the lane shell and pipeline siblings such
  as `assay … | tee` are all allowed. The devcontainer refuses on PID 1's other children and on its `ppid 0`
  exec sessions.
* "PID 1 is `docker-init`/tini" is rejected as an identity allowlist. It would false-pass a devcontainer
  started with `--init` and false-refuse a lane without `--init`. The rule above is indifferent to both, and when
  pytest is PID 1 itself, `top` = 1.
* Comparing `ns/pid` or `NSpid` gives no signal: both read the same in all four columns.
* A strict "ancestor or descendant of me" rule refuses sibling xdist workers and `| tee` siblings.
* The re-scan exists for one known transient: run-gate's resource sampler `docker exec`s
  `sh -c 'cat /sys/fs/cgroup/…'` into lane containers (`run_gate.py`, `_read_container`), which shows up as a
  `ppid 0` process for milliseconds. The re-scan never turns a refusal into a wrong pass, because it only passes
  on a scan that is clean. The window reuses the existing ini `estate_policy_settle_seconds` (default 5 s), so
  a devcontainer run of a marked test fails after about 5 s.

Known false refusals, all conservative:

* An orphan reparented to PID 1 from outside the subreapers. This is measured above; assay and the TP-12 guard
  make the test-side orphans their own.
* A run-gate `exec`-mode environment (`top` has ppid 0). Such a container is shared by design.

Known false pass: a shared namespace whose other processes all sit inside `top`'s tree. That needs a PID 1 that
started one session holder (say tmux), with every session in it, no other PID 1 child and no exec session. The
measured devcontainer is not in that shape (205-221 foreign pids). Processes in other PID namespaces are invisible,
but they also cannot be signalled by pid from here.

The check runs at setup of each marked test and is not cached. It costs one `/proc` scan, which is 2 pids in a
lane container. `cgroup v1` and hybrid hosts are refused, and v1 is not supported.

## Marker semantics per mode

The rule name is `contained-cgroup`. Its mode is the plugin mode resolved for that rule: CLI `--estate-policy`
over ini `estate_policy`, plus any per-rule override that P-core-04 defines. The entry-point default is `off`.

| mode | contained | refused |
|---|---|---|
| `fail` | test runs | setup FAILS with the message below; the body does not run |
| `warn` | test runs | test SKIPPED with the same text, plus an `EstatePolicyWarning`; the body does not run |
| `off` | test runs | predicate not evaluated; the marker is inert; the body runs |

In `warn` the body does not run because, for this rule, running the test is the hazard. Projects that carry the
marker (cli-extended, cmru, ciu, nyxloom) set this rule to `fail` from the start. `estate_allow` does not accept
`contained-cgroup`.

Message shape (one line per failed fact, then the remedy):

```
estate-test-policy: requires_contained_cgroup refused:
  pids.max: effective value is max (walked /sys/fs/cgroup/<path> .. /sys/fs/cgroup)
  pid-namespace-shared: 221 processes outside this test's tree (pid 117 sh ppid 0; pid 2194 tmux: server ppid 1; ...first 5)
  remedy: run under a capped container with a private PID namespace (docker run --init --memory … --pids-limit …)
```

## Test oracle (P-core-04)

All cases use fake roots under `tmp_path`: a `proc/` holding `self` -> `<pid>`, `<pid>/stat` and
`self/cgroup|mountinfo`, and a `cgroup/` tree.

* Capped and private passes: memory.max 536870912, pids.max 512, PID 1 `docker-init` (ppid 0), and the test pid
  plus an xdist worker under PID 1's child.
* Uncapped refuses: `memory.max` = `max` at every level refuses and names `memory.max`; the same holds for
  `pids.max`. A `max` leaf under a finite parent passes, because the effective value comes from the walk.
* Shared namespace refuses: the devcontainer shape, PID 1 `sh` with a foreign child plus a `ppid 0` exec process.
* Entry refuses: a chain that ends at ppid 0 without reaching PID 1.
* Other refusals: a v1 or hybrid `self/cgroup`, an unreadable `memory.max`, a missing leaf directory, a missing
  `cgroup.controllers`, and `hidepid=2`.
* Settle: a foreign pid that is present in the first scan and gone in the second passes, with an injected
  clock. A foreign pid present in every scan refuses.
* Modes (pytester): under `fail` the setup fails and a sentinel file written by the body is absent. Under `warn`
  the test is skipped, warns, and the sentinel is absent. Under `off` the sentinel is present.
* Live acceptance: in the registered cli-extended `r0-r1` run, the marked procio tests report passed, not refused.

## Not included

* Lane identity, image identity, interpreter path, run-gate env facts (`RUN_GATE_LANE_CAPS_V1`).
* Allowlists of any kind: PID 1 names, process names (including the sampler's `cat`), or lane and environment
  pairs.
* A minimum cap size. Any finite value passes. Whether this host's backstop values should count as uncapped is an
  open question, and the PID rule is what refuses the devcontainer either way.
* cgroup v1 support, and cross-namespace detection beyond what `/proc` shows.
