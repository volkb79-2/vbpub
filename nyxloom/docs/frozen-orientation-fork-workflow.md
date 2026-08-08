# Frozen orientation and forked execution

> **Status:** design proposal for a semi-manual pilot. This is not a description
> of current `nyxloomd` behavior. The Assay P20+ wave is the first measurement
> candidate. Do not automate it until the measurements and failure modes below
> have been observed.

## Outcome

Pay the large, mostly stable repository-orientation cost once per role/model and
epoch, then fork a clean child for each handoff. The child receives only current
facts at the end of its prompt: the handoff, the commit delta since orientation,
and a small adjudicated successor brief. Implementation and review contexts stay
disposable; the frozen parent never accumulates package-specific reasoning.

This is a cost optimization with a correctness contract, not permission to use
stale context. A cache hit is useful only when the child is forced to reconcile
its frozen model with the current tree.

## Terms

- **Epoch:** a period in which the product contracts and orientation manifest
  remain materially stable.
- **Frozen base:** a named, read-only CLI session created for one role, model,
  effort, repository, and epoch. Example names: `I0` (implementer), `R0`
  (reviewer), `C0` (carver/controller advisor).
- **Orientation anchor:** the full 40-character commit OID at which the base read
  the repository.
- **Orientation manifest:** the exact files and sections read by the base,
  including their blob OIDs. It is a change-detection scope, not a permission
  list and not a substitute for the current handoff's context list.
- **Fork:** a new child session made from a frozen base. Work happens only in the
  child.
- **Successor brief:** short-lived information that is valuable to the next leg
  but cannot yet be derived from the repository or the handoff.

## Non-negotiable invariants

1. A frozen base is never resumed in place for real work. Fork it. If a tool
   cannot prove that it created a child, use a fresh session rather than risk
   contaminating the parent.
2. The base is package-neutral. It stops after doctrine, product goals,
   architecture, the wave map, recurring traps, and a high-level tree survey. It
   does not read the next handoff, form an implementation plan, or inspect a
   package-specific source seam.
3. Every child names both the orientation anchor and the expected current HEAD.
   It verifies both against Git before trusting inherited source knowledge.
4. A diff is a stale-context detector, not present truth. A child rereads the
   complete current version of every relevant file that changed.
5. Implementer and reviewer bases are separate. The reviewer first analyzes the
   diff without the implementer's narrative; briefs arrive only after that blind
   findings pass.
6. The controller routes facts and checks state. It does not invent a missing
   product contract or silently reinterpret a handoff.

## Frozen-base record

Persist this record outside the model transcript:

```yaml
schema_version: 1
base_name: I0
role: implementer
provider: anthropic
model: sonnet
effort: high
session_id: "<provider session id>"
repo_root: /workspaces/vbpub
orientation_commit: "<full commit oid>"
created_at: "<RFC3339 UTC>"
manifest:
  - path: AGENTS.md
    sections: ["all"]
    blob_oid: "<git blob oid>"
  - path: assay/nyxloom-trove/series/README.md
    sections: ["current wave", "dependency order"]
    blob_oid: "<git blob oid>"
cache_policy:
  assumed_ttl: "1h"
  source: "operator configuration"
last_cache_read_tokens: null
health: ready
```

The TTL is an assumption until telemetry proves a read. Record full commit and
blob OIDs; abbreviations are for display only. A base is valid only for its exact
role/model/effort tuple because providers do not promise cache sharing across
different model identities.

## Creating a base

The orientation prompt names a full commit OID and a compact context manifest.
It asks the agent to:

1. verify the repository root and exact commit;
2. read the canonical and project doctrine in prescribed order;
3. read stable product goals, decisions, architecture, and the wave overview;
4. inspect only enough of the tree to learn ownership and recurring patterns;
5. return a concise orientation manifest, unresolved product risks, and
   `READY_TO_FORK`; and
6. stop before reading any outstanding handoff or making an implementation plan.

Create a distinct base for each role/model/effort expected during the epoch. Do
not orient all 150-200k tokens merely because an earlier workflow did. Start with
the smallest context list believed sufficient and measure defects as well as
tokens. Stable prompt bytes go first; timestamps, current task identifiers, and
briefs never enter the base.

For Claude Code, the supported shape is:

```sh
claude --model sonnet --effort high --name I0 \
  --exclude-dynamic-system-prompt-sections -p "<orientation prompt>"
```

Capture the actual session identifier returned by the CLI. A human-friendly
name is not a sufficient identity if names can collide.

## Forking a package

Fork the frozen session directly into the package worktree:

```sh
claude --resume "<I0-session-id>" --fork-session \
  --name "I0-assay-P20" --model sonnet --effort high \
  --exclude-dynamic-system-prompt-sections -p "<branch prompt>"
```

The branch prompt must begin with state reconciliation, in this order:

1. `cd`/tool context is the declared worktree; refuse another checkout.
2. `git rev-parse HEAD` equals the expected current HEAD supplied by the
   controller.
3. The frozen base was oriented at the supplied full anchor OID.
4. The anchor is an ancestor of current HEAD. If not, stop with
   `STALE_ORIENTATION_BASE`; do not guess a merge base.
5. List changes since orientation with:

   ```sh
   git diff --name-status --find-renames <orientation-oid>..HEAD
   git diff --find-renames <orientation-oid>..HEAD -- <manifest paths and current context paths>
   ```

6. Reread the whole current version of each changed file needed for the task.
   Read any new current-context file that the frozen base could not have seen.
   A deletion, rename with uncertain ownership, or changed public contract is a
   reason to stop or rotate the base, not to rely on memory.
7. Read the current handoff and then the one-hop predecessor brief. The brief is
   advisory evidence; the handoff and repository remain authoritative.

After reconciliation, the child implements exactly one package, self-reviews,
runs the named gate in the prescribed gate environment, commits in its isolated
worktree, and emits a structured successor-brief proposal. It never writes into
or resumes the frozen parent.

## Independent review without throwing away the cache

Maintain a reviewer base `R0` with the same anchor discipline. Review has two
phases in one forked reviewer child:

1. **Blind findings:** inspect Git state, the handoff, its normative context, and
   the implementation diff. Do not expose the implementer's report or successor
   brief yet. Record requirement omissions, false-PASS tests, defaults,
   namespace errors, and at least one new combined-axis attack.
2. **Reconciliation and repair:** append the implementer report and proposed
   briefs. Check each claim against Git and gate evidence, repair/enhance within
   the handoff's existing scope, run the real gate, and produce adjudicated
   successor briefs.

The controller verifies the branch, gate result, and review disposition before
the serial `--no-ff` merge. Reports are evidence to verify, never the source of
Git truth.

## Successor-brief lifecycle

A one-hop brief is intentionally allowed to anticipate the immediate successor.
That is its value: it can name a discovered trap before the next implementer
encounters it. It must not become a growing shadow specification.

Use these dispositions:

| disposition | owner and lifetime |
|---|---|
| `promote-contract` | carver writes the durable fact into a handoff/spec/decision before the next affected dispatch |
| `promote-epoch` | carver updates the epoch orientation material and rotates affected bases |
| `one-hop` | controller injects it only into the named next leg, then expires it |
| `decision` | controller routes an unresolved product call to `D-<NNN>`; no agent invents it |
| `discard` | reviewer records why the item is derivable, stale, duplicate, or wrong |

Each candidate has this minimum shape:

```yaml
- id: SB-P20-01
  text: "<non-derivable fact or trap>"
  evidence_ref: "<commit/path/log finding>"
  audience: implementer
  applies_to: [P21]
  proposed_disposition: one-hop
  invalid_if: "<specific contract or file changes>"
```

Responsibility is deliberately split:

- The implementer **proposes** items and may anticipate the immediate successor.
- The independent reviewer **adjudicates** truth, audience, disposition, and
  expiry after its blind pass.
- The carver/design authority **promotes** lasting information into handoffs,
  specifications, epoch material, or decisions.
- The controller performs only mechanical routing: inject into named targets,
  reject malformed/expired items, remove one-hop items after consumption, and
  stop for the carver when adjudication or promotion is unresolved.

This keeps the semantic judgment with capable roles. A cheap controller is safe
only because it never decides whether an undocumented claim should become
product truth.

## Cache observation and keepalive pilot

Prefix reuse is a hypothesis until usage telemetry reports cached input. Capture
total input, cache-creation input, cache-read input, output, elapsed time, and
time-to-first-edit for every invocation. Nyxloom already parses Claude's
`cache_read_input_tokens` into `cached_in`; surface it per base and child.

For the operator-reported one-hour Claude cache, a keepalive may be worthwhile
only when another real fork is expected. Fork a disposable child so the frozen
parent stays pristine:

```sh
claude --resume "<base-session-id>" --fork-session \
  --name "I0-warm-<sequence>" -p "Return only CACHE_WARMED"
```

Pilot one keepalive around 45-50 minutes. A second around 90-100 minutes is
reasonable only when the next useful dispatch is still expected soon. Do not
run a perpetual heartbeat by default: cache reads consume quota, provider TTL
details can change, and an idle warm cache has no product value. Record
`CACHE_MISS` unless telemetry shows nonzero cached input; the model saying
“warmed” is not evidence.

## Epoch rotation

Rotate affected bases when any of these is true:

- a product contract, schema, public interface, ownership boundary, or topology
  read during orientation changed;
- the orientation anchor is no longer an ancestor of current HEAD;
- the context manifest changed enough that repeated child reconciliation costs
  approach fresh orientation;
- the role/model/effort changes;
- session health or cache telemetry is unreliable; or
- three child briefs in one epoch require `promote-epoch` (a drift backstop, not
  a substitute for the semantic triggers above).

Batch durable documentation updates when safe so one intentional rotation
replaces repeated cache invalidation. Never delay a correctness-relevant update
merely to preserve a cache.

## Semi-manual experiment

Compare at least these conditions on comparable handoffs:

1. fresh session with current narrow context;
2. warm fork from a compact frozen base;
3. cold fork after the assumed TTL; and
4. the historical broad 150-200k orientation, if affordable as a baseline.

Measure cost and latency, but also reviewer defect count, handoff deviations,
rework turns, stale-context stops, and false-PASS escapes. Record the anchor,
manifest size, model/effort, prompt-cache telemetry, and whether a keepalive was
used. The workflow wins only if total cost falls without increasing escaped or
review-discovered defects.

## Nyxloom implementation seams (after the pilot)

Likely product work, subject to redesign:

1. a frozen-parent session policy distinct from today's growing `resume` policy;
2. a base registry keyed by project/role/model/effort/epoch;
3. explicit CLI fork templates and capture of the child session ID;
4. orientation manifests plus an anchor-to-HEAD reconciliation renderer;
5. structured successor-brief candidates, adjudication, promotion, consumption,
   and expiry;
6. cache-read/write telemetry and an opt-in keepalive scheduler;
7. contract-class (`2a`-`2e`) linting and routing to five implementer bands; and
8. a trace view tying base, fork, handoff, brief, gate, review, and merge together.

Until these exist, a Luna-class controller may run the procedure semi-manually,
but only with the mechanical prompt and stop conditions stored in the project
trove. Sol/Opus remain the carver/adjudicator when a contract must change.

