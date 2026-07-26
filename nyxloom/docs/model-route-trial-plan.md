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

### Harness policy

Prompt text is not a capability boundary. The P115/P116 probes showed that a
cheap route can read outside an explicitly frozen context before it makes a
single edit. Every later OpenCode package therefore starts with a
**per-worktree `opencode.json` capability capsule**: deny reads except the
handoff and its explicitly named source/test context, deny edits except the
nominated test and receipt files, and deny shell, network, task delegation,
and external directories. The controller alone executes the declared
`tester-unified` commands and records their raw evidence. The capsule is
untracked trial harness configuration, never product configuration, and is
removed after the trial.

This first hardening pass is a **draft-floor probe**, not a claim that the
route is already autonomous: a route cannot become `QUALIFIED` until its
second package also performs the declared self-review and supplies correct
evidence under a safely allowlisted runner. It can, however, be rejected
cheaply if it cannot make a causal, gate-clean test under an intentionally
small, controller-attested context capsule.

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
| 1 | `openrouter/poolside/laguna-m.1:free` | Larger free Poolside candidate at the same known 262K context tier. | Catalog-verified 2026-07-26; route added, live probe required. |
| 2 | `openrouter/inclusionai/ling-3.0-flash:free` | Additional free coding/tool-use candidate. | Catalog-verified 2026-07-26; route added, live probe required. |
| 3 | `claude/haiku` at `high` via Claude Code | User-requested low-cost Claude implementation floor. | Route resolves to `claude-haiku-4-5-20251001`, but CLI authentication is required before an implementation probe. |
| 4 | `claude/sonnet` at `medium` via Claude Code | User-requested Claude comparison at a higher capability tier. | Route added; authenticate Claude Code, then perform alias/effort probe and USD-meter reserve. |
| 5 | `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | Free route with substantially more capacity/context. | Configured and previously provider-probed; re-probe required. |
| 6 | `openrouter/qwen/qwen3.7-plus` | Large-context paid OpenRouter coding candidate. | Catalog-verified 2026-07-26; route added, live probe required. |
| 7 | `openrouter/xiaomi/mimo-v2.5` | Low-price, large-context paid OpenRouter candidate. | Catalog-verified 2026-07-26; route added, live probe required. |
| 8 | `gpt-5.6-luna` at `low` via Codex | First OpenAI low-effort floor. | Dedicated Codex route added; live probe required. |
| 9 | `gpt-5.6-luna` at `medium` via Codex | Next OpenAI effort if Luna-low misses the oracle. | Requires a distinct Codex route/probe. |
| 10 | `gpt-5.6-terra` at `low` via Codex | Next low-effort paid check; DeepSWE's 24% is a prior, not an automatic rejection for narrow test packages. | Requires a distinct Codex route/probe. |

Laguna XS has a deliberately early abort: after the no-edit route probe, stop
without a second package on the first runner/worktree breach, out-of-scope read,
empty/non-causal test proposal, provider refusal, or rate-limit exhaustion. It
is not failed merely for being cheap; a clean first package earns the ordinary
second package. `x-ai/grok-4.5` and `z-ai/glm-5.2` remain **probe candidates**:
exact OpenCode slugs, availability, price, context, and effort semantics must
be verified without a generation before they join the ladder. Sonnet 5 Medium is deferred: the captured P51 result cost about USD
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

OpenCode may itself retry a free-provider error on a short fixed cadence.
Wrap each invocation in a controller deadline, terminate the *exact session
process* on the first observed refusal, and apply the schedule above outside
the client. Do not mistake the client's hidden rapid retries for the approved
backoff policy. The configured `session_discover` template must also be kept
compatible with the installed CLI: this build's `opencode session list` has no
`--dir` option, so the controller records the session id emitted by `run`
rather than treating discovery output as authoritative.

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

## Trial record

| Route | Package(s) | Verdict | Evidence |
|---|---|---|---|
| `openrouter/poolside/laguna-xs-2.1:free` | P115 only | **UNFIT** | No-edit probe returned `ROUTE_READY` at zero cost. The implementation session then read an unlisted, stale global coverage report before editing; this met the explicit early-abort trigger. Controller terminated it, confirmed a clean worktree, issued no second package, and spent no reviewer. See Topos P115 LOG. |
| `openrouter/poolside/laguna-m.1:free` | P116 only | **UNFIT** | No-edit probe returned `ROUTE_READY` at zero cost. The first turn hit a temporary free-tier limit; controller stopped OpenCode's rapid internal retries, waited 30 seconds, and resumed the same session once. It then read the full 1,186-line test file despite a narrow subsection boundary. Worktree stayed clean; no second package or reviewer. See Topos P116 LOG. |
| `openrouter/inclusionai/ling-3.0-flash:free` | P117 complete; P118 attempted | **UNFIT** | No-edit probe `ses_062f6992cfferqcy2hVCXmo73u` returned `ROUTE_READY` at zero cost. The capability capsule blocked initial `glob`/shell exploration; a controller allowlist omission then prevented the nominated test read and was corrected before resume. Ling added exactly the six requested `TestPathSafety` cases (commit `99d4a063`); controller found and repaired its one assertion-free delegator call in `45e0df6b`. The exact merged result `9183276f` passed focused `tester-unified` (138 tests, 6.12s) and the full isolated gate (2,175 tests, 44.99s); `catalog.py` had `missing_lines=[]` and `missing_branches=[]`. P118 was invoked in a fresh declared worktree under the same session, but OpenCode retained P117's workspace identity and the model attempted stale/wrong P117 and non-existent P118 paths. The firewall denied those external reads; no P118 file changed. This is the L12 relocation failure trigger: rotate/stop rather than prompt through it. No second clean package and no batched reviewer, so the route receives no autonomous implementation credit. |
| `claude/haiku` at `high` | Probe only | **AUTH_REQUIRED** | The installed CLI accepted `--model haiku --effort high` and resolved `claude-haiku-4-5-20251001` with only `Read`/`Edit` exposed under `dontAsk`; the zero-cost probe then returned `Not logged in · Please run /login`. Do not rate model quality, spend the USD budget, or attempt Sonnet until the user/account owner authenticates Claude Code. |
| `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | P120 attempted | **INVALIDATED — controller oracle defect** | The zero-cost probe returned `ROUTE_READY`. The capsule instructed a global `topos.bpf_gate.os.access` `OSError` patch, but that same module object is consulted indirectly by the network-provider `shutil.which` path, so the specified test necessarily crashed outside the intended `try` block. It also failed to list the required `BpfGateReport` constructor fields, inviting an unverifiable synthetic report. Nemotron did attempt a forbidden source read (blocked by the capsule) and then produced tests that failed exactly on both capsule defects; no model-quality verdict is assigned and no repair is applied to its dirty worktree. Re-carve a controller-preflighted P121 with a path-selective `os.access` seam and a complete dataclass constructor before retrying this route. |
