---
kind: backlog-entry
schema_version: 1
id: NL-23
title: "estate tool-skill copies drift from released tool versions; no version-banner check or consumer sync mechanism"
status: open
type: "feature"
severity: "medium"
provenance: "dstdns 2026-09-30, skill refresh dstdns@fc2fd28a (assay 7.2.0, ciu 7.15.0, nyxloom 0.8.1, cmru 5.4.2)"
filed_date: "2026-09-30"
---

## Observed
dstdns keeps vendored copies of the estate's tool skills. On 2026-09-30 they were found behind the installed tools and were refreshed by hand in dstdns@fc2fd28a: assay 7.2.0, ciu 7.15.0, nyxloom 0.8.1, cmru 5.4.2. One concrete drift: cmru targets became positional and `--project` is now rejected, which the old skill still taught. The canonical copies under `vbpub/<tool>/.claude/skills/` (assay-cli, ciu-cli, ciu-stack, cmru-cli, run-gate-cli, nyxloom-*, pwmcp-fetch) carry their own "tool versions as of last verified update" banners, but nothing checks a banner against the tool's released version.

## Why this tool owns it
The skills ship with each tool; consumers (dstdns) vendor them. A consumer cannot know a canonical copy is stale, and each consumer refreshing by hand repeats the work and drifts between consumers. Estate tooling (nyxloom's lint/CI) is the natural owner of a cross-tool consistency check.

## Proposed mechanism (pick one, or both)
1. Version-banner check: a machine-readable banner (`tool`, `version`) in each SKILL.md, and a check (CI step or `nyxloom lint` rule for vbpub) that the banner is at least the tool's current `pyproject`/CHANGES release, failing when a release lands without a skill touch.
2. Single-source plus consumer sync: a `nyxloom skills sync` verb that copies canonical skills into a consumer's `.claude/skills/` and rewrites the "Vendored copy" header with the source commit, plus a `--check` mode that fails when the vendored copy differs.
Do not edit the skills as part of this entry.

## Oracles
A release bump with an unchanged banner fails the check; a consumer copy that differs from canonical fails `--check`; a controlled wrong implementation that compares only file existence must fail.

## Spec owner
STANDARD (trove/skills layout), AGENTS.md root guide.
