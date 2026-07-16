# P72 — Admin action kill/update verbs — Frontier review (pass #2)

Reviewer: Opus high (frontier session, controller-workflow-v2 §6 pass #2 — the merge gate).
Date: 2026-07-13. Branch: `feat/topos-p72-admin-action-kill-update` (implementation
`22b2091`, self-review + doc fixup `e2317c4`). Verdict: **merged after review-fixes**.

## Headline

The package's three safety contracts — protected entities refused, a memory limit
below current usage refused, and one execution path — were **all satisfied only under
the test seams**, and the REPORT said "Deviations from handoff: None. All named
requirements and contracts are met."

Every gate this package exists for was green in its own suite and inert in
production:

- a `kill` from the CLI would never refuse a protected service;
- an `update --memory` from the CLI would never refuse an OOM-inducing limit;
- and `execute_plan("docker-kill", …, confirm="EXECUTE")` — the generic path — would
  run `docker kill <target>`, which defaults to **SIGKILL**, with no signal
  allowlist, no `--force`, and no protected check.

The handoff had pre-named exactly one of these ("**This is the contract most likely
to be silently skipped — it is the reason this package is flash-max**", contract 10)
and it was skipped anyway, with the skip disclosed as a "known gap" rather than a
deviation. This is the §6 same-tier blind spot in its purest observed form to date:
the implementer wrote the seam, wrote a test that injects the seam, watched it pass,
and reported the contract met.

## Findings

### F1 — Protected-entity refusal (contract 7) was inert in production

`flagged-by-pass-1: no` — pass #1 recorded contract 7 as met ("Injectable check,
runner-not-invoked test ✓") and listed the always-`False` default under "Known gaps"
as a *safe* default.

`kill_ops._default_protected_check()` returned `False` unconditionally, and
`cli._main_action` calls `execute_kill()` without passing `protected_check`. So the
production default was "nothing is ever protected". The two oracle-4 tests both
inject their own check, so they prove the seam exists — they cannot fail if the
production check is a stub. Worse, `execute_kill` wrapped the call in
`except BaseException: pass`, so a check that *raised* also fell through into the
kill: fail-open on the one gate that stands between an operator typo and a killed
production service.

Fix: the default now loads `[tiers] protected_services` and compares it to the target
the same way `collect/collector.py` does when it stamps `Entity.is_protected` (key or
name). A raising check is now a refusal (`is_protected is not False` → refuse),
matching the `is_root is not True` idiom already used in this module. New test
`test_default_protected_check_refuses_config_protected_target` drives `execute_kill`
with **no injected check** and asserts both the refusal and that an unprotected target
on the same config still proceeds; `test_protected_check_that_raises_refuses` covers
the fail-closed path.

### F2 — The below-current OOM guard (contract 10) was inert in production

`flagged-by-pass-1: no` — pass #1 recorded contract 10 as met, then described the
dead reader under "Known gaps" without noticing the two statements are incompatible.

`update_ops._default_current_memory_reader()` read `memory.current` only when the
target contained a `/`. But `catalog.validate_target` admits a docker-update target
only if it matches `_DOCKER_NAME_RE` (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`) or a
64-hex id — neither can contain `/`, and `_INVALID_TARGET_RE` rejects `/` outright.
So the reader returned `None` for **every target the CLI can reach**, and the guard's
condition (`current_usage is not None and parsed_memory < current_usage`) was
unreachable. `docker update --memory 100M` on a container using 4G would have been
executed, which is an instant OOM-kill — the exact scenario the contract names.

Fix, two parts:

1. The reader now resolves a container name/id to its cgroup key through one
   collector sweep — the same path `cli._resolve_container_target` already uses for
   `--container` — and reads `memory.current` from the resolved cgroup.
2. The guard is now **fail-closed**: if current usage cannot be established at all,
   the update is refused under the same `--below-current` override as a known breach.
   Silently proceeding on an unreadable usage is how the guard came to be theatre in
   the first place, and "I could not check" is not evidence of safety. A `--cpus`-only
   update cannot OOM anything and is not gated on the read.

This is a deliberate tightening beyond the handoff's letter (which specifies refusal
only for a *known* breach); it is recorded in the REPORT's Deviations section and in
OPERATIONS.md rather than being slipped in.

### F3 — The new kinds in `EXECUTION_ALLOWLIST` opened a bypass of all three gates

`flagged-by-pass-1: no`.

`EXECUTION_ALLOWLIST` gates the **generic** `execute_plan()` path, whose argv comes
from the catalog's argument-free builders (`_docker_kill` → `[docker, kill, target]`).
Admitting `DOCKER_KILL`/`SYSTEMD_KILL`/`DOCKER_UPDATE` therefore made
`execute_plan("docker-kill", "c1", admin=True, confirm="EXECUTE")` a working,
audited, root-gated **SIGKILL** (docker `kill` defaults to KILL) that never touches
`validate_signal`, `--force`, or the protected check. P49 had already established the
correct pattern for an argument-carrying verb — `systemd-set-property` is *excluded*
from the allowlist and reachable only via `execute_set_property` — and the handoff's
contract 1 said to reuse that kernel, not widen it.

Fix: the three kinds are excluded from `EXECUTION_ALLOWLIST` (comment states why),
the now-dead branches inside `validate_target`'s allowlist arm are removed (the
kinds' real validation was already sitting in the trailing blocks, previously
unreachable), and `test_actions.py`'s allowlist expectation is restored to the
start/stop/restart set it asserted before. New test
`test_execute_plan_refuses_kill_and_update_kinds` asserts all three kinds are refused
through the generic path **and that the runner was never invoked**.

### F4 — Oracle 6 asserted a refusal but not the required message

`flagged-by-pass-1: no` — pass #1 scored oracle 6 "NO — would succeed" as
non-hollow.

The handoff requires that an `update` against a systemd target "exits 2 and the
message names `set-property`" — the point being that the operator is *redirected*,
not merely blocked. `test_update_systemd_target_refused` asserted only
`outcome == "refusal"`, which also passes if the refusal is the unrelated
invalid-target error. Now asserts `"set-property" in result.stderr`.

### F5 — LOG dated 2026-07-17 (four days in the future)

`flagged-by-pass-1: no` — pass #1 checked this item explicitly and certified it:
"LOG date: `2026-07-17` — current date ✓". Corrected to 2026-07-13.

### F6 — Docs inserted mid-section, splitting the `topos squeeze` runbook entry

`flagged-by-pass-1: n/a` — the doc updates *were* pass #1's own fix (its one real
catch, F1 in its report: the implementer skipped every doc update).

The kill/update guidance was appended as indented paragraphs *inside* the `topos
squeeze` bullet, landing between squeeze's safety requirement and squeeze's "Default
options" continuation. Moved to top-level bullets in the Safety Model list, next to
the other action verbs, and rewritten to state the corrected semantics (config source
of `protected_services`, fail-closed override, name-vs-id limit, and that the generic
`EXECUTE` path cannot reach these verbs).

### Accepted as-is

- **Contract 9 (memory parser) deviates and the deviation is right.** The handoff
  said to reuse "P49's parser"; the implementer reused `squeeze.parse_size`. P49's
  `validate_memory_high_value` accepts the literal `max` and rejects suffixes —
  `memory.high` semantics, not `docker update --memory` semantics. Keeping
  `parse_size`. Recorded as a deviation in the REPORT, which had reported none.
- `execute_kill`/`execute_update` are structurally near-copies of `execute_plan`'s
  gate/audit/runner body (~200 lines each). This duplicates the P46 kernel's *shape*
  while reusing its parts, and it is what `execute_set_property` already did in P49 —
  so the precedent, not this package, is where that debt belongs. Flagged as a carve
  candidate rather than churned here mid-wave (see the carve).

## Gates

Rerun by the reviewer in the clean package venv (`/workspaces/vbpub/.venv`, Python
3.13.5, pytest 9.1.1 — no `schemathesis`, so the handoff's `-W error` gate command
runs verbatim). The agent's own greens were not trusted: its REPORT quotes a full
suite run **without** `-W error` (its self-review says so outright) and with a
pre-existing failure. Merge evidence is committed on `main`.

## Pass-#1 overlap (workflow v2 §6 trial metric)

0 of 6 findings flagged by pass #1 (0%). Its one real catch — the implementer had
skipped every doc update — was a mechanical omission, and its fix then introduced F6.
On F1, F2 and F5 it did worse than miss: it walked the checklist, reached the right
questions, and certified the wrong answers ("contract 7 ✓", "contract 10 ✓", "LOG
date 2026-07-17 — current date ✓") while the same document listed the inert
implementations under "Known gaps" without registering the contradiction.

Consistent with §6 and with the P70 lesson: a self-review can only check what the
carve made checkable, and this carve's most emphatic warning ("the contract most
likely to be silently skipped") was aimed at the implementer, not phrased as a probe
the self-review had to execute. The actionable carve-side fix is to state such
contracts as a **self-review probe with a named production call path** — e.g. "prove
the guard fires with no injected seam" — which is what the new regression tests now
encode permanently.
