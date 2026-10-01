# P111 report — exact Docker update coverage

## Result

`actions/update_ops.py` has exact 100% statement and branch coverage in two
clean, immutable, complete parallel gate runs. Each run passed 2,135 cases and
covered all four changed executable lines. Reader failures remain fail-closed,
operator interrupts now propagate, and booleans can no longer masquerade as
numeric resource limits.

## Evidence

Both accepted runs produced:

```text
update_ops.py missing_lines=[]
update_ops.py missing_branches=[]
target_record_sha256=a48772803e64446ac7b90be20102b056f5feb29ee19dcba90e885c72dcfb0dc7
```

| Run | Pytest | Changed-line floor | Exit |
| --- | --- | --- | ---: |
| 1 | 2,135 passed in 69.75s | 4/4, 100% ≥ 100% | 0 |
| 2 | 2,135 passed in 69.34s | 4/4, 100% ≥ 100% | 0 |

Twenty-five new cases collect as twenty-five cases: 2,110 plus 25 equals 2,135.

## Behavioral coverage

The tests pin complete validation errors, collection/resolution/read calls,
cgroup paths, fail-closed usage errors, and rendered plans. Ordinary exceptions
return `None` or the documented refusal; `KeyboardInterrupt` propagates from
both reader boundaries. No test invokes Docker or reads host cgroupfs.
