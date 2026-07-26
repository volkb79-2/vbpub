# Cheap implementation-route trials

**Status:** approved 2026-07-26. This is a routing experiment for Topos global
coverage healing, not a contest between parallel implementations. The controller
owns carves, runner policy, gates, evidence, merge, and product-defect repairs.

## Operator decisions

- Free OpenRouter endpoints may receive Topos source and tests. They still get
  the route's no-secrets prompt guard; that approval does not permit sending
  credentials, host configuration, or unrelated repository content.
- The hard provider budget is **USD 1.00 per model route's two-package trial**,
  including retries and one independent review. The meter's billed USD is the
  authority; cached-token counts are diagnostic, not an assumed discount.
- The objective is the lowest *viable* implementation route for bounded,
  repository-aware test creation. It is not the lowest leaderboard price or the
  strongest general coding model.

## Trial unit and oracle

One route gets exactly two sequential, independently mergeable **test-only**
Topos coverage packages. The controller selects comparable residuals (roughly
10–25 executable lines plus reachable branch arcs) in isolated, low-risk
modules. A package that exposes a product defect is stopped: the controller
fixes the defect first and it does not count as a model-floor trial.

Every package supplies a literal frozen residual, an immutable base, a private
worktree, the exact `tester-unified` command, a focused test command, the full
Topos gate, coverage JSON, and an explicit BLOCKED trigger. A model must not
run host Python, substitute the devcontainer, weaken tests, expand scope, or
claim success without both gates. A violation ends that route's trial as
**UNFIT**, even before a code change.

The controller runs the full gate and literal coverage check for each result.
Only after both pass does one resumed DeepSeek Pro review session receive the
complete frozen receipts for the pair. It reviews both commits independently
and reruns their gates; it reports findings but does not edit. This batches
review-cache reuse without turning two commits into one unverifiable review.
The reviewer is itself subject to L12 rotation and controller verification.

Before a paid call, reserve budget for the final review. If the live provider
meter makes the remaining package plus review unable to stay within USD 1.00,
stop and record `BUDGET_EXCEEDED`; do not silently downgrade effort or skip the
review to manufacture a result.

## Candidate ladder

Run serially and stop promoting a route on the first hard-contract breach.
The next candidate starts only after the prior candidate has its two-package
record and review verdict.

| Order | Route | Why it is here | Status before trial |
|---:|---|---|---|
| 1 | `openrouter/poolside/laguna-xs-2.1:free` | Lowest cash-cost configured implementation route; establishes the lower bound. | Configured and previously provider-probed; re-probe required. |
| 2 | `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | Free route with substantially more capacity/context. | Configured and previously provider-probed; re-probe required. |
| 3 | `gpt-5.6-luna` at `medium` | First paid low-effort Codex floor. It has historical operational competence but missed semantic defects on a far harder P51 protocol package. | Requires a distinct route/probe. |
| 4 | `gpt-5.6-terra` at `low` | Next low-effort paid check; DeepSWE's 24% is a prior, not an automatic rejection for narrow test packages. | Requires a distinct route/probe. |
| 5 | DeepSeek Flash High/Max | Retest only after the dispatcher/tool wrapper can mechanically prevent host-runner drift. Prompt wording alone failed that requirement in P113. | Not eligible yet. |

`poolside/laguna-m.1`, `qwen/qwen3.7-plus`, `x-ai/grok-4.5`, and
`z-ai/glm-5.2` are **probe candidates**, not runnable routes yet: exact
OpenCode slugs, availability, price, context, and effort semantics must be
verified without a generation before they join the ladder. Sonnet 5 Medium is
deferred: the captured P51 result cost about USD 4.53 for a much larger task,
so it cannot satisfy this experiment's hard budget. The configured free
Laguna model is `laguna-xs-2.1`, not Laguna M.1; do not conflate them.

## Promotion record

For each package record base/HEAD, residual, diff size, task elapsed time,
input/cache/output and billed USD, retry count, runner/worktree violations,
focused/full gate results, literal residual closure, controller repair time,
and independent-review findings. Apply `UNFIT`, `CONDITIONAL`, `QUALIFIED`,
and `PREFERRED` exactly as defined by [L14](../reference/LESSONS.md#l14--evaluate-an-implementation-route-with-sequential-oracle-bound-packages-a-benchmark-is-only-a-prior).

Public benchmarks only determine ordering: DeepSWE is the per-effort coding
prior; Terminal-Bench is the terminal/tool-use prior; Artificial Analysis can
supply current Terminal-Bench, LiveCodeBench, and SCIcode values when its API
credential is available. The two real Topos packages are the acceptance test.
