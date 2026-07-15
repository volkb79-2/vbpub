# P81 REPORT — Redaction: one enforcement point, no bypass

## What was built

- **`groop.daemon.redaction`** — the single server-side enforcement point.
  Both read frontends route every value-bearing payload through
  `redact_payload(payload, *, shape, metrics_meta, ceiling)` before
  serialization; neither frontend keeps a redaction walk of its own.
  - `classify_metric(name, metrics_meta)` — trusts the daemon's `metrics_meta`
    sensitivity when present, **fails closed to `sensitive`** otherwise.
  - `redaction_marker(sensitivity)` — the one marker dialect,
    `{"redacted": true, "sensitivity": "<level>"}`.
  - `PayloadShape` — a closed registry of typed visitors
    (`FRAME`, `ENTITY_FRAME`, `MCP_OVERVIEW`, `MCP_ENTITY`, `MCP_HISTORY`).
    An unregistered shape raises `RedactionError` (fail closed), so a new
    value-bearing payload path cannot ship without a typed visitor.
- **`findings[]` is covered.** A finding whose `source_metrics` names a metric
  above the ceiling has its `message` (and `remedy`, when present) replaced by
  the typed marker; `rule_id`, `severity`, and `source_metrics` stay as
  operational facts. The MCP entity finding now carries `source_metrics` so the
  shared point can classify it.
- **Fail closed on unrecognized value-bearing fields.** The frame/entity
  visitors recognize a closed set of fields (`host`/`metrics` maps, `findings`,
  identity metadata). Any other field — `governance`, `network`, `damon`,
  `host_meta`, or a future addition — is replaced with the sensitive marker
  rather than emitted above the ceiling. `governance.limits.*.live_value` was a
  live value-bearing leak past the old pass; it is now closed.
- **One dialect.** The gateway's typed marker wins; MCP's bare `"__redacted__"`
  string (`_REDACTED_MARKER`) and its `_redact`/`_sensitivity` walk are removed.
- **No P52 change.** The daemon socket wire, envelope, and error codes are
  untouched; redaction stays a gateway/MCP serialization concern.
- Both frontends now call `redaction.redact_payload`; the gateway's
  `_redact_frame`/`_redact_metrics`/`_redaction_marker`/`_SENSITIVITY_RANK` are
  deleted.

## Deviations from handoff

Two deliberate, contract-driven behavior changes (neither is a wire change):

1. **`governance`/`network`/`damon`/`host_meta` are now redacted fail-closed on
   the gateway routes.** The handoff requires "an enforcement point that fails
   closed on payload shapes it does not recognize, so adding a new value-bearing
   field cannot silently widen the boundary." These fields carry metric values
   and were previously shipped verbatim. No test read them out of a frontend
   response (verified). A future package may register a typed visitor to
   classify their internals more finely.
2. **MCP's classification fallback is now fail-closed.** Previously
   `_sensitivity(None, name)` returned the canonical classifier
   (`metric_sensitivity`); oracle #4 requires an unclassified metric to be
   redacted below `sensitive` in both frontends, so the shared `classify_metric`
   returns `sensitive` for absent/invalid metadata. The one MCP test that
   asserted the old fallback was rewritten to assert the fail-closed contract
   (and that valid metadata is still trusted verbatim).

Not BLOCKED: no marker unification required a P52 wire/envelope change, and the
`Sensitivity` enum expressed every field's classification (including the
fail-closed default).

## Acceptance oracles (`tests/test_p81_redaction_enforcement.py`)

Every oracle drives a real `DaemonApi` over a temp `AF_UNIX` socket through both
frontends (real `DaemonClient`; no mocked client). The frame carries a `Finding`
whose message embeds the literal value of a `sensitive` metric it names in
`source_metrics`.

1. **Findings bypass closed** — the sensitive value appears nowhere in the raw
   response bytes of `/v1/current`, `/v1/entity`, or MCP `groop_entity`; the
   finding's `rule_id`/`severity`/`source_metrics` survive and its
   `message`/`remedy` become the marker.
2. **Disarming the walker goes red** — with `redact_payload` monkeypatched to
   the identity and both frontends re-driven, the same leak check the real
   oracles use fires (`pytest.raises(AssertionError)`), proving the suite cannot
   stay green against a disarmed redactor.
3. **One dialect** — the gateway and MCP markers for the same sensitive input
   are byte-equal (`{"redacted":true,"sensitivity":"sensitive"}`); `"__redacted__"`
   appears in neither raw response.
4. **Fail-closed unclassified metric** — `classify_metric(name, {})` is
   `sensitive`, and `redact_payload` redacts an unclassified value at `public`
   and `operational` ceilings in both the `FRAME` and `MCP_ENTITY` shapes; a
   registry frame can never carry a metric the daemon omits from `metrics_meta`,
   so this is exercised at the shared point both frontends delegate to, plus an
   end-to-end public-ceiling check that a below-ceiling value is hidden.
5. **Keys/units/`metrics_meta` survive** — on `current`/`history`/`entity` and
   MCP, redacted metric keys stay, `metrics_meta` passes through with `unit` and
   `sensitivity`, and a below-ceiling value (`ram`) is untouched.

Plus: an unrecognized value-bearing field (`governance`) fails closed; an
unregistered shape raises `RedactionError`; a `None` ceiling is a faithful
no-op.

## Test evidence

Package virtualenv `.venv` (Python 3.14.6, Linux; `pip install -e './groop[dev]'`
— `zstandard`, `mcp`, `textual` all present, so the P84 gate permits zero skips).

```bash
# Focused P81 gate
PYTHONPATH=groop/src .venv/bin/python -m pytest \
  groop/tests/test_p81_redaction_enforcement.py \
  groop/tests/test_daemon_http_gateway.py \
  groop/tests/test_mcp_server.py -q -W error -p no:schemathesis
# 75 passed

# Required full gate (P84 zero-skip)
timeout 900 env PYTHONPATH=groop/src .venv/bin/python -m pytest \
  groop/tests -q -W error -p no:schemathesis
# 1461 passed in ~176s (0 skipped)

.venv/bin/python -m py_compile groop/src/groop/daemon/redaction.py \
  groop/src/groop/daemon/http_gateway.py groop/src/groop/mcp/server.py \
  groop/tests/test_p81_redaction_enforcement.py
# clean

git diff --check
# clean
```

## Proposed contract changes

None to the P52 wire. CONTRACTS §10's `Sensitivity` semantics are unchanged;
P81 makes the ceiling a property of the data at one enforcement point rather
than of each frontend's serializer. The redaction marker
`{"redacted":true,"sensitivity":"<level>"}` is now the single dialect for both
frontends (documented in the README P81 row).

## Known gaps / open items

- `governance`/`network`/`damon`/`host_meta` are redacted wholesale (fail
  closed) rather than field-classified. A follow-up may register typed visitors
  so their non-value-bearing sub-fields can be surfaced at lower ceilings.
- The enforcement boundary is ready for P88 query results and future
  process/lifecycle/incident/evidence payloads: add a `PayloadShape` + visitor;
  do not add a local walker to a frontend.

## Files changed

```text
groop/src/groop/daemon/redaction.py            new: single fail-closed enforcement point
groop/src/groop/daemon/http_gateway.py         route redaction through the shared point; local walk deleted
groop/src/groop/mcp/server.py                  route redaction through the shared point; "__redacted__" retired
groop/tests/test_p81_redaction_enforcement.py  new: 10 adversarial oracles over both live frontends
groop/tests/test_mcp_server.py                 dialect + fail-closed fallback + finding-fake updates
groop/tests/test_daemon_http_gateway.py        docstring refers to the shared FRAME visitor
groop/README.md                                P81 status -> Done
groop/handoff/reports/P81-LOG.md               resumability log
groop/handoff/reports/P81-REPORT.md            this report
```
