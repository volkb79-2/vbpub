# ciu-P40 — CIU-70: `pg:`/`minio:` probes resolve their container from the providing stack

**Branch:** `fix/ciu-P40-probe-container-resolution`
**Worktree:** `/workspaces/vbpub/.worktrees/ciu-P40-probe-container-resolution`
**Base HEAD at start:** `a78a0046` (tree clean, confirmed with
`git status --porcelain && git log --oneline -3` before any edit).
**Final base:** `main` `7d8cd0df` (`merge(ciu): ciu-P37 — CIU-71
docker compose --project-directory fix`), merge-base == `main` tip, 0 commits
behind.

`main` moved **three** times during this package (`a78a0046` → `858766d1` →
`aa6cf1fd` → `7d8cd0df`); the branch was rebased onto each. Entry 1 is one
tree throughout, re-hashed by each rebase. Entry 3 records the third rebase
and the conflict it required.

---

## Entry 1 — `ba69a40a` · `fix(ciu): CIU-70 -- pg:/minio: probes resolve their container from the providing stack`

*(the same tree, re-hashed by three rebases: `5d5dc1b8` on `a78a0046` →
`84b57560` on `858766d1` → `9227046d` on `aa6cf1fd` → `ba69a40a` on
`7d8cd0df`, the last of which also folded in the two review docstring notes
below. The superseded hashes appear here only so the gate runs quoted in
REPORT §4d can be tied to a commit; they no longer exist on the branch.)*

### Files

| file | why |
|---|---|
| `src/ciu/provisioning.py` | the two probe functions + three new helpers + `probe_ref`'s `stacks=` parameter |
| `src/ciu/deploy.py` | `provisioning_graph()` (new) + both `probe_ref` call sites |
| `docs/SPEC.md` | S13.2 ref-kind table + two new normative notes (mandated: the naming requirement is REMOVED as a constraint — a user-visible capability change) |
| `docs/FEATURES.md` | one bullet, matching the two S13.2 bullets already there |
| `KNOWN_ISSUES_TODO_BACKLOG.md` | CIU-70 `OPEN` → `FIXED` (this file's own header defines FIXED as "code, behavioral tests, SPEC, and user documentation landed together" — leaving it OPEN would contradict the file's stated contract) |
| `tests/tests/test_ciu_provisioning_ciu70_probe_container.py` | NEW — 19 tests, the whole CIU-70 contract |
| 9 existing test files | threaded `stacks=` / widened fake `probe_ref` signatures / added `stderr` to `SimpleNamespace` docker fakes |

### What changed, and why that shape

1. **`probe_ref(..., stacks=None)`.** `stacks` is the same
   `{stack_path: {"requires": [...], "provides": [...]}}` shape `lint_graph`
   consumes. Threaded — not smuggled through module state — so the graph a
   probe resolves against is always the caller's, visible in the call.
2. **`_resolve_probe_container(ref, config, stacks) -> (cname, reason)`.**
   Finds the stack(s) whose `provides` carries the exact ref via the new
   shared `provider_index`, then resolves the declared path through the
   existing `_stack_container_name`, as the brief directed — reusing the
   tested declared-path → final-segment → `container_name` path rather than
   inventing a second one.
3. **`provider_index(stacks)`** extracted from `render_graph`'s inline
   provider map and shared with it, so the drawn edge and the probed
   container cannot disagree about who provides what.
4. **`deploy.provisioning_graph(rendered)`.** THE non-obvious part of this
   package: `provisioning_preflight` builds its `stacks` map from
   `selection`, and live probing is invoked PER PHASE with only that phase's
   entries as `selection`. The stack that `provides` a ref a phase requires
   is, by construction, in an EARLIER phase — so a `selection`-scoped graph
   would have reported every cross-phase ref as "no stack provides it",
   turning this fix into a worse bug than the one it replaces. The graph
   handed to the probe is therefore built from the full `rendered` map, which
   the per-phase call already receives intact. `action_check` passes its own
   `stacks` because there `selection` IS the full selection.
5. **Reason strings** split what used to collapse (AGENTS.md
   "absence for emptiness"): see REPORT §3e for the exact set and the
   judgment calls behind the wording.

### Ripple

Contained, as the brief anticipated: `provisioning.py` (the two probes, their
helpers, `probe_ref`) and `deploy.py` (the two call sites + one new helper).
Nothing rippled into a different feature area, so the LOG-and-stop clause did
not fire. Nine test files needed a widened fake signature or a `stacks=`
argument; one (`test_ciu_provisioning_luna_medium40.py`) needed a real
rewrite, because the behaviour it pinned —
`test_probe_ref_uses_service_default_when_container_name_cannot_be_built`,
asserting the probe falls back to the literal `postgres`/`minio` — is exactly
the defect being removed. It is now
`test_probe_ref_falls_back_to_the_selector_when_container_name_cannot_be_built`
and pins the *provider stack's* path as the fallback.

### Two docstring notes from adversarial review round 1 (non-blocking, taken)

Folded into this commit at the third rebase rather than trailed as a
follow-up, so the commit stays self-contained:

* `_docker_level_failure` — its classification matches Docker's **English**
  error text, so a wording change or a localized client stops it recognizing
  either phrase. The docstring now says so, and says why that is acceptable
  here: the degradation is fail-safe *by construction*. The only thing lost is
  the more specific phrasing; the caller falls through to "could not be
  checked (rc=N)", which is still honest. It can never invert into the
  dangerous direction — "does not exist" is reachable only from `rc == 0`,
  which a failed `docker exec` never returns.
* `provisioning_graph` — the docstring argued why the wide graph must be used
  but not why it is *safe*. It now names the property that actually makes the
  design sound: `rendered` is not a repo-wide scan, it is itself
  selection-scoped and built per invocation
  (`render_selected_stacks(repo_root, profile, selection, ...)`), so widening
  from `selection` to `rendered` widens from "this phase" to "this run" and no
  further — a probe can never resolve its container from a stack this run did
  not select.

### Scope note (deliberate, flagged for review)

The brief scoped docs to `docs/SPEC.md` S13.2 and said "Do NOT touch anything
else". Two files outside that were still touched, both under AGENTS.md's
MANDATORY "user-facing docs are part of the change" rule:

* `docs/FEATURES.md` — one bullet. FEATURES.md already carries two S13.2
  bullets (`pg:schema` targets the app DB; `consul:token` path is
  config-driven); a capability change of exactly that class with no bullet
  would be the discoverability gap that rule exists to prevent.
* `KNOWN_ISSUES_TODO_BACKLOG.md` — CIU-70's status cell only, per that
  file's own definition of FIXED.

`README.md`, `docs/DESIGN-GUIDE.md` and `docs/CONSUMERS.md` were checked and
NOT touched: none of them asserts the `postgres`/`minio` naming requirement,
which is precisely CIU-70's finding (it was undocumented everywhere), so none
of them is made wrong by removing it.

---

## Entry 2 — `<this commit>` · `docs(ciu): ciu-P40 LOG + REPORT -- review round 1 closeout, clean gate`

`nyxloom-trove/reports/ciu-P40-LOG.md` and
`nyxloom-trove/reports/ciu-P40-REPORT.md`, written to their final state.
Replaces three earlier doc commits (`040738c3`/`116539f9`,
`0070644a`/`b238c98d`, `b399617a`) that were collapsed with
`git reset --soft ba69a40a` before this one was written: they narrated a
red-gate state that three upstream merges have since made obsolete, and
leaving three successive "here is the real verdict" commits on the branch
would make the record harder to verify, not easier. Their content is
preserved as REPORT §4d's gate-run history rather than deleted.

Markdown under `nyxloom-trove/reports/` only — no source, no tests, outside
assay's `source_roots`. Gating is covered in REPORT §4c: the gate ran on
`ba69a40a` (the tree this commit does not change) and again on this commit,
and the artifact left on disk is the second run, so
`.assay/verdict-ciu.json`'s `commit` equals the branch HEAD.

---

## Entry 3 — the third rebase (`aa6cf1fd` → `7d8cd0df`), folded into entry 1

Recorded here because it needed a real conflict resolution, not a fast-forward.

**Why.** Adversarial review round 1 returned ACCEPT-conditional on two
mechanical points, both closed by rebasing: (i) the branch was 22 commits
behind `main`, missing ciu-P36's merge `384993b6` — which fixes CIU-76, this
package's only remaining R0 failure — and ciu-P37's merge `7d8cd0df`; and
(ii) the `.assay/verdict-ciu.json` left on disk recorded `commit: aa6cf1fd`
with R1 `considered: 0, covered: 0` — a null judgment over zero lines. That
artifact was **`main`'s own baseline run**, which this package ran *after*
the branch run to prove the failures were pre-existing, and which therefore
overwrote the branch verdict at the shared artifact path. Real defect in my
procedure, correctly caught: the gate run whose verdict you want on disk must
be the LAST one. Fixed by ordering, and by the two-run scheme in REPORT §4c.

**Conflict.** One file, `KNOWN_ISSUES_TODO_BACKLOG.md`, one hunk, three rows:

| row | upstream (`main`) | this branch | taken |
|---|---|---|---|
| CIU-69 | `FIXED` (ciu-P36) | stale `OPEN` text | **upstream** |
| CIU-70 | stale `OPEN` text | `FIXED` (this package) | **branch** |
| CIU-71 | `FIXED` (ciu-P37) | stale `OPEN` text | **upstream** |

Resolved by taking `main`'s whole file (`git checkout --ours`) and re-applying
*only* the CIU-70 status cell — not by hand-merging three 2-4 kB table rows,
where a silent truncation of somebody else's just-landed entry would be very
easy and very hard to spot. `docs/SPEC.md` auto-merged (ciu-P37 edited S18,
this package edits S13.2); verified afterwards that both changes are present
and that no conflict marker survived anywhere in `ciu/`.
