---
kind: backlog-entry
schema_version: 1
id: CP-59
title: "the cgprofile-scores/1 fallback table ships empty: source well-attributed single-core entries"
status: open
type: "feature"
component: "daemon"
priority: 3
provenance: "rigor-cp1 2026-11-05: the sources (Primate Labs published charts) could not be read in the package session, and scores must never be invented"
filed_by: "rigor-cp1"
filed_date: "2026-11-05"
---

## Problem

`src/cgprofile/data/cgprofile-scores.json` has `entries: []`. Until entries exist, a host without a valid `cpu-benchmark.json` reports
`speed: null` with `NO_HOST_MEASUREMENT_AND_MODEL_NOT_IN_TABLE` (an honest unknown, but no normalisation basis).

## Expected

Follow `docs/SCORES-TABLE.md`: for each CPU model of the estate's hosts (and the two Geekbench baseline machines: Core i7-12700 for GB6,
Ryzen 7 7700 for GB7, both 2500 by definition), add the model string exactly as `/proc/cpuinfo` reports it, the published single-core score, the
public source URL and the retrieval date. Do not scrape or copy the database; cite it. The mdt host-setup `cpu-benchmark` run on each host makes
the table unnecessary for that host.

Also unverified (from HB-1): the real Geekbench 7 stdout/result-page layout.
