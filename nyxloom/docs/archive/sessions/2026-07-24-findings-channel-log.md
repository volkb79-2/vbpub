# Findings channel (option C) — build log · 2026-07-24

Operator-review log for the findings-channel epic (continuation session after the
benchmark-ingest epic — see the sibling `2026-07-24-benchmark-fanout-log.md`).
Controller-driven fan-out: fully-contracted specs → deepseek-flash/luna in
isolated worktrees → SOLO gate → review → `--no-ff` merge.

## The decision (operator, 2026-07-24): option C
The system→user **findings** channel surfaces advisory insights that are neither
task events nor blocking decisions (those are `DECISIONS-INBOX.md`). Operator chose
**option C**: a typed kind-registry drives SPEC-13-safe pushes for enumerated
kinds, PLUS a generic free-text card for everything else.

### Architecture — event-first (not files-first like decisions)
`DECISIONS-INBOX.md` is files-first because decisions are human-owned and two-way.
Findings are **machine-generated, one-way (system→user), and structured** — their
value is in fields (model-ids, metrics, scores) that must survive verbatim to build
a SPEC-13-safe push. So a finding is recorded DIRECTLY as a typed
`FINDING_RECORDED` event (payload = `{kind,title,body,fields,severity}`), riding the
existing event-log → SQLite → greppability → dashboard plumbing. Precedent:
`ARTIFACT_REGISTERED` (a projection-less informational event).

### How option C maps to the SPEC-13 push boundary
`findings.FINDING_KINDS` marks each kind `pushable` with a FIXED, code-owned
`push_template` over TYPED `required_fields`. Pushable kinds (`cost_crossover`,
`model_near_equivalent`) → SPEC-13-safe push (template + typed fields only, never
the free-text title/body). The `generic` kind is `pushable=False` → dashboard-only
card (free-text renders in the UI, html-escaped, where the push boundary doesn't
apply — exactly like the redacted `blocked_reason` preview).

## Package plan (non-overlapping files, FN-1 first then a parallel fan-out)
| pkg | scope (touch) | builder | depends |
|---|---|---|---|
| FN-1 core | findings.py (new), types.py (+FINDING_RECORDED), test_invariants.py, test_findings.py | controller inline (contract owner) | — |
| FN-2 notify | notify.py, config.py, test_notify.py | deepseek-flash (reasonix) | FN-1 |
| FN-3 dashboard | render.py, test_render.py | deepseek-flash (reasonix) | FN-1 |
| FN-4 cli | cli.py, test_cli.py | deepseek-flash (reasonix) | FN-1 |
| FN-5 auto-emit | capability_map.py + refresh wiring | controller inline (central) | FN-1, FN-4 |

FN-2/3/4 touch strictly disjoint source + test files → true parallel implementation;
gates still serialize (concurrent test-runners OOM → SIGKILL-137).

## Merge log
| main commit | package | builder | gate | pro-max review |
|---|---|---|---|---|
| `da1fe36d` | FN-1 findings channel core (option C) | controller inline | 58/58 diff-cov, suite green | (pre-reviewer) |
| `d44f5ade` | FN-2 findings push wiring (SPEC-13) | deepseek-flash/reasonix | 12/12 diff-cov, suite green | (pre-reviewer) |
| `a839e3b9` | FN-3 findings dashboard page | deepseek-flash/reasonix | 20/20 diff-cov (after 1 coverage-resume + 1 minor-fix), suite green | CLEAN (three-dot); 1 minor→fixed |
| `d5aa6054` | FN-4 findings CLI verbs | deepseek-flash/reasonix | 48/48 diff-cov (after 1 coverage-resume), suite green | CLEAN, no findings |

**✅ ONE-WAY CORE COMPLETE + LIVE (main `d5aa6054`).** Live smoke test (real CLI, no
monkeypatch, isolated NYXLOOM_STATE, in the test-runner): `nyxloom finding record
--kind cost_crossover ...` → `recorded F-demo-1`; `nyxloom finding list` → the row with
`push=yes`. The full record → SPEC-13 push → dashboard card → CLI loop works end-to-end.
Remaining: FN-6 (interaction bridge, in flight), FN-5 (auto-emit cost-crossover).

### Cache-hit across the RESUMED reviewer session (climbs as context accumulates)
| review | tok in | cached | hit% | ¥ (≈$) |
|---|---|---|---|---|
| FN-3 #1 (fresh) | 51617 | 50816 | 95.8% | ¥0.0123 ($0.0017) |
| FN-3 #2 (resume) | 54438 | 52608 | 96.6% | ¥0.0147 ($0.0021) |
| FN-4 (resume) | 66860 | 66688 | **99.7%** | ¥0.0155 ($0.0022) |
Verdict on the operator's question: **session reuse works** — cache-hit rises toward
~99.7% as the session grows, a max-effort pro review costs ~$0.002/package, and
deepseek-v4-pro reviews with real rigor (verifies escaping/edge-cases/non-hollowness,
steers correctly on a diff-range correction, no fabricated concerns). The one caveat is
load-bearing: **it needs the merge-base (three-dot) diff and a controller validation
layer** — with a two-dot diff it produced a confident, damaging-if-executed false blocker.
Implementer coverage misses (FN-3, FN-4) were caught by the mechanical GATE, not the
reviewer — the two layers are complementary.

## Parallel fan-out result (deepseek-flash-high via reasonix)
All three self-committed cleanly, no BLOCKED, touched EXACTLY their scoped files:
| pkg | commit | steps | cost (reasonix `-metrics`) | notes |
|---|---|---|---|---|
| FN-2 notify | `209fd7ad` | 18 | — | SPEC-13 leak-test present (sentinel-in-free-text absent from push) |
| FN-3 dashboard | `48499e8b` | — | **¥0.0020 (~$0.0003)** · 58624/58664 tok cached (99.9%) | one hallucinated date (`2026-07-30`) fixed in-worktree → `f45f855f` |
| FN-4 cli | `12c453ad` | 29 | — | verb reg + dispatch + handlers exactly to spec |

The FN-3 cost line is the cost-optimization thesis in miniature: a full
render-page + tests package for ~$0.0003 at 99.9% prompt-cache hit. Controller
review caught the one cosmetic defect (a wrong date in a comment — a review-checklist
item) before the gate; everything else was to-spec on the first pass.

## Interactivity refinement (operator, mid-build): findings are NOT one-way
Operator direction: a finding must be something the user can START AN INTERACTION
on — "a chat that will eventually turn into some action being done, doc changed".
So findings are the SEED of an interaction, not a dead-end notification. This
composes with the existing chat/action machinery rather than duplicating it, and
becomes **FN-6** (built after the one-way core lands).

## Reviewer experiment — persistent deepseek-v4-pro @ max (operator ask 2026-07-24)
Trial: run a SECOND-opinion reviewer (deepseek-v4-pro, effort=max) on each gate-green
branch before merge, RESUMING one reasonix session (`-c`, stable `-dir /workspaces/vbpub`)
so its rubric + codebase context stay cached; controller keeps final merge authority.

**Cache-hit (the headline metric):** session reuse holds **~96–98%** prompt-cache hit,
even on the FIRST review (reasonix's own system/tool context caches provider-side within
the multi-step agent loop), and stays there across a RESUMED turn:
| review | tok in | cached | hit% | ¥ (≈$) | out (reasoning) |
|---|---|---|---|---|---|
| FN-3 #1 (fresh session) | 51617 | 50816 | 95.8% | ¥0.0123 ($0.0017) | 1438 (928) |
| FN-3 #2 (RESUMED turn) | 54438 | 52608 | 96.6% | ¥0.0147 ($0.0021) | 1313 (1100) |
A max-effort pro review costs ~$0.002/package. Cheap enough to run on every package.

**Review QUALITY of deepseek-v4-pro:** genuinely good on the ACTUAL code — it verified
the XSS-escaping oracle line-by-line, confirmed edge-case handling (empty/broken-project/
missing-task_id), and explicitly certified the tests as behavioral-not-hollow (not a
rubber stamp). It also surfaced a real (if minor) maintainability gap the spec's own
oracle missed (findings.html absent from the canonical page-list test).

**THE load-bearing finding — reviewer diff-scoping + why controller oversight stays:**
The reviewer's first pass raised a confident **[blocker]** claiming FN-3 "removes the
FINDING_RECORDED notify handler + push_classes" and told the implementer to "strip the
dead push infrastructure from findings.py". FALSE POSITIVE — an artifact of the diff
RANGE I gave it: a TWO-dot `main..branch` diff shows a sibling's already-merged work
(FN-2, merged after FN-3 forked) as if this branch DELETED it. Had I forwarded that
blocker blindly, the implementer would have deleted FN-1's real push registry and broken
FN-2. Fixes: (1) reviewer must use the **THREE-dot / merge-base** diff `git diff
main...branch` (matches the coverage gate's own scoping); (2) **a controller validation
layer over the reviewer is non-negotiable** — a strong reviewer with a wrong diff-window
produces confident, damaging-if-executed findings. Given the three-dot correction on a
RESUMED turn, the reviewer immediately revised to VERDICT: CLEAN — it is steerable.

**Workflow (operator-corrected):** code-quality feedback flows reviewer→implementer
(forward the reviewer's OWN findings verbatim to a resumed implementer session), NOT
controller-authored. Controller = oversight: reject reviewer false-positives, fix the
harness, decide merges. (Mechanical GATE output — coverage %, pass/fail — is objective
and relayed as-is; that is not a "review opinion".)

**Implementer reuse (operator ask):** serial packages (FN-5, FN-6) — since gates
serialize anyway — will be driven from ONE resumed implementer session, same cache
rationale as the reviewer.

## FN-6 design (finding → interaction/action bridge) — reuse, don't reinvent
`intake_chat.advance_intake(cfg, project, intake_id, user_text) -> str` already
launches-or-resumes an interactive agent turn whose reply auto-opens `D-NNN`
decisions (`PRODUCT_CALL:` lines) and persists a carve-ready backlog item
(`BRIEF:` line). So "promote a finding to action" = allocate an `intake_id`
(`new_id("intake")` — the same path-traversal/XSS-safe id the `/api/intake`
surface already constrains), seed `advance_intake` with a FIXED-template message
built from the finding, redirect to `intake.html`. The finding is the seed; the
whole chat→decision→carve→doc-change pipeline downstream is reused verbatim.
(SPEC-13 note: §13 governs notification PUSHES, not internal agent prompts —
seeding an intake with the finding's free-text is fine, exactly like a human
pasting it into the intake box.) Touch: `render.py` (a "Discuss / Act" button per
card), `daemon.py` (POST `/api/finding/promote`, added to the constrained POST
set). daemon.py's API surface is security-sensitive → luna@high + careful review.

## FN-5 design (token-spend tie-in — the reason findings matter for cost)
The catalog from the benchmark-ingest epic already knows every model's score per
metric AND its price. A `cost_crossover` detector over that catalog auto-surfaces
"model X matches model Y on metric M at ~R× cost" — a direct, actionable
token-spend-optimization signal pushed to the operator. This is why the findings
channel is not just UI polish: it closes the loop from *the catalog we built* to
*a cheaper routing choice the operator can act on*. Wired into
`capability-map refresh` (real run only), it emits one pushable `cost_crossover`
finding per detected pair.
