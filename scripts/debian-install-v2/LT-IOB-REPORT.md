# LT-IOB report status

The original LT-IOB report described an installer benchmark that created,
formatted, mounted, and removed a temporary tail partition. That implementation
was superseded by the first-class `iobench-raw` partition in
`div2-data-area-v2`; the old action counts, tests, mutation results, and gate
result in the predecessor report do not describe the current code.

Current behavior and its test contract are documented in
[`IO-BENCHMARK-DESIGN.md`](IO-BENCHMARK-DESIGN.md) and the
[fix report](docs/evidence/div2-data-area-v2-FIX-REPORT.md).
The registered gate result for this round is recorded there after completion.
