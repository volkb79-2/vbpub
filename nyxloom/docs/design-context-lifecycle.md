# Agent context lifecycle — two compaction-class optimizations (design + validation plan)

Status: DESIGN — validation plan defined, none of the end-to-end cycles proven yet.
Origin: dstdns operator directive 2026-08-20 (session `fb05e89e`, Fable controller).
Home: **nyxloom** — these are dispatch/lifecycle strategies for the agents nyxloomd
(and interactive controllers) run; once proven they distill into copyable rules
(AGENTS.md if cross-CLI, CLAUDE.md if Claude-only — see §7).

Related, already written:
- `docs/frozen-orientation-fork-workflow.md` — the fork/freeze mechanics both patterns build on
- `reference/LESSONS.md` L24 — fork = pure cache reuse under four invariants (headline
  corrected 2026-08-20, vbpub `345f289e`)
- backlog **B46** — the external compaction service these patterns need
- dstdns `CLAUDE.md` § "Long-running agent context discipline" — the interim
  checkpoint+successor rule already in force there

---

## 1. The problem, measured

A long-running agent's transcript is re-billed on every turn, and tool-call output
(file dumps, gate logs) dominates it while carrying almost no forward value.
Measured on dstdns 2026-08-20: the P107 implementer closed at **~640k subagent
tokens** across three resumes; its durable value fit in a REPORT + LOG a successor
consumed in full. Auto-compaction exists but is **undirected** — it summarizes
without knowing which 3 seams and 2 open questions the next iteration needs.

Two mechanisms were validated the same day (all numbers from live probes):

| Fact | Measurement |
|---|---|
| Headless designed compaction works | `claude -p --resume <sid> "/compact <instructions>"` → `compact_result: success`; knowledge survives resume; instructions steer the summary. ~90 s API call at 747k pre-tokens. |
| Compact JSONL anatomy | `system/compact_boundary` line (`parentUuid: null` = chain reset; `compactMetadata` incl. `preservedMessages` tail) + `user` line with `isCompactSummary: true`. |
| Direct JSONL forging | **REJECTED** — 3 variants failed (resume leaf-selection skips synthetic lines). Drive the CLI, never file surgery. |
| Fork cache purity | `--resume <frozen> --fork-session` with `--exclude-dynamic-system-prompt-sections` + same model + same effort + stable toolset → cache_read 24,918 / **creation 100** (≈ pure reuse). Any prefix drift re-bills everything. |

Prior art to study before building: `rocketlabs-ai/infinite-context` (session
rebuild + compact-compatible smart compact), `swyxio/claude-compaction-viewer`,
badlogic's cross-CLI compaction research gist
(`gist.github.com/badlogic/cd2ef65b0697c4dbe2d13fbecb0a0a5f` — covers Claude Code,
Codex CLI, OpenCode, Amp).

---

## 2. Pattern (a) — checkpoint → designed compact → resume

**Loop:** agent notices the context threshold (~300k) → finishes the current work
item to a coherent state (never starts a new major item past threshold) → writes
its own compaction prompt (the designed retention: done-list, next-list,
load-bearing `file:line` seams, open questions, gate state) → exits. The
controller (or the B46 service) runs `claude -p --resume <sid> "/compact
<agent's prompt>"` → resumes the agent on the same session with "continue".

**Cost model per cycle:** one summarization API call that re-reads the whole
transcript once (input ≈ T_transcript), then the session continues from a small
context (summary + preserved tail). Cache after compaction is cold but tiny.

**Properties:**
- Session id persists → external references (task ids, monitors, logs) stay valid.
- The `preservedMessages` tail keeps the last few verbatim turns — good when
  mid-flight nuance (a half-diagnosed failure) must survive verbatim.
- Simplest bookkeeping: no snapshot chain, one live session.
- The agent authors the retention prompt with full context still in view —
  strictly better than auto-compact's generic summary.

**Weaknesses:** pays the full-transcript read every cycle; the compaction step is
a serialization point (~90 s + tokens); depends on `/compact` CLI behavior
(version-internal, though the *command* is a stable documented surface).

## 3. Pattern (b) — snapshot chain: fork → work → iteration-summary → re-fork

**Snapshot 0 is a DISTILLED brief, not the raw orientation session** (operator
refinement 2026-08-20): a raw orientation routinely closes at ~150k — most of it
tool output and search dead-ends with no forward value. So orientation runs as a
throwaway session whose *deliverable* is a crafted 20-30k **orientation brief**
(all load-bearing facts from the base commit: architecture seams, contracts,
file:line anchors, decisions in force); snapshot 0 is a fresh session seeded
with that brief (+ ack turn), frozen. Every later fork then re-reads 20-30k,
not 150k — and the brief doubles as a reviewable, versionable artifact on disk.

**Tiered orientation** (operator refinement 2026-08-20, same day): the orientation
session itself runs on a CHEAP model (haiku/sonnet) — it gets a refined
orientation prompt (must-read doc list as the floor, freedom to explore beyond
it, plus what the upcoming task IS so relevance is judged against real work) and
authors the brief. Premium models (opus/fable) then never spend a single
tool-call turn on orientation. Caveat, load-bearing: **the prompt cache is
model-specific** — a snapshot minted by haiku yields zero cache reuse for an
opus fork. Correct shape: the cheap model's deliverable is the brief FILE;
snapshot-0 is minted per target tier (one ack turn over the 20-30k brief at that
tier's price), and forks within a tier are pure reuse. The residual premium cost
of orientation is therefore one 20-30k input creation — never the ~150k of
exploration. Risk: a cheap model may miss a load-bearing subtlety; the must-read
floor + task-aware prompt + a controller lint pass over the brief are the
mitigations, and V2c measures whether they suffice.

**Loop:** maintain a frozen **snapshot** session (snapshot 0 as above). Each iteration: controller forks the snapshot → agent
works → at iteration end (or threshold) the agent **self-compacts its own
iteration**: writes an iteration-summary (what changed since the snapshot, what
the *known future work* needs — and deliberately not what it won't) → exits, its
working transcript abandoned. Controller mints the next snapshot: fork the old
snapshot, inject the iteration-summary as one message with a one-token ack turn →
freeze that id as snapshot k+1 → fork it for the next work session.

**Cost model per cycle:** **no summarization API call at all** — the summary is
ordinary output tokens written by the agent in its live session (the transcript
was already in its context; marginal cost ≈ summary length). Fork pays ~0
creation when the snapshot prefix is warm, or one re-create of the *snapshot*
(orientation-sized, not transcript-sized) when cold. The bloated working
transcript is dropped, never re-read.

**Properties:**
- Strictly cheaper API-wise than (a): the full transcript is never re-read.
- Snapshots are **reusable and parallel-friendly**: N workers (or an implementer
  + a reviewer) fork the same snapshot — this is exactly the proven B1 harness,
  extended with a chain.
- Bounded working context per iteration ≈ snapshot + summary + new work.
- Task-aware pruning: because the task plan is known, the agent can anticipate
  what the future work does *not* need — something no generic summarizer can do.

**Weaknesses / cares:**
- **Anticipation risk**: the agent may prune something a future iteration needed.
  Mitigation (load-bearing): summaries are *indexes, not archives* — the durable
  state lives in files (LOG, briefs, handoffs, the branch itself); a summary
  omission costs a re-read, never a loss.
- Snapshot chain grows monotonically (orientation + Σ summaries) → periodic
  **re-orientation** (fresh fact-only orientation at the new base commit) resets
  it. Trigger: base moved substantially (a merge into the oriented area) or the
  snapshot exceeds a size budget.
- New session id per iteration → the controller must track the chain (nyxloomd
  state; a plain JSON chain-manifest per task in the interim).
- The four prefix invariants (flag, model, effort, toolset) must hold along the
  whole chain or every fork re-bills. TTL matters: warm-cache forks are a bonus
  (rapid iterations, parallel role fan-out inside the TTL window), the *bounded
  context* benefit survives cold.
- Snapshots must be frozen clean: no dangling tool calls, no pending permissions
  (the B1 freeze discipline).

## 4. When to use which

| Situation | Pattern |
|---|---|
| Iterative work with a known task structure (waves, slices, package pipelines) | **(b)** — the plan makes anticipatory pruning safe and snapshots reusable |
| Parallel roles needing the same grounding (implementer + reviewer) | **(b)** — one snapshot, N forks |
| Exploratory/diagnostic work where mid-flight verbatim nuance matters | **(a)** — preserved tail + full-context summarizer |
| External references must keep pointing at one session id | **(a)** |
| Cheapest possible long-haul | **(b)**, falling back to (a) when an iteration can't reach a clean boundary |

They compose: a (b)-chain worker that hits an *unplanned* deep-dive mid-iteration
can use (a) once to survive it, then return to the chain at the next boundary.

## 5. Validation plan (none of these are done)

| id | test | oracle |
|---|---|---|
| V1 | (a) full cycle on a real worker: threshold → checkpoint → controller compacts with the agent's own prompt → resume → next work item | post-resume work correctly uses pre-compact facts (seams, decisions); no re-derivation turns; usage shows small context |
| V2 | (a) designed-vs-auto A/B: same transcript, default auto-compact vs agent-authored prompt | blind grading of the two summaries against a checklist of the facts the NEXT item actually needed |
| V2b | (b) distilled-brief quality: orientation session → brief → fresh seeded snapshot; a probe task run from the snapshot vs from the raw orientation session | equal task performance at ~5-7× less re-read cost; brief omissions surface as measurable re-reads |
| V2c | tiered orientation: haiku/sonnet-authored brief vs premium-authored brief, same task, premium worker | equal worker performance; brief-lint catches the cheap model's omissions; premium orientation cost collapses to one 20-30k creation |
| V3 | (b) 3-iteration chain on a real task | per-iteration cache_creation ≈ summary size (warm) or ≈ snapshot size (cold), never ≈ transcript; final work product correct |
| V4 | (b) parallel forks: implementer + reviewer from the same mid-chain snapshot | both pure-reuse (within TTL); reviewer independence preserved (no leaked implementer conclusions in the snapshot) |
| V5 | (b) anticipation failure injection: summary deliberately omits a fact the next iteration needs | agent recovers via durable files at bounded cost (re-read, not restart); measures the mitigation |
| V6 | re-orientation trigger: chain past the size budget / base moved | fresh orientation cheaper than continuing the chain (measure crossover) |
| V7 | `--model` on the compact invocation: which model summarizes? | usage/modelUsage in the compact result names the summarizer; if steerable, cheap-model compaction for (a) |
| V8 | cross-CLI survey: do codex/opencode/amp expose resume+fork+compact equivalents? | per-CLI mechanism table (seed: badlogic gist); decides AGENTS.md vs CLAUDE.md placement (§7) |
| V9 | cost accounting harness: parse usage from session JSONL per pattern | automated per-agent cost report (feeds the nyxloom dashboard) |

V1/V3 are the gate for adoption; V2/V5 justify the "designed" part; V7/V8 shape
placement and the service.

## 6. Adoption plan

- **Phase 0 (now):** run V1–V5 on a sandbox task in nyxloom's own repo (it is a
  registered project and can dogfood). Keep results in this doc's §5 table.
- **Phase 1:** B46 service grows the two verbs: `compact <sid> --prompt-file`
  (pattern a) and `advance-chain <task>` (pattern b: mint snapshot, fork worker,
  update chain-manifest). nyxloomd dispatch policy chooses per task shape
  (routes/policy config, not hardcoded).
- **Phase 2:** distill the proven loop into copyable rules: → `AGENTS.md` if V8
  shows cross-CLI mechanisms exist; → `CLAUDE.md` (per-project) if Claude-only.
  The rules reference this doc; mechanics stay here so the rules can't go stale
  on numbers.
- **Phase 3:** per-agent cost metrics (V9 harness) on the dashboard; thresholds
  tuned from data instead of the initial ~300k guess.

## 7. Placement rule (answering "where do the rules live")

nyxloom is the **home of the mechanism** (service, chain state, dispatch policy,
this doc). Projects receive only the distilled **discipline** (when to
checkpoint, what a brief must contain) — dstdns already carries the interim
version in `CLAUDE.md`. If V8 proves the mechanisms portable across CLIs, the
discipline is promoted to each repo's `AGENTS.md`; otherwise it stays in
`CLAUDE.md` files, and other-CLI agents simply get pattern (b)'s
successor-spawn-from-files fallback, which needs no CLI support at all.

---

**Measurement log:** run data lives in [design-context-lifecycle-experiments.md](design-context-lifecycle-experiments.md) — one pre-registered E-NNN entry per run.
