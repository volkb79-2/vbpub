# P58 Frontier Review (pass #2) - v4 attempt - APPROVED, MERGED

Reviewer: Opus 4.8 (frontier review + merge authority, controller-workflow-v2 §6).
Date: 2026-07-13. Branch: `feat/topos-p58-daemon-mcp-frontend-v4` (worktree
`.worktrees/topos-p58-daemon-mcp-frontend-v4`, base `27e0a6a`).
Prior rejection: `reports/P58-REVIEW-v3.md`.

## Verdict

**APPROVED, merged `--no-ff` after review-fixes.** P58's fourth attempt closes all
five v3 merge blockers for real, not on paper. I verified each one against the
code rather than against the REPORT's claims, because "the REPORT says it is
fixed" is exactly what failed in v3.

| v3 blocker | Status in v4 | How I verified |
|---|---|---|
| B1 `MAX_RESPONSE_BYTES` never enforced | **Fixed** | `_ok()` serializes, measures, returns typed `over-limit`. A test drives a >4 MiB entity payload through a real MCP `ClientSession` and asserts the typed error. Deleting the check fails the test. |
| B2 descriptions/CONTRACTS promise absent behavior | **Fixed** | Discovered descriptions now state only enforced bounds (16 components, 1..50 rows, 1..100 points, 128 metrics/64 findings, 4 MiB). `topos_history` has a real `limit`. The "1000-point" claim is gone and a test asserts `"1000" not in` every description. `test_textual_boundary.py` is genuinely extended. |
| B3 hand-rolled `_metric_sensitivity` | **Fixed** | The copy is deleted; `daemon.api.metric_sensitivity` is called. A test compares the fallback against the canonical function across all 113 registry metrics. |
| B4 third docker resolver in `_handle_history` | **Fixed** | No MCP-local prefix matching remains; both selector tools go through P57's `resolve_container_key`. |
| B5 MCP layer never exercised | **Fixed** | Tests drive the real registered FastMCP server through the SDK's in-memory `ClientSession`. Discovery asserts exactly the four tool names and fails if a registration is deleted. `bool`-as-int is now *rejected*, reversing the v3 test that asserted the opposite of its own contract. |

The v3 non-blocking findings are also all addressed: the startup `hello` probe
exists, overview ranks before redacting, no `str(exc)` reaches a tool result
(the leak test now raises a `DaemonResponseError` carrying the secret, closing
the branch the v3 test missed), `until_ts` is no longer dead, and the bare
`assert isinstance` control flow is gone.

This is a genuine repair, and the tier escalation to `sonnet5-high` did what it
was escalated for: the failure mode that killed v3 was specification fidelity,
and v4's descriptions now match its code.

## Review-fixes applied before merge

Five defects survived pass #1 and the implementer's own gates. All were fixed in
the feature worktree (commit `2f11d17`) rather than triggering a fifth attempt:
none is architectural, and the handoff contracts they violate are local.

### R1 (blocker-class) - the optional extra was not actually optional

`test_mcp_server.py` hard-imported `mcp.server.fastmcp` at module scope. In a base
install (no `topos[mcp]`), that is a **collection error, and pytest aborts the
entire run** - not a skip:

```
ERROR topos/tests/test_mcp_server.py
!!!!!!! Interrupted: 1 error during collection !!!!!!!
no tests collected, 1 error in 0.20s
```

So the standing gate `pytest topos/tests` was broken for anyone without the
optional extra installed - which is the whole point of an extra, and is the
packaging contract the handoff spends a paragraph on. It went unnoticed because
the agent installed `mcp` into its own environment and never tested the absent
case; the two tests it *does* have for the absent SDK both cover the CLI path,
not the suite's own collectability. Repo convention already exists
(`test_headless_record.py` uses `pytest.importorskip` for `zstandard`). Fixed
with a module-level `importorskip`; the absent-extra run now reports
`2 passed, 1 skipped`, and the structural boundary test in
`test_textual_boundary.py` still runs unconditionally.

### R2 - `topos_history` accepted any string as a metric name

The tool description promises "one registry metric's time series", and the handoff
forbids tools that "accept registry keys that reach beyond P52's already-validated
surface". `_handle_history` only checked that `metric` was a non-empty string.
Probed on the branch:

```
topos_history(metric='nonexistent_typo_metric')
  -> {'ok': True, 'data': {..., 'series': [], 'count': 0}}
```

A typo returns a *successful empty result*, indistinguishable from "this metric
exists but had no data" - the calling model concludes the container was idle when
it actually asked a meaningless question. This is a milder instance of the exact
description-vs-code class that killed v3. Fixed: `metric not in REGISTRY` is now a
typed `invalid-selector`, the description says so, and a test asserts both the
rejection and that a valid name still resolves.

### R3 - an empty history window reported the selector as invalid

```
valid entity key + daemon has no frames in the window
  -> {'error': {'code': 'invalid-selector',
                'message': 'selector is not present in history'}}
```

`_history_selector` treated "no frames in the window" as "selector not found". An
agent asking "did this spike in the last 30s?" against a freshly started daemon is
told its container does not exist. Fixed: with no frames in the window, the
selector is resolved against the live frame instead, so a real entity yields an
empty series (`count: 0`) and a bogus selector is still refused. Both halves are
tested - the fix must not turn the tool into a rubber stamp.

### R4 - exact-key `topos_entity` did two daemon round trips

`_resolve_entity_selector` called `request_entity(selector)` to *test* whether the
key resolved, discarded the successful result, and `_handle_entity` then called
`request_entity` again. Measured: 2 RPCs per exact-key lookup on the agent-facing
hot path. Fixed by returning the first hit instead of refetching it; a test pins
`entity_calls == 1`. The refactor also collapsed both tools' resolution onto one
`_resolve_in_entities` helper, which is what B4 was really asking for - v4 had
satisfied B4's letter (both use P57's resolver) while the two tools still resolved
against different frames via different code.

### R5 - ASCII hygiene

`topos/src/topos/mcp/__init__.py` shipped two em dashes. The self-review explicitly
claims it "Fixed the LOG/REPORT title em dashes; both evidence files are now ASCII"
- it de-dashed the evidence files it was looking at and missed the source file it
had written. Standing contract: "ASCII by default".

## Gates (re-run by the reviewer, package venv)

The implementer's LOG/REPORT quote the package venv
(`/usr/local/py-utils/venvs/pytest/bin/python`), not bare `python3`; bare `python3`
cannot run the suite under `-W error` at all (an unrelated `hypothesis_jsonschema`
-> `jsonschema.RefResolutionError` DeprecationWarning is promoted to an error at
import). Recorded here so the next reviewer does not mistake that for a P58 break.

Post-review-fix, on the branch:

```text
PYTHONPATH=topos/src <venv>/python -m pytest \
  topos/tests/test_mcp_server.py topos/tests/test_textual_boundary.py \
  topos/tests/test_packaging_metadata.py -q -W error
20 passed in 2.71s          # 17 from v4 + 3 review-fix regression tests

PYTHONPATH=/tmp/blockmcp:topos/src <venv>/python -m pytest \
  topos/tests/test_mcp_server.py topos/tests/test_textual_boundary.py -q -W error
2 passed, 1 skipped         # base install without topos[mcp]: skips, does not abort

python3 -m py_compile <all changed/new files>   # exit 0
git diff --check                                # exit 0
```

Full suite from `main` after merge: see the evidence row in `docs/STATUS.md`.

### Note on a flaky UI test

One full-suite run on the branch failed
`test_ui_app.py::test_pilot_snapshot_running_status_appears_immediately`. It passes
in isolation on **both** the branch and unmodified `main`, and P58 touches no UI
code. Pre-existing Textual pilot timing flake under full-suite load, not a P58
regression - recorded rather than papered over.

## Pass #1 overlap (trial metric, controller-workflow-v2 §6)

| # | Pass-2 finding | flagged-by-pass-1 |
|---|---|---|
| R1 | Optional extra not optional: suite aborts at collection without `topos[mcp]` | **no** |
| R2 | `topos_history` accepts any string as a registry metric name | **no** |
| R3 | Empty history window reported as `invalid-selector` | **no** |
| R4 | Exact-key `topos_entity` costs two daemon RPCs | **no** |
| R5 | Non-ASCII em dashes in `topos/src/topos/mcp/__init__.py` | **no** (pass #1 fixed the em dashes in the LOG/REPORT and missed the source file) |

**0 of 5 findings flagged by pass #1.** Pass #1 again did real *mechanical* work
(removed unused imports, de-dashed the evidence files, added the discovery and
113-metric regression assertions) and again found **zero** substantive defects -
consistent with every prior package and with §6's correlated-blind-spot framing.

R5 is the sharpest illustration in the trial so far: pass #1 ran the ASCII check,
found violations, fixed them **in the two files it was reading**, and did not
re-run the check across the diff it had just written. It executed the checklist
item as a local edit rather than as a property of the change. That is the same
shape as the P70 finding recorded in the deciding log - a self-review can only
check what it is pointed at, and it will point itself at the file in front of it.

R1 is the substantive one to learn from: the self-review verified the extra was
optional *for the CLI* (both absent-SDK tests target `topos mcp serve`) but never
asked whether the extra was optional *for the test suite it had just written*. The
handoff named the property ("all other subcommands work with the extra absent") and
the agent tested exactly the named surface and no further. Carve-time contracts buy
checks; they do not buy generalization.

## Follow-ups surfaced (carved separately)

- The `topos_health` 16-component and `topos_entity` 128-metric/64-finding caps
  *reject* rather than truncate. That is what the handoff asked for ("never
  silently clamp"), but it means one oversized entity makes the tool permanently
  unusable for that entity rather than degrading. Worth revisiting if it ever bites.
- `limit` is typed `object` in the tool signatures so that `bool`-as-int reaches
  the strict validator. Correct behavior, but it means the advertised JSON schema
  for `limit` is untyped, so the calling model gets no type hint from the schema
  (only from the description prose). A `Annotated[int, ...]` + explicit bool guard
  would give both. Non-blocking; noted for a future ergonomics pass.
