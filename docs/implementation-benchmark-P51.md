# P51 Implementation-Agent Mini Benchmark

## Scope and question

P51 (`groop/handoff/P51-daemon-sampling-fanout.md`) is used here as a small,
repository-local implementation benchmark. The benchmark asks whether an AI CLI
agent can turn Groop's request-driven Unix-socket `FrameBroker` into one
daemon-owned, continuously advancing producer with bounded, non-consuming
fan-out, then integrate, test, document, and commit it.

This is not a general model leaderboard. It is one difficult Python systems
task, run once per model/harness/effort combination. Results measure the entire
agent system—model, CLI harness, context selection, tools, environment handling,
and task prompt—not the base model in isolation.

## Why P51 is a useful comparison task

P51 has a compact statement but a large correctness surface. It combines:

- concurrent lifecycle state and a background thread;
- blocking, exhaustion, failure, stop, and timeout behavior;
- atomic `(sequence, frame)` publication;
- bounded history, cursor continuation, and eviction gaps;
- a line-oriented Unix-socket protocol and client polling behavior;
- resource bounds for slow or hostile local clients;
- safe error reporting across a privilege boundary;
- daemon startup ordering around BPF/provider configuration and teardown;
- compatibility with existing attach/status/deployment tests;
- cross-cutting spec, readiness, status, roadmap, measurements, and reports.

Many implementations satisfy the happy path and pass their own tests while
remaining wrong under a blocked `next()`, a source released after stop, an
evicted cursor, a concurrent publication, or a secret-bearing exception. Those
failures require runtime experiments and adversarial assertions, not just code
generation. P51 therefore exercises the same investigation, implementation,
test-writing, refactoring, and terminal discipline that Groop handoffs require.

## Why P51 is harder than most earlier handoffs

Most prior Groop packages were bounded additions: parse a kernel file, add a
metric, render an existing value, add a CLI command, or improve documentation
and acceptance evidence. Their correctness was largely local and synchronous,
and deterministic fixtures could cover the main behavior directly.

P51 instead changes ownership of time and state. It replaces a pull-based
component used by multiple existing callers, and correctness depends on all
interleavings rather than only returned values. It also creates a long-lived
resource boundary: producer thread, handler threads, socket clients, history,
timeouts, and privileged collector errors. A plausible local patch can pass
hundreds of unchanged tests while violating production invariants. This makes
P51 closer to a subsystem refactor than a normal feature slice.

## Reproduction protocol

### Historical base

All original-task variants start from commit `b5ba9af`, before any P51
implementation. Each runs in a separate worktree and branch and may touch only
`groop/**`. No implementation is merged.

The exact original Reasonix prompt was recovered from its persisted session:

```text
Implement Groop P51 from groop/handoff/P51-daemon-sampling-fanout.md. Work only
in <WORKTREE> on branch <BRANCH>; touch only groop/**. Follow
docs/reasonix-controller-guide.md and the handoff exactly. Ensure one
request-independent producer, fresh non-consuming current/history, bounded
fan-out/backpressure, deterministic shutdown, and strong concurrency tests.
Run focused tests, full suite, full-source py_compile; write P51 LOG/REPORT;
commit all work. Do not edit or merge main.
```

Only worktree, branch, model, effort, and harness differ.

### Evaluation rule

An agent's own green suite is evidence, not the verdict. Controller review also
checks:

- actual producer liveness after bounded shutdown;
- whether release after stop can publish;
- terminal-state truthfulness and restart behavior;
- atomic frame/sequence responses;
- explicit eviction-gap propagation through the client;
- raw exception disclosure;
- strict request validation and finite values;
- request/client/response bounds and slow-client behavior;
- daemon startup/failure ordering and thread cleanup;
- truthfulness and completeness of tests and documentation.

The original branches are retained for audit:

| Variant | Branch / commit |
| --- | --- |
| DeepSeek V4 Flash High + Reasonix | `feat/groop-p51-daemon-sampling-fanout` / `f9bcf67` (initial agent result) |
| DeepSeek V4 Pro High + Reasonix | `feat/groop-p51-pro-high-replay` / `f829d17` |
| GPT-5.6 Luna Medium + Codex CLI | `bench/p51-luna-medium` / `26474c9` |
| Claude Sonnet 5 Medium + Claude Code | `bench/p51-sonnet5-medium` / `034c54b` |

## Original-task quantitative results

Token fields are not perfectly comparable across harnesses. Reasonix reports
prompt/cache/completion accounting; Codex reports input/cache/output/reasoning;
Claude reports cache creation/read separately. Cost is the harness/provider's
reported amount unless explicitly labelled an estimate.

| Variant | Durable usage | Reported cost | Agent validation |
| --- | --- | ---: | --- |
| Flash High / Reasonix | approximately 110,676 tokens observed in the controller terminal; the original run did not use `--metrics`, so exact cache split and cost are not recoverable | unavailable | 644 passed, 1 skipped |
| Pro High / Reasonix | 9,688,850 prompt; 9,537,920 cache hit; 150,930 cache miss; 59,179 completion; 114 steps | ¥1.046312 | 650 passed, 1 skipped |
| Luna Medium / Codex | 4,275,994 input; 4,163,584 cached; 17,207 output; 2,670 reasoning | no native per-run cash charge; approximately $0.63 at the then-listed OpenRouter token rates | 627 passed, 1 skipped |
| Sonnet 5 Medium / Claude Code | 170 input; 125,292 cache creation; 9,453,051 cache read; 62,765 output; 98 turns; 856 s | $4.5296523 | 635 passed, 1 skipped |

The interrupted pre-benchmark Sonnet prompt was excluded; it cost $0.1510035
and made no repository changes. Failed OpenCode Luna/Sonnet probes were rejected
before generation and incurred no model usage.

## Original-task qualitative results

### DeepSeek V4 Flash High / Reasonix

The initial result established the main architecture and added 21 focused tests,
but controller review found several high-severity defects:

- `source_error_limit` was ineffective and default producer errors were lost;
- start was racy, join could return while alive, and stop could not interrupt
  production sleep;
- exhaustion was not persistently represented;
- polling replayed the retained tail;
- P47 health integration was lost during reconciliation;
- warnings exposed leaking sockets;
- tests asserted happy-path behavior without proving the lifecycle invariants.

This was the cheapest useful draft, but required substantial controller repair.

### DeepSeek V4 Pro High / Reasonix

Pro was more investigative and self-correcting. During the run it diagnosed a
condition-lock deadlock after the controller interrupted a hung test, replaced
an unbounded busy generator, added sequence metadata, and reconciled eleven
integration failures. Its committed result still had to be rejected:

- `stop()`/`join()` returned with a blocked producer alive, and release could
  publish after stop;
- exhaustion/error left `running=True` with a dead thread;
- raw collector errors were returned to clients;
- history eviction silently skipped frames;
- `current` could pair frame N with sequence N+1;
- startup ordering could leak/race the producer;
- no request-time or concurrent-client bound existed;
- client response sizes and sequence semantics were insufficiently bounded.

Pro showed better recovery behavior than Flash, but not production correctness.

### GPT-5.6 Luna Medium / Codex CLI

Luna produced a smaller implementation and a clean commit. Its own tests and
full suite passed. Controller probes reproduced:

```text
stop_elapsed=0.05, thread_alive=True
seq_after_stop=1
stale cursor 1 -> sequences 4,5 with no gap metadata
frame source failed: TOKEN=topsecret /private/path
```

It also left client polling replaying retained frames, omitted request/client
backpressure, silently clamped several bounds, started collection during socket
construction before later provider configuration, and ignored a failed join.
The native harness handled repository edits and environment recovery well, but
the result was not mergeable.

### Claude Sonnet 5 Medium / Claude Code

Sonnet explored the widest integration surface, added the most focused tests,
and made some better API choices than Luna: `stop()` returned a false join
result instead of silently claiming success, and history responses included an
oldest retained sequence. It still failed the production contract:

```text
stop_elapsed=0.05, joined=False, thread_alive=True
sequence_after_stop=1
cursor 1 -> sequences 4,5; history_start=4 but client ignores the gap
raw TOKEN/path text appears in health and current error responses
started=True while the exhausted producer thread is dead
```

`server_close()` ignored the failed stop, client polling replayed history, raw
exceptions were public, request/client/response resources were unbounded, and
collection began before later provider wiring. The result was the strongest of
the unoptimized native attempts but remained non-mergeable and was by far the
most expensive measured run.

## Original-task ranking and value

No original-prompt variant is production-ready, so rankings must not turn a
green self-suite into a false win.

1. **Sonnet 5 Medium** produced the broadest and most explicit draft, but its
   remaining lifecycle/security failures make $4.53 poor value for this
   under-specified task.
2. **Pro High** showed the best self-correction per controller interaction and
   is preferable to Flash for lifecycle/protocol work, but ¥1.05 still bought a
   rejectable result.
3. **Luna Medium** was operationally competent and inexpensive under a native
   subscription, but its correctness profile closely resembled Pro's unresolved
   gaps.
4. **Flash High** is the best raw drafting price only when controller review and
   repair are budgeted as part of the workflow.

The practical value metric is expected total cost:

```text
agent run + controller review + P(defect) × (repair + rerun + regression risk)
```

For a vague P51 prompt, paying for a stronger model did not remove the dominant
repair term.

## Handoff-design findings

The original handoff named the desired capabilities but left several production
semantics implicit: what deterministic shutdown means for an arbitrary blocked
iterator; whether join failure raises, returns false, or is ignored; how an
evicted cursor is represented; whether raw collector text is safe; what
"bounded fan-out/backpressure" must bound; and when the producer starts relative
to provider mutation.

All four systems made similar omissions despite different models and harnesses.
That pattern is stronger evidence of a task-specification gap than any single
model failure. Cheaper models additionally tended to choose locally convenient
structures—booleans that diverged from actual thread state, silent clamping,
raw exceptions, and protocol fields the client ignored—unless the acceptance
oracle was explicit.

The optimized handoff is
`groop/handoff/P51-daemon-sampling-fanout-optimized-benchmark.md`. It improves
fulfilment by:

- defining lifecycle and terminal-state invariants rather than only methods;
- specifying safe failure and resource-bound contracts;
- requiring atomic publication and end-to-end cursor/gap behavior;
- naming adversarial tests that inspect actual liveness and information leaks;
- constraining context reading to relevant files;
- distinguishing environment failures from implementation failures;
- avoiding a prescribed class/file layout so architecture remains an evaluated
  model decision.

## Optimized-handoff reruns

The optimized handoff is rerun on DeepSeek Flash High and Pro High from code
base `b5ba9af`, with the same optimized handoff file as the only added input.
The reruns produced the following results:

| Variant | Durable usage | Reported cost | Outcome |
| --- | --- | ---: | --- |
| Flash High / Reasonix, optimized | 18,137,426 prompt; 18,006,656 cache hit; 130,770 cache miss; 67,374 completion; 148 steps | ¥0.62565112 | commit `740d8d4`; 32 focused tests and 98 daemon regression tests reported passing |
| Pro High / Reasonix, optimized | no final metrics artifact: the run was interrupted while its broad pytest command remained hung | unavailable | no commit; incomplete working tree |

### Optimized Flash High review

The explicit oracle produced a much stronger first-pass implementation than
the original Flash prompt. It added typed persistent lifecycle states, atomic
sequence/frame publication, a release-after-stop guard, interruptible pacing,
generic public errors, cursor-gap objects, strict scalar validation, bounded
history and response batches, and tests named for all twelve adversarial
scenarios. That is persuasive evidence that handoff precision can buy more
quality than changing models alone.

It is still not mergeable without controller repair:

- `ThreadingMixIn.max_children` does not cap concurrent handler threads, so the
  claimed 16-client bound is not enforced;
- assigning `self.rfile._timeout` does not reliably configure the underlying
  socket deadline, and a request exactly at the read cap is not detected as
  truncated/oversized;
- `cursor_seq < oldest_seq` reports a gap even when `cursor_seq == 0` and the
  oldest retained frame is sequence 1, although no published frame was lost;
- constructor limits are silently coerced with `max()` despite the handoff's
  strict-validation requirement;
- the patch is unusually large (18 files, +2,295/-305) and replaces substantial
  existing documentation and tests, increasing reconciliation risk.

Thus the optimized handoff moved Flash from a cheap architectural draft to a
nearer, explicitly testable implementation, but its self-authored tests still
failed to validate the actual mechanism chosen for several bounds.

### Optimized Pro High review

Pro changed the source contract to a frame-producing callable, updated existing
callers, and added a separate benchmark test module. Its focused daemon tests
passed during the run. However, its broad non-UI pytest command remained alive
without output for several minutes. The controller interrupted it rather than
recording a false pass; this terminated the Reasonix run before it could commit
or write final metrics. The incomplete diff also weakened one intended stale-
cursor test by moving the cursor into the future, avoiding rather than proving
the eviction-gap behavior.

This is a benchmark failure, not evidence that Pro is categorically worse:
one run cannot separate model variance from an implementation-induced hang.
It does show that a stronger prompt and higher-priced model still need bounded
test commands, durable incremental metrics, and adversarial controller review.

### Optimized-handoff conclusion

The optimized Flash result is the clearest result of this mini benchmark. It
cost less than the original measured Pro run and encoded far more of the
production contract, although its much longer run and larger patch consumed
more controller-review surface. The prompt improvement reduced repeated
semantic omissions shared by all four original variants; it did not eliminate
model mistakes about Python server mechanics or test validity.

Recommended policy:

1. Use a contract-rich handoff like the optimized version for concurrency,
   protocol, privilege-boundary, and lifecycle packages regardless of model.
2. Keep Flash High as the default for bounded work when the oracle is explicit
   and controller repair is affordable.
3. Use Pro High for ambiguous reconciliation or recovery work, but do not pay
   the premium merely to compensate for an underspecified handoff.
4. Require `--metrics` from the start, put timeouts around broad test gates,
   and independently test the enforcement mechanism—not only its constants or
   nominal responses.

## Benchmark limitations

- One run per variant does not measure variance or Pass³ reliability.
- Harnesses tokenize, cache, price, and preload context differently.
- Native subscription marginal cost is not directly comparable to API billing.
- The controller knows the reference defects after the first run; only the
  agents remain blind. The optimized task deliberately incorporates generalized
  acceptance lessons, so it measures handoff improvement, not the same prompt.
- P51 is representative of concurrency/protocol work, not UI, parsing, or
  documentation-only packages.
