---
kind: backlog-entry
schema_version: 1
id: NL-69
title: "Document the implementation/review route switch when a Luna usage limit lasts for days"
status: open
type: "feature"
severity: "medium"
component: "controller"
priority: 2
filed_by: "backlog-filing8, 2026-11-12 controller job record"
filed_date: "2026-11-12"
---

## Problem

The controller route doctrine names Luna, DeepSeek and OpenCode trailers, but
`scripts/controller/rules.md:1-49` gives no action for a Luna usage limit that
will not clear for days. `scripts/controller/codex-impl.sh:24-39` supports
Luna and DeepSeek as explicit routes, so a switch is mechanically available,
but operators must infer it from wrapper source.

On 2026-11-12 a Luna usage-limit response carried a dated reset several days
away and killed six in-flight jobs. The resume helper on `main` recognizes
only the time-only reset form (`scripts/controller/resume-usage-limited.sh:52-73`);
the dated form is fixed on the pending `ctl-hardening2-20261112` branch.

## Wanted

Add a short rule to `scripts/controller/rules.md`: when Luna is unavailable
for days, start new implementation work on the DeepSeek implementation route
and route independent review to Sonnet; record both routes and their exact
trailers, preserve resumable sessions, and do not describe the switch as a
transparent fallback or infer its trigger from a short usage window.

## Close when

`scripts/controller/rules.md` names the days-long trigger, the DeepSeek
implementation and Sonnet review switch, the route evidence and trailer
obligations, and the resume/session-preservation boundary; the controller
documentation test pins that closed vocabulary, and the controller suite passes
on a clean final commit.
