# run-gate design guide

## SemVer names the installed tool

Consumers install Run-Gate as a wheel and invoke its `run-gate` console
command. The wheel's SemVer, read from installed distribution metadata, is the
public version and compatibility floor. `run-gate --version` and the normal
CLI banners all report that same value. Missing or malformed package metadata
is an error; Run-Gate does not guess from a source checkout.

The old integer `__revision__` existed to show whether copied scripts had
drifted. Once copied scripts and consumer symlinks are retired, that number
cannot tell an adopter whether its installed package satisfies a release
floor. Release tags therefore carry SemVer (`run-gate-vX.Y.Z`), and consumers
compare that value with their required version. Persisted `revision` fields
can remain where they identify the writer of older records or a record
format; those are data provenance, not the installed tool's identity.

This separates two questions: package compatibility is answered by SemVer,
while saved-record compatibility is answered by the record's schema and
provenance contract. See the [user-facing version contract](../CONSUMERS.md#distribution-installed-wheel-and-semver)
and [CLI overview](../README.md#distribution-installed-wheel).

## Changed-line coverage follows content across renames

The selftest coverage floor compares executable lines changed since the
selected base. Git must identify renames before Run-Gate filters the diff to
`run_gate.py`. A path-limited diff hides the deleted path from rename
detection; when a compatibility symlink becomes the wheel module, Git then
reports every line in the module as newly added. The gate uses rename and
rewrite detection on the project diff, then applies its source filter to the
parsed paths. This keeps the denominator tied to changed content while still
ignoring unrelated files. See the [release-gate description](../README.md#version-probe).

## A worktree is the complete judgment boundary

`--worktree W` selects the source tree whose commit is judged. The lane policy
must come from that same tree: a worktree can add a lane, change an Assay pin,
or update a shared root config before any of those changes reach the invoking
checkout. Reading the invoking checkout's config would make the run look
successful while judging a different declared gate.

run-gate discovers the invoking project to learn its position inside the Git
toplevel, then maps that relative path into W. It reads the mapped project's
`run-gate.toml` and the nearest `run-gate.root.toml` inside W's Git toplevel.
Central lookup stops at that boundary, so a nested standalone repository
cannot inherit a parent's gate policy. The mapped project config is required.
The config path is printed before a lane starts,
and the run record stores the config path and SHA-256 for the exact bytes
parsed. It also records the path and SHA-256 of the inherited root config when
one supplied shared policy.

This rule preserves nested projects in a monorepo: a project at
`packages/service` in the invoking checkout maps to
`W/packages/service`, while the worktree root stays the Git judgment root.

## Shared Git metadata keeps host hooks inert

Linked-worktree container lanes need the common Git directory read-write for
object writes and in-lane commits. Making that entire mount read-only would
break supported lane behavior, while masking only Git config still leaves
`<common>/hooks` writable. A tester could write `post-commit` there and have a
later host commit execute tester-controlled code.

Run-Gate keeps the shared directory writable, but overlays its common `hooks/`
directory with a private empty directory mounted read-only. It mounts the real
common `info/` directory read-only as well, protecting host exclude metadata.
Both overlays follow the physical and namespace mount paths. Missing, symlinked,
or non-directory hook/info paths refuse as infrastructure before the lane
starts. The real-container oracle plants a host hook, proves the tester cannot
create a hook or edit `info/exclude`, and verifies the in-container commit
does not execute the host hook. A subsequent host commit runs the original hook
unchanged. This preserves normal in-container commits while keeping the host's
later Git operations outside the tester's hook-writing authority.

CMRU independently disables hooks around its own host Git calls while it
prepares release-baseline facts. That protects the aggregate controller's
credential-bearing Git operation; the Run-Gate mount protects later host Git
operations after a tester lane.

## The runner belongs to the judged worktree

An exec lane uses a persistent runner managed by that project's deployment
tool. In a multi-instance workflow, the runner's name, network, and rendered
environment belong to the selected worktree. run-gate derives the name only
from that worktree's rendered `ciu.global.toml`. If the file is missing, the
worktree has no known runner identity, so run-gate refuses and directs the
operator to start that worktree's test-runner with CIU. The prescribed
`ciu up --dir <test-runner stack> --deploy --healthcheck` remedy requires
CIU 7.15.2 or newer (CIU-124), which added those actions to `--dir` mode.
Before `docker exec`,
run-gate checks `docker ps`; a stopped runner receives the same worktree-local
remedy.

An environment may declare `container_name` when its deployment authority
provides that explicit stable name. CIU-derived names use the worktree-local
rendered file and report its path in the execution header.

## Runner modes name the runtime contract

Environment names cannot safely select execution behavior. The former
implicit `host` name meant a container, while `bare-host` meant a subprocess;
the same config line could therefore change meaning when built-in resolution
changed. Each environment now declares `mode` directly:

| mode | execution | required facts |
|---|---|---|
| `ephemeral` | a fresh container for this lane | `image` |
| `exec` | a persistent externally managed runner | `image` and a declared or judged-worktree-derived identity |
| `host` | subprocess on the invoking host | no image |

This makes the name ordinary data. `[environments.host] mode = "ephemeral"`
is a container; any other name with `mode = "host"` is a host subprocess.
Missing `mode` is an error instead of an implicit choice. `migrate-modes`
inserts the old implicit choice as literal config text, preserving comments
and requiring the operator to review the resulting diff. It migrates one
file per call so a project config and an ancestor `run-gate.root.toml` can be
checked independently.

## PID 1 and cgroup resource events

A Python supervisor running as container PID 1 does not reap orphaned
grandchildren. Process-heavy tests can therefore fill the container's process
limit with zombies while the lane keeps running. run-gate refuses that
process placement and names Docker `--init` or Compose `init: true` as the
remedy. A read-only version query remains available because it starts no
supervisor work.

Exit status alone cannot establish that a lane had enough process and memory
capacity: a test command can ignore a failed fork and still exit zero, and an
OOM kill may look like a test failure. run-gate compares the same cgroup's
v2 `pids.events:max` and `memory.events:oom_kill` counters immediately before
and after each real lane. Those monotone kernel counters identify resource
events without parsing test output. Any increment forces ERROR/2 and retains
the lane's raw status for diagnosis. Unreadable or inconsistent post-lane
counters also produce ERROR and retain the raw status when available, because
run-gate cannot certify the run. A dry run starts no lane and does not sample
these counters.

## Bounded headroom admission is observational, not atomic

Before a daemon-backed lane starts, R-45 reads the profiler's host document
and compares the verified gates slice's current memory plus the lane's declared
or derived request with 90% of its maximum. If headroom is short, run-gate
checks every 30 seconds for at most `[profile].admission_wait` (10 minutes by
default). Headroom appearing admits immediately; expiry emits one warning and
the lane proceeds. Missing or indeterminate capacity is disclosed as skipped.
Host pressure, profiler availability, and this wait never become a functional
or mutation verdict.

This policy is deliberately an observation and bounded courtesy wait, not a
capacity reservation. Two callers can read the same headroom before either
starts, so the check cannot promise that aggregate demand stays below the
slice ceiling. It improves disclosure and avoids some avoidable overlap; it
does not close the race or provide cross-worktree admission control. A dry run
performs only the read-only version/host probes needed to report `would-wait`;
it does not sleep, reserve capacity, start a profiler session, or start the
lane.

The atomic reservation protocol is a separate open RG-56 design. It requires
the daemon to validate and reserve demand against its complete live registry
before any lane process or container exists, then bind that reservation to
the eventual target. R-45 must not be described as delivering RG-56: its
non-reserving behavior and its non-effect on the verdict are intentional.

## Explicit placement is a pre-body launch gate

A placement request cannot safely be sent before the lane process exists:
cgprofile identifies the target from the per-invocation token in that
process's environment. Sending `ctl start --place` first can therefore create
a session with `place-refused:no-target-pids`, after which an ordinary
warn-and-run client would execute the workload outside the requested leaf.
That is not acceptable for an explicit placement request: the workload would
be an unaccounted shared-scope lane, and the record could imply controls that
were never applied.

Run-Gate instead starts a non-body wrapper carrying the unique token. The
wrapper emits a private ready marker and blocks. The client then asks the
daemon to start the session and placement, validates the returned leaf and
read-back limits against the request, and only then sends the release signal.
For exec lanes, `docker exec -i` carries that one-shot signal; after release,
a small input-forwarding thread preserves the caller's original stdin for the
lane. For bare-host lanes, a separate inherited control pipe leaves the
lane's stdin untouched, and the Python wrapper `exec`s the command in place.
The barrier's process identity is therefore the same one the daemon discovers,
and no candidate instruction can run before placement read-back succeeds.

An explicit placement error, absent leaf, malformed limit, or read-back that
is weaker than requested closes the launch gate: Run-Gate aborts/reaps the
wrapper, stops the profiling session, and returns an infrastructure refusal
before lane-body entry. It produces no assay candidate verdict. If the daemon
or transport is unavailable before returning a placement result, R-36h still
allows its disclosed measurement fallback; the wrapper is released without
the profile token. This distinguishes an unavailable optional measurement
from a daemon that affirmatively refused a requested control. The bounded
wrapper-ready timeout is likewise a pre-body infrastructure refusal, never a
candidate timeout or verdict.

R-45's headroom wait remains observational and does not reserve capacity. If
the separate Run-Gate lane-admission ticket is enabled, the existing runner
finally releases that ticket on refusal; no new atomic placement reservation
is implied (RG-56 remains separate). This ordering honors explicit placement
without turning host contention, profiler availability, or measurement data
into a functional or mutation result.

## Private-namespace R2 uses a read-only cgroup observer

Assay B145 must see every enforcing ancestor of a mutation worker. A private
cgroup namespace intentionally hides those ancestors, so native R2 refuses
before candidates instead of treating a parent pids.max or memory limit as
a test failure. Giving the test container --cgroupns=host would solve
visibility by widening the workload's kernel view; granting host PID access
would also expose host process information. Neither is needed for a
read-only counter query.

The existing cgprofile daemon already has the host proc and cgroup view
required for its measurement and placement duties. Run-Gate's explicit
resource_observer = "cgprofile" lane setting mounts a separate
observer-only Unix socket, not ctl.sock, into the opted-in tester container.
It is supported on native Assay lanes and command lanes whose resolved
environment has `mode = "ephemeral"`. Its intended command use is a wrapper that
launches an Assay campaign (such as Assay's B105 self-qualification driver).
Run-Gate cannot establish from arbitrary command argv whether a script will
launch Assay, so the opt-in is defined by the declared lane rather than a
fragile command-string heuristic. This keeps the observer available across
the wrapper boundary without asking the wrapper to recreate Docker mount or
credential logic; the inherited endpoint variable must reach its Assay
process. Before launching the lane, Run-Gate queries the daemon's control
`version` response and requires an observer advertisement with contract 2,
the fixed socket path, and `listening = true`. This catches stale daemons and
failed socket binds before test code starts. The advertisement is a startup
capability check; it does not prove the listener will remain healthy for the
whole lane.
Observer-backed container lanes also require explicit local memory and PID
ceilings. These put memory and process-count rejection events on the candidate
container's own cgroup, where Assay can distinguish them from an event caused
by a sibling. The observer uses a dedicated group and a stable directory bind,
so a daemon restart does not pin an already-running lane to an unlinked socket
inode. Observer lanes refuse extra mounts because they could expose the Docker
API to test code that receives the observer group.
The first cut restricted the setting to native Assay lanes, but that excluded
B105's qualification wrapper: it builds and installs the exact source wheel,
then runs Assay in a private venv, so replacing it with the ordinary lane
driver would discard its deadline and receipt guarantees. The initial command
lane extension allowed only `tester-unified`; revision 66 generalized it to
any command lane whose resolved environment is ephemeral. The environment name
does not grant the capability: the lane must still opt in explicitly. This
broadens which runner may receive the same read-only endpoint, not what the
endpoint can do: the daemon still binds each query to that peer's native thread
and returns only that peer's gates-slice ancestry, never a caller-selected
path, sibling cgroup, or control operation. The explicit lane declaration is
the grant; the command process is trusted with the same narrow query
capability whether or not it passes the endpoint onward to Assay.
The observer accepts only a resource-counter request; filesystem read-only
alone would not constrain the existing control socket's verbs. Docker also
receives the socket's observed GID as a supplementary group, and the socket
path, type, owner, group, and mode are checked before launch. Missing service
or unsafe socket state is an infrastructure refusal, never a reason to
silently run without B145. To reject a socket that uses Docker's powerful
group, Run-Gate reads the numeric GID from the root-owned `/run/cgprofile`
control directory provisioned as `root:docker` by host setup. It does not use
`getgrnam("docker")` from the devcontainer's potentially different group
table, and it refuses when that host path cannot be verified.

The client sends only its native thread ID. The daemon binds that request to
SO_PEERCRED, checks the thread's host/namespace PID mapping, verifies that
all peer threads share one cgroup and credentials, and derives the cgroup
path itself. Because the daemon has a private cgroup namespace too, host
procfs paths may begin with parent components such as /../../dev.slice/....
The observer accepts them only when their parent-component count exactly
matches the full-root cgroup2 mount's mountinfo root, then derives the
canonical hierarchy path and verifies it lies below the configured gates
slice. It returns that cgroup's ancestry counters and signatures only—no PIDs,
process listing, caller-selected path, or sibling data.

Assay prefers its local hierarchy and consults the observer only when the
local failure specifically proves ancestors are hidden. It validates the
remote identity and complete hierarchy, then compares counters per cgroup
across each candidate window. A shared-ancestor event without a matching leaf
delta aborts the lane without assigning a candidate bucket. Thus the query
adds no placement authority and cannot turn unrelated sibling pressure into a
mutation kill or survivor. The tradeoff is deliberate fail-closed behavior:
ambiguous shared-limit activity makes the lane ERROR and requires a clean
retry; it is not guessed away or assigned to whichever candidate happened to
run.

Two mutant workers inside one tester container still share its *leaf* event
counters, despite separate scratch trees. An event from worker B can fall
inside worker A's before/after window and look like A's own limit hit.
The shipped R2 declaration therefore uses `jobs = 1`; `--resume` continues a
budget-limited campaign on the same tree. Parallel workers require separate
cgroups and counter bindings before they can be classified independently.

### Assay unavailable observation is an infrastructure error

Assay's `NO_MEASUREMENT` has several causes. Most describe a judged claim that
cannot be made and keep run-gate's established FAIL mapping. The specific
`CGROUP_OBSERVATION_UNAVAILABLE` reason means native R2 could not start because
its required counters were unavailable before the first candidate. The gate
maps that reason to `ERROR` only after validating the claim and evidence
records and confirming every other result passed. This preserves the outcome
precedence that the top-level `NO_MEASUREMENT` rollup can otherwise hide: an
independent budget stop remains `BUDGET_EXCEEDED`, and an independent failure
or inconclusive claim remains `FAIL`. A malformed claim or evidence record is
`ERROR`. The gate preserves the raw Assay outcome and reason. It does not say
that the tested code failed. If observation is lost after a candidate starts,
Assay itself returns `ERROR/EXEC_FAILED`; the candidate is withheld because
its event window cannot be attributed. See the
[consumer example](../CONSUMERS.md#native-r2-resource-observation-refusals).
The v16 reason-code mapping requires Assay 10.0.0 or newer because older
versions do not provide the independent claim and evidence rollup it inspects.

### Assay result requires agreement between process and artifact

An Assay verdict artifact is not a substitute for the process status that
produced it. Run-Gate compares the raw lane exit with the artifact's top-level
`exit_code`, then checks that this code matches the outcome's closed table
(PASS 0, FAIL 1, ERROR 2, NO_MEASUREMENT 3, BUDGET_EXCEEDED 4,
INCONCLUSIVE 5). Missing, malformed, or disagreeing codes produce infrastructure
ERROR with the raw runner status and Assay outcome preserved. The gate does not
repair a mismatch by trusting whichever source says PASS: the artifact can be
stale or forged, while the process can fail after writing its verdict. This
comparison keeps both facts visible and fails closed only on an actual
inconsistency.

## Env drift does not certify unreadable source

`--check-env` is a heuristic scan, so its result is only as complete as the
Python files it can read. A syntax error can use the documented line-regex
fallback; an I/O or decoding error cannot be scanned at all. Run-Gate names
that file and marks the overall scan incomplete instead of folding missing
input into a clean zero-reference result. The drift scan remains advisory:
it does not decide whether a lane is allowed to run, while a toolchain failure
still keeps its existing exit status.

## Preflight probes preserve lane context

An Assay inventory or toolchain probe must reach the same ephemeral
environment as its judged lane. It uses the lane's configured container user
and the same operator extra mounts. Without those, an imported Assay command
or tool can be unavailable to preflight even though it is present in the
actual lane, or a probe can pass under permissions the lane does not have.

## Source-backed Assay identifies code as source

Internal vbpub lanes install Assay from the selected worktree with an editable
install. That source tree is not a built wheel or zipapp, so an artifact digest
would claim evidence that does not exist. Both the import check and the actual
`assay.cli` command use Python isolated mode (`-I`), which removes `PYTHONPATH`
and the working directory from ordinary module lookup. Run-gate separately
checks the consumer directory with `PathFinder.find_spec` without executing
its contents. A regular local module or package is rejected; a namespace-only
directory is allowed because Python continues searching for the installed
regular package. It then checks that the installed package spec resolves to
the selected worktree's
`assay/src/assay/__init__.py`. It also requires the verdict to have a non-empty
`assay_version` plus a full Git commit matching the commit captured in the run
record. Both checks matter: the path binds Python to the chosen source tree,
while the commit binds the verdict to the tree run-gate sampled. A source-mode
verdict without that identity or with a different commit is ERROR. The same
Python interpreter both performs the import check and executes Assay, so a
different `assay` script on `PATH` cannot replace the checked package.

The source identity comparison accepts a full SHA-1 or SHA-256 Git object ID.
That check does not change Assay's P22 snapshot source, which currently
requires SHA-1 object storage for high-rigor snapshot lanes. Run-Gate's
evidence grammar stays separate from the judge's repository-format support.

External consumers use immutable artifacts and retain the full
`judge_provenance` check. The two modes use evidence appropriate to their
input: source location and selected commit for an editable source tree, or an
artifact digest for a built distribution. Run-gate does not convert one form
into the other.

Container re-attachment also needs the launch-time identity mode. The
inflight record stores `source` or `artifact` beside the verdict path, and
schema 2 makes that field part of the recovery grammar. Current config can
change while the old container is running; reading its verdict under the new
mode could either certify the wrong judge or report a false failure. The
collector therefore uses the recorded mode, and validates the mode and both
exact lane-derived artifact paths before inventory or admission touches
Docker for a container-runner record. A schema-2 record missing its mode, or
a non-Assay record carrying Assay artifact paths, is refused rather than
being treated as a command result. A lost container does not transfer its old
mode to a fresh replacement. If the current lane has changed away from Assay,
the recorded Assay mode still causes a refusal instead of passing the
container exit code as a command result. A promoted follower preserves the
mode until `_dispatch` parses the verdict; `finish()` then records that parsed
result, and history never stores the private mode. `--fresh` deliberately
discards an old run and uses the current mode only after the operator has
confirmed the old container has stopped, and is available only when the
current lane uses an ephemeral-container environment. Host and exec lanes
have no container for run-gate to replace, so recovery advice preserves the
record until that runner's lifecycle owner confirms the named run has stopped;
only then may the stale record be removed. When an older-schema record
identifies a non-container runner, `--fresh` also refuses rather than
removing that shared runner.

When a lane changes from an ephemeral container to host or exec mode, the
current runner no longer passes through container re-attachment. Starting it
without checking the old container could overwrite the shared verdict path, so
preflight refuses that container record before inventory, admission or lane
execution. The prior container's lifecycle owner confirms it has stopped
before the operator removes the record.

Recovery ownership must be checked before Assay inventory and sequence
admission because both can cause Docker side effects before a lane starts.
The preflight walks every sequence node and member, and refuses a record at a
sequence's own name because that lane has no runner to re-attach to. It also
refuses foreign-runner records before inventory, even with `--fresh`; that
flag authorizes replacement only for the current lane's ephemeral container.

The same closed-contract principle applies to process status. A raw command
status is preserved in `LaneResult`; run-gate returns only PASS 0, FAIL 1,
ERROR 2, NOT_RUN 3, or BUDGET_EXCEEDED 4. This prevents a command's code 2 or
3 from being mistaken for a run-gate refusal. ERROR means the gate could not
produce a judge result; NOT_RUN names a precondition that prevented the judge
from starting; a hard lane budget has its own status. `--json` and the human
summary carry the verdict and raw code so callers need not infer them from
stderr text.

See [the normative mapping](../SPEC.md#2-cli-contract) and [migration
instructions](../CONSUMERS.md#closed-results-and-runner-mode-migration).

## Sequences own the whole composite

A shell `&&` chain returns the wrapper command's status. That flattens a
nested gate's result and makes wrappers reimplement stop policy, worktree
forwarding, base selection, and history. A native `kind = "sequence"` keeps
the members and order in run-gate's lane model. It runs members serially,
records each result, and carries one admission ticket across the composite.

The sequence resolves its request base once. `[project].trunk` only supplies
a base when HEAD is a merge commit on that declared local branch; the first
parent is the base. A non-merge trunk HEAD has no post-merge change set, so it
is NOT_RUN with `no-base`. The resolved commit is passed only to member lanes
that request it. This avoids comparing different members against different
moving refs during one composite.

## Selective requests stay with the selected lane

Assay's `--reuse-from`, `--rejudge`, and `--rejudge-outcome` describe one
specific R2 rerun. run-gate accepts them only on a single assay lane and
forwards them only to that judge invocation; a sequence cannot accidentally
apply one candidate selection to every member. Command argv is closed by
default. A command must declare `accepts_args = true` before arguments after
`--` are appended, and composites refuse those arguments because their member
argv has separate ownership. The [consumer examples](../CONSUMERS.md#selective-lane-requests)
show both entry points.

## Docker names order daemon-wide admission

An OS lock file coordinates only processes that share its filesystem. A host
shell, a devcontainer, and CI can all reach one Docker daemon while seeing
different filesystems, so run-gate allocates a ticket by attempting
`docker create --name ciu-res-gates-N`. Docker's name uniqueness is the shared
compare-and-swap. A conflict causes a fresh listing and retry; the ticket
number is the highest visible ticket or tombstone plus one.

Releasing the highest ticket starts its `/bin/true` container and waits for
it to exit, leaving a tombstone so a later gate cannot reuse the number. A
lower ticket can be removed once a higher number is visible. The published
count is a separate `ciu-admission-N` object, replaced by generation with
same-daemon `docker create --name` allocation. This mode is opt-in and count
only; resource governance stays independent.

Docker may ask systemd to auto-create a misspelled slice, which then reports
`LoadState=loaded` despite having no installed unit file. Where run-gate can
reach host systemd, it also requires a non-empty `FragmentPath`; containerized
gate clients rely on their outer launcher to verify the host unit before
launching.

An owner tuple includes boot id, pid, start ticks, and PID namespace inode.
A reader may prove an owner dead only when host, boot id, and PID namespace
match its own view. A different namespace is unknown and remains live until
the wait or run deadline; this avoids reclaiming a live ticket by mistaking
an invisible pid for a dead process. The cross-tool label contract is pinned
in the shared `tests/fixtures/admission-label-grammar.json` fixture so the
later CIU port can compare the same bytes.

Each lane's elapsed budget starts after the runner locks are held and count
admission succeeds. Waiting for another gate to release its ticket therefore
does not consume the lane's own execution budget.

## Status uses the authoritative host signals

The selected project's inflight file answers which lane and checkout wrote a
run record. It is useful context, but it is per worktree and can outlive its
owner. The exec lock answers a different question: whether any run-gate
process currently holds or waits for the exact persistent runner. The lock
file's contents are intentionally empty, so status matches its device and
inode against `/proc/locks`; a caller-side wrapper lock cannot stand in for
it because invocations that bypass the wrapper still take run-gate's lock.

Admission tickets answer the host-wide queue question for ephemeral as well
as exec lanes. They are read from the shared Docker daemon and decoded with
the same label grammar the allocator and future CIU port use. Status never
reaps, repairs, or starts anything. It shows project scope for inflight
records and host scope for locks and Docker tickets, and marks a source
unknown when it cannot read it. A partial answer exits with the closed ERROR
code so an unavailable `/proc` or Docker response cannot look like an empty
queue. `doctor` checks admission setup only when the project switch is on;
the image check is `docker image inspect`, with no ticket container probe.
When a visible publication has unreadable identity labels, doctor names it
and does not recommend `admission set`: that command refuses to replace an
object whose ownership cannot be verified.

## Progress is scoped to the current Assay run

The daemon's watch and Run-Gate's local silence timer cannot both be verdict
authorities at once. The daemon can pause its liveness clock under measured
pressure; a local timer running alongside it could call the same healthy lane
stalled. Run-Gate uses the local timer only after the watch transport is
unavailable, and reconciles a terminal daemon kill from the stopped session
summary when the lane exits before its last stream event is read. A shared
exec or bare-host cgroup without an explicit placement request cannot be a
kill target, so its daemon policy reports and leaves the declared local
watch in charge.

Assay keeps progress in an append-only JSONL file so a later `--resume` run
does not erase the earlier diagnostic history. Each invocation begins with a
`run` event; that record is the attribution boundary for every later event in
that invocation. Run-Gate therefore selects candidate events only after the
latest valid `run` header. If an already-running watcher sees another header,
it discards its previous candidate, ETA, and silence baseline before observing
the new invocation. The progress file is never truncated as a monitoring fix:
truncation would discard Assay's diagnostic history without addressing the
consumer's attribution mistake. Streams from older Assay versions without a
`run` event retain the previous whole-file behavior.

This boundary matters especially when a campaign is resumed after a budget
limit. The old run may have judged hundreds of candidates; those records are
still useful history, but they are not evidence that the new process has
started making progress. Reporting them as current can produce a false ETA or
let a stale event seed a silence timeout. The reader treats progress as
run-local telemetry; it does not change the Assay verdict. Assay also keeps
`candidate_index` campaign-wide while restarting `elapsed_s` at each `run`
header. Run-Gate counts candidate events in the active segment for rate and
uses `pending_total` for the remaining-work ETA when that field is present;
for a stream with a `run` header, an absent, invalid, or inconsistent
`pending_total` suppresses the ETA instead of falling back to a campaign
index. Only legacy streams with no `run` header retain that fallback. Dividing
a resumed campaign index by the new invocation's elapsed time would report an
artificially fast rate.

See [SPEC.md §5](../SPEC.md#5-execution-contract) for the normative reader
behavior.

## Assay resume state needs an environment-owned mount

Assay keeps verdict and progress beside the judged project for this run, but
mutation resume state must outlive a CIU-managed worktree. run-gate therefore
passes `--state-dir` under the checkout that owns the shared Git directory,
keyed by the project's path there. This keeps retries resumable after the
worktree is removed.

A worktree-owned runner may mount the worktree and `.git` separately. Docker
can create the missing repository parent as `root:root 0755`; the non-root
lane user then cannot create `.run-gate` under it. Creating that directory
inside the runner would make the immediate run pass but keep state in the
container's writable layer, which is lost when the runner is recreated.

Run-Gate creates its default checkout-owned `.run-gate` directory before a
live ephemeral container or bare-host Assay lane probes it. A symlink or
another object at that path is refused. An explicitly configured `state_root`
is an environment-owned mount and must already exist; Run-Gate does not
invent it. The root is then checked in the lane's own environment and must
be writable by the lane user. When keyed state directories already exist, the
deepest existing directory on that path must also be writable. `doctor`
reports a creatable but absent default `.run-gate` root as `[SKIP]` in
`ephemeral` and `host` environments; a known invalid parent fails. It neither
creates the root nor pretends it can certify the future lane user's access.
The first live lane in those environments creates the root and checks access.
A configured `state_root` or an `exec` environment is checked
through its declared runner, never by host-statting a container path.
When the default `<checkout>/.run-gate` path is not mounted there, the result
is NOT_RUN/`state-mount` with the required read-write mount named. A container
environment can set `state_root` when its durable mount uses another path;
`doctor` checks that path once per assay environment. The Assay command's
`TMPDIR` and `GIT_CEILING_DIRECTORIES` are derived from this same selected
state path and passed through to pytest; a fixed `/worktree` path would be
wrong in a differently mounted checkout. Run-Gate creates keyed per-project
descendants only beneath the checked root. See the
[consumer contract](../CONSUMERS.md#resume-progress-and-durable-assay-state)
for the config and mount requirement.

In-repository project keys keep their relative path so existing resume data
remains addressable. Projects outside the checkout use a separate namespace
and the SHA-256 digest of their resolved path. Replacing `/` with `-` is not
injective: `/a-b/c` and `/a/b-c` would share one state directory.

## Keep failed assay evidence outside short-lived worktrees

Assay writes its current verdict and progress under the judged project. A
retry intentionally overwrites those paths, and a CIU-managed worktree may
then be deleted. On a failed assay result, run-gate copies the current files
to the checkout's `.run-gate/failed/<lane>/<run_id>/` archive before the next
run can replace them. The archive is git-ignored, owner-only, and bounded to
ten entries per lane. A short digest is printed at the point of failure so
the operator can see the first failing node ids without opening the full
transcript.
