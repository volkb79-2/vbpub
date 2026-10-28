---
name: nyxloom-usage
description: Read and interpret local Claude Code usage analytics with `nyxloom usage report`, `prices`, `windows`, `anchor`, and `calibrate`; use when an operator asks about token totals, API-equivalent prices, reset windows, or account UI readings.
---

# Nyxloom Claude Code usage analytics

Use the installed `nyxloom usage` CLI for local metadata-only analytics. Start
with the [consumer guide](../../../docs/CONSUMERS.md#report-claude-code-usage)
for examples and the [design guide](../../../docs/DESIGN-GUIDE.md#local-usage-report)
for the measurement rules. The accepted grammar is in the
[CLI reference](../../../docs/CLI-REFERENCE.md#current-command-and-option-contract).

## Common commands

```sh
nyxloom usage report --since 24h
nyxloom usage report --since 7d --format json --no-labels
nyxloom usage prices --fit --format json
nyxloom usage windows --since 7d --format json
nyxloom usage anchor list
nyxloom usage calibrate --format json
```

For an account UI reading, enter its displayed values and source explicitly:

```sh
read -r -p 'Observed at (ISO-8601 with timezone): ' OBSERVED_AT
read -r -p 'Displayed percentage (0..100): ' PERCENT
read -r -p 'Next reset at (ISO-8601 with timezone): ' RESET_AT
nyxloom usage anchor add --kind five_hour \
  --at "$OBSERVED_AT" --percent "$PERCENT" \
  --reset-at "$RESET_AT" --source settings-usage \
  --coverage-assumption local-only-lower-bound
```

Replace the example observation with a reading actually seen on the account
UI. Enter the UI's displayed reset timestamp; never calculate it by subtracting
five hours from a rejection event.

## Interpret the measures

- **Raw token units** sum input, cache reads, cache writes by known TTL, any
  unclassified cache writes and output. Thinking tokens are an output subset.
- **Weighted units** use the configured experimental profile. They are useful
  for comparing local work under that profile; they do not measure account
  consumption.
- **API-equivalent USD** applies dated exact-model Anthropic list prices to
  observed token classes. It is an estimate, not a bill or subscription cost.
  Unknown models or modifiers remain unpriced.
- **Observed local share** divides a node's weighted units by the local
  report's weighted total. It is not a Claude usage-limit percentage.
- **Estimated limit percentage** stays unknown unless explicit timed UI
  readings, reset boundaries and coverage make it identifiable. A rejection is
  only a threshold event, never a 100% capacity anchor.

`usage windows` separates observed-reset five-hour sessions and the fixed
account weekly reset from a rolling analysis range. Anthropic's reset
description is verified here for Max plans only; other plans remain
unverified. `usage prices --fit` treats
transcript `cost-state` records as diagnostics and emits coefficients only
when scope, monotonicity, cache TTL identifiability, full rank and residual
checks all pass.

The only persistent usage data are explicit operator anchors in the private
`usage-anchors-v1` state file. Reports and transcript ledgers are not cached.
Other devices and shared account surfaces may consume the same allowance, so a
local-only calibration is a lower bound. Use `--no-labels` when topic and
session labels are not needed; reports with labels are sensitive local files.
