# P02 — verbosity config, bootstrap & runtime control: implementation LOG

Branch: `feat/logging-p02-runtime-control` · worktree:
`/workspaces/vbpub/.worktrees/logging-p02-runtime-control`
Spec: `nyxloom/docs/plan-logging.md` §3 (D-L3, D-L4), §4.4, §6 (P02), §7
(NYXLOOM_LOG_LEVEL bootstrap note).

## Sequence of actions

1. Created worktree `feat/logging-p02-runtime-control` from `main` (`9321dd6`,
   which already carries P01's `log.py` core + `paths.py` helpers).
2. Read `docs/plan-logging.md` §3/§4.4/§6/§7, then `log.py` (`configure`,
   `set_level`, `_normalize_level`, level constants), `paths.py`
   (`logs_dir()`, `daemon_log_level_path()` already exist from P01), and
   `daemon.py`'s HTTP handler (`_handle_get`/`_handle_post`,
   `_CONFIG_POST_PATHS`, the existing `POST /api/config/*` and
   `GET /api/stream` patterns to mirror), and `config.py`'s `ProjectConfig`/
   `ProjectConfig.load`.
3. **Found a real naming collision before writing any daemon.py code**:
   `daemon.py` already does `log = get_logger("daemon")` at module scope
   (P01's own convention — every module does this). That means the name
   `log` is the per-module BOUND LOGGER, not the `nyxloom.log` MODULE — so
   `log.configure(...)` / `log._normalize_level(...)` as the handoff's
   prose literally suggests would be an `AttributeError` (a `BoundLogger`
   has no `.configure`). Fixed by importing the module under a distinct
   alias: `from . import log as log_module`, used for
   `log_module.configure`/`log_module.set_level`/`log_module._normalize_level`,
   while the existing `log.info(...)`/`log.warning(...)` calls keep using
   the bound-logger name unchanged.
4. Added `config.ProjectConfig.logging_level: str | None = None` (D-L3
   layer 3) + minimal `load()` parsing (`data.get("logging", {}).get(
   "level")`) — a new, entirely optional top-level `[logging]` TOML table.
   Confirmed `nyxloom-config.schema.json`'s top-level `additionalProperties:
   true` already permits an unrecognised `[logging]` table, so **no schema
   file edit needed** (out of scope.touch anyway).
5. Added `daemon.resolve_level(registry=None) -> tuple[str, str]` (level,
   source) implementing D-L3's four-layer precedence: runtime-override
   file → `NYXLOOM_LOG_LEVEL` env → `[logging] level` in the alphabetically-
   first ("primary") registered project's config → hardcoded `INFO`. Picked
   "alphabetically-first registered project id" as the concrete meaning of
   the handoff's "primary project" (never defined elsewhere in the repo) —
   this is the EXACT same convention `_handle_get`'s `/api/stream` bare-
   `EventSource` fallback already uses one page down in this same file
   (`next(iter(sorted(self.registry)), None)`), so it is not a novel
   invention, just reuse of an existing precedent for "one project must
   stand in and none is more authoritative." Each layer is defensively
   re-validated via `log_module._normalize_level`; an invalid value at any
   layer is treated as ABSENT (falls through), never raised — a corrupted
   runtime-file or a typo'd project toml can't crash daemon bootstrap.
6. Wired `Daemon.run()`: `resolve_level(self.registry)` +
   `log_module.configure(level, paths.logs_dir())` as the FIRST two lines
   of `run()` (before the pidfile check), then
   `log.info("daemon started", version=__version__, effective_level=level,
   level_source=level_source, projects=sorted(self.registry))`. Added
   `from . import __version__` for the version field.
7. Added `POST /api/config/log-level {level}` (`_post_config_log_level`,
   joining `_CONFIG_POST_PATHS`) and `GET /api/logs/level` (inline in
   `_handle_get`, returning `resolve_level(self.registry)` as
   `{"level":..., "source":...}`), mirroring the existing POST-endpoint
   validation/400 pattern and the existing GET-endpoint inline-dispatch
   pattern exactly. D-L4: **no** `storage.append_and_apply`/
   `_append_ui_event` call in the POST handler — only a `log.info(...)`
   call, unlike every other `POST /api/config/*` endpoint on this surface.
8. **Caught a field-name collision via a quick standalone smoke check**
   (not the gate): `structlog.stdlib.add_log_level` unconditionally sets
   `event_dict["level"] = <severity name>`, so a custom log-call kwarg
   named `level=...` (my first draft, for both the "daemon started" and
   "log level changed" records) would be silently overwritten by the
   record's own severity ("info") — destroying the very data (the NEW
   effective level / the resolved boot level) the record exists to carry.
   Verified with a 5-line standalone script
   (`log.configure(...); l.info("...", new_level="debug"); read the JSONL
   back`) before touching any test. Renamed the fields to `effective_level`/
   `level_source` (daemon-started) and `new_level`/`change_source`
   (log-level-changed) — never `level` as a custom field name anywhere in
   this package's log calls.
9. Wrote 16 new tests in `tests/test_daemon.py` (append-only, nothing
   pre-existing touched): the precedence chain (4 required + 3 more
   closing corrupt-layer/OSError/config-load-failure branches the diff-
   coverage gate flagged), live-flip + persistence + simulated-respawn,
   invalid-level 400 (+ missing-level 400), the `GET /api/logs/level`
   contract, the `405` guard on `POST`-only paths, the D-L4 no-domain-event
   assertion, and two `Daemon.run()` bootstrap tests (default level +
   project-`[logging]`-sourced level) using the existing `_stop_event.set()`
   pre-`run()` immediate-exit pattern (no HTTP fixture needed). Added one
   local helper `_set_logging_level(cfg, level)` (append a `[logging]`
   table to a project's toml), mirroring the file's existing
   `_set_ephemeral_http_port`/`_set_http_bind` local-helper convention —
   never touched the FROZEN `conftest.py`.
10. Local sanity: `PYTHONPATH=src python3 -c "import nyxloom.daemon"` +
    `ast.parse` on both edited source files, to catch import-time/syntax
    errors before spending a container gate cycle (the devcontainer's own
    venv happens to already have `structlog`/`pytest` installed, but this
    was an import/syntax smoke check only, never treated as a ship signal
    — the actual pytest suite was run ONLY inside `tester-unified:local`,
    per the cockpit-vs-gating-runner policy).
11. Committed (`361ec69`), ran the real gate — **1 failure**:
    `test_log_level_post_emits_log_not_domain_event` (see next section).
    Fixed, committed (`bab0575`), re-ran the gate — all green but
    diff-coverage **91.0% (61/67)**, 6 uncovered lines in
    `resolve_level`'s defensive `except` branches. Added 3 more tests
    directly targeting those branches, committed (`861854b`), re-ran the
    gate — **100.0% (67/67), `GATE_EXIT=0`**.

## The one real bug the gate itself caught (not hidden)

First full run failed `test_log_level_post_emits_log_not_domain_event`:
`_post_config_log_level` called `log_module.set_level(canonical)` **before**
`log.info("log level changed", ...)`. When flipping to a STRICTER level (the
test flips `info` → `warning`), the `set_level()` call took effect first, so
the very INFO record announcing the change was gated out by the level it had
just switched to — a real, if subtle, ordering bug (flipping the OTHER
direction, e.g. `warning` → `debug`, would have masked it, since the INFO
record would then still pass under the new, more permissive level). Fixed by
reordering: emit the announcement while still under the OLD (soon-to-be-
previous) effective level, then apply the flip. Documented in-line at the
call site so the ordering isn't accidentally reversed later.

## Diff-coverage gap (not hidden)

After the ordering fix, the full run passed with no test failures, but
`coverage_gate` reported 91.0% (61/67) — 6 uncovered lines, all inside
`resolve_level`'s defensive `except`/fallback branches (the runtime-file
`OSError` path, the primary-project config-load `except Exception`, and the
primary project's own `[logging] level` being itself invalid). My original 4
precedence tests + 1 "corrupt layers" test never happened to exercise these
THREE specific branches (the corrupt-layers test used a VALID `[logging]`
value as its bottom layer, so it never hit the config-value-invalid branch;
neither of the other two failure modes was exercised at all). Added three
direct tests (a directory where the runtime-file should be; a registry
entry whose root has no project.toml at all; a project's own `[logging]
level` set to a garbage string) — each asserts the correct fall-through
result. Re-ran: 100.0% (67/67).
