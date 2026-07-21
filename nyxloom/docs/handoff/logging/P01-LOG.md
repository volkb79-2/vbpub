# P01 — logging core: implementation LOG

Branch: `feat/logging-p01-core` · worktree: `/workspaces/vbpub/.worktrees/logging-p01-core`
Spec: `nyxloom/docs/plan-logging.md` §2, §3 (D-L1/D-L2/D-L3), §4.1, §4.2, §6 (P01).

## Sequence of actions

1. Read `docs/plan-logging.md` in full (§2 logs≠events, §3 decisions, §4.1
   log.py design, §4.2 paths.py, §6 P01 oracle list, §7 rollout note).
2. Read `src/nyxloom/paths.py` (found `state_root()`/`daemon_dir()`) and
   `src/nyxloom/daemon.py` (found the single http_bind `print(...,
   file=sys.stderr, ...)` at the old line ~3734).
3. Created worktree `feat/logging-p01-core` from `main` (622d4cb).
4. Added `paths.logs_dir()` / `paths.nyxloom_log_path()` /
   `paths.daemon_log_level_path()`; added `logs_dir()` to
   `ensure_layout()`'s directory list. Additive only, no existing helper
   touched.
5. Added `structlog>=24,<27` to `pyproject.toml`'s **main** `dependencies`
   (not `[test]`) — it's a runtime dep per D-L1. `<27` (not the prompt's
   example `<26`) because the currently-published latest (26.1.0) is the
   version pip actually resolves and installs into the image; capping at
   `<26` would have pinned to a stale `25.x` for no reason.
6. Added `structlog` to the tester-unified Dockerfile's closure smoke-
   import line (the `import pytest, hypothesis, ... coverage` line).
7. Wrote `src/nyxloom/log.py` from scratch. Design notes not obvious from
   the plan doc alone, worked out by reading structlog 26.1.0's actual
   source (`structlog._config`, `structlog._native`, `structlog._base`)
   rather than assuming API behaviour:
   - **Live `set_level()` requires `cache_logger_on_first_use=False`.**
     structlog's own docstring for `make_filtering_bound_logger` says a
     filtering bound logger "can't [have its level changed] once
     configured" for an *already-resolved* instance. But
     `structlog.get_logger()` returns a `BoundLoggerLazyProxy`, and when
     caching is OFF (structlog's actual default, verified by reading
     `structlog._config._CONFIG.cache_logger_on_first_use == False`), that
     proxy's `bind()` re-reads `_CONFIG.default_wrapper_class` on **every**
     single log call, not just the first. So a module that does
     `log = get_logger(__name__)` once at import time DOES pick up a later
     `set_level()` call with no restart — but only because caching is
     explicitly kept off. This is the single most load-bearing design
     decision in the file (documented at length in `configure()`'s
     docstring) and is directly what D-L3/G2 ("flip to DEBUG live, no
     restart") requires.
   - **Scoping without touching the stdlib root.** A custom
     `_NyxloomLoggerFactory` routes every `get_logger(name)` call to a real
     stdlib channel `nyxloom.<name>` (child of `nyxloom`, never of the bare
     root). `configure()` only ever calls
     `logging.getLogger("nyxloom")` — it never calls `logging.getLogger()`
     (no args) or touches any other logger. `nyxloom.propagate = False`
     stops it from bubbling further up too.
   - **The `logging.NOTSET` trap.** First implementation set
     `logging.getLogger("nyxloom").setLevel(logging.NOTSET)` intending
     "maximally permissive". This is wrong: for a *non-root* logger,
     `NOTSET` means "delegate to the parent chain" (Python logging docs),
     which walked all the way up to the real, untouched stdlib root
     (default level `WARNING`) and silently dropped every INFO/DEBUG/TRACE
     record that structlog's own gate had already let through — caught by
     a local smoke test (record never appeared in the JSONL file) before
     it ever reached the gate. Fixed to `setLevel(1)` (the lowest *real*
     level, distinct from `NOTSET`'s delegate-upward meaning). Left an
     explanatory comment in the source so a future reader doesn't
     reintroduce it.
   - **TRACE(5)** has no stdlib equivalent, so `.trace()` can't reuse
     structlog's `_proxy_to_logger` as-is (it does
     `getattr(self._logger, method_name)(...)` on the wrapped *stdlib*
     `Logger`, which has no `.trace` method — this raised
     `AttributeError` in a smoke test). Fixed by calling structlog's
     documented lower-level extension point (`self._process_event(...)` +
     `self._logger.log(<numeric level>, ...)`, stdlib's generic
     arbitrary-level method) instead — the same pattern structlog's own
     docs describe for custom wrapper classes.
   - `bind()` wraps `structlog.contextvars.bind_contextvars`/
     `reset_contextvars` in a `contextlib.contextmanager` with try/finally
     — nests and fully resets, including on exception, by construction
     (`reset_contextvars` restores each contextvar to its prior Token,
     which for the outermost `bind()` is "unset").
8. Converted the daemon.py print to `log.warning(...)`: added
   `from .log import get_logger` + module-level `log = get_logger("daemon")`
   (minimal supporting lines; scope said "nothing else" meaning no other
   *behavioural* change, not literally zero new lines, since the call site
   conversion is impossible without an import + a logger). Kept the message
   text's `UNAUTHENTICATED` substring for continuity; moved `http_bind`/
   `http_port` from f-string interpolation to structured kwargs (idiomatic
   structlog, and lets the updated tests assert on typed fields instead of
   string-parsing). Left `import sys` in place even though it's now
   otherwise-unused in the file — scope said touch nothing else, and an
   unused import doesn't fail the pytest/coverage gate this package is
   scored against.
9. Updated exactly the 2 named tests in `test_daemon.py`
   (`test_nonloopback_bind_prints_unauthenticated_notice`,
   `test_loopback_bind_prints_no_notice_THE_NEGATIVE`) to call
   `log.configure(level=log.INFO, log_dir=tmp_state/"logs", console=False)`
   and read back the JSONL record instead of using `capsys`. Added one
   local helper `_read_log_records()` immediately above them (same rule as
   this file's other local helpers — never added to the FROZEN
   `conftest.py`). No other test in the file touched.
10. Wrote `tests/test_log.py` covering oracles 1–7 (oracle 8 is the two
    updated daemon tests). Added a local `autouse` fixture resetting
    `structlog.contextvars` before/after each test in *this file only* —
    structlog's config and the `nyxloom` stdlib channel are process-wide
    globals, and since every test explicitly calls `configure()` itself
    with its own `tmp_path`, cross-test contamination is a non-issue (each
    call fully replaces the prior handler set — verified by the
    idempotency test).
11. Built `tester-unified:structlog` (NOT `:local` — two sibling agents are
    gating against that tag) from this worktree, smoke-imported structlog
    (26.1.0), then ran the full gate.

## Local smoke-testing before the docker gate (cockpit, not a ship signal)

Ran quick interactive Python snippets against the local devcontainer
interpreter (which already had structlog 26.1.0 installed) purely to catch
the `NOTSET`-delegation bug and the `.trace()` `AttributeError` fast, before
paying for a docker build cycle. Neither of these runs is treated as a ship
signal — the real gate is the docker run below, reported in P01-REPORT.md.

## First gate run caught a self-inflicted process error, not a code bug

First `docker run ... coverage_gate` returned `GATE_EXIT=0` with
`diff-coverage OK: 0/0 changed executable lines covered` — suspicious, since
real lines were added. Root cause: the branch had never been *committed*
yet (`git worktree add -b ...` only creates the branch pointer at `main`'s
tip; all edits were still working-tree changes). `coverage_gate` diffs
`git diff <merge-base> HEAD` (committed history), so with HEAD still equal
to `main`, the delta was correctly empty — not a gate bug, a missing commit.
Fixed by committing before re-running the gate (see P01-REPORT.md for the
post-commit run).
