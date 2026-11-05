---
kind: backlog-entry
schema_version: 1
id: CP-60
title: "R2 survivors of cgprofile 2.1.0: machine.py/metrics.py boundaries were untested"
status: fixed
type: "bugfix"
severity: "low"
component: "tests"
provenance: "R2 campaign on bb1194bbf (base cgprofile-v2.0.0), 69 judged, 4 MUTANTS_SURVIVED, 2026-11-05"
filed_by: "cgprofile-survivors"
filed_date: "2026-11-05"
closed_date: "2026-11-05"
closed_reason: "behavioural tests added; each mutant verified killed by hand-applying it"
---

## Observed mechanism

Survivors: `machine.py:57` `LtE->Lt` (score ceiling), `machine.py:257` `And->Or` (status/payload agreement), `machine.py:263` `Or->And`
(`model is None or cpus_online is None` redundant-looking guards), `metrics.py:235` `Gt->GtE` (steal field guard).

## Fix

Tests only; no dead code: the 263 guards are NOT redundant (a record with no `cpu_model` and an unreadable live model compares `None == None` as
a match without them). Tests: `test_a_score_is_valid_up_to_and_including_the_ceiling`, `test_a_record_at_the_score_ceiling_is_still_measured`,
`test_status_and_payload_must_agree_before_a_record_is_trusted`, `test_unverifiable_model_is_stale_even_when_the_record_has_no_model_either`,
`test_unverifiable_cpu_count_is_stale_even_when_the_record_has_none_either` (tests/test_machine.py),
`test_cpu_jiffies_seven_fields_end_before_steal` (tests/test_metrics.py).
