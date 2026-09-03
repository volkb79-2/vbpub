---
kind: backlog-entry
schema_version: 1
id: NL-5
title: "R2 mutation-as-assay-claim + gate_canary.py/canary.py R3 reconciliation for tester-unified"
status: fixed
type: "feature"
severity: "low"
component: "gates"
provenance: "NL-1 item 3/reconciliation note, deferred by nyxloom-P48 (2026-09-02) matching ciu's own precedent scope"
priority: 3
filed_date: "2026-09-02"
closed_date: "2026-09-03"
closed_reason: "nyxloom-P98, merged 5bb8579d: gate_canary.py deleted outright (Assay's own R3 canary mechanism supersedes it, per the 2026-08-17 reorientation report's deletion-inventory analysis); GA1/GA4 retired end to end. No R2 mutation-as-assay-claim reconciliation was needed since mutation_gate.py's gate-judgment half was deleted wholesale, not reconciled -- only its pure Mutant/generate_mutants engine survives as mutants.py, used by tools/remote_mutation_audit.py, unrelated to R2/tester-unified"
---

## Observed mechanism and reproduction

## Why nyxloom owns it

## Proposed contract

## Oracles

## SPEC ownership

## Updates
