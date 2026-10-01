---
kind: backlog-entry
schema_version: 1
id: NL-28
title: "nyxloom lint L4 (P78) fires on any two quoted tokens, including file paths, and once per oracle"
status: open
type: "bugfix"
severity: "low"
component: "nyxloom-lint"
provenance: "dstdns 2026-09-30 (P136, P143, P189 carves)"
filed_date: "2026-09-30"
---

## Observed mechanism and reproduction

`_check_l4` (`src/nyxloom/lint.py` l.877-900) warns "universal contract with enumerated oracle subset (P78)" when (the body OR the oracle observable matches `\b(every|all)\s+\w+\s+(field|record|column|key|property)s?\b`) AND the oracle's observable contains >= 2 backticked or quoted tokens. It (1) scans the whole body including code fences, tables and quoted prose, (2) counts ANY two quoted tokens, including file names, commands or test ids, not identifiers that enumerate members of the universal set, and (3) emits one warning per oracle with no dedupe and no link between the universal phrase and the oracle's tokens.

Concrete instances judged benign by the reviewer: `dstdns/nyxloom-trove/reviews/dstdns-P143-carve-review-r1.md` l.27 ("six L4 'universal contract with enumerated oracle subset' (the P78 heuristic; benign here)"); `dstdns/nyxloom-trove/archive/dstdns-P136-carve-review-r1.md` l.362 (L4 warning listed as the only lint finding, zero errors); `dstdns/nyxloom-trove/archive/dstdns-P189-infra-cluster-analytics.md` l.~643 (the handoff has to argue in prose that its "all five agree" wording is not a universal contract). Not reproduced live; the firing condition is read from source.

## Why nyxloom owns it

The heuristic is nyxloom's (P78). A rule that fires six times on one handoff and is dismissed as "benign here" trains reviewers to skim warnings, which is the failure P78 was meant to prevent.

## Proposed contract

Fire only when the universal phrase and the enumeration are related: the universal phrase's noun (field/record/column/key/property) appears in the same oracle's observable, or the quoted tokens look like members of that noun class (identifier-shaped, not path- or command-shaped: exclude tokens containing `/`, spaces or a file extension). Report once per handoff listing the oracle ids, not once per oracle. Strip code fences before scanning the body.

## Oracles

- A body containing "every record field" plus an oracle enumerating two field-name identifiers warns once.
- The same body with an oracle quoting two file paths does not warn.
- Six oracles that each match produce one finding naming all six ids.
- A controlled wrong implementation that keeps the per-oracle any-two-quotes condition must fail the file-path oracle.

**Found in:** dstdns 2026-09-30 (review of earlier carves P136, P143, P189; first seen P78 era).
