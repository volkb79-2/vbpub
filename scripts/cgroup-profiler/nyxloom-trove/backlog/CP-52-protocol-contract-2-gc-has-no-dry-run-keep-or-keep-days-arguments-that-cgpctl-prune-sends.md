---
kind: backlog-entry
schema_version: 1
id: CP-52
title: "PROTOCOL contract 2 gc has no dry_run / keep / keep_days arguments, but CGPCTL-SPEC prune needs them (daemon half belongs to package 3)"
status: open
type: "feature"
component: "serve"
priority: 2
provenance: "cgp2 package 2 (cgpctl), 2026-11-04: wire verification against serve.py handle_gc and docs/PROTOCOL.md section 10"
filed_by: "cgp2-pkg2"
filed_date: "2026-11-04"
---

## Problem

`docs/CGPCTL-SPEC.md` (`prune`) promises `--dry-run`, `--keep N` and `--keep-days D` with a "list what would go" preview and per-caller
ownership. `docs/PROTOCOL.md` section 3 still defines `gc` as "no arguments", and `serve.py` `handle_gc` ignores `args` and always
applies the daemon's configured retention to everyone's sessions. Section 10 of the protocol does not extend `gc`.

## What cgpctl does today

`cgpctl prune` sends `gc` at contract 2 with `{"dry_run": true, "keep"?, "keep_days"?}` first (preview), asks for confirmation, then repeats
with `dry_run: false`. Against the current daemon (contract 1 only) it exits 3 `contract-unsupported` before sending anything, so nothing is
removed by accident. The fake-daemon tests pin the contract-2 shape.

## Proposed contract (package 3 / CP-48)

Add to PROTOCOL.md section 10.4: `gc` takes optional `dry_run` (bool, default false), `keep` (int >= 0), `keep_days` (number > 0), both
refused above the daemon's configured maxima with `bad-argument`; the answer is `{"ok": true, "dry_run": bool, "removed": [...], "kept": N}`
where a dry run removes nothing; a non-root peer's candidates are restricted to sessions it owns.

## Oracles

A dry run leaves the sessions directory byte-identical; a non-owner peer never sees or removes another uid's session; over-maximum
arguments return `bad-argument`.
