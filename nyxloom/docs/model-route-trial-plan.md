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
| 0 | `openrouter/poolside/laguna-xs-2.1:free` | Lowest cash-cost configured route; an availability/scope probe, not a presumed full trial. | Configured and previously provider-probed; re-probe required. |
| 1 | `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | Free route with substantially more capacity/context. | Configured and previously provider-probed; re-probe required. |
| 2 | `openrouter/qwen/qwen3.7-plus` | Large-context paid OpenRouter coding candidate. | Catalog-verified 2026-07-26; route added, live probe required. |
| 3 | `openrouter/xiaomi/mimo-v2.5` | Low-price, large-context paid OpenRouter candidate. | Catalog-verified 2026-07-26; route added, live probe required. |
| 4 | `gpt-5.6-luna` at `low` via Codex | First OpenAI low-effort floor. | Dedicated Codex route added; live probe required. |
| 5 | `gpt-5.6-luna` at `medium` via Codex | Next OpenAI effort if Luna-low misses the oracle. | Requires a distinct Codex route/probe. |
| 6 | `gpt-5.6-terra` at `low` via Codex | Next low-effort paid check; DeepSWE's 24% is a prior, not an automatic rejection for narrow test packages. | Requires a distinct Codex route/probe. |

Laguna XS has a deliberately early abort: after the no-edit route probe, stop
without a second package on the first runner/worktree breach, out-of-scope read,
empty/non-causal test proposal, provider refusal, or rate-limit exhaustion. It
is not failed merely for being cheap; a clean first package earns the ordinary
second package. `poolside/laguna-m.1`, `x-ai/grok-4.5`, and `z-ai/glm-5.2`
remain **probe candidates**: exact OpenCode slugs, availability, price, context,
and effort semantics must be verified without a generation before they join the
ladder. Sonnet 5 Medium is deferred: the captured P51 result cost about USD
4.53 for a much larger task, so it cannot satisfy this experiment's hard budget.
The configured free Laguna model is `laguna-xs-2.1`, not Laguna M.1; do not
conflate them. **OpenAI models always use Codex, never OpenRouter.**

## OpenRouter rate-limit handling

For an OpenCode/OpenRouter route, a provider `429`, quota, or temporary
availability refusal is a `LIMIT`, not a model-quality failure and not an
invitation to start a fresh session. Preserve the session id and worktree; wait
30 seconds, then 60 seconds, then 120 seconds (at most three resumes), and
resume the same session with the same bounded handoff. Record every wait and
provider message. Stop as `LIMIT_EXHAUSTED` after the third refusal or when the
budget reserve cannot fund a resumed call. Do not test a competing route while a
free route is merely backoff-pending: the experiment stays serial.

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
