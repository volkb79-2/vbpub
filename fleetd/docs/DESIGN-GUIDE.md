# fleetd design guide

## Why MVP-B-lite uses one-shot control

MVP-A supplied facts, contracts, and a deterministic planner. MVP-B-lite adds
the smallest campaign loop on top of the existing transport and runner: a
`Transport` boundary, named remote jobs, local JSON state, one-step scheduling,
and combined reporting. The controller calls the identity-checked `hx.sh`
wrapper; the configured host aliases stay in local operator configuration.
Fleetd contains no SSH client, host address, key, provider token, daemon,
socket, or lease protocol.

This package deliberately keeps host placement in the planner. It starts at
most the planned number of campaign slots per host (currently one), then
returns. A controller can call `status` to inspect progress and ETA and `tick`
to schedule one next step. The persistent runner checkout and scripts must
already exist on each selected host.

## RAM is the placement constraint

Lane caps bound runaway containers; they are not expected worker charges.
Expected charges may sum against the configured 1.5 overcommit budget, but a
single candidate is compared to physical lane RAM after the control-plane
reserve. A candidate that cannot fit physically is unplaceable. Low and high
RAM work is mixed to keep the average expected charge near each host's budget.

## Geekbench is only a time estimate

The planner normalizes serial candidate time with the Geekbench single-core
score and the declared reference score. It multiplies by one stored factor per
host, defaulting to `1.0`. It never divides by multi-core score and never fits a
factor per lane or hardware flavor. Learning the host EWMA and skipping
self-predicted samples are tracked under
[FD-005](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-005-learn-the-per-host-correction-factor).
ETA is informational and cannot reject or time out work.

Time samples are comparable only when the hosts share a Geekbench major,
exact version, and baseline score. Refused hosts do not set the ETA reference,
so an ineligible machine cannot make ETA unavailable for eligible hosts. A
missing or mismatched benchmark among eligible hosts keeps RAM placement
available but makes ETA unavailable with a reason. Placement then balances
normalized reference work per worker as a deterministic tie-break. This is not
a speed comparison or ETA. A candidate time record from a host absent in the
current host set falls back to the next timing source and carries a reason.

The planner resolves one reserve term per listed control-plane service and
sums them, instead of treating the whole list as all-or-nothing. The three
sources are deliberately different because three different situations are:

- **reported** — the service's own positive `MemoryMin`, within its documented
  per-service ceiling. This is what the host actually promises.
- **default** — that service's documented floor, used when its own fact is
  missing or implausible. A missing MANDATORY fact must not become zero: zero
  silently lowers the reserve below what the control plane needs, and the
  failure would only appear under load. The documented floor is the measured
  value the installer writes, so it is the best available statement of what
  that service needs.
- **absent-optional** — `0`, used ONLY for a service the installer declares
  optional whose systemd `LoadState` is `not-found` or `masked`. A host without
  an optional unit legitimately has no such service, so it promises nothing.
  An unknown or uncollected load state is not absent: it is a missing fact and
  falls back to the service's default.

The old all-six-positive rule was wrong in exactly one way that mattered:
v1001 has no `mdt-psi-guard.service`, so every plan on that host used the 768
MiB constant forever and the measured table could never take effect. A
per-service fallback removes that dead end while keeping the "never invent a
reserve below what was reported" property: the total always includes every
reported value, and only a *missing* fact is replaced.

`reserve_source` names the aggregate mode (`service-memory-min` when all terms
were reported, `per-service-default` otherwise), while `reserve_services`
carries the per-unit provenance (`reported` / `default` / `absent-optional`)
so the plan evidence shows exactly which term produced each service's
contribution. The fixed profile constant survives only for the
no-per-service-fact case (a facts root that queried no service, or a table that
never arrived), and the collector now emits `service_memory_min_reason` there
rather than six null rows, so "collection failed" is a stated fact rather than
an inference from an empty-looking table.

Two sanity bounds are DERIVED from the same table rather than hand-tuned
beside it: one service above 4 x its own documented floor, or a total above
twice the documented floors, is refused with a reason (the host is not planned
this wave) rather than clamped or summed. A clamped reserve would hide a bad
drop-in; a refused one puts it in the plan evidence. The collector does not
read parent `ctl.slice/memory.min`. Older facts may include
`control_plane.memory_min_bytes`, which is accepted for compatibility and ignored. Then `P = memory.for_lanes_bytes - reserve_h`, O = other
running expected charges + campaign_slots × instance_memory, and B = P ×
overcommit - O. It derives worker slots from CPU capacity after other running
workers and bounds them by
`efficient_upper_bound`. This manual plan uses one campaign slot per host;
parallel campaign slots require the future supervisor because same-lane
selected Run-Gate calls share an in-flight record and Assay store; this
accepted capacity limit is tracked under
[FD-006](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-006-lift-the-manual-one-slot-per-host-and-j-cap).
The fallback is a per-host profile constant from
`control_plane.profile_reserve_bytes`, or 768 MiB when not configured. Host
plans record `reserve_bytes`, `reserve_source` (`service-memory-min`,
`per-service-default` or `profile-constant`), and `reserve_missing_services`. Reserve selection does
not depend on RAM size, available RAM, or measured RSS. Physical lane RAM is
still required for placement. Calibration of the profile constant from
measured p95 under load remains open as
[FD-007b](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-007b-calibrate-profile-reserve-from-measured-p95-under-load).
For a host with W
slots, b = B /
W; a candidate's shared cap is the smaller of B - (W - 1) × ram_low and
P - O - (W - 1) × ram_low. A candidate that runs alone or as a retry must
also fit the host's sum budget B and physical alone cap P - O. Thus
overcommit never raises the physical cap, and every running wave's expected
charge stays within B.

Placement refuses a host with unknown role, explicit shared-prod role, PSI
above the configured admission threshold, or missing facts needed for a safe
RAM/CPU budget. A missing max_lane_containers is not guessed.

## Why the default R2 scope is changed lines

A mutation verdict is only about the lines a release changed; judging the whole
target every release re-pays the baseline for code nobody touched. So the
default scope is `git diff -U0 <previous tag>..<commit>`, and a file the release
did not touch contributes nothing at all — not even its unchanged lines inside
a touched file (`lines_unchanged` says how many were dropped).

Three shapes of "empty" are kept apart, because folding them together is the
defect this producer exists to end. A scope with no candidates is
`nothing-to-judge` with a reason and exit 4, and `--out` refuses to write the
empty file, so a campaign can never judge nothing and look green. A scope file
that names a mode is echoed in `mode_source`, so a report cannot claim the
default while a project opted into something else. And a tool that commits no
scope file keeps the default, so no existing lane changes behaviour.

**Renames are judged whole.** Assay's candidate identity folds in the
repo-relative path, so every candidate in a renamed file is an identity that has
never been judged; a pure rename produces no hunk at all, so added-line
filtering alone would silently drop the file. The diff is read with
`--find-renames` and a `rename to` header marks the new path whole.

The diff is parsed with a real hunk state machine rather than a header regex:
a hunk header's own `-old_count/+new_count` is what closes a hunk, so a body
line whose content starts with `++` (patch text `+++ value`) can never be
mistaken for a file header. A test pins exactly that.

**Why the rolling baseline is an opt-in, not the default.** Successive releases
covering the whole target is what a project wants when its candidate list is
larger than one campaign should judge; it is also what makes a cursor
necessary. A cursor that resumes at a raw offset can silently skip candidates
when the list changes underneath it, so this producer does not resume at an
offset at all.

**Why the sweep is keyed by file content, not by a candidate id (FD-033).**
Assay's candidate id folds in the source file's SHA-256
(`assay/candidate_identity.py`), so almost every release that edits a target
file rotates the ids of that file's candidates — and a cursor keyed by the id
list (the original `fleet-r2-scope-cursor/1`) therefore went stale on nearly
every release and restarted at slice 0 after `--rebaseline`. Rolling mode was a
sample of the first slice, not a sweep. The state is keyed by facts an edit
cannot rotate:

- the **file's content** (its git blob id at the judged commit) decides whether
  a mark still applies, and
- a **location-stable candidate key** `(path, operator, ordinal within that
  path's operator group)` identifies a site inside that content. It is derived
  in fleetd from what `assay plan --json` already emits (`path`, `operator`,
  `lineno`, `id`); no assay change is needed, and `assay/src` is untouched.

This gives the behaviour the mode's name promises: an edit makes exactly that
file due again (judged whole, because a site identity — byte span, replacement,
description — shifts when anything before it moves), every other file keeps its
marks, and the sweep continues. An edit re-judges a changed file WHOLE rather
than only its changed lines: that is a superset of the changed-lines scope and
it is the price of a key that cannot drift out from under a mark. The remaining
limit is FD-037.

**Why a share of the rolling slice is reserved (FD-038).** Changed files sort
first, and they share `slice_size` with the sweep. A target whose files are
edited every release by more than `slice_size` candidates would fill every slice
with changed candidates and never reach its never-judged remainder, which looks
like coverage and is not. Exempting the changed candidates from the budget was
rejected (an unbounded release), so a minimum share of the slice is reserved for
the non-changed remainder instead: `reserve_percent`, default 25, rounded up,
capped at `slice_size - 1` so changed files are never starved completely, and
returned to the changed files when the remainder cannot fill it. The price is
visible, not hidden: the changed candidates that did not fit are listed per file
in `deferred_changed_files`, and a deferred file keeps its mark and recency.

Three things are accounted instead of dropped, because each is a way a cursor
could silently claim coverage:

- a mark whose **planned count** for the path changed, or whose **judged key**
  the plan no longer contains (an operator-selection change reuses the same path
  and blob), makes the path due again and is reported as `changed` — it is never
  carried into a new content;
- a mark for a path with **no planned candidate any more** is `retired`, named
  with its count and the pass that last judged it;
- a **finished sweep** is a recorded new pass (`new_pass: true`), not an empty
  `nothing-to-judge`, because "all candidates were judged at this content" is a
  pass boundary, not an absence of work. In rolling mode `nothing-to-judge`
  means the plan itself is empty.

The cursor is versioned (`fleet-r2-scope-cursor/2`), digest-protected over the
whole document (`state_digest`), and resumed only for its own lane; a cursor
for another lane is a refusal (exit 3), never a resume. `--rebaseline` is kept
as the explicit migration from schema 1 and as the deliberate "start over";
since the sweep resumes across edits by itself, it should now be rare.

The alternative rejected: keying the sweep by `(path, lineno)` alone. Line
numbers shift whenever anything above a site changes, so a shift would silently
re-point a mark at another site — exactly the silent skip this producer exists
to prevent — and a rename would lose the whole file's history. Content plus a
site ordinal fails closed instead: a content change is visible (the blob id),
and a plan change is visible (the count and the key set).

## Why sizing is a derivation, not a declaration

A lane that declares `cpus = "3"` and `budget = "12h"` has stated the right
numbers for exactly one shard size on one worker count. With several instances
per host, every one of those numbers must come from the plan:

- **the per-worker unit** (one pytest worker's CPU share and memory) is the
  charged unit, and the fixed **per-instance** charge covers the judge, the
  container and its workspace, so the first worker of an instance costs
  instance + one unit — a per-container cap that already includes three workers
  cannot express that;
- **shards = instances**, because each instance is one `--shard K/N` run;
- **each shard's budget** from its candidate count divided by its workers and
  the per-candidate seconds, plus the lane baseline — an instance with no
  candidate still pays the baseline, because sizing must never pretend an idle
  instance is free;
- **the outer bound** as budget plus a launch/collection allowance, so a lane's
  declared budget and Run-Gate's bound are one derivation instead of the two
  numbers that produced the 11h-below-12h warning.

The new demand keys are optional in the contract on purpose: every existing
demand file must keep validating, and a project that does not size its campaign
does not have to declare a worker unit. Sizing refuses a plan without them **by
name** — a silent fallback here would size the campaign from a number nobody
chose.

Every figure carries its `basis`. A `declared` sizing document is honest about
being a guess; `measured` means a finished campaign's own per-candidate records
supplied it. This is the one place where the tool could easily lie, so the
label is part of the document rather than a comment.

## Why a missing measurement is null, not zero

The measurement recorder exists because budgets were estimates. A candidate
nobody measured contributes `null`, and the summary counts how many numbers it
actually had (`measured_candidates`, `memory_measured_candidates`). A zero would
look like a measurement and drag every median down, sizing the next campaign's
budgets too small — the exact class of silent wrong answer this estate treats as
a defect. Memory comes from the cgprofile observer's `resource-profile/1`
`memory_peak_bytes` and nothing else: Assay 12 removed its own sampled
`peak_rss_bytes`, so a lane without the observer records elapsed seconds only
and says so.

**Why the budget uses the mean and a measured spread (FD-031).** A shard's wall
time is the sum of its candidates, so the estimator that matches the budget is
the per-candidate MEAN, and a right-skewed lane has a mean above its median. The
mean is taken over every candidate that occupied a worker, including
`budget_exceeded` and `hung` ones at their bound, because they held a worker for
that long. The margin over that sum is derived from the measured standard
deviation as the one-sided 95th percentile of a sum of `n` candidates
(`1 + 1.645 x cv / sqrt(n)`, never below 1.10), so a heavy tail widens the
smaller shards most; `--margin` is the explicit override. It is deliberately an
ESTIMATE, not machinery to guarantee a bound, and the margin has no upper cap on
purpose (a cap would be one more number to tune): a single outlier in 100
measured candidates raises the standard deviation, and a one-candidate shard is
then budgeted at `1 + 1.645 x cv` times its estimate, which can be very large;
`--margin` sets one fixed margin instead. Beyond that, heavy candidates that cluster in
one shard can still reach the budget, and the remainder continues in a later
instance. The new fields are additive and optional in `fleet-r2-measurement/1`,
and a document without them is sized from its median as before.

Medians (kept for the existing fields) are taken over `killed` and `survived`
candidates only. For THOSE fields a `budget_exceeded` or `hung` candidate's
elapsed time is the budget that stopped it, not the cost of judging, and
including it would inflate the next median forever; the per-candidate sum fields
above count those candidates deliberately, at their bound, because they held a
worker that long.

## Why the merge check compares identity, not counts

Two shards that judged different commits or different tester images produce a
merged verdict that *looks* complete either way. The check therefore compares
the lane, full commit, image digest, plan digest and shard count across every
shard, and reports both values on a mismatch instead of picking one. Three
shapes are kept distinct:

- a **duplicated index** is a disagreement about which slice a store holds — a
  refusal, never an "incomplete" report;
- a **missing index** is a genuinely incomplete set: reported as `incomplete`
  and refused (`--require-complete` is accepted and changes nothing), because a
  merge over a missing shard reports its candidates as absent work;
- a **differing field** is `mismatched` regardless of completeness.

The image digest is optional in the plan executor and required by the merge
check. That asymmetry is deliberate: refusing to run a campaign because nobody
declared an image would be a default invented by the tool, while merging two
unknown image identities is precisely the mixed-image merge to refuse.

## Facts are read from their owner

The collector accepts a root directory so tests use synthetic `/proc` and
`/sys` trees. Production collection reads the host-owned Geekbench record; it
does not run the benchmark. Missing facts stay `null` with reasons. Planning
resolves each service's reserve term separately (reported, documented default,
or zero for an absent optional unit) and keeps the host profile's constant only
for the case where no per-service fact exists at all; other required capacity
facts still fail closed.

Collection requires versioned `/etc/fleetd/facts.toml` policy and source-path
configuration. `HOST_ROLE` is read from the configured host-setup environment
file, not duplicated in fleetd policy; unknown or invalid roles remain null
with a reason and cannot receive work. The CPU lane count comes from the
online CPU list, and `memory.for_lanes_bytes` is raw `MemTotal`. Fleetd probes
the fixed `CONTROL_PLANE_SERVICE_UNITS` list with `systemctl show -p MemoryMin`.
Each service is one of three terms: its reported value, its documented
default when the fact is missing or implausible, or zero when a
declared-optional unit is absent. Legacy parent-slice `memory_min_bytes` is
accepted and ignored. Host plans and campaign status show `reserve_bytes`,
`reserve_source`, `reserve_missing_services`, and the per-unit
`reserve_services` provenance. Reserve selection never scales with `MemTotal`
or uses measured RSS. Old facts without per-service values use the profile
constant, so host setup can roll out service drop-ins without a flag day. Calibration of
the profile constant from measured p95 under load remains open as
[FD-007b](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-007b-calibrate-profile-reserve-from-measured-p95-under-load).

The demand schema validates `candidate`, `worker`, and `run` as its closed
vocabulary. This R2 candidate planner only accepts `unit = "candidate"`;
other units fail with a clear input error until their placement meaning is
specified.

## Determinism and manual command delivery

Candidate, host, batch, and command ordering use explicit stable keys. JSON
contains no generated timestamp. `--commands` prints self-contained sections;
each writes one batch ID file in the checkout and invokes Run-Gate once with
`--only-candidates` and the planned `--jobs` value. Batches run serially in
the section. Candidate selection normally returns exit 4 because other campaign
candidates were not selected (the claim is `PARTIAL`; the code is Run-Gate's
`BUDGET_EXCEEDED`, so a genuinely exhausted batch budget looks the same).
Generated scripts accept 0 or 4 and stop on any other result; the whole-lane
closeout finishes any selected candidate that did not complete. Run only the
matching host section from that host's lane checkout. Copy each host's whole
Assay store (the path each run prints as `run-gate: state directory:`)
beneath the final checkout's ignored `.run-gate/collected/` directory, then run the lane without
`--only-candidates`, using repeatable `--merge-state` paths, to write the
whole-lane verdict. The planner does not copy stores or provide transport.
Every host must use the same lane commit and tester image; `--merge-state`
ignores records from a different judge identity, so compare the closeout's
candidate counts with the planned list before trusting its ETA. See the
[consumer guide](CONSUMERS.md) for the exact store-path rules.
The unimplemented Run-Gate plan client is tracked as
[RG-276](../../run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md#rg-276-the-run-gate-campaign-plan-client-is-not-implemented).

## Named jobs and restart recovery

Each planned batch name is
`<campaign>.<host>.s<slot>.b<n>.<list8>`. `list8` is the first eight hex
characters of SHA-256 over the sorted candidate IDs, each followed by a
newline. Before starting, fleetd probes the runner's job table and compares
the full list digest. An existing matching name is adopted; the same name with
a different or unverifiable list refuses. A changed candidate list therefore
has a new name, and the old job is stopped before replacement work starts.

Fleetd delegates start and stop to the runner's `run-job` and
`run-r2-campaign.sh` commands. The real transport shells out to the local
identity-checked `scripts/controller/hx.sh` wrapper. Tests use an in-memory
fake with a controllable clock, job table, progress files, and unreachable or
slow hosts; tests do not start a host, SSH client, or Docker container.

The controller keeps `fleet-campaign-state/2` JSON outside Git and updates it
with a temporary file plus rename. Deadline records include monotonic and UTC
timestamps plus the controller boot ID. On the same boot, calculations use
monotonic time. After reboot, deadlines are rebased using UTC elapsed time; a
backward UTC step clamps elapsed time to zero and delays a detector; a forward
UTC step counts as downtime and can advance detection after reboot. A restarted
controller probes by job name and re-adopts existing work. There are no leases.
On a failed probe, stall time pauses for that host. More than 30 minutes
without contact marks the host lost; `tick`
re-plans only unfinished candidate IDs from running and queued batches on the
reachable planned host with the largest `cap_alone_bytes`. Idle planned hosts
are probed too, so they can receive requeued work. If the original host later
returns, the duplicate run may finish too; collection chooses one owner per
candidate.

The first detached command writes `list.sha256` before the campaign supervisor
starts. During the brief startup interval, probe can recompute it from the
uploaded ID file, so a controller disconnect cannot leave an existing job
unadoptable. Probe transmits job names and ID-file paths rather than candidate
arrays. It resolves the Assay lane from the checkout's Run-Gate declaration
and reads the plain-run `progress-<assay-lane>.jsonl` beneath the project
`.assay` directory; this matches `run-r2-campaign.sh` without relying on its
optional instance pointer. Project paths follow the runner contract and cannot
contain `..` within a component. The campaign client passes generated
`--jobs` and `--only-candidates` only; it does not forward arbitrary Run-Gate
options such as `--base` or `--carry-over-main`.

## Campaign stall detection and no-progress failures

A stall detector needs to protect a genuinely stuck batch without rejecting a
slow but healthy first candidate. Its threshold is the larger of 900 seconds,
three times the p95 predicted candidate time for that batch on that host, and
three times the p95 observed candidate time on that host. Prediction uses the
plan's per-candidate reference time and only that host's matching Geekbench
score and correction factor. When scores cannot be compared, the reference
time is used. Neither prediction nor observations are pooled across hosts;
r1002 and v1001 therefore use independent thresholds.

The first progress window also needs room for Assay's baseline. Fleetd adds a
declared `baseline_seconds` from lane demand, or the p95 of completed baseline
measurements on that host when no estimate was declared. Once a candidate
finishes, later windows measure silence since the last finished candidate and
do not add the baseline again. This keeps the threshold tied to expected work
instead of hiding a slow lane behind a larger fixed timeout.

The scheduler treats an `EXIT`, missing runner job, or stalled batch with no
finished candidate from its own start as `FAILED`. Re-queuing such a batch
cannot make progress and would start another identical failing job on every
tick. A batch that has finished at least one candidate remains eligible for
unlimited re-queues, because those retries move the campaign forward. A
stalled batch with progress keeps its `STALLED` marker and creates one
replacement. These decisions use the job's candidate IDs and its own progress
file start/end offsets; the append-only progress file alone is not evidence
that a later campaign finished the same candidate. Assay-resumed candidates
come from that runner job's verdict.

Transport or contract errors set only the affected host to `ERROR`. The
scheduler records the message and continues the tick for other hosts; identity
and guard refusals remain errors rather than being misclassified as host loss.

## Collection and ownership

`collect` fetches each known progress stream and batch verdict. Per candidate,
the earliest valid finished event owns the result; later events are ignored
and counted. Timestamps come from separate host clocks, so skew can change
which valid duplicate is selected without changing one-owner accounting. If
timestamps tie or are absent, stable host and job ordering keeps the report
deterministic. The collector counts `killed`, `survived`,
`timeout` (`budget_exceeded` and `hung`), `crashed`, `equivalent`, and
`missing`, and reports artifact gaps. Per-host wall time and observed candidate
rate accompany the one-owner table. hx timeouts and unreachable results pause
the host's deadlines. Identity-check and guard refusals (exit 97/98) remain
transport errors and fail closed; they are not converted into `UNREACHABLE` or
`LOST` status.

The lightweight collector does not merge Assay state stores. Assay's strict
`--merge-manifest` and Run-Gate forwarding are tracked under Assay B544 and
Run-Gate RG-277; the exact-key Assay baseline cache is already tracked as
[B542](../../assay/nyxloom-trove/4-backlog.md#b542-r2-restarts-repeat-a-passing-single-core-baseline-and-leave-a-serial-retry-tail).

## Deliberate limits

The guard rules are parsed only; no process or cgroup is changed. There is no
daemon, socket, Docker, retry service, or strict Assay store merge. Guard configuration accepts the design's
`report` and `enforce` modes and the `least-progress-per-ram` victim policy, but
this package does not act on them. The future daemon is fleetd FD-011; the
campaign-aware cgprofile guard is CP-64 in cgprofile's backlog. The current Run-Gate selected-candidate
interface also does not accept fleetd's host-specific RAM budget; the printed
commands use the planned worker count while runtime limits remain host and lane
policy. Track this integration under
[FD-003](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-003-fleet-ram-budget-is-not-sent-to-the-current-runner).
The restricted forced-command entry for campaign hosts remains CIU-218. The
strict result merge remains fleetd FD-008, Assay B544, and Run-Gate RG-277.
Released Run-Gate does not yet accept the
`demand` lane key, so the lane demand lives in a separate TOML input until
RG-275 lands; the missing client verb is tracked as RG-276. `efficient_upper_bound`
must match Assay's authoritative `judge.mutation.jobs_max`, tracked as
[FD-002](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-002-demand-upper-bound-is-not-joined-to-assay-inventory). See
[FD-001](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-001-lite-campaign-lifecycle).

## One grammar, owned by cli-extended

`fleet` is registered with the shared `cli-extended` library rather than a
hand-written `argparse` builder, for the same reason cmru, ciu and the pwmcp
client are: the estate already pays for that grammar once, and every hand-built
copy of it drifts. What the library owns and what fleetd owns is the whole
decision, so it is written down here instead of being re-argued per verb.

Library-owned (fleetd must not re-implement it):

- the help screens, the identity banner and the `version`/`--version`/`help`
  built-ins, the shared output controls (`--json`, `--log-level`, `--quiet`,
  `--debug*`, `--traceback`, the colour policy) and the JSON-mode result stream;
- the exception boundary: domain refusals become one `[ERROR]` line with the
  status a `CliFailure` carries, unexpected exceptions stay visible as a bug;
- the surface lifecycle: the manifest, the generated SPEC region, the reviewed
  catalog and the pytest link between a catalog row and its test (`surface
  sync`/`check`, the `cli_case` marker) — so an unreviewed grammar change fails
  a check instead of passing review by silence;
- the `skills` verbs and the stamp/integrity sidecar behind them.

Fleetd-owned: every verb, argument, option and constraint of the product
interface, the handlers behind them, and the skill *content*. A grammar change
therefore lands in `fleetd.cli.build_cli`, and the library delivers the rest.

### Why the version comes from installed metadata

The package version is derived by `setuptools_scm` from the `fleetd-v*` tag, so
a literal `__version__` in the source could only go stale between a tag and the
next commit — and it would be read by `--version` and by anything that imports
`fleetd`. The CLI therefore resolves the version from the installed
distribution and refuses (exit 3, one actionable line) when it cannot. That
refusal is deliberate: a silent fallback would report a plausible number for a
wheel that is not installed, which is the "defaults and fallbacks are hazards"
failure this estate already paid for elsewhere.

### Why the console script is a separate module

`fleet`'s console script is `fleetd.entry:main`, not `fleetd.cli:main`. The
entry module imports only the standard library, so on a managed host without
`cli-extended` the user sees one install instruction and exit 3 instead of a
`ModuleNotFoundError` traceback that names no remedy. The check is narrow on
purpose: only the declared dependency is reported that way. Any *other* missing
module still raises, because swallowing it would hide a broken installation —
one status must not stand for two different conditions.

### Why the reviewed catalog is a characterization, not a wish list

`docs/cli-review.toml` is generated from the exported surface by
`tools/gen-cli-review-catalog.py`: each row records the invocation *and* the
outcome the real registry produced in `tests/cli_review_harness.py` (a
throw-away HOME, a private non-repository working directory, fixture inputs).
Two consequences are deliberate:

- the catalog pins the grammar and the early refusals (spellings, accepted
  value shapes, exclusive groups, declared constraints, and the refusals that
  precede any host contact). It is not a behavioural specification: the
  behaviour behind a verb is pinned by that verb's own tests, listed in
  [SPEC "Verb semantics beyond the catalog"](SPEC.md#verb-semantics-beyond-the-catalog);
- a row must be machine-independent. Where a command's outcome would otherwise
  depend on the generating machine — `facts collect` reading a real
  `/etc/fleetd/facts.toml`, a campaign command contacting a host — the
  generator supplies a fixture root or an input that cannot exist in the
  sandbox, so the recorded status is a property of the grammar, not of a host.

A stale row, a changed signature or a catalog row with no linked test fails
`cli-extended surface check` (and the pytest plugin at collection), which is the
mechanical part of the review. The judgement — is this spelling, this default,
this refusal the right product decision? — stays a reviewed row with its
`rationale`, `effects` and `decision`.

### Why `run` and `tick` need no confirmation prompt

AC-12 asks every mutating verb for `--yes` or `--dry-run` unless there is a
recorded reason. These two are idempotent supervisors over one campaign's own
versioned state file: `run` re-adopts jobs by their deterministic names, and
`tick` performs one scheduling step whose only effect is that state file. A
repeated call cannot do what a first call would not have done, and a prompt
would break the controller loop that calls `tick` unattended. The destructive
case in this CLI — overwriting a locally *modified* skill copy — is guarded by
the library's `--overwrite-modified`, not by this package.

### Why there is no `fleet doctor`

A doctor earns its place when there is an environment to verify and no other
verb reports it. Here both environments already are reported: `fleet facts
collect` *is* the host-environment report (`host-facts/1` carries a reason for
every unavailable leaf), and the controller side refuses rather than guesses a
missing runner checkout or `hx` wrapper. A `doctor` would duplicate those two
and add a third place to drift. The row is recorded as a judgement in
[the consumer guide](CONSUMERS.md#adoption-checklist-status), with the trigger
to revisit: a third environment that nothing else reports.

### Why `--state-dir` has no declarative default

`--state-dir` falls back to `$XDG_STATE_HOME/fleetd/campaigns`, or
`~/.local/state/fleetd/campaigns` when that variable is unset. That value belongs
to the *invocation's* environment, so it is derived in `fleetd.cli._manager` when
a campaign command runs, not declared as an `OptionSpec` default.

A declared default is resolved once, while the registry is built, which put one
machine's home directory into the exported surface manifest, the generated SPEC
region and therefore into every reviewed-case signature. Two hosts would then
report different signatures for the same CLI (each one *self-consistent*, so no
check would fail), and the generated documents would carry a path from whichever
machine happened to run `surface sync` first. The spec-export test therefore
asserts the surface carries no environment-specific default, and the outcome —
which directory a command reads — is pinned by a behavioural test in two different
`XDG_STATE_HOME` environments.
