# P81 - Redaction: one enforcement point, no bypass

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** sonnet5-high
> **Depends-on:** P67 (merged - HTTP gateway), P58 (merged - MCP frontend)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** unifying the marker shape would require a wire/envelope change to P52 (it must not); or the `Sensitivity` enum in CONTRACTS §10 cannot express a field's classification. Do NOT invent a third redaction dialect.

<!--
CARVE SOURCE (controller-workflow-v2 §8): **review-derived** (P67 pass #2).
Two independent read frontends now redact the same closed enum, in two
different ways, and both redact only `metrics` while shipping other fields that
can carry the same values. Carved from the P67 review, which verified the
gateway's four trust-boundary groups and found this as the residue.
-->

## Goal

Make the `Sensitivity` ceiling (CONTRACTS §10) a property of the **data**, enforced
once, rather than a property of each frontend's serializer. Today two frontends
enforce it independently and neither covers the whole payload.

## The two defects (both real, one latent, one live)

### 1. The ceiling covers `metrics` and nothing else -- `findings[]` is a bypass

`daemon/http_gateway.py` redacts `frame["host"]`, `entity["metrics"]`, and the
entity route's `metrics` map. But `entity_frame_to_jsonable` also emits
`findings[]`, and a `Finding` carries a free-text `message` plus `source_metrics`
(`model.py`). A rule whose message interpolates the value of a `sensitive` metric
(`pids_current`, `cgroup_procs`, `pids_max`, `pids_events_max_per_s`) would ship
that value verbatim to a `public`-ceiling principal, past a redaction pass that
believes it succeeded.

**This is latent, not live:** as of this carve no rule in `diag/` interpolates a
sensitive metric value into a message (verified at review time). That is luck and
a small rule set, not a guarantee -- nothing in the code, the types, or the tests
prevents the next rule from doing it. A redaction boundary whose correctness rests
on "no current caller happens to violate it" is not a boundary.

The same argument applies to any future entity field that can carry a metric
value. The fix is not "also redact findings"; it is an enforcement point that
**fails closed on payload shapes it does not recognize**, so adding a new
value-bearing field cannot silently widen the boundary.

### 2. Two redaction dialects for one closed enum

- `daemon/http_gateway.py` replaces a value with `{"redacted": true, "sensitivity": "<level>"}`.
- `mcp/server.py:52` replaces it with the bare string `` "__redacted__" `` (`_REDACTED_MARKER`).

Same enum, same intent, two wire shapes. Any consumer (P73's web UI) must handle
both, and a consumer that handles only one will render a redacted value as a
literal string or a raw object. Pick one typed marker -- the gateway's shape is
the better of the two, since it preserves *why* a value is hidden -- and make the
other frontend emit it.

## Required Contracts

- **One enforcement function**, shared by both frontends, that takes a decoded
  payload plus a ceiling and returns the redacted payload. Neither frontend may
  carry its own redaction walk afterward.
- **Redaction replaces a value, never drops a key** (the standing P58 lesson,
  restated in P67). Key, label, and unit stay; `metrics_meta` passes through
  intact so a UI can render *why* a value is hidden.
- **Fail closed on unknown shape.** A metric absent from `metrics_meta` is
  treated as `sensitive` (the gateway already does this -- preserve it). A payload
  field the enforcement point does not recognize must not be emitted above the
  ceiling by default.
- **`findings[]` is covered.** A finding whose `source_metrics` includes a metric
  above the ceiling has its `message` and `remedy` replaced by a typed marker; the
  `rule_id`, `severity`, and `source_metrics` list stay (they are `operational`
  facts, not values).
- No change to the P52 wire, envelope, or error codes. This is a serialization
  concern, not a protocol one.

## Acceptance Oracles (numbered, adversarial)

Stand up a real `DaemonApi` over a temp `AF_UNIX` socket and drive **both**
frontends. No mocked client.

1. **The findings bypass is closed.** Construct a frame carrying a `Finding` whose
   `message` embeds the literal value of a `sensitive` metric and whose
   `source_metrics` names it. Request it at an `operational` ceiling through the
   HTTP gateway and through MCP. **Grep the raw response bytes for the value** --
   if it appears anywhere, in `metrics` or in prose, the test fails.
2. **Disarming the walker fails a test.** This is the oracle the P67 review had to
   add by hand: replace the enforcement function's body with a no-op and assert
   that the suite goes red. A redaction test suite that stays green against a
   disarmed redactor is the defect this package exists to prevent -- P67's
   original suite had 47 green tests and would not have caught it on two of its
   three routes.
3. **One dialect.** Both frontends emit the same marker shape for the same input;
   assert byte-equality of the marker object.
4. **Fail-closed on an unclassified metric.** A metric absent from `metrics_meta`
   is redacted at every ceiling below `sensitive`, in both frontends.
5. Keys, units, and `metrics_meta` survive redaction on every route (regression:
   redaction must not degrade into deletion).

## Out Of Scope

- The `Sensitivity` classification itself (which metrics are `sensitive`) -- that
  is CONTRACTS §10 and does not change here.
- Client-side masking of any kind. Redaction is server-side; this package exists
  because that is the only place it means anything.
- Auth, bind, or CSRF posture (P67 owns those and they were verified).

## Gates

```bash
PYTHONPATH=groop/src python3 -m pytest <focused P81 tests> -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
python3 -m py_compile <changed files>
git diff --check
```

State the environment for each result. Write P81-LOG.md / P81-REPORT.md.

Note: `groop/tests/test_report.py::TestReportCLI::test_zst_without_zstandard_exits_2`
currently fails on unmodified `main` (P82 owns the repair). Do not attribute it to
this package, and do not "fix" it here.
