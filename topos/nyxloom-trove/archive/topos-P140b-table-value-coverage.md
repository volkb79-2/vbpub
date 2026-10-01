---
schema_version: 1
id: topos-P140b-table-value-coverage
project: topos
title: "Cover table profile and value contracts"
tier: luna-low
input_revision: "a1696b75"
depends_on: [topos-P140a-container-table-coverage]
session: fresh
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch: ["topos/tests/test_ui_table.py", "topos/nyxloom-trove/handoffs/topos-P140b-table-value-coverage.md"]
  forbid: ["topos/src/topos/ui/table.py", "topos/src/topos/ui/app.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "Profile discovery and normalization expose custom valid profiles and fall back predictably when a requested or configured profile is invalid."
    negative: "An invalid profile produces unsupported columns or a silently arbitrary layout."
    gate: topos-suite
  - id: O2
    observable: "Public table values visibly distinguish governance origin/drift, DAMON mode, byte/rate/ratio/percentage/microsecond values, and unavailable or unknown values."
    negative: "A meaningful table value is misformatted, loses its source distinction, or crashes."
    gate: topos-suite
  - id: O3
    observable: "Public metric sort keys rank name/tier/network/current-CPU/numeric/text/missing values deterministically."
    negative: "A sort key makes a valid value incomparable or sorts a missing CPU value as real data."
    gate: topos-suite
gates: [topos-suite]
escalate_if: ["the observable contract needs a production-code change", "a required registry metric cannot be constructed through public model objects"]
---

# P140b — Cover table profile and value contracts

## Context to read first

1. `topos/src/topos/ui/table.py`: `available_profiles` through
   `metric_sort_value`, plus `_format_metric` and `_profile_from_config` only.
2. `topos/tests/test_ui_table.py`: P140a fixtures and existing public API
   test style.
3. `topos/src/topos/registry.py`: `cpu_quota_us`, `cpu_period_us`, and
   DAMON/governance-related registered metrics.
4. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

1. Add public-API tests to `test_ui_table.py` for profile discovery and
   normalization: a configured supported custom profile, an unavailable request
   falling back to a valid configured default, and an invalid configured default
   falling back to `auto`. Include a malformed custom profile list that falls
   back to the normal built-in layout.
2. Through public `format_metric_value`, build real `EntityFrame` values that
   observe governance origin and no-drift special display, DAMON mode, and the
   registered byte, byte-rate, percentage, ratio, `damon_sample_age_s`, and
   `cpu_quota_us` unit formats. Also assert unknown/missing values degrade
   visibly without a fabricated value.
3. Through public `metric_sort_value`, assert deterministic tuples for name,
   tier, network source, numeric/text ordinary metrics, and both missing and
   present CPU trend. Do not call underscore helpers or modify source.

## Oracle

Run the declared tester-unified `topos-suite` gate. All assertions must use
the named public APIs and cover the corresponding profile/value/sort branches.

## Scope / forbid

Touch only the named test and handoff. Do not mock table internals, change
production code, introduce coverage exclusions, or alter the gate.

## BLOCKED rule

If a required contract cannot be expressed via the named public APIs without a
forbidden file, STOP. Write `BLOCKED: <specific reason>` to this handoff's LOG,
commit that log-only change, and exit.
