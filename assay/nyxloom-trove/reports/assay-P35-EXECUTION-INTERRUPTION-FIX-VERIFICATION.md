# Assay P35 execution-interruption design — fix verification

**Result: NOT READY — do not dispatch or implement P35.**

## Reviewed revision and boundary

This is a fix-verification review of:

- corrected design tip: 40b2106e3efe91c718b8555c30d38957a4040f5f
- corrected design parent: c132d598474ab1dcc9c84e3dbe943dc672417874
- checkpoint parent requested for the original review: bcb70320edc26cdfe1ed52d90b4495aa025a905e
- prior NOT READY report: 9c9f6d99b983ef7ae97f659f0338463dcb908025
- corrected-design worktree inspected: /workspaces/vbpub/.worktrees/assay-b099-p35-repair
- reviewer worktree receiving this report: /workspaces/vbpub/.worktrees/assay-p35-execution-interruption-review

I re-read B099, A-448 and the appended A-449, the corrected P35 packet,
B078/R0 structured-report material and current assay verdict/error/schema/
receipt surfaces, plus the RG-55 and run-gate watcher obligations. I also
inspected the P36 companion packet and the actual scripts/cgroup-profiler
tree. No implementation, handoff, backlog, decision, operator-owned path,
Docker container, mutation campaign, or full gate was run or modified.

Line references below are to the corrected-design checkout at the reviewed
tip, unless a different file is named.

## F-1 through F-8 disposition

| finding | verification result |
|---|---|
| F-1 — report-only fallback and pre-kill receipt | **NOT CLOSED.** The report-only rule and the intended intent/receipt/fsync/ack/kill order are now stated, but the daemon-side owner and implementation scope are absent. |
| F-2 — durable inflight and owner recovery | **CLOSED at design level.** The v2 lifecycle, owner identity, recovery points, idempotence, and refusal-preserving cleanup are materially specified. Implementation proof remains required. |
| F-3 — one-winner CAS and status/start ordering | **CLOSED at design level.** The create/inspect/receipt/journal/running/start sequence and one lock/CAS winner are explicit. Implementation proof remains required. |
| F-4 — budget ownership | **CLOSED at design level.** Assay budget is advisory/non-writing; the optional outer ceiling has an explicit owner, monotonic clock, range, and no default. The implementation must still add and document its closed config key. |
| F-5 — journal schema and grammar | **NOT CLOSED.** The required pre-start journal has no implementable cross-component owner, and its purported exact schema still contains placeholders and unspecified bindings. |
| F-6 — schema-v12 mixed claims and rollups | **CLOSED at policy level.** The corrected matrix preserves complete functional claims, payload-free unfinished suffixes, precedence, and no R2/R3 partial credit. It still needs the named schema/fixture tests after implementation. |
| F-7 — receipt identity, namespace and refusal rules | **NOT CLOSED.** The receipt contract directly contradicts itself on intent_nonce width, and the unresolved daemon handshake means the cgprofile receipt cannot yet be a closed producer fact. |
| F-8 — dependency, vocabulary and scope metadata | **NOT CLOSED.** D-449 does not exist, P36 is not a discoverable/lintable handoff, the selected tier fails against the deployed live route state, and P35 grants write scope over planning records. |

The remaining P0 blockers are enough for NOT READY even though F-2, F-3,
F-4 and the mixed-claim policy in F-6 are substantially repaired.

## Remaining blocking findings

### RV-1 — the pre-kill cgprofile guarantee has no daemon owner or complete scope [P0]

The corrected P35 packet correctly says that the current RG-55 ordering kills
before its existing verdict=killed line and therefore mandates report-only
until the capability exists (P35 lines 338–346). It also states the desired
sequence: bounded kill_intent, session/container verification, receipt
publication and fsync, ack_kill, then daemon kill and the existing verdict
(P35 lines 348–359). P36 repeats that sequence (P36 lines 132–156).

That is not yet an owned implementable boundary. P36's exact implementation
scope is only run-gate-project/run-gate.py, run-gate tests/spec/docs, the
run-gate copy of the RG-55 contract, and its own log (P36 lines 158–178).
It excludes the actual daemon package and its contract:

    scripts/cgroup-profiler/cgprofile.py
    scripts/cgroup-profiler/lib/...
    scripts/cgroup-profiler/tests/...
    scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md

The actual daemon contract still permits --on-stall kill to call
cgroup.kill/SIGKILL before emitting verdict=killed
(scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md:450-467,508-523).
An exact search of the daemon tree found no kill_intent or ack_kill symbol.
The P36 packet cannot implement the sender, its wire grammar, or the
daemon-side “no kill without ack” guard while forbidding all of those paths.
Updating only the run-gate copy of the contract would document a capability
that the producer does not implement.

Until a named companion/owner adds the daemon transport and its tests, the
only sound path is mechanically report-only for every P35-enabled profile
watch, with kill refused before lane start. The packet currently describes
that escape but does not assign a file-level mechanism that enforces it. This
leaves the original false-certification route open: a daemon can still kill
without a receipt, after which the assay side cannot distinguish that event
from an ordinary incomplete/crashed run.

### RV-2 — the journal's required pre-start write conflicts with its declared owner [P0]

The corrected lifecycle requires, after docker create and exact docker
inspect, that the initial journal and running receipt be committed before
docker start releases the assay command (P35 lines 290–296; P36 lines
61–68). The journal section, however, declares
src/assay/execution_journal.py to be the writer/reader and sets the top-level
writer to “assay” (P35 lines 389–412). P35's own scope also says P35 owns the
journal, while P36's scope contains no assay module or journal schema.

Assay cannot write that initial journal after docker start without violating
the pre-start rule. Conversely, if P36 writes it, the packet must define an
owned bootstrap API and the exact handoff of resolved metadata; otherwise
run-gate has to invent or duplicate assay-owned values such as
argv_effective, declared rigor, report/progress paths and writer revision.
No such API or byte-level bootstrap document is specified. This is an
ownership/order contradiction, not an implementation detail.

The claimed “exact” journal grammar is also not closed enough to resolve that
contradiction. The top-level example contains “bounded strings,”
“project-relative POSIX path or stdout,” “complete canonical claim object,”
“bounded exact identity,” “committed result,” and provenance “bounded”
(P35 lines 400–440). “Existing per-payload limits” is referenced but not
enumerated (lines 391–398). The transaction grammar takes a result and
provenance argument without defining their wire shapes (lines 451–480). It
does not state the canonical serialization or digest binding for a claim, the
closed candidate identity/status vocabulary, the relation between
candidate_ledger and its result digest, or the exact cross-field checks for
resolved and boundaries.

Thus a future implementation can still choose different journal bytes,
bootstrap ownership, candidate completion semantics, or report provenance
while claiming to satisfy the same prose. Reconcile cannot be certified as
“only committed boundaries” until the schema and transaction/API boundary are
closed.

### RV-3 — the receipt schema has an internally contradictory nonce grammar [P0]

The sample receipt uses a 32-hex cgprofile.intent_nonce (P35 line 182), and
the pre-kill protocol repeats 32 hex (P35 line 350). P36 also requires a
32-hex nonce in the kill_intent and returns that nonce in ack_kill (P36
lines 141–149). But the P35 field-bounds paragraph says “nonce and digest
64 lowercase hex” (P35 lines 202–209). The receipt schema cannot accept the
same acknowledged cgprofile event under both readings. A valid daemon event
will either be rejected as too short or violate the stated producer contract.

This is directly on the receipt identity/refusal boundary: it must be fixed
to one width and repeated consistently in the receipt schema, daemon intent,
ack, digest binding, fixtures and reader checks before F-1/F-7 can close.

### RV-4 — the dependency and companion metadata do not mechanically hold P35 [P0]

P35 frontmatter declares depends_on: [D-449] (line 10) and explains that this
is a local substitute for the cross-project companion id (lines 90–99).
The corrected decision actually appended is A-449 (assay decisions line
897), and an exact search of the assay/run-gate troves finds no D-449 other
than P35's self-reference. The nyxloom dependency implementation separates
D- decision refs from task refs (nyxloom/src/nyxloom/types.py:540-545), and
the reconcile hold checks only whether that D id is in the open-decision set
(nyxloom/src/nyxloom/reconcile.py:1132-1147). It does not resolve the prose
companion name, nor does A-449 satisfy a D-449 lookup.

The canonical authoring contract says depends_on is the mechanical mix of
merged handoff ids and open D ids that holds dispatch
(nyxloom/reference/AUTHORING.md:389-421). The corrected packet therefore has
a prose dependency, not an enforced dependency. P35 can be dispatched without
an accepted P36 state unless a real task/state id is registered in a namespace
the controller actually reads, or the decision dependency is correctly
recorded as an open D id.

The P36 “companion handoff” does not repair this: it is a Markdown report at
run-gate-project/nyxloom-trove/reports/...HANDOFF.md beginning with a heading,
not schema frontmatter containing id, project, input_revision, depends_on,
oracles, gates, and exact scope metadata. Nyxloom discovery explicitly
excludes reports (nyxloom/src/nyxloom/frontmatter.py:139-148), and the
run-gate project has no owning nyxloom.toml in this checkout. An explicit lint
attempt returns:

    L0 error no owning project found for
    'run-gate-project/nyxloom-trove/reports/run-gate-P36-...-HANDOFF.md'

It is a useful design note, but it is not a machine-addressable companion
task that P35's current dependency can hold on.

### RV-5 — the tier is not valid against the live route state, and planning files are over-scoped [P1]

The corrected packet claims implement-1 is a live authoring tier (frontmatter
line 6; repair report lines 13–16). The tracked nyxloom/routes.host.toml does
contain that post-B16 tier, but the live file consumed by nyxloom lint is
/home/vscode/.local/state/nyxloom/routes.toml, which still contains the
pre-B16 tier names. The required live check therefore returns:

    assay/.../assay-P35-execution-interruption-boundary.md:
    L14 error tier 'implement-1' is not a key in the live routes.toml

Linting against a temporary copy of tracked routes.host.toml returns clean,
but that is a tracked-route snapshot, not the live route state. It does not
justify the repair report's unqualified “clean” claim for dispatch metadata.
This should either be resolved by the route deployment/metadata contract or
recorded as an explicit environmental limitation rather than calling the
tier live.

Finally, P35's exact scope.touch now grants the implementer write access to
nyxloom-trove/decisions.md and nyxloom-trove/4-backlog.md (lines 37–43), even
though A-449 is already recorded and the packet says it is design-only. Those
are append-only/controller planning records, not implementation files. The
repair also says P35 may not edit run-gate paths while the P36 packet is a
non-dispatchable report. The implementation scope should forbid the planning
records and name the real cross-project admission mechanism; otherwise the
metadata permits mutation of the very B099/A-449 records used to decide
whether the work is ready.

## What the repair does establish

The repaired text materially fixes the following false-certification logic:

- The report-only fallback is explicit until the pre-kill capability exists;
  a final RG-55 profile summary is not treated as causal evidence (P35 lines
  135–148, 338–359; P36 lines 23–29 and 153–156).
- The v2 inflight lifecycle has durable allocated, running, pending,
  reconcile and refusal states; exact Docker identity, owner PID/start/boot/
  namespace, one lock/CAS, owner recovery, and cleanup preservation are all
  stated (P35 lines 269–336; P36 lines 31–97).
- Assay budget is explicitly a non-writer. The outer safety ceiling is
  separately owned, monotonic, bounded to 1 second–24 hours, and has no
  shadow default (P35 lines 361–387; P36 lines 121–130).
- The v12 rollup matrix explicitly keeps a verified complete R0 report and
  its functional PASS/FAIL payload, preserves completed higher-rigor claims,
  makes every unfinished claim payload-free, keeps ERROR/
  NO_MEASUREMENT/BUDGET_EXCEEDED precedence, and awards no mutation or
  canary credit to interrupted work (P35 lines 482–530 and 555–571).
- The receipt namespace and refusal policy now distinguish omitted optional
  input from supplied-missing and invalid input, bind run/lane/commit/
  container identity, reject symlinks and malformed/oversized/mismatched
  artifacts, use fixed permissions, and require receipt publication before a
  permitted termination (P35 lines 152–267).

Those statements are necessary but not sufficient: the missing producer
owner, contradictory nonce, non-addressable dependency, and journal bootstrap
gap prevent them from becoming an enforceable “no incomplete execution is a
functional or mutation outcome” boundary.

## Commands and results

All checks were read-only and run from the corrected-design worktree unless
noted. No product tests were run because this round is design verification and
the request forbids product code, Docker, mutation and full-gate execution.

    git status --short
        clean

    git diff --check c132d598474ab1dcc9c84e3dbe943dc672417874..40b2106e3efe91c718b8555c30d38957a4040f5f --
        exit 0, no output

    nyxloom lint assay/nyxloom-trove/handoffs/assay-P35-execution-interruption-boundary.md
        exit 1
        L14 error: tier 'implement-1' is not a key in the live routes.toml

    XDG_STATE_HOME=<temporary directory containing a copy of nyxloom/routes.host.toml>/nyxloom/routes.toml
      nyxloom lint assay/nyxloom-trove/handoffs/assay-P35-execution-interruption-boundary.md
        exit 0, clean

    XDG_STATE_HOME=<same tracked-route snapshot> nyxloom lint
      assay/nyxloom-trove/handoffs/assay-P35-execution-interruption-boundary.md
      run-gate-project/nyxloom-trove/reports/run-gate-P36-execution-interruption-producer-HANDOFF.md
        exit 1
        L0 error: no owning project found for the P36 report path

    git diff --name-status c132d598474ab1dcc9c84e3dbe943dc672417874..40b2106e3efe91c718b8555c30d38957a4040f5f --
        only the three assay design records, the assay repair report, and the P36 companion report changed;
        no product implementation path changed

    rg -n -F 'D-449' assay/nyxloom-trove run-gate-project/nyxloom-trove
        only P35's frontmatter/prose self-reference

    rg -n -F 'A-449' assay/nyxloom-trove/decisions.md
        A-449 is present; no D-449 decision is present

    rg -n -i 'kill_intent|ack_kill' scripts/cgroup-profiler
        no matches

## Decision

**NOT READY.** Do not dispatch or implement P35. Resolve RV-1 through RV-4
at minimum: assign the daemon handshake to an actual owner and scope, make
the pre-start journal bootstrap and wire grammar implementable, choose one
nonce grammar, and replace the nonexistent D-449/report-only companion
dependency with a controller-resolvable admission fact. Then rerun this
review with the live route state and the required focused red-first checks.
