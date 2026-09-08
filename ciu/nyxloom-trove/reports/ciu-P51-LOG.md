# ciu-P51 — implementer LOG

Branch: `ciu-p51-bundle` (worktree `/workspaces/vbpub/.worktrees/ciu-p51-bundle`).
Input revision: `2bcdd708` (the carve commit), post ciu-P50.

Three independent parts, disjoint files, per the handoff
`ciu/nyxloom-trove/handoffs/ciu-P51-ciu91-ciu58-ciu96-ciu88.md`.
CIU-93 (`ciu host enroll`) deliberately untouched — out of scope for this
dispatch; nothing under `vbpub/cmru/`, `ciu8/` or
`modern-debian-tools-python-debug/` was read or written.

---

## Order of work

1. **Part A investigation first**, because the handoff's step 3 asked to
   confirm marker registration before relying on `pytest.mark.xdist_group`.
   That investigation found a blocker in the prescribed mechanism (see
   DEVIATION 1 below) and changed the shape of the fix, so it had to settle
   before any Part A code was written.
2. **Part C next**, expected to be a two-line edit. It turned out the backlog
   entry's premise is stale (see DEVIATION 2), so the correct action became
   "verify and close", not "edit the file".
3. **Part B last**, the genuine design call.

---

## DEVIATION 1 (Part A) — `pytest.mark.xdist_group` is inert in this suite

The handoff's concrete step 2 says to give the colliding tests a shared
`@pytest.mark.xdist_group(name=...)`. **Measured, not assumed: that marker is
a complete no-op under this suite's scheduler.**

- `xdist/remote.py:241` — the collection hook that rewrites a marked test's
  nodeid to `<nodeid>@<group>` (which is what makes `LoadGroupScheduling`
  group them) is guarded by `if config.getvalue("loadgroup")`.
- `run-ciu-tests.py:40` runs `--dist loadfile`, whose `_split_scope` is
  `nodeid.split("::", 1)[0]` — the group suffix never exists and is never
  consulted.
- Empirical confirmation on the pinned pytest-xdist 3.8.0 (four one-test
  files, all carrying the same `xdist_group` name, `-n 4`):
  - `--dist loadfile` → `a→gw1  b→gw0  c→gw2  d→gw3` (four different workers)
  - `--dist loadgroup` → `a→gw1  b→gw1  c→gw1  d→gw1` (one worker)

Marker registration itself is a non-issue and was checked as asked: there is
no `[tool.pytest.ini_options]`, `pytest.ini`, `setup.cfg` or `tox.ini` in
this project at all, so strict-markers is off; and `xdist_group` is registered
by pytest-xdist's own `pytest_configure` (`plugin.py:262`) regardless.

**Switching the runner to `--dist loadgroup` was rejected**, on two grounds:
`run-ciu-tests.py` is not in the package's Touch list, and more importantly
`loadgroup`'s `_split_scope` returns the full nodeid for every UNMARKED test,
which is plain `load` distribution — exactly the non-deterministic
coverage split CIU-56 adopted `loadfile` to fix (its own module docstring
records 2-of-3 runs under-reporting `hook_templates/post_compose_db.py`).

**What shipped instead:** CIU-91's fix direction (b) names two alternatives —
"a dedicated non-parallel xdist group **or a module-scoped lock**". The
second one was implemented. It must be a FILESYSTEM lock rather than a
literal module-scoped one, because xdist workers are separate processes and
an in-process lock would serialize nothing.

This is a deviation from the handoff's literal step 2 and is flagged here for
the adversarial reviewer explicitly. It is not a workaround around a BLOCKED
condition: it is the other half of the same fix direction the handoff cites,
using only files in the Touch list.

---

## DEVIATION 2 (Part C) — CIU-88's premise is stale; the prescribed fix would be a regression

The handoff's Part C says `"assay-verdict"` is not a valid value in nyxloom's
asserts schema enum, and prescribes dropping it from
`ciu/nyxloom-trove/nyxloom.toml:58` with a comment mirroring nyxloom-P48.

**That is no longer true.** `vbpub@479d7e71` ("fix(nyxloom): P98 -- NL-4, add
assay-verdict to the asserts enum") landed the schema change. The enum in
`nyxloom/src/nyxloom/schemas/nyxloom-config.schema.json` now reads
`tests-pass | changed-line-coverage | mutation | canary-verified |
assay-verdict`.

Verified by running nyxloom's own CFG1 rule — the exact
`Draft202012Validator(nyxloom-config.schema.json).iter_errors(raw)` loop from
`nyxloom/src/nyxloom/lint.py:363-379` — directly against
`ciu/nyxloom-trove/nyxloom.toml`: **0 findings**.

(`nyxloom lint <path>` is the HANDOFF linter, not the config linter; pointed
at a `.toml` it reports `L1 error parse/schema error: missing leading '---'`,
which is the handoff-frontmatter rule, not CFG1. The dogfood path above is
what nyxloom's own `test_repos_own_config_no_findings` uses and is the right
precedent to reuse here.)

Applying the prescribed edit would DELETE a now-valid, semantically
meaningful declared assert from ciu's gate config and add a comment that is
factually wrong. **Not done.** `nyxloom.toml` is unmodified; CIU-88 is flipped
to FIXED-UPSTREAM with the citation and the verification.

---

## Commits

Three code/doc commits plus this report pair. All three backlog flips are in
ONE commit rather than split across the two fix commits — the four rows live in
one file and splitting a single file's hunks across commits buys nothing an
adversarial reviewer can use.

### 1. `d59ac689` — `fix(ciu): P51 — CIU-91+CIU-58, serialize shared test-repo/ access across xdist workers`

- `tests/conftest.py` — `_hold_test_repo_lock` (fcntl SH/EX on a lockfile
  keyed by the resolved `test-repo/` path in the system temp dir),
  `pytest_configure` registering `ciu_test_repo_inplace` /
  `ciu_test_repo_reader`, the autouse `_serialize_shared_test_repo_access`
  dispatcher, and the explicitly-requested `shared_test_repo_read_lock`
  fixture for the case where the copy lives in a fixture (which cannot carry
  a mark). Module docstring gains a "Shared `test-repo/` serialization"
  section; the mechanism's own long comment carries the xdist_group finding.
- `tests/tests/test_ciu_test_repo.py` — 7 writer marks, 1 reader mark.
- `tests/tests/test_ciu_render_selection_context.py` — 4 reader marks.
- `tests/tests/test_spec_contracts.py` — module-level `pytestmark` reader.
- `tests/tests/test_ciu_identity_cutover_ciu75.py` — `verb_repo` requests
  `shared_test_repo_read_lock`.
(Backlog flips are in commit 3.)

### 2. `7e5ddc4c` — `fix(ciu): P51 — CIU-96, exclude a redeploying entry's own outgoing instance from mem_min admission`

- `src/ciu/governance.py` — `check_mem_min_admission` gains keyword-only
  `exclude_scopes`; the sum drops occupants by NAME INTERSECTION and the note
  reports what was excluded. API-summary line updated.
- `src/ciu/deploy.py` — new `_entry_prior_instance_scopes`;
  `mem_min_admission_check` threads the result through and logs at `[INFO]`.
- `docs/SPEC.md` — S15.23's "Known v1 limitation — a redeploy double-counts"
  callout replaced by the fix's description; the separate concurrent-admission
  over-admit is restated as still open and deliberately accepted.
- `tests/tests/test_ciu_governance.py` — `TestMemMinAdmissionExcludesOutgoingInstance`, 7 tests.
- `tests/tests/test_ciu_deploy_actions.py` — 9 tests, plus 6 pre-existing
  `check_mem_min_admission` stubs updated for the new keyword argument.

### 3. `4e6c2f62` — `chore(ciu): P51 — flip CIU-91/CIU-58/CIU-96 to FIXED; close CIU-88 as fixed upstream by nyxloom NL-4`

- `KNOWN_ISSUES_TODO_BACKLOG.md` only, all four rows. CIU-88 carries NO config
  change — see DEVIATION 2.

### 4. `docs(ciu): P51 — implementer LOG + REPORT`

- `nyxloom-trove/reports/ciu-P51-{LOG,REPORT}.md`. Markdown only; written
  after the gate so the REPORT carries the real verdicts.

---

## Not done, deliberately

- **`CHANGES.md` is NOT edited.** It is outside the Touch list, and its
  CIU-94/S15.23 block is a HISTORICAL release section: the redeploy
  double-count was genuinely a limitation of the version that shipped it, so
  rewriting that section would falsify the record. The fix belongs in the
  NEXT release's section, written at release time — flagged as a required
  release-time action in the REPORT, since this branch has a fourth part
  (CIU-93) still to come before any release.
- **`vbpub/nyxloom/nyxloom-trove/nyxloom.toml`'s own "assay-verdict is NOT
  listed" comment is now stale** in the same way CIU-88 was. Not touched —
  it is nyxloom's file, outside this package entirely. Recorded in the
  CIU-88 backlog row for whoever picks it up.
- **CIU-58's heavier proposal** (a per-session pristine copy, or a synthetic
  fixture tree) — explicitly out of scope per the handoff's Part A step 4.
- **Nothing merged, nothing pushed, nothing released.**
