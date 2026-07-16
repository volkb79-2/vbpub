# nyxloom-P25-dashboard-project-info-archive — REVIEW

Reviewer: independent frontier reviewer (Opus 4.8), fresh session. Date: 2026-07-16.
Branch: `feat/nyxloom-P25-dashboard-project-info-archive` @ `0382601` (+ review-fix commit).
Handoff: `nyxloom-trove/handoffs/nyxloom-P25-dashboard-project-info-archive.md`.

## Verdict

**APPROVED after review-fixes.** Both oracles are genuinely met and the tests are
not hollow — I verified each by mutation, not by reading. I fixed one real
defect the package introduced (a dashboard-wide crash vector) and closed one
coverage gap (the production config layout was never exercised). No
architectural defect: the one significant deviation from the handoff is a
*correct* response to a handoff whose stated premise was factually wrong.

Do NOT merge — per role contract, this branch is left for the pipeline.

## Verified git state (not the receipt)

The attempt's `receipt.json` **does not exist** at the path the packet names, so
nothing about it was trusted. Git state directly:

- `git log main..feat/…` → exactly one implementer commit, `0382601`.
- `git status` in the real worktree (`/workspaces/vbpub/.worktrees/feat/nyxloom-P25-…`,
  *not* the `/workspaces/vbpub/nyxloom` path the packet lists, which is on `main`)
  → clean. The packet's "no uncommitted changes" claim is confirmed.
- Diff = `src/nyxloom/render.py` + `tests/test_render.py` only. **Scope respected**:
  `config.py` / `daemon.py` / `reconcile.py` / `storage.py` all untouched.

## Gate — re-run by me, not trusted from a report

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd <worktree>/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
→ EXIT_CODE=0   (whole suite green, before and after my fixes)
```

## Oracle verification (adversarial, by mutation)

Passing tests prove little on their own, so each oracle was checked against its
own declared *negative* and against a surgical mutation of the mechanism it names.

| Check | Result |
|---|---|
| Both new tests vs `main`'s `render.py` (feature absent = oracle negatives) | **FAIL** ✅ not vacuous |
| O2 mutant: `cap = 10` (ignore `archive_keep_visible`) — O2's literal negative | **FAIL** ✅ binds to the config value |
| O1 mutant: gate argv dropped from render | **FAIL** ✅ `<code>true</code>` binds only to gate argv |

**O1 — met.** `config.html` renders per project: gate id + `html.escape`d argv,
`ntfy_url` + both topic names, and the folder paths. All escaped; no new API.
The test's weakest-looking assertion (`<code>true</code>`, the sample gate's
argv) is legitimate — the mutation proves it has no other source in the page.

**O2 — met.** Completed packages are capped per project at `archive_keep_visible`,
newest-first; the remainder still renders as `class="archive-row"`, hidden by
default via CSS and revealed by an `#archive-toggle` checkbox.

**On the "no JS" concern — not a defect.** The toggle ships a small
`addEventListener`/`classList.toggle` script. I checked the pattern it was told
to mirror: the existing carve-toggle (`render.py:738`) and live.html's raw-toggle
do exactly this. The handoff's constraint was "not JS **innerHTML**", and the
implementer honoured it — the test even asserts `"innerHTML" not in content`.

## Findings

### F1 — Unguarded `int()` crashed the entire dashboard (CONFIRMED, **fixed by me**)

`_trove_project_extras` ended with:

```python
return proj.get("trove"), proj.get("archive_dir"), int(proj.get("archive_keep_visible", 10))
```

Only `OSError`/`TOMLDecodeError` were caught, so a non-numeric value raised
straight through `_render_config`/`_render_history` → `render_all()`, failing
**every page for every project**, not just the one misconfigured project. The
docstring claimed tolerance it did not have.

This is a vector **P25 introduced**: `ProjectConfig.load()` deliberately drops
unknown `[project]` keys, so before this package a bad `archive_keep_visible`
was inert. `archive_keep_visible` is a hand-edited value in the trove template,
so a bad value is reachable in practice.

Reproduced against the original code:
`ValueError: invalid literal for int() with base 10: 'lots'` out of `render_all`.

Fixed: fall back to the default cap of 10 on `TypeError`/`ValueError`, docstring
corrected. Added `test_config_html_tolerates_non_numeric_archive_keep_visible`,
which I confirmed **fails against the original code** and passes against the fix.

### F2 — The production config layout was never exercised (**fixed by me**)

Every test uses the legacy `.nyxloom/project.toml` fixture, but real projects
(including nyxloom itself) config via `nyxloom-trove/nyxloom.toml`. So the
trove-first branch of `_trove_project_extras` — the branch production actually
takes — and the `trove:` folder line (named in O1's observable) were rendered by
no test at all. O1's *test* requirement only demanded the archive/reports paths,
so this was compliant-but-uncovered rather than a contract miss.

Added `test_config_html_project_info_reads_trove_layout`: asserts trove-first
resolution wins over the legacy file for both `ProjectConfig.load` (gates/notify,
incl. the `notifications_topic`/`feedback_topic` mapping) and
`_trove_project_extras` (folders), and covers all four O1 folder paths including
`trove:`. Mutation-verified (dropping the `trove:` line fails it). **The
production path was already correct** — this locks it in.

### F3 — Handoff premise was wrong; the workaround is the right call (NOT a defect)

O2 names `cfg.archive_keep_visible`, and the handoff's "read first" section
asserts `ProjectConfig` exposes `archive_dir`/`archive_keep_visible`. **It does
not** — `config.py` contains zero occurrences of "archive", and `load()`
explicitly discards unknown `[project]` keys. Since `config.py` is a forbidden
file, the oracle as literally specified was unsatisfiable.

The implementer added `_trove_project_extras`, reading the two fields off the raw
TOML. I checked the real `nyxloom-trove/nyxloom.toml`: `trove`, `archive_dir`
and `archive_keep_visible = 10` are all genuine documented keys, the last
commented *"dashboard shows last N completed; rest behind an Archive button"* —
i.e. the config was authored for exactly this feature; only `ProjectConfig`
never modelled it. The workaround therefore delivers both observables, honours
"no schema change", respects the forbid list, and is documented prominently in
the frozen interface contract and the helper's docstring.

A strict reading of *"do not improvise a workaround"* would have made this a
BLOCKED. I judge **approve** instead: the handoff contradicted itself, the
implementer resolved it in the direction the handoff's own intent points, and
disclosed it honestly rather than overclaiming. Rejecting would burn a cycle to
arrive at the same code.

**Follow-up (not this package):** `_trove_project_extras` duplicates
`ProjectConfig.load()`'s trove-first/legacy resolution. They agree today — I
checked line-by-line — but they can silently diverge. The clean fix is to add
these fields to `ProjectConfig`, which requires the `config.py` edit P25 was
forbidden. Worth a backlog item.

### F4 — History rows now interleave across projects (minor, doc imprecision only)

`_render_history` sorts **globally** by completion time, where the old code
grouped by project then task id. The cap is correctly per-project, but with 2+
projects registered the rows now interleave. The interface-contract comment says
rows are ordered newest-completed-first *"per project"*, which reads as
per-project grouping. Behaviour is defensible (global recency in a history view);
only the wording is loose. Left as-is — rewording the frozen contract block is
not worth a churn commit.

### F5 — `_completed_at` proxy has a known blind spot (minor, accepted)

Falls back to `tsf.since` (task creation) when no attempt has `ended` — so a
long-lived task merged recently sorts as stale and can be archived despite being
a recent completion. The implementer documents this in the docstring. Acceptable
for a display cap; no state-schema field exists to do better without touching
forbidden files.

**Non-issue checked and dismissed:** naive/aware `datetime` mixing in the new
sort would be a crash, but `parse_iso` rejects naive datetimes and normalises to
UTC, so every loaded timestamp is aware. `_render_history`'s signature change is
internal — `render.py:526` is its only caller.

## What I changed

- `src/nyxloom/render.py` — guard the `archive_keep_visible` int conversion (F1); docstring corrected.
- `tests/test_render.py` — `test_config_html_project_info_reads_trove_layout` (F2),
  `test_config_html_tolerates_non_numeric_archive_keep_visible` (F1 regression).

Both fixes stay inside the handoff's `touch` list. Full gate re-run after: exit 0.

## Note on packet accuracy

Two packet fields were wrong and are worth fixing upstream: the review-report
path `topos/handoff/reports/` does not exist (live convention is
`<project>/nyxloom-trove/reports/<task-id>-REVIEW.md`; this report follows
nyxloom's own `reports_dir`), and `Worktree:` pointed at `/workspaces/vbpub/nyxloom`,
which is on `main` — the task worktree is under `.worktrees/`.
