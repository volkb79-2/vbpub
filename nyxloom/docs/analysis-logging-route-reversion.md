# Analysis: a log record escaping nyxloom's configured route under xdist

Status: **RESOLVED 2026-07-27** · fix `8b7258e6` · canonical rule:
`reference/LESSONS.md` **L19** · local record: `nyxloom-trove/LESSONS.md` **PL10**

> **The title is a misnomer, kept only so existing links resolve. There was no
> route reversion.** structlog's configuration was never reverted: instrumenting
> `_Configuration.__setattr__` recorded 619 and 543 `logger_factory` writes across
> two full runs, **all** of them `_NyxloomLoggerFactory`, **zero** default.
>
> **Root cause.** `tests/test_carver_session_executor.py`'s two O6 tests did
> `monkeypatch.setattr("nyxloom.daemon.log.warning", ...)`. `daemon.log` is a
> structlog `BoundLoggerLazyProxy`, which defines no per-level methods — they are
> synthesised by `__getattr__`, which re-binds against the live config on every
> call. monkeypatch saved that *synthetic* bound method as the "old value" and, on
> teardown, restored it with `setattr` — **materialising it as a permanent instance
> attribute**. `log.warning` then bypassed `__getattr__` forever, using a logger
> frozen to structlog's unconfigured default, for every later test in that worker.
>
> **§(b) below is DISPROVEN and must not be carried forward.** There is no
> production impact: nothing reverts the config, and only a test ever monkeypatches
> the proxy. `nyxloomd` was never at risk of losing file logging.
>
> The material below is preserved as the falsification record — it is accurate
> about what was ruled out, and the renderer evidence was correct and load-bearing;
> it simply pointed at the config when the answer was the object.

## Symptom

`tests/test_daemon.py::test_resume_attempt_emits_warning_attempt_retry`

```
E   assert 0 == 1  +  where 0 = len([])
------------------------------ Captured stdout call ----------------------------
2026-07-27 12:16:56 [warning  ] attempt-retry  attempt=att-retry project=demo resume_n=1 route=fake-cli task=t-retry
```

Emitted, but absent from the configured `log_dir/nyxloom.jsonl`. A **routing**
failure — not a lost write, not a flush race.

## Reproduction matrix (measured, reliable)

| Condition | Result |
|---|---|
| test alone, 3× | pass |
| `tests/test_daemon.py -n 4` | pass |
| full suite **serial** | pass |
| full suite `-n 4` | **FAIL 2/2** (worker `gw3`) |

## The decisive evidence: renderer shape

Both shapes were reproduced in plain Python:

| Route | Output shape |
|---|---|
| nyxloom-configured | `[warning] [daemon] ... msg=attempt-retry ... ts=2026-...T...` |
| structlog **default** | `2026-... [warning] attempt-retry ...` |

**The failure shows the structlog-default form exactly.** nyxloom's processors
necessarily rename `event` → `msg` and emit an ISO `ts` (`log.py:160`,
`log.py:169`); both transformations are **absent** from the failing output.

Conclusion: when `daemon.log.warning()` resolved its lazy proxy, that worker was
using **structlog's default configuration**, not the one installed by the test.

Corroborating: nyxloom's console handler binds `sys.stderr` (`log.py:347`) and
the test passes `console=False`, so our console path cannot have produced a
stdout line. A handler-less *configured* logger doesn't produce it either — at
WARNING, stdlib's `lastResort` writes the processed dict to **stderr**
(verified by probe).

## Falsified — do not revisit

1. `log.configure` handler-swap race — the P27-followon fix is present and
   correct (atomic list rebind, close-after-swap, `log.py:321`).
2. Daemon reconfigures mid-pass — `daemon.py:702/709` is inside `Daemon.run()`;
   the test calls `run_pass()`. Never executes.
3. `paths.logs_dir()` memoised — it is recomputed every call (`paths.py:14`).
4. structlog reset by our code — **no** `reset_defaults()`, no
   `structlog.configure()` outside `log.py`, no `cache_logger_on_first_use=True`
   anywhere; `conftest.py` never touches logging.
5. Test-order pollution inside one process — the serial full suite passes.
6. Cross-worker interference — workers are separate processes and cannot mutate
   each other's structlog or logging objects. The test's dir is per-test
   (`conftest.py:16`) and path lookup is fresh.

Also ruled out for the emission path specifically: no fork and no thread. The
warning is emitted synchronously at `daemon.py:6458`, *before*
`wrapper.launch_detached()` (`daemon.py:6502`), which this test fakes
synchronously anyway (`test_daemon.py:111`).

## TWO defects, not one

### (a) The test's premise is unsound — test-design defect

It reconfigures **process-global** logging and then asserts on a file. That is
only safe if nothing else in the same worker can reconfigure logging
concurrently — and this suite runs `Daemon.run()` threads whose teardown does
`join(timeout=5)` **without asserting termination** (`test_daemon.py:3102`). A
neighbouring test already documents a prior instrumented full-suite failure
attributed to exactly such an outliving thread (`test_daemon.py:3227`).

**Fix:** assert the daemon's *behaviour* — one `warning("attempt-retry", ...)`
with the required fields — through the `daemon.log.warning` seam, which is the
pattern the same file already uses at `test_daemon.py:3242`. File routing and
JSON rendering are a separate contract and belong in `test_log.py` (`:66`).

This does **not** weaken the oracle: the production behaviour under test is
*emission*; persistence through a process-global transport is a different
contract, tested elsewhere.

### (b) ~~Something reverts structlog to defaults~~ — **DISPROVEN 2026-07-27**

> **This section was wrong.** Nothing reverts structlog. Kept only to record the
> mistake, because it was a *plausible* reading that explained every symptom and
> therefore survived a long time.
>
> Measured: `_Configuration.__setattr__` instrumentation logged 619 and 543
> `logger_factory` writes over two full suites — every one `_NyxloomLoggerFactory`,
> none default. A fingerprint captured at the exact moment of the escaping emission
> showed the live config **entirely correct**: right factory, right processors, and
> a handler open on precisely the `nyxloom.jsonl` the failing test then read.
>
> The real cause is a permanently materialised instance attribute on the logger
> proxy (see the header, and canonical **L19**). It is confined to the test suite:
> only a test monkeypatches the proxy, so **`nyxloomd` has no exposure**.
>
> The lesson to keep from this section is methodological, not technical: it
> reasoned from a *correct* observation (the record used structlog's default
> renderer, so it bypassed our chain) to a *wrong* conclusion (therefore the
> configuration must have changed). The alternative — the configuration is fine and
> is simply not being consulted — was never enumerated. When evidence shows a
> subsystem's output is wrong, instrument the **object** that produced it, not only
> the subsystem's state.

**Experiment (needs docker; blocked at time of writing):** a session-start
pytest plugin that wraps `structlog.configure` and `structlog.reset_defaults`,
recording PID, thread ident, and stack on every call, plus a monotonic
generation counter. Snapshot `structlog.get_config()` — `logger_factory` type,
processor identities, wrapper class — and
`logging.getLogger("nyxloom").handlers` + each `baseFilename`, immediately
after the test's `log.configure()` and again at the emission. If the second
snapshot shows `PrintLoggerFactory` / `ConsoleRenderer`, the family is
confirmed and the recorded stack names the caller. Also grep all
`popen-gw3/**/nyxloom.jsonl` for `att-retry` — a hit in another file proves
in-worker rerouting instead.

## Separate latent hazard (suite-wide)

`tests/test_log.py:45`'s autouse teardown destructively removes every handler
from the `nyxloom` logger without restoring the prior set, and leaves
`propagate=False` (`log.py:308`). WARNING+ then leaks via `lastResort` to
stderr; lower levels vanish entirely. **The same destructive pattern appears in
several other test modules.** It does not explain this failure — stripping
handlers cannot restore structlog's `PrintLogger` defaults — but it is unsound
cleanup that should save and restore rather than destroy.

## Credit

Independent diagnosis by codex `gpt-5.6-sol` (effort high), after five
controller hypotheses were falsified. It correctly reported "not determined"
rather than producing a sixth confident-but-wrong mechanism — the behaviour
D-R2b's evidence-discipline axis is meant to select for.
