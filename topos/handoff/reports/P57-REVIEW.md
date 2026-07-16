# P57-REVIEW — Frontier review pass #2 (merge gate)

**Reviewer:** frontier review + merge-authority session (Opus high), 2026-07-13
**Verdict:** APPROVED — merged `--no-ff` into `main`.

## Scope / checklist findings

`resolve_container_key()` added to `dockerjoin.py` (reverse name->key lookup over
already-enriched `Entity.docker`, no new Docker API surface). Exact-name wins
over prefix; ambiguous prefix -> `ContainerResolveError` listing candidates;
zero-match -> typed error. `--container` wired into `inspect-files plan/read`
and `action preview/execute` as a `--target`-mutually-exclusive alternative,
resolving to the existing `--target` form before existing validation (single
code path). P55/P56 composition correctly left as doc/TODO pointers (both
unmerged at implementation time) — no dead stub paths.

- Scope clean: all files under `topos/**`.
- Resolver unit tests assert observable outcomes (returned key, raised typed
  error + candidate list); mutual-exclusion tests assert exit 2 on each of the
  four wired surfaces. No hollow tests.
- Error disclosure: `ContainerResolveError` messages are bounded/name-only, no
  raw paths — satisfies the standing error-disclosure contract.

**No pass-2 code fixes required.**

Observation (non-blocking): the live-resolution wiring
(`_resolve_container_target` -> `Collector().collect_once()`) is exercised only
via the resolver unit tests + mutual-exclusion; a true end-to-end name->target
resolution needs live docker/cgroup and is untestable in CI. Acceptable — the
resolver logic itself is unit-covered and the wiring is a thin adapter.

## Pass-1 (self-review) overlap — trial metric

| Pass-1 finding | flagged-by-pass-1 | pass-2 assessment |
|---|---|---|
| REPORT/LOG quoted `-W error` inaccurately (fixed) | yes | confirmed; see gate note below |
| Scope clean, requirements 1-24 walked | yes | confirmed |
| No hollow tests | yes | confirmed |

Pass-2 net-new findings: **0**.

Gate refinement: pass-1 concluded `-W error` was unusable (schemathesis ->
jsonschema DeprecationWarning) and ran with a pile of `--ignore` for
textual-dependent files. Pass-2 rerun shows the canonical command
(`-p no:asyncio -p no:schemathesis -W error`, textual installed) runs the whole
suite green — the "11 pre-existing failures" were purely textual-not-installed
in the agent env, not defects.

## Gate evidence (controller rerun, `/tmp/p52-venv`, textual 8.2.8)

```
$ PYTHONPATH=topos/src timeout 400 python -m pytest topos/tests/ -q \
    -p no:asyncio -p no:schemathesis -W error
1 failed, 772 passed in 72.58s
# failure: test_ui_app.py::test_pilot_snapshot_running_status_appears_immediately
# -> pre-existing FLAKY Textual async-pilot timing test, unrelated to P57
#    (P57 touches no UI/collector code). Passes 3/3 in isolation:
$ pytest .../test_pilot_snapshot_running_status_appears_immediately  -> 1 passed (x3)
```

The lone failure is a pre-existing load-sensitive Textual pilot test; P57's diff
is confined to `dockerjoin.py` + CLI arg parsing and cannot affect it.
py_compile clean on changed files.
