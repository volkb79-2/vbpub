# P110 report — exact action policy coverage

## Result

`actions/catalog.py` and `actions/governance.py` have exact 100% statement and
branch coverage in two clean, immutable, complete parallel gate runs. Each run
passed 2,110 cases and covered the one changed executable line. The product now
also propagates operator interrupts from the preview reader rather than
swallowing them.

## Evidence

Both accepted runs produced:

```text
catalog.py missing_lines=[]
catalog.py missing_branches=[]
governance.py missing_lines=[]
governance.py missing_branches=[]
target_record_sha256=374dd7751da55ddfd3de60c47a98443b1579177754795798f02621f6898ebcfd
```

| Run | Pytest | Changed-line floor | Exit |
| --- | --- | --- | ---: |
| 1 | 2,110 passed in 63.26s | 1/1, 100% ≥ 100% | 0 |
| 2 | 2,110 passed in 64.13s | 1/1, 100% ≥ 100% | 0 |

Twenty-two new cases collect as twenty-two cases: 2,088 plus 22 equals 2,110.

## Behavioral and deletion oracles

The catalog tests pin complete validation errors across all residual specialized
target paths. The removed set-property empty guard duplicated the unconditional
shared empty check; the public empty behavior and neighboring kind-specific
behavior remain exact.

Governance tests pin complete subprocess arguments/results, argv and preview
plans, persistence, integer-conversion failure, and reader failure boundaries.
`RuntimeError` yields the complete fallback plan; `KeyboardInterrupt`
propagates. No test invokes systemctl or another host mutation.
