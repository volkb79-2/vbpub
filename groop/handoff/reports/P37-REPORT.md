# P37 - Network Loss Diagnostics - Report

## What was built

Add host/interface-level network drop/error visibility from existing kernel
counters while keeping per-cgroup attribution explicitly in the v2 BPF domain.

### Data collection (`groop/src/groop/collect/host.py`)

- **`_net_dev_counters()`** extended to also parse `rx_errors` (field index 2),
  `rx_drop` (field index 3), `tx_errors` (field index 10), and `tx_drop`
  (field index 11) from `/proc/net/dev`. The minimum field count was raised
  from 10 to 16 to match the full interface line width. The returned dicts now
  include `rx_errors`, `rx_drop`, `tx_errors`, `tx_drop` alongside existing
  byte/packet counters.

### Rate computation (`groop/src/groop/collect/collector.py`)

- **`_apply_host_device_rates()`** extended to compute drop/error rates:
  `rx_errors_s`, `rx_drops_s`, `tx_errors_s`, `tx_drops_s` from raw counter
  deltas. All four new fields are `None` on the first sample (collecting state)
  and for new devices that appear mid-stream. Counter regression (reset)
  produces zero rates, never negative.

### Banner rendering (`groop/src/groop/ui/banner.py`)

- **`_net_device_line()`** extended to append a `LOSS` annotation to each
  interface in the NET banner line when any drop/error rate is non-zero.
  Annotation format: `LOSS rx_dropX/s tx_dropY/s rx_errZ/s tx_errW/s`.
  Zero-valued rates and `None` (collecting state) fields are omitted.
- **`_fmt_loss_rate()`** helper added: formats loss rates with 0, 1, or 2
  decimal places depending on magnitude.

### Diagnostics (`groop/src/groop/diag/__init__.py`)

- **`_annotate_host_network_loss()`** function added: called from `annotate()`
  after per-entity diagnostics. Checks `host_meta.net_devices` for non-zero
  drop/error rates and appends a structured `Finding` (rule_id =
  `"host_network_loss"`) to the root entity (`""`). The finding message is
  explicitly host-scoped and includes the phrase *"per-cgroup attribution
  requires BPF"* to avoid implying any specific cgroup caused the loss.
  The finding includes a remedy referencing NIC/switch-level checks.

### Tests

| Test file | Test | Coverage |
|---|---|---|
| `test_host_device.py` | `test_net_dev_counters_includes_drops_and_errors` | Non-zero drop/error counters are parsed correctly |
| `test_host_device.py` | `test_apply_host_device_rates_computes_drop_error_rates` | Second-sample rates compute correctly |
| `test_host_device.py` | `test_apply_host_device_rates_drop_error_reset_handled` | Counter regression produces zero rates |
| `test_ui_banner.py` | `test_banner_shows_loss_annotation_when_drops_nonzero` | LOSS annotation appears when non-zero |
| `test_ui_banner.py` | `test_banner_shows_no_loss_annotation_when_all_zero` | No LOSS annotation when all zero |
| `test_ui_banner.py` | `test_banner_loss_annotation_omits_none_values` | None values (collecting) produce no annotation |
| `test_ui_banner.py` | `test_banner_loss_annotation_ignores_malformed_rates` | Malformed replay metadata does not crash or produce false LOSS text |
| `test_diag.py` | `test_annotate_adds_host_network_loss_finding` | Finding created on root entity with correct wording |
| `test_diag.py` | `test_annotate_no_host_network_loss_when_zero` | No finding when all rates zero |
| `test_diag.py` | `test_annotate_no_host_network_loss_when_no_meta` | No finding when host_meta absent |
| `test_diag.py` | `test_annotate_preserves_existing_host_network_loss_when_no_meta` | Existing replay finding is preserved when host_meta is absent |
| `test_diag.py` | `test_annotate_host_network_loss_replaces_existing_finding` | Repeated annotation replaces its own host finding without duplicating |
| `test_diag.py` | `test_annotate_host_network_loss_ignores_malformed_rates` | Malformed replay metadata is ignored safely |

Plus updates to all existing `test_host_device.py` rate tests to include the
new `rx_errors`, `rx_drop`, `tx_errors`, `tx_drop` fields in test data, and
the golden fixture `gstammtisch-once.jsonl` updated.

## Deviations from handoff doc

None. Implementation follows the handoff spec precisely.

## Proposed contract changes

None. All data is additive `host_meta` only - no new registry metrics, no frame
schema changes.

## Test evidence

### Full suite (349 passed)

```bash
PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q
# 349 passed in 40.95s
```

### Focused P37 tests (13 new + updated existing)

```text
groop/tests/test_host_device.py::test_net_dev_counters_includes_drops_and_errors PASSED
groop/tests/test_host_device.py::test_apply_host_device_rates_computes_drop_error_rates PASSED
groop/tests/test_host_device.py::test_apply_host_device_rates_drop_error_reset_handled PASSED
groop/tests/test_ui_banner.py::test_banner_shows_loss_annotation_when_drops_nonzero PASSED
groop/tests/test_ui_banner.py::test_banner_shows_no_loss_annotation_when_all_zero PASSED
groop/tests/test_ui_banner.py::test_banner_loss_annotation_omits_none_values PASSED
groop/tests/test_ui_banner.py::test_banner_loss_annotation_ignores_malformed_rates PASSED
groop/tests/test_diag.py::test_annotate_adds_host_network_loss_finding PASSED
groop/tests/test_diag.py::test_annotate_no_host_network_loss_when_zero PASSED
groop/tests/test_diag.py::test_annotate_no_host_network_loss_when_no_meta PASSED
groop/tests/test_diag.py::test_annotate_preserves_existing_host_network_loss_when_no_meta PASSED
groop/tests/test_diag.py::test_annotate_host_network_loss_replaces_existing_finding PASSED
groop/tests/test_diag.py::test_annotate_host_network_loss_ignores_malformed_rates PASSED
```

### py_compile clean on all changed files

```bash
PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m py_compile \
  groop/src/groop/collect/host.py \
  groop/src/groop/collect/collector.py \
  groop/src/groop/ui/banner.py \
  groop/src/groop/diag/__init__.py \
  groop/tests/test_host_device.py \
  groop/tests/test_ui_banner.py \
  groop/tests/test_diag.py
# exit 0
```

## Known gaps / open items

- **Per-cgroup network loss attribution** remains v2 BPF work (P18+) and is
  explicitly out of scope for P37.
- **`[banner] net_device_exclude` config keys** are still hardcoded module-level
  tuples in `host.py` (same gap as P34); TOML config support is a
  straightforward follow-up.
- **Golden fixture topology:** the reference frame uses zero drop/error rates
  on `eth0`, so golden tests don't depend on the controller host's network
  error state.

## Files changed

| File | Change |
|---|---|
| `groop/src/groop/collect/host.py` | Extended `_net_dev_counters()` to parse `rx_errors`, `rx_drop`, `tx_errors`, `tx_drop` |
| `groop/src/groop/collect/collector.py` | Extended `_apply_host_device_rates()` to compute `rx_errors_s`, `rx_drops_s`, `tx_errors_s`, `tx_drops_s` |
| `groop/src/groop/ui/banner.py` | Added LOSS annotation to `_net_device_line()`, added `_fmt_loss_rate()` |
| `groop/src/groop/diag/__init__.py` | Added `_annotate_host_network_loss()`, wired into `annotate()`, with deduplication, replay preservation, and malformed metadata tolerance |
| `groop/tests/test_host_device.py` | Added 3 new tests, updated 4 existing tests for new fields |
| `groop/tests/test_ui_banner.py` | Added 4 new tests for LOSS annotation display and malformed-rate tolerance |
| `groop/tests/test_diag.py` | Added 6 new tests for host network loss finding behavior |
| `groop/tests/fixtures/frames/gstammtisch-once.jsonl` | Updated net_devices entry with new fields |
| `groop/docs/STATUS.md` | Updated diagnostics input notes |
| `groop/handoff/reports/P37-LOG.md` | Work log |
| `groop/handoff/reports/P37-REPORT.md` | This report |
