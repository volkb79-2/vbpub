# ciu-P42 — LOG (CIU-75, the v8 F2 identity cutover)

Worktree: `/workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2`
Branch: `fix/ciu-P42-cutover-identity-f2` — originally cut from `main` @
`332af5a1`, **rebased onto `main` @ `815c50d6`** (the ciu-P43 merge:
CIU-77/79/80/81) on 2026-08-31. The rebase is Entry 4, and it rewrote every
hash below; the pre-rebase hash is named in each entry so an earlier reference
to it still resolves.

One entry per commit, each naming the commit hash it describes.

---

## Entry 1 — `ebcc7ad7` (pre-rebase `c1985542`) — `feat(ciu)!: CIU-75 -- the overlay becomes the sole instance-fact source (BREAKING)`

**What it did.** The whole code + test cutover.

*Site derivation (not trusted from the entry).* The backlog's line numbers had
moved, as it warned. Re-derived with
`grep -rn '"ciu\.env"\|WORKSPACE_ENV' src/ciu/`, which found exactly twelve
exact-path constructions in the three named modules — `worktree.py` 1137,
1232, 2652, 2901, 3267, 3876, 4293; `engine.py` 971, 1221, 1538; `deploy.py`
2220, 3285 — matching the entry's count with shifted numbers. Four further
`ciu.env` uses live in `cli.py` (`ciu env` / `ciu env print`), and those are
deliberately NOT internal identity reads: they are the legacy export's own
shell-facing surface, which contract item 2 keeps.

*Design decision, recorded because a reviewer will ask.* The reader is a
text-level scan of the CIU-owned block, mirroring `upsert_generated_facts`'s
own documented ownership boundary — NOT a `render_global_chain` call. Three
reasons, each of which the chain render fails: (a) the overlay is a Jinja
template whose render needs the merged config it is a layer of, and CIU-74
made an undefined name a hard error, while the CIU-owned block is plain TOML
by construction; (b) six of the twelve sites read a checkout that is NOT this
process's repo root (a shared-infra reference, a budget candidate, a reap
group), whose committed chain may legitimately be absent or broken — the
`ciu.env` read they replace had no such dependency and neither may the
replacement; (c) the block is merged LAST, so its own bytes ARE the merged
value; a chain render could only agree with it.

*Two behaviour deltas, both deliberate and both documented in code, CHANGES
and SPEC S3.1c.* (1) The three child/render-environment sites (`_clean_in`,
`_sanitized_target_env`, `_resolve_budget_candidates`) now build
ambient-minus-identity + the target's own facts. The overlay carries identity
facts only; the alternative — passing just six variables where the whole
`ciu.env` used to go — would have broken any candidate template referencing a
machine fact, which under CIU-74's StrictUndefined is now a hard error.
(2) A DIRECTORY where the record belongs is now indeterminate rather than
silently absent: `ciu.env`'s `is_file()` guard folded it into "never
provisioned", the estate's absence-for-emptiness anti-pattern.

*The one existence-check.* `worktree._reap_uses_clean` was the only pure
`.is_file()` readiness signal, and it did NOT stay as-is. Post-cutover
`ciu clean` derives both its identity network and its identity compose project
from the overlay table, so a checkout carrying only a legacy `ciu.env` can no
longer clean itself; keeping the old check would have made it certify a
readiness that no longer holds. It now asks for the generated table's
PRESENCE and deliberately not its readability — a corrupt table answers "yes"
so `_clean_in` refuses loudly instead of the predicate demoting indeterminacy
into a bare `docker rm`.

*Test work.* 32 existing test files were migrated off `ciu.env` fixtures;
`write_instance_facts` was added to `tests/conftest.py` so a fixture goes
through the SHIPPED writer rather than hand-rolled TOML. Three existing tests
had to change their claims rather than their fixtures, and each is called out
in its own docstring:

- `test_ciu_worktree_budget.py::test_candidate_env_isolation_ambient_leak_does_not_apply`
  → `…_ambient_identity_never_leaks`. The old test proved NO ambient variable
  reached a candidate render; that is no longer true and cannot be, given the
  delta above. It now proves the property that actually matters: an ambient
  `INSTANCE_ID` set to a third value never reaches either candidate.
- `test_ciu_worktree_shared_infra.py::test_reference_with_no_global_config_at_all_refuses`
  → split into `…_no_deploy_identity_refuses` and
  `…_whose_own_chain_cannot_render_refuses`. A reference with no committed
  config now RENDERS (its gitignored overlay alone is a legal chain layer —
  true since CIU-60, and the old fixture was simply unrealistic in not writing
  one), so the refusal arrives one step later, from `container_name`. The
  render-failure arm is still covered, by a genuinely unparseable template.
- `test_ciu_render_selection_context.py`'s two S3.12 degradation arcs now
  monkeypatch the READER rather than corrupting the file: the overlay is also
  a config-chain layer, so corrupting the file fails the render long before
  step 12 — a strictly louder outcome, and not the arc under test. The three
  file-level corruption forms are proven against the reader itself in the new
  cutover suite.

*Acceptance oracle.* `tests/tests/test_ciu_identity_cutover_ciu75.py` — a real
`git init` + real `git worktree add` + the real `generate_ciu_env` (only its
host detectors pinned), then every fact-reading site exercised TWICE: once
with `ciu.env` unlinked, once with it replaced by undecodable bytes. Plus the
converse (`…_leaves_the_overlay_as_the_only_load_bearing_record`), without
which "still works with `ciu.env` deleted" would also be satisfied by a site
that reads neither record.

**Gate at this commit:** not run (docs still owed). Full suite green,
`3393 passed`, coverage `100%` line+branch via `run-ciu-tests.py`. (Both
numbers are as-written; Entry 4's rebase merged ciu-P43's tests in and the
post-rebase count is `3399`.)

---

## Entry 2 — `f8f29778` (pre-rebase `788908e2`) — `docs(ciu): CIU-75 -- SPEC S3.1c, CONSUMERS migration, BREAKING changelog; CIU-75 FIXED, CIU-82 filed`

**What it did.** The docs half, which AGENTS.md makes part of the change, not
a follow-up.

- `docs/SPEC.md` **S3.1c — Identity-source precedence** (new; the entry
  correctly noted no existing section owned it). Seven normative clauses: the
  sole source, the write-only export, the one-release WARN, the reader's three
  outcomes (absent / table-absent / indeterminate — never two), the scoped
  read and why a chain render must not be required, readiness meaning the
  table rather than the file, and the child/candidate environment rule.
- `docs/CONSUMERS.md` **§11b — "Migrating off `ciu.env` as an identity
  source"**: what changed, what does NOT break (with the working commands), a
  five-row pattern→replacement table, a paste-able `tomllib` one-liner, the
  four dstdns shapes found by the real grep, the sweep commands another
  consumer can run, and a post-migration sanity check that deletes `ciu.env`.
- `CHANGES.md` `## [7.7.0] - UNRELEASED` with the deliberate-minor override
  stated up front, **Adoption / Migration Notes** (action-needed vs
  safe-to-ignore), and `feat(ciu)!:`-marked BREAKING entries. Not folded into
  an ordinary bullet.
- `README.md`: the file-inventory line now says `ciu.env` is a legacy
  write-only export and adds a short "Where instance identity lives" block
  linking CONSUMERS §11b and SPEC S3.1c; the quick-start gains
  `eval "$(ciu env print)"`; the comparison-table row is renamed off `ciu.env`.
- `KNOWN_ISSUES_TODO_BACKLOG.md`: CIU-75 marked **FIXED (ciu-P42)** with all
  four acceptance boxes checked and each annotated with what was actually
  done; the header's "Last updated" line rewritten. **CIU-82 filed** for the
  dstdns notification (numbered 82 because CIU-81 was already taken by
  ciu-P38's scaffold filing — an ID collision caught before it landed, the
  same hazard this backlog has hit before).

**Consumer grep, as required.** `/workspaces/dstdns` WAS reachable (HEAD
`a1098ad8`). The entry's "plausible but unconfirmed" risk is **confirmed**,
not ruled out — four shapes, listed in CONSUMERS §11b and in the REPORT. The
load-bearing one is `dstdns/scripts/ciu/workspace_env.py`, a vendored stub
that PARSES `ciu.env` for the test-runner container. dstdns was NOT edited:
that is outside this worktree's scope, and estate convention files a
consumer-side finding in the consumer's backlog. CIU-82 exists so the
notification is not lost between repos.

**Gate at this commit:** `./run-gate.py ciu --worktree
/workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2` → `ciu: PASS (exit 0)`,
`run-gate: lane 'ciu' exit 0`, `GATE_EXIT=0`, with
`.assay/verdict-ciu.json`'s `"commit"` equal to `788908e2c94956abd37dd0ed8b6ef4b89cc77e62`
== HEAD *at that run* (the pre-rebase hash — Entry 4 rewrote it and re-ran the
gate against the rebased tip). No baseline/comparison gate was run at any point
in this package, so nothing could have overwritten that artifact. Verbatim
verdict and full artifact in `ciu-P42-REPORT.md` §6.

---

## Entry 3 — `67a588f8` (pre-rebase, same content) — `report(ciu): ciu-P42 -- CIU-75 REPORT + LOG gate/hash record`

**What it did.** Added `ciu-P42-REPORT.md` (per-site classification table for
all 12 sites, the two behaviour deltas, the delete-and-corrupt evidence, the
verbatim gate verdict plus the full verdict artifact, the dstdns sweep with
`file:line`, and the four acceptance criteria answered one by one) and filled
Entry 2's own hash and gate result into this LOG.

Report-only: no source, test or user-facing doc changed.

**Gate at this commit:** re-run after the rebase (Entry 4) — see there.

---

## Entry 4 — `ebcc7ad7`/`f8f29778`/`67a588f8` — the rebase onto ciu-P43 (`815c50d6`)

**Why.** ciu-P43 (CIU-77/79/80/81) merged to `main` as `815c50d6` while this
package was in flight, and it touches the SAME identity code: CIU-80 added
`HookContext.identity_unreadable`, set at BOTH S3.12 readers. This package
moves where those readers read FROM. Neither change is correct without the
other, so the reconciliation is the work, not the merge mechanics.

**Conflicts, and how each was resolved.**

| file | conflict | resolution |
|---|---|---|
| `src/ciu/deploy.py` | P43 made `_workspace_identity` return `(facts, unreadable)`; CIU-75 changed what it reads | BOTH: `-> tuple[dict, bool]`, reading `read_generated_facts(repo_root)`, `({}, False)` for absent, `({}, True)` for present-but-unreadable |
| `src/ciu/engine.py` | same shape at the STEP-12 inline reader | BOTH, and P43's comment about the now-deleted `if _env_path.is_file()` guard was corrected rather than carried forward stale |
| `src/ciu/hooks_runner.py` | **none — auto-merged** | the field's docstring was re-pointed off `ciu.env` onto the overlay table by hand; see the hazard note below |
| `tests/.../test_ciu_render_selection_context.py` | P43's pairing test corrupts the identity record | re-pointed at the overlay with a **non-string fact** (`instance_id = 7`) — valid TOML the config chain renders, which the identity reader refuses, so the oracle stays end-to-end instead of failing earlier in the render |
| `tests/.../test_ciu_deploy_actions.py` | P43's directory-case early return | dropped: post-CIU-75 a DIRECTORY is present-but-unreadable, so all three parametrized cases now warn and set the flag |
| `tests/.../test_ciu_identity_cutover_ciu75.py` | mine, against P43's new tuple | destructured, and each site's assertion tightened to `(… , False)` |
| `KNOWN_ISSUES_TODO_BACKLOG.md` | two "Last updated" headers | both kept, chained "Previously, …"; my stale "CIU-81 FILED" paragraph dropped because P43 FIXED CIU-81 |
| `CHANGES.md`, `docs/SPEC.md`, `docs/CONSUMERS.md`, `README.md` | header/section adjacency | both sets of entries kept; P43's S9.3 and CONSUMERS §10 `identity_unreadable` text re-pointed at the overlay, and a CIU-75 × CIU-80 bullet added to `[7.7.0]` since both ship in the same release |

**The hazard the reviewer named, checked explicitly.** `hooks_runner.py`
auto-merges cleanly, so a conflict resolution that dropped the
`identity_unreadable=` argument at the two `HookContext(...)` constructions
would leave the field permanently `False` with nothing failing. Both sites
carry it, both carry CIU-75's renamed snake_case keys, and both now have a
comment naming the two ways the pair can silently rot:

```
deploy.py:2554  instance_id=identity.get("instance_id"),
                network=identity.get("network"),
                identity_unreadable=identity_unreadable,
engine.py:1566  instance_id=_hook_identity.get("instance_id"),
                network=_hook_identity.get("network"),
                identity_unreadable=_hook_identity_unreadable,
```

`grep -n "HookContext(" src/ciu/*.py` returns exactly those two.
`test_identity_unreadable_agrees_between_check_preflight_and_real_run` (P43's,
now over the overlay) is the test that fails if either side is dropped.

**The re-derived contract.** CIU-75 does not just survive CIU-80 — it makes
CIU-80's field honest. P43 drew absent-vs-unreadable with `ciu.env.is_file()`,
which answers "absent" for a DIRECTORY named `ciu.env`; the overlay reader
answers `WorkspaceEnvError` for anything PRESENT it cannot read, directory
included. The estate's absence-for-emptiness anti-pattern was still live in
the field designed to defeat it.

**Also in this rebase (test hygiene the cutover itself made stale):** an
`import os` left unused when a chmod-based fixture became a reader injection;
the comment still describing that chmod; and
`test_engine_identity_read_survives_an_unparseable_ciu_env` →
`…_an_unparseable_identity_record`, since it no longer touches `ciu.env`.

**`main` moved again during this work** (`7c47a707`, run-gate-P03/RG-27). Not
rebased onto: `comm -12` over `git diff --name-only` from `815c50d6` shows
**zero** overlap with this branch's 39 files — it is entirely
`run-gate-project/` — so there is nothing to reconcile and a merge is trivial.

**Gate after the rebase:** `ciu: PASS (exit 0)`, `run-gate: lane 'ciu' exit 0`,
`GATE_EXIT=0`, verdict artifact `"commit": "67a588f8…"` == HEAD at that run,
now under assay **3.2.0** (P43 bumped the judge from 2.3.0) and verdict schema
8. Verbatim in `ciu-P42-REPORT.md` §6. A second run followed this LOG/REPORT
commit so that the LAST gate invocation of the package is the one against the
branch tip.
