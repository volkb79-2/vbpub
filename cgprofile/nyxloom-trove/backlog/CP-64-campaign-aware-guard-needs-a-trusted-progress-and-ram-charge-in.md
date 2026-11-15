---
kind: backlog-entry
schema_version: 1
id: CP-64
title: "Campaign-aware guard needs a trusted progress and RAM-charge input"
status: open
type: "feature"
severity: "high"
component: "guard"
context_estimate: "medium"
provenance: "fleetd MVP-B-lite handoff; compute-host campaign design rev 5 §§3.6-3.7, 4.5, WP7"
filed_by: "Codex fleetd MVP-B"
spec_owner: "docs/PROTOCOL.md guard contract"
priority: 1
filed_date: "2026-11-13"
---

## Observed mechanism and reproduction

The compute-host campaign design ranks a reclaimable lane by useful progress
per RAM and makes the host guard the only component allowed to freeze,
reclaim, or thaw that lane. fleetd MVP-B-lite can refresh named-job progress
and report an owner per finished candidate, but it has no campaign-aware
guard input contract. cgprofile therefore cannot distinguish a productive
campaign, a duplicate re-queued batch, a stale progress stream, and an
unreachable host using fleetd's source of truth.

## Why cgprofile owns it

cgprofile owns host-local PSI sampling, victim ranking, report/enforce mode,
and all cgroup actions. fleetd owns candidate progress, job state, and
single-owner campaign results. The boundary needs a reviewed versioned
read-only input; the guard must not probe remote hosts itself or infer campaign
ownership from runner directory names.

## Proposed contract

- Define the campaign progress and RAM-charge input with fleetd and its
  consumer before implementation. It must distinguish host/job identity,
  planned and finished candidate counts, latest trustworthy progress time,
  memory charge, duplicate/requeued work, and stale, unreachable, or missing
  evidence. It must be local to the host and require no second SSH probe.
- A stale, malformed, incomplete, or unowned progress record cannot authorize
  an enforcement action. Victim ranking uses the design's least-progress-per-
  RAM rule with deterministic tie-breaking and exposes the evidence used.
- Preserve the guard's memory-PSI-full trigger, useful-lane-CPU soft/hard
  ordering, report mode default, freeze/reclaim/thaw discipline, and control
  plane protection. Assay must invalidate a frozen candidate before it can
  become final or be re-queued.
- Keep report mode available while the input and policy are qualified. Enable
  enforcement only after the host-owned config and required CIU amendment are
  accepted for the host.

## Oracles

- A fixture with two campaigns and different progress-per-RAM values reports
  the same victim and score on repeated reads; candidate arrival order does
  not change the decision.
- Duplicate or re-queued jobs count each finished candidate once and do not
  inflate useful progress. Missing owners, stale progress, unreachable hosts,
  and corrupt input produce a report-only refusal with a specific reason.
- Trace replay proves memory PSI full is required; useful CPU above the soft
  threshold suppresses parking, while the hard threshold follows the host
  policy. A frozen candidate never becomes a final Assay result.
- Report mode performs no cgroup writes. Enforcement writes only beneath the
  admitted lane scope, never acts on the control plane, and thaws only under
  the accepted low-pressure and psi-guard ordering rules.
- The fleetd input producer and cgprofile consumer run in tester-unified with
  fake clocks, cgroup trees, and progress records; no host, SSH, or Docker
  behavior is needed by these tests.

## SPEC ownership

cgprofile `docs/PROTOCOL.md` guard contract and the campaign progress/ownership
contract jointly maintained with fleetd FD-012. Relates to campaign design
rev 5 §§3.6-3.7, 4.5, and WP7 in §5.2.

## Updates
