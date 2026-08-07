# Measurement protocol — the controller/implementer loop

> Started 2026-08-06, during the assay series. `WORKFLOW.md` describes the loop;
> this file records what it actually costs and what it actually catches. Both
> halves matter: a loop that is cheap and catches nothing is not a bargain.

## Why measure at all

Three claims were made about this loop before any of it ran, and all three were
guesses:

1. that re-orientation dominates cost;
2. that a snapshot/restore lineage would remove most of it;
3. that a pre-flight phase pays for itself.

Two of the three have now been measured and one estimate was wrong by ~8x — in
both directions, at different times. Hence this file.

## What to record, per agent

| Field | Source |
|---|---|
| tokens (cumulative) | the task-notification's `subagent_tokens` |
| tool uses | same |
| wall clock | same (`duration_ms`) |
| transcript bytes / turns | `~/.claude/projects/<cwd>/<session>/subagents/agent-<id>.jsonl` |
| phase | orientation / implementation / brief / controller-repair |
| defects found, and by whom | see "Quality" below — this is the half that is easy to skip |

## Cost so far

| Agent | Phase | Tokens | Tools | Wall | Transcript |
|---|---|---|---|---|---|
| P01 pre-flight | orient + investigate + report | 91,682 | 27 | 7:35 | — |
| P01a | orient + implement + self-review | 199,278 | 91 | 39:14 | — |
| P01a (brief turn) | successor brief | +2,252 | +2 | — | 1,148,552 B / 252 turns |
| P01b | **orientation only** | **142,198** | **32** | **6:49** | **667,376 B / 85 turns** |
| claude-code-guide | snapshot feasibility | 54,920 | 24 | 2:20 | — |
| snapshot probe ×2 | trivial resume, 0 tools | 57,823 / 57,888 | 0 | — | — |

**External reviewer (gpt-5.6-sol, high effort) — a SEPARATE accounting.** These
are the model's own footer figures. They are a different model and tokenizer and
a different budget; they are **not** comparable to the rows above and must never
be summed with them.

| Run | Purpose | Tokens |
|---|---|---|
| 1 | adversarial review of ten outstanding handoffs | ~? (footer not captured — record it next time) |
| 2 | remediation guidance, pushbacks, design verdict | 391,430 |
| 3 | contract repair + reissue the whole series | 965,358 |

Round 3 produced 4 commits, 53 files, +2843/−1316, and a 13-package reissued
series that validates against nyxloom's schema. Round 1 found 23 confirmed
defects. For comparison the two in-house readiness passes found 3 between them.
**The most expensive single check in the run was also by far the most
productive**, which is the opposite of what the cost table alone would suggest.

### The number that changed the design

**Orientation alone costs ~142k tokens.** The raw documents an implementer must
read total ~17k (README 1.1k + DESIGN-GUIDE 8.3k + decisions 5.9k + handoff
1.8k). The 8x multiplier is **turn accumulation**: 85 turns each re-sending a
growing context.

This invalidated two successive controller estimates. The first (~1M across the
series) was right by luck; the correction to "~250k of content" measured the
wrong thing — document bytes rather than spend. Record spend, not content.

Consequence: at cache-read rates a restored S0 costs ~14k against ~142k fresh,
so the lineage saves ~128k per package. That is what justifies the snapshot
machinery, and it was not knowable without measuring.

### Resume cost

A trivial resume (0 tool uses, two-line reply) re-sent ~57.8k — the whole
accumulated context. Billed at cache-read that is ~5.8k. **Report both numbers
or the comparison is meaningless**: raw tokens make a lineage look ruinous and
cached cost makes it look free, and neither alone is the bill.

## Snapshot / restore — verified, with its own protocol

Mechanism confirmed empirically on a disposable agent, not assumed:

1. `cp` the agent's `.jsonl` (the `.output` path handed back by the tool is a
   **symlink** to it) → S0.
2. Resume the agent with anything. Transcript grows (66 → 69 lines).
3. `cp` S0 back.
4. Resume again and ask what it remembers: it reported **2 messages** and quoted
   the ORIGINAL task. The intervening turn was gone.

So restore works. Caveats that are not observable from the controller and must
be assumed rather than checked: the cache is keyed on **(model, effort, prefix)**,
so any change to either silently drops to full price; and the JSONL format is
documented as internal and unstable across versions.

**Time to snapshot matters and should be recorded**: P01b's orientation took
**6:49** before it could be taken. That is the cost of establishing a lineage,
paid once.

### S0 design — a flaw found immediately

The S0 taken for P01b contains **P01b's handoff, its reading of `errors.py`, and
its implementation plan**. All three are package-specific and become dead weight
in every later restore. A reusable S0 must stop BEFORE any handoff:

> series README → DESIGN-GUIDE → decisions.md → the brief chain → a look at the
> existing source tree → **stop**.

Each package then appends only its own handoff after restore. The P01b snapshot
is therefore a valid measurement and **not** the reusable base.

## Quality — what each phase actually caught

The half that justifies the loop, and the half most easily skipped.

| Phase | Found |
|---|---|
| **P01 pre-flight** (before any code) | 3 blockers, 7 ambiguities — all defects in the SPEC, none in the plan. Including an oracle that **could not fail** (`grep -rn` prefixed every line with a path containing `assay`, and the `-v` alternation contained `assay`; verified passing clean on a file importing `requests`, `flask` and a function-level `boto3`). |
| **P01a self-review** | Its own dependency-purity test was partially vacuous — installed with a `PYTHONPATH` exposing host site-packages to pip's resolver, so a declared runtime dependency installed clean. Found only by mutating the thing the test existed to catch. |
| **Controller review of P01a** | One interpretation overruled (A-062); one test **passing for the wrong reason** (the surplus guard rejected before the type check ran); 4 uncovered branches, closing which found a real boundary (an empty judge table is the only one an R0 lane may carry). |
| **P01b orientation** (before any code) | A **carving defect**: only P01b and P09 could touch `verdict.py`/`schemas/**`, yet P04, P05, P08 and P10 all need claim payloads there. A **wrong sentence in DESIGN-GUIDE §6**: nyxloom's `_Serde.from_dict` rejects unknown keys, so "consumes by reading six keys" can only mean cherry-picking. An **oracle demanding the impossible**: O4's rigor-coverage clause needs cross-instance comparison, which draft 2020-12 cannot express. |

**Pattern worth naming:** every phase that ran BEFORE code was written found
defects in the specification, not in the implementation. The pre-flight and
orientation phases are not gates on the implementer — they are the cheapest
available review of the controller's own work.

### Caveat that limits every row above — the readiness evidence is contaminated

**All of it was produced at Opus.** In nyxloom's ladder that is `review-3`
("review + carve authority"), not an implementation tier: implementation routes
cheap-first through `implement-1` (haiku, deepseek-high, terra-med) and
`implement-2` (sonnet5-high, luna-high). So the table above does NOT establish
"a readiness pass finds spec defects". It establishes "a REVIEW-TIER model
asked to assess a handoff finds spec defects", which is a much weaker and much
less surprising claim.

The specific findings make the gap concrete. Disproving DESIGN-GUIDE §6 required
reading `nyxloom/src/nyxloom/types.py` and noticing that `_Serde.from_dict`
rejects unknown keys. Finding the carving defect required reading `scope.touch`
across **all ten** handoffs and intersecting it with what four later packages
need. Neither is plausible band-1 work.

Two consequences, both recorded in nyxloom's backlog:

1. **Handoff review belongs to the reviewer, not the implementer.** It must not
   depend on the implementer's capability at all.
2. **The readiness pass is still worth keeping, for a different reason at each
   tier.** At review-3 it is a spec review. At implement-1 it is *capability
   triage* — a cheap model reporting "I do not understand O3" before an
   implementation is bought is the BLOCKED escape hatch fired early, which is
   most valuable precisely where the implementer is cheapest.

Until a readiness pass is measured at band 1, the cost rows here transfer and
the quality rows do not.

## Tooling caveats that distort a measurement if you do not know them

- **A backgrounded CLI's completion signal reports the WRAPPER, not the work —
  and the cause is fixable.** Running `codex exec` under `nohup … &` *inside* a
  `run_in_background` Bash call produced a "completed, exit code 0" notification
  within seconds while the model ran another ~25 minutes; the output file held
  only the echoed prompt. **The `&` detached the CLI from the tracked shell, so
  the harness watched the wrapper exit.** Drop the `nohup`/`&` and let
  `run_in_background` do the detaching — then the CLI itself is the tracked
  process and the notification is genuine. Verified both ways: spurious twice
  with `nohup … &`, correct once without.
- **The 10-minute foreground tool cap kills a long CLI run mid-flight.** The
  first attempt at the adversarial review died at exactly 10:00 with exit 143.
  Long external-model runs must be backgrounded, which then imports the caveat
  above.
- **An external reviewer's cost is not in the token fields above.** `codex exec`
  reports its own usage in an output footer; it does not appear in any
  `subagent_tokens`. A protocol that sums only the notification fields will
  silently omit the most expensive review in the run.

## Open questions this protocol should answer

- Does a restored S0 measurably hit the cache? Not observable from the
  controller today; the usage field is a single token count.
- Where is the lineage crossover in practice — at what accumulated size does a
  fresh orientation beat a cached restore?
- Do successor briefs measurably reduce the next package's orientation cost, or
  only improve its quality? Requires one package oriented WITH the brief chain
  and one WITHOUT, at comparable difficulty.
- Does brief length stay bounded under the 500-word budget as the chain grows?
