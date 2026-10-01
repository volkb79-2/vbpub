---
schema_version: 1
id: ciu-P26-ciu25-lease-schema-and-labels
project: ciu
component: worktree+engine
title: "CIU-25 foundation: worktree-instance record schema v2 adds an explicit lease (holder/mode/expiry), ciu up stamps a ciu.instance/ciu.repo-root ownership label on every container/volume/network it creates, ciu clean/worktree rm clear the lease on success — the substrate a future reap command destroys resources by, not the destroyer itself"
tier: implement-4
input_revision: "13c039ac"
source: {kind: research, ref: "CIU-25 Docker-resource reap design, controller session 2026-08-25, grounded in KNOWN_ISSUES_TODO_BACKLOG.md#CIU-25 and live source at src/ciu/worktree.py (CIU-28 record schema, worktree.branches.v1 precedent)"}
stack: none
depends_on: [P25]
session: fresh
scope:
  touch:
    - "src/ciu/worktree.py"
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_worktree_lease.py"
    - "tests/tests/test_ciu_worktree_lifecycle.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P26-ciu25-lease-schema-and-labels-LOG.md"
  forbid:
    - "src/ciu/composefile.py"
    - "src/ciu/config_model.py"
    - "src/ciu/provisioning.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-schema-v2
    observable: "`WORKTREE_INSTANCE_SCHEMA_VERSION` (worktree.py ~line 56) bumps to 2. The record gains an optional `lease` field: `null`, or an object `{holder: str, acquired_at_utc: str, renewed_at_utc: str, expires_at_utc: str|null, mode: \"held\"|\"perpetual\"}`. `expires_at_utc` is REQUIRED (non-null) when mode is 'held' and FORBIDDEN (must be null) when mode is 'perpetual' — a mismatch is a tagged validation error. `_record_from_dict` (~142-200)'s exact key-set comparison (~150) is extended to require `lease` for schema_version==2 records. A schema_version==1 record (missing `lease` entirely) is read successfully and treated as `lease: None` IN MEMORY ONLY — it is never silently rewritten to v2 on a mere read; only an operation that legitimately mutates the record (acquire/renew/release, O3) upgrades it on write. All timestamps are ISO-8601 with an explicit UTC offset (mirror the existing `_utc_now` helper ~line 231's format exactly) — a naive (offset-less) timestamp anywhere in a lease is a validation refusal, not a lenient parse."
    negative: "silently rewriting a v1 record to v2 on a plain read (list/inspect); accepting a naive timestamp; accepting mode='held' with expires_at_utc=null or mode='perpetual' with a non-null expiry"
    gate: "tester-unified"
  - id: O2-ttl-config
    observable: "`[ciu.worktree]`'s existing closed-key table (resolved by `resolve_max_concurrent_instances`, worktree.py ~2355-2400, which already hard-refuses unknown keys at ~2374) gains ONE new recognized key: `lease_ttl_hours` (positive number). Absent -> no lease is acquired by O3's `ciu up` wiring at all (lease stays null forever for that project) — this is the additive default: a consumer who configures nothing gets zero lease behavior, zero new expiry risk to anything already running."
    negative: "defaulting lease_ttl_hours to some nonzero value when absent (this would start expiring leases for consumers who never opted in — exactly the kind of default this estate's AGENTS.md calls a hazard)"
    gate: "tester-unified"
  - id: O3-lease-lifecycle
    observable: "When `[ciu.worktree].lease_ttl_hours` IS configured: `ciu up`, when run against a checkout that has a `ciu.worktree-instance.json` record (i.e. a managed worktree instance — a PRIMARY/unmanaged checkout is untouched, has no such record, gets no lease logic at all), acquires or renews a `mode: \"held\"` lease with `expires_at_utc = now + lease_ttl_hours` (holder = `ciu@<hostname>:<INSTANCE_ID>`, reusing whatever hostname/instance-id resolution this codebase already has — do not invent a new one). `ciu clean` and `ciu worktree rm`, ON SUCCESS ONLY, clear the lease (`lease: null`) — a failed clean/rm leaves the lease exactly as it was (this is deliberate: a failed teardown should not erase the evidence that something still owns these resources). Add `ciu worktree lease LOGICAL (--extend DURATION | --perpetual | --release) [--json]` as an explicit operator verb — `--extend` sets mode=held with a new expiry `now + DURATION` (accept the same duration-string format `_seconds` elsewhere in this codebase already parses, e.g. '24h'), `--perpetual` sets mode=perpetual (expires_at_utc=null), `--release` sets lease=null unconditionally. Wire it into cli.py's `_USAGE`/`_VERB_HELP` and the worktree verb dispatch chain (mirror the existing `worktree inspect`/`worktree branches` inline pattern)."
    negative: "acquiring a lease for a PRIMARY/unmanaged checkout (only managed worktree-instance checkouts with an existing record participate); clearing the lease on a FAILED clean/rm; `ciu worktree lease` requiring the instance to be currently up/running (it must work on a stopped instance too — releasing/extending a lease is independent of whether containers are currently running)"
    gate: "tester-unified"
  - id: O4-ownership-labels
    observable: "`ciu up`, for a managed worktree instance (same gating as O3 — has a `ciu.worktree-instance.json` record; a PRIMARY checkout's containers/volumes/networks are NOT newly labeled by this package, out of caution against changing primary-instance behavior), stamps two labels on every container, volume, and network it creates: `ciu.instance=<INSTANCE_ID>` and `ciu.repo-root=<PHYSICAL_REPO_ROOT>` (read from that workspace's own generated ciu.env by exact path, matching this codebase's existing 'never read ambient identity, read the workspace's own generated record' discipline — see CIU-41's fix in warn_policy/workspace_env.py if you need the precedent for 'exact-path read, never ambient'). Locate the exact label-injection point (`engine.py`'s overlay/label construction that composefile.py's generate_overlay-style mechanism consumes — this package's scope.forbid excludes composefile.py itself, so the injection must happen on the ENGINE.PY side, feeding label values INTO whatever composefile.py already renders, not by editing composefile.py's own label-merging logic; if this proves impossible without touching composefile.py, that is a real BLOCKED finding, not a reason to work around the forbid list)."
    negative: "labeling a PRIMARY/unmanaged checkout's resources (out of scope, more caution warranted there); reading INSTANCE_ID/PHYSICAL_REPO_ROOT from the ambient environment instead of the workspace's own generated ciu.env by exact path (the exact CIU-41 contamination species this whole codebase has spent multiple packages fixing — do not reintroduce it here)"
    gate: "tester-unified"
  - id: O5-docs
    observable: "docs/SPEC.md documents the v2 record schema (lease field, mode enum, the required/forbidden expiry pairing), `[ciu.worktree].lease_ttl_hours`, `ciu worktree lease`'s three modes, and the ownership label pair — under S16 (worktree lifecycle, extending CIU-28's existing normative section). docs/CONFIG.md documents `lease_ttl_hours` in the existing worktree config table. CHANGES.md Unreleased entry. `KNOWN_ISSUES_TODO_BACKLOG.md`'s CIU-25 row (search for it) is updated: NOT to FIXED (the reap verb itself is a SEPARATE, later package, ciu-P27) but to a PARTIAL state naming this package as the lease/label foundation and pointing at the follow-up."
    negative: "marking CIU-25 FIXED in this package (the actual reap/destroy behavior doesn't exist yet — that's ciu-P27)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "no clean injection point exists for O4's labels without editing composefile.py (forbidden) — BLOCKED naming exactly what you traced and why every option requires the forbidden file; this is a real, plausible outcome given the deliberate forbid boundary, and the RIGHT thing to do here is stop and report, not to widen scope.touch yourself"
  - "the hostname/instance-id resolution O3's lease holder string needs doesn't already exist somewhere reusable — BLOCKED naming what you searched and what you'd need to invent, rather than inventing a new identity-resolution mechanism ad hoc"
mutexes: [merge-lane]
review_focus:
  - "a v1 record reads successfully as lease:null and is genuinely NOT rewritten by a mere `worktree inspect`/`worktree list` (only an explicit lease mutation writes schema_version:2)"
  - "a PRIMARY checkout is provably untouched by both lease acquisition and label stamping — construct a fixture and check"
  - "labels are read from the workspace's OWN ciu.env by exact path, never the ambient process environment (construct a fixture with a DIFFERENT ambient INSTANCE_ID than the workspace's generated one and confirm the label uses the workspace's own value)"
  - "a failed ciu clean/worktree rm leaves the lease untouched, not cleared"
---

# ciu-P26 — CIU-25 foundation: lease schema + ownership labels

## Context to read first

1. `KNOWN_ISSUES_TODO_BACKLOG.md#CIU-25` (search for it) — the five states a real design distinguishes; this package builds the substrate two of them need (owned-with-lease, lease-expired). The others (checkout-missing, orphaned/unattributable, partial-cleanup) are ciu-P27's concern — this package does not implement detection or destruction of anything, only the record schema and the labels a LATER package will read.
2. `src/ciu/worktree.py` in full sections: `WORKTREE_INSTANCE_SCHEMA_VERSION` (~56), the record dataclass/`_record_from_dict` (~142-200, especially the exact key-set comparison ~150 and the existing `allocating`/`ready`/`recovery-required` state enum ~57-62), `_utc_now` (~231), `resolve_max_concurrent_instances` and the `[ciu.worktree]` closed-key table (~2355-2400, especially the unknown-key refusal ~2374 — mirror this discipline exactly for `lease_ttl_hours`), and the `worktree branches` JSON envelope (~972-1058) as your general "closed vocabulary + versioned envelope" style reference even though this package doesn't ship a survey verb itself.
3. `src/ciu/engine.py` — trace where `ciu up` currently constructs container/volume/network labels today (search for `deploy.labels.prefix`, `com.docker.compose`, or wherever this codebase's own label injection already happens) to find O4's injection point BEFORE assuming one exists; this is genuinely uncertain per this handoff's own `escalate_if` — investigate honestly.
4. `src/ciu/workspace_env.py` or wherever `INSTANCE_ID`/`PHYSICAL_REPO_ROOT` are read by EXACT PATH from a workspace's own generated `ciu.env` (not the ambient environment) — this is the exact discipline CIU-41/CIU-47's fixes established; reuse that reading mechanism, do not write a new ambient-environment read.
5. `src/ciu/cli.py` — `worktree inspect`/`worktree branches`'s inline-argparse verb pattern, to mirror for `ciu worktree lease`.

## Work

1. Schema v2 + lease field + validation (O1).
2. `lease_ttl_hours` config key (O2).
3. Lease acquire/renew/release lifecycle wired into `ciu up`/`ciu clean`/`worktree rm`, plus the explicit `ciu worktree lease` verb (O3).
4. Ownership label stamping on `ciu up` for managed instances (O4).
5. Docs + CIU-25 backlog row update to PARTIAL (O5).

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

No live Docker in tests — fake seams only, per this codebase's existing convention (grep `tests/tests/test_ciu_worktree*.py` for the fixture style already used for schema/lifecycle tests).

## BLOCKED rule

Per `escalate_if` — both listed triggers are real, plausible outcomes for this package, not edge cases. If either fires, write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P26-ciu25-lease-schema-and-labels-LOG.md`, commit what you have, and exit. Do not widen `scope.touch` yourself to work around a forbidden-file finding.
