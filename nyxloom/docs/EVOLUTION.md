# Evolution from the md-file workflow

Status: **design / pilot**. Draft 1 needed a *migration* (importer → drift
audit → shadow → cutover) because it introduced a second store. Draft 2 needs
an *evolution*: the md files remain the store, so every step below changes one
duty in place, is individually rollback-able ("stop the timer / keep carving
by hand"), and never leaves the repos unusable without the tool.

## Artifact mapping (what becomes what)

| Today | Draft 2 | Change class |
| --- | --- | --- |
| v2 §7 blockquote header | YAML frontmatter, schema-validated | mechanical syntax conversion (M0 tool) |
| Handoff body, LOG/REPORT/SELFREVIEW md | unchanged | none — still the human/agent contract & narrative |
| REPORT prose as completion signal | + `receipt.json` (typed result, oracle results, usage) | one template edit; wrapper writes it automatically from M2 |
| Controller slot table (chat replies) | dashboard index + `status` | replaced at M1 |
| `controller-dispatch-<date>.md` seed docs | `routes.toml` + per-attempt route snapshot events | retired at M2 (kept as historical evidence) |
| Sonnet controller session + heartbeats | `nyxloom tick` | retired at M2 |
| v2 §5.2/§5.4 resume + stall prose | route adapter templates + tick logic | absorbed at M2; prose shrinks to rationale |
| `.CARVE_LOCK` / `.STACK_LOCK` | flock leases | retired at M4 (read-only honored until then) |
| DECISIONS-INBOX.md | unchanged file; tick ingests status lines; `decide`/`discuss` + push added | augmented at M3 |
| BACKLOG.md / ROADMAP.md / gap-analysis | unchanged (product truth); carve admission + ratchet read them | policy only |
| `implementation-benchmark-P51.md` addenda | dashboard quality pane (living per-tier/route table) | augmented at M2; doc keeps methodology |
| `controller-workflow-v2.md` | stays the *role/protocol* rationale; operational sections marked "absorbed by nyxloom §…" as each milestone lands | shrinks over time |
| dstdns `docs/ai-dev/controller-workflow.md` | project deltas move into `.nyxloom/project.toml` + project adapter; doc keeps the narrative why | shrinks at M4 |

## Step-by-step

1. **Now (no code):** adopt two prose rules that pay immediately and de-risk
   later steps — (a) implementers also write `receipt.json` next to the
   REPORT (template addition); (b) new carves use frontmatter (converter
   arrives in M0; blockquote headers remain valid input until M2).
2. **M0:** lint gates carve commits in both repos. Manual workflow otherwise
   unchanged. Rollback: remove the pre-commit hook.
3. **M1:** dashboard runs read-only beside the live Sonnet controller for at
   least one wave; disagreements are adapter bugs to fix, not authority
   questions (operator truth wins). Rollback: stop rendering.
4. **M2:** tick takes dispatch/monitor/collect for mutex-free packages; the
   controller session is not started for the next wave. The old launch
   command (`claude --model sonnet … dispatch doc`) remains a documented
   fallback for one milestone. Rollback: stop the timer, relaunch the
   controller with a hand-written dispatch doc.
5. **M3:** tick assembles waves and launches frontier review legs; merge stays
   frontier/human. Decisions get push + `decide`. Rollback: assemble packets
   by hand (the packet format is unchanged from v2 §6).
6. **M4:** dstdns onboards fully; marker locks retired; multi-project caps on.
   Rollback per project: deregister it; its md workflow still stands alone.

## Coexistence and authority rules during evolution

- Until a milestone's exit evidence lands, the **existing workflow document is
  authoritative for that duty** (mirrors draft 1 MIGRATION §1).
- The tool never edits product truth (specs, ROADMAP, DECISIONS-INBOX bodies)
  — at any stage, only humans and frontier sessions do (inherited non-goal).
- Legacy artifacts are honored read-only, never rewritten in place: history
  stays greppable where it always was.
- Worktrees, dirty state, and user changes are preserved at every step
  (inherited Git-adapter rule: no destructive resets, refuse ambiguous state).

## What happens to draft 1

Draft 1 remains the reference for the domain model, review/merge invariants,
security boundary, and the daemon end-state (ARCHITECTURE §9 graduation).
If the pilot falsifies draft 2's bets — e.g. tick latency actually hurts, or
flock leases prove insufficient — draft 1's daemon is the designed fallback,
and everything durable (events, statefiles, frontmatter, leases directory) is
forward-compatible input to it by construction.
