# Frozen orientation and forked execution

> **Status:** executable semi-manual pilot contract. This is not a description
> of current `nyxloomd` behavior. The Assay P20+ wave is the first measurement
> candidate. Its Luna controller prompt and state formats are named below. Do
> not automate it in the daemon until the measurements and failure modes below
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
- **Frozen execution base:** a named, read-only CLI session created for one
  implementation or review role, model, effort, repository, and epoch. Example
  names: `I-sonnet-0`, `I-opus-0`, and `R-opus-0`.
- **Evolving carver thread:** a resumable design-authority session, such as
  `C-sol-0`, that is intentionally updated after each merge. It is not a frozen
  execution base and is never forked as an implementation or review context.
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

1. A frozen execution base is never resumed in place for real work. Fork it. If a tool
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
7. The repository is authoritative. A session, cache entry, compaction item,
   brief, and controller state file are accelerators or evidence, never a shadow
   specification.

## Frozen-base record

Persist this record outside the model transcript:

```yaml
schema_version: 2
base_name: I0
role: implementer
provider: anthropic
model: sonnet
effort: high
session_id: "<provider session id>"
repo_root: /workspaces/vbpub
orientation_commit: "<full commit oid>"
created_at: "<RFC3339 UTC>"
cli_version: "<observed version>"
system_and_tools_fingerprint: "<sha256 of stable injected prefix inputs>"
manifest:
  - path: AGENTS.md
    sections: ["all"]
    blob_oid: "<git blob oid>"
  - path: assay/nyxloom-trove/series/README.md
    sections: ["current wave", "dependency order"]
    blob_oid: "<git blob oid>"
cache_policy:
  ttl: "1h"
  source: "provider usage object: ephemeral_1h_input_tokens"
  verified: true
last_touch_request_started_at: "<RFC3339 UTC or null>"
last_cache_read_tokens: 0
last_cache_creation_tokens: 0
expected_cache_read_floor: "<measured lower bound after orientation>"
health: ready
```

The TTL is an assumption until provider usage telemetry names the TTL class and
proves a read. Record full commit and blob OIDs; abbreviations are for display
only. A base is valid only for its exact role/provider/model/effort/CLI/system-
and-tools tuple because prefix caches match serialized request bytes and model
identity, not a human notion of equivalent prompts.

`bases.yaml` is a generated current-state projection, not an event log. Write a
complete temporary sibling, schema-check it, `fsync`, and atomically replace the
old file; never regex-edit YAML or ask an agent to preserve comments while
mutating it. Persist append-only invocation evidence separately as JSON Lines,
one complete object per request, in `invocations.jsonl`. Persist each reviewed
brief as its own immutable `briefs/Pxx.yaml`. This split makes YAML useful for
operator-readable state without making repeated YAML rewrites the audit trail.
Nyxloom should eventually own and version these schemas; during the pilot the
controller prompt below is their contract.

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

## Frozen bytes, moving repository truth

Changing `decisions.md` on disk does **not** rewrite an already-created Claude
session. The frozen session still serializes the old exact prefix and can still
receive a cache hit. What changed is semantic staleness, not cache identity.
That is why every child starts from the frozen orientation OID, reads the exact
OID-to-HEAD diff, and rereads relevant current files in full. Appending the
current delta and brief at the end preserves the stable prefix while exposing
the progression directly.

Do not reconstruct an almost-identical orientation prompt merely to include a
new `decisions.md`; that creates a different serialized prefix. Rotate the base
only at a deliberate epoch boundary or one of the triggers below. Until then,
land every durable ruling immediately in Git and pay the scoped reconciliation
cost. Cache preservation is never a reason to defer product truth.

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

The normal serial order is reviewer adjudication -> durable merge -> JIT carve
of the immediate successor. The JIT carver receives only the adjudicated
successor candidates plus the predecessor merge range, checks both against the
current repository, promotes lasting facts while carving, and returns a smaller
one-hop remainder. This is the preferred place to fold discoveries into the new
reality; the controller is not asked to perform that semantic compression.

## JIT carve and the evolving carver thread

**JIT carve** means freezing the next handoff at the last responsible moment:
after every dependency is merged and reviewed, and before an implementer sees
the handoff. It runs only for the immediate next package, not the whole future
queue. It resolves interfaces that are now knowable, commits compiling
skeletons/goldens/hostile fixtures where AUTHORING requires them, witnesses the
controlled pre-implementation failures, runs the exact adversarial handoff
review, and marks the packet `READY` or `NOT_READY` with evidence.

Keep one evolving Sol carver thread when it remains healthy. On every JIT turn
it must receive and verify:

1. its last acknowledged full repository OID;
2. the current full main OID and predecessor merge range;
3. `git diff --name-status` and scoped diffs from the acknowledged OID to HEAD;
4. the current handoff, immediate roadmap horizon, and adjudicated incoming
   brief; and
5. any unresolved `D-<NNN>` decisions.

The thread may be automatically compacted by its provider. Treat compaction as
opaque and potentially lossy. Before a long gap, expected compaction, or epoch
change, Luna writes a structured **carver checkpoint proposal** containing the
last acknowledged OID, decisions/rulings not yet durable, incoming brief
dispositions, open probes, and next-handoff risk. On resume, Sol validates it
against Git before relying on it. Luna can prepare and route this external
compression; it cannot declare its interpretation design truth.

Rotate `C-sol-N` only when the session cannot identify its acknowledged OID,
compaction has lost required rationale that Git/checkpoint cannot reconstruct,
the product enters a new schema/ownership/topology epoch, or the model/effort
changes. A long thread is an optimization, not authority. Unlike execution
bases, normal JIT work is resumed in the evolving carver thread itself.

For Codex, official compaction documentation describes server-side compaction
as an opaque state item and the standalone compact endpoint still takes a model;
it does not publish a special cheaper "compaction token" tariff. Treat its cost
as ordinary model usage unless actual product telemetry says otherwise. At the
2026-08-09 API list prices, GPT-5.6 Sol/Terra/Luna are respectively
$5/$2.50/$1 input, $0.50/$0.25/$0.10 cached input, and $30/$15/$6 output per
million tokens, before the documented long-context multipliers. Terra is one
half and Luna one fifth of Sol for the corresponding token class. A Codex
subscription need not expose a per-request charge, so the pilot records raw
tokens/quota and labels any API-equivalent dollar calculation as a simulation.
Sources: <https://developers.openai.com/api/docs/guides/compaction> and the
official GPT-5.6 Sol/Terra/Luna model pages.

## Cache observation and keepalive pilot

Prefix reuse is a hypothesis until usage telemetry reports cached input. Capture
total input, cache-creation input (including TTL class), cache-read input,
uncached input, output, request-start time, elapsed time, and time-to-first-edit
for every invocation. Nyxloom already parses Claude's
`cache_read_input_tokens` into `cached_in`; surface it per base and child.

For Claude Code, usage is already in the top-level session JSONL. Dedupe repeated
content blocks by message id:

```sh
jq -s '[.[] | select(.message.usage) |
  {id: .message.id, ts: .timestamp, u: .message.usage}] | unique_by(.id) |
  {requests: length,
   cache_read:  (map(.u.cache_read_input_tokens // 0) | add),
   cache_write: (map(.u.cache_creation_input_tokens // 0) | add),
   uncached_in: (map(.u.input_tokens // 0) | add),
   out:         (map(.u.output_tokens // 0) | add),
   ttl_classes: (map(.u.cache_creation // {}) | unique)}' SESSION.jsonl
```

Record per-request rows too; an aggregate can hide the exact request on which a
prefix read collapsed. The TTL window is measured from request **start** and is
sliding on a cache hit. Generation time consumes the window. Schedule any
keepalive from the last request start, not from when its response finished.
Verify the TTL class in telemetry: observed top-level Claude sessions may use
one-hour entries while internal subagents may use five-minute entries. Never
promote an internal subagent transcript to a frozen external base merely because
both are called Claude.

For the operator-reported one-hour Claude cache, a keepalive may be worthwhile
only when another real fork is expected. Fork a disposable child so the frozen
parent stays pristine:

```sh
claude --resume "<base-session-id>" --fork-session \
  --name "I0-warm-<sequence>" -p "Return only CACHE_WARMED"
```

Pilot one keepalive around 45-50 minutes from the previous request start. A
second around 90-100 minutes is reasonable only when the next useful dispatch
is still expected soon. Do not run a perpetual heartbeat by default: cache
reads consume quota, provider TTL details can change, and an idle warm cache has
no product value. Record
`CACHE_MISS` unless telemetry shows nonzero cached input; the model saying
“warmed” is not evidence.

## Epoch rotation

Rotate affected bases when any of these is true:

- a product contract, schema, public interface, ownership boundary, or topology
  changed enough that scoped reconciliation is no longer cheaper or reliably
  understandable than reorientation;
- the orientation anchor is no longer an ancestor of current HEAD;
- the context manifest changed enough that repeated child reconciliation costs
  approach fresh orientation;
- the role/model/effort changes;
- session health or cache telemetry is unreliable; or
- three child briefs in one epoch require `promote-epoch` (a drift backstop, not
  a substitute for the semantic triggers above).

Batch a **base rebuild**, not repository truth. Land durable documentation and
decisions as soon as they are required, then let children reconcile them from
the anchor. At a deliberate new epoch, rebuild each affected base once from the
current repository instead of reconstructing it after every small change.

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
9. an evolving JIT-carver continuation with typed checkpoint/route-to-carver
   artifacts, explicitly distinct from frozen execution parents; and
10. versioned schemas and atomic writers for `bases.yaml`, brief YAML, and the
    append-only invocation JSONL.

Until these exist, a Luna-class controller may run the procedure semi-manually,
but only with the mechanical prompt and stop conditions stored in the project
trove. Sol/Opus remain the carver/adjudicator when a contract must change.
