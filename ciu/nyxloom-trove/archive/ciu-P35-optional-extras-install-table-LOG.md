# ciu-P35 — README.md: a consolidated "Optional extras" table

**Handoff:** `nyxloom-trove/handoffs/ciu-P35-optional-extras-install-table.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `d3175625` (confirmed
with `git status --porcelain && git log --oneline -3` before any edit — tree
was clean).

**Status: COMPLETE.** Docs-only. Full gate green (3261 passed, 100.00%
line+branch coverage), zero source or test file changes. No `escalate_if`
fired.

---

## 1. Reading, before writing anything

Read the handoff in full, then in order: `pyproject.toml`'s
`[project.optional-dependencies]` block, `src/ciu/provisioning.py`'s
`_load_pydantic` (the lazy-import rationale this package does not change),
the three existing per-feature mentions in `docs/CONFIG.md` and
`docs/CONSUMERS.md`, `docs/SPEC.md`'s S14 section (for the exact verb name
the `ssh` extra unlocks), and `README.md` in full to find the right natural
placement for one new table.

## 2. Re-verified the handoff's premise against the live `pyproject.toml` — unchanged

The handoff explicitly warned it could be stale (`input_revision: 2c842ba0`,
carved before several packages landed). Checked directly:

```toml
[project.optional-dependencies]
test     = [...]                    # dev-only — pytest + the same jsonschema/pydantic pins
ssh      = ["paramiko>=5.0"]
schema   = ["jsonschema>=4.18"]
registry = ["pydantic>=2"]
```

Exactly the three real extras the handoff named (`ssh`/`schema`/`registry`),
plus the dev-only `test` — no fourth extra added, none removed, no shape
change. The `escalate_if` condition ("optional-dependencies has changed
shape... a fourth extra added, or one of the three removed") does **not**
fire. Per the outer task's instruction, no version number from the handoff's
prose was trusted or copied — the table below omits version floors entirely
and instead points at `pyproject.toml` as the single source of truth, so it
cannot drift out of sync with a future bump the way a copied number would.

## 3. Confirmed which verb/feature each extra unlocks (not guessed)

- **`ssh`** — `docs/SPEC.md:1901` "S14 — Remote SSH transport (`ciu ssh` /
  `--host`)", S14.5 "Packaging": paramiko is optional; the **default**
  transport is subprocess `ssh`/`rsync` (zero added Python deps). The extra
  only matters when `CIU_SSH_TRANSPORT=paramiko` is set to opt into the
  paramiko transport — the table's one-sentence description says so
  explicitly rather than implying `ciu ssh` is unusable without it.
- **`schema`** — `docs/CONFIG.md` (currently ~line 773, shifted from the
  handoff's stale `:765` by packages landed since `input_revision`): the
  optional `schema = "..."` key on a
  `[<root>.<service>.configfile.<name>]` block, checked on the up/dev render
  path (engine step 12), S5.7.
- **`registry`** — `docs/CONFIG.md` (currently ~line 484-505, shifted from
  the handoff's stale `:485-488`) and `docs/CONSUMERS.md:740-751` (shifted
  from `:604-605`): `ciu check`'s stage 7 validation of
  `[registry.postgresql].database` and
  `[registry.consul].token_vault_path`, S13.4b.

All three line-number citations in the handoff had drifted (packages P30-P34
landed above these sections since `input_revision: 2c842ba0`) — a pure
line-shift, not a content/shape change, so re-found by grep rather than
trusted, per the outer task's explicit instruction. No `escalate_if` fires on
a line-shift; the actual mechanism content at each location matches what the
handoff describes.

## 4. Placement

Chose a new `## Optional extras` H2 section immediately after the `## Quick
start` block (right after "`ciu --help` and `ciu <verb> --help` list the
public commands and their options.", before "## Release: portable CMRU
project contract") — the natural point a first-time reader hits right after
seeing the base `pip install -e .` command, matching the handoff's own
suggested anchor ("near the existing install instructions, e.g. after the
`pip install` block").

## 5. What the table does NOT do (per O2)

No existing per-feature mention was touched, reworded, or moved:

```
$ git diff --stat -- docs/CONFIG.md docs/CONSUMERS.md docs/SPEC.md
(empty)
```

Only `README.md` and `CHANGES.md` changed — confirmed by `git diff --stat`
below. The new table cross-references `docs/CONFIG.md` and
`docs/CONSUMERS.md` for the full per-feature detail rather than duplicating
it; the `test` extra is explicitly called out as excluded (dev-only, not a
consumer concern) rather than silently omitted with no explanation.

## 6. Files changed

| File | What |
|---|---|
| `README.md` | New `## Optional extras` section (23 lines) between `## Quick start` and `## Release: portable CMRU project contract`: one 3-row table (`ssh`/`schema`/`registry`), each row's install command, package name, and one-sentence "what it unlocks + which verb"; an explicit note that version floors live in `pyproject.toml` (not repeated here); an explicit note that `test` is intentionally excluded (dev-only) |
| `CHANGES.md` | New entry appended to the existing `### Documentation` subsection under the existing `## [Unreleased]` header (no duplicate `[Unreleased]` created), `docs(ciu):` prefix, no `!` marker — nothing behavioral changes |
| `nyxloom-trove/reports/ciu-P35-optional-extras-install-table-LOG.md` | This file |

No `scope.forbid` file was touched — confirmed both before writing and again
just before committing:

```
$ git diff --stat -- src/ciu/ tests/tests/ pyproject.toml docs/SPEC.md \
    docs/CONFIG.md docs/CONSUMERS.md docs/DESIGN-GUIDE.md \
    nyxloom-trove/backlog.md nyxloom-trove/decisions.md nyxloom-trove/roadmap.md
(empty)
```

## 7. Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** table matches pyproject.toml exactly | **MET** | §2-3 above — exactly three rows (`ssh`/`schema`/`registry`), each with the exact `pip install 'ciu[<name>]'` command, the underlying package name (no hardcoded version — `pyproject.toml` named explicitly as source of truth instead), and one sentence naming the exact ciu feature/verb (`ciu ssh`/`--host` paramiko transport; configfile `schema=` on the up/dev path; `ciu check` stage 7 registry validation). `test` is explicitly not a row. |
| **O2** does not duplicate or contradict existing per-feature docs | **MET** | §5 above — `git diff --stat -- docs/CONFIG.md docs/CONSUMERS.md docs/SPEC.md` is empty; only `README.md` and `CHANGES.md` changed. |
| **O3** gate stays green with zero code changes | **MET** | §8 below — 3261 passed, 100.00% line+branch coverage, `git diff --stat -- src/ tests/tests/` empty before and after the run. |

## 8. Gate output (verbatim, read in a separate step from the run itself)

```
$ .venv/bin/python run-ciu-tests.py
...
--------------------------------------------------------------------------------------------
TOTAL                                             9688      0   3948      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 3261 passed in 23.51s =============================
```

3261 tests passed, exit code `0`, zero source or test edits (`README.md` and
`CHANGES.md` are the only diffed files — confirmed by `git diff --stat`
before this run).

## 9. Commits

1. `README.md` + `CHANGES.md` — one commit, per
   `git commit --only -F - -- ciu/README.md ciu/CHANGES.md`.
2. This LOG file — a separate commit.

Exact hashes are in this package's final report (read back via `git log
--format=%H`, not predicted ahead of the actual commit).
