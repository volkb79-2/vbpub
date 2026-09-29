# W8 LOG (B111 measurement evidence and campaign hygiene, plan-estimate)

Base: `bdd9cde7` (W7 head). Branch `wave-a-w8-measurement`.

Collect counts before: `tests` 5438, `analysis/tests` 224, `gate/tests` 350.

## Commits
(filled per step)

## Oracles (positive result, controlled break red then reverted)
| item | owner | oracle | test id | controlled break | result |
|---|---|---|---|---|---|
| L | tests/core/test_liveness.py | O10 | tests/core/test_liveness_outer_stream.py::test_o10_nested_liveness_suite_keeps_the_outer_stream_intact | first `sessionfinish` test's `monkeypatch.context()` scope removed (call moved outside the scoped env) | red (session_finish leaked to the outer stream), reverted, green |

## Deviations

## For the controller's gate run

## Residuals
