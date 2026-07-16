# P24 — nyxloom.toml schema + `nyxloom lint` config validation — REVIEW

- **Task:** `nyxloom-P24-config-schema-lint`
- **Branch:** `feat/nyxloom-P24-config-schema-lint`
- **Reviewed commit:** `13a4d6a` (implementer) + `<this commit>` (reviewer fix)
- **Reviewer:** independent frontier reviewer (merge gate)
- **Verdict: APPROVE** (one small defect found and fixed in-branch)

## Git state (verified, not taken from the receipt)

`git log main..feat/nyxloom-P24-config-schema-lint` → exactly one implementer
commit, `13a4d6a`. Worktree had no uncommitted implementer changes. Diff touches
exactly the three files in `scope.touch`:

```
 nyxloom/src/nyxloom/lint.py                        |  85 ++++++-
 nyxloom/src/nyxloom/schemas/nyxloom-config.schema.json | 106 +++++++++
 nyxloom/tests/test_lint.py                         | 190 +++++++++++++++-
```

No forbidden file (`config.py`/`cli.py`/`daemon.py`/`reconcile.py`/`storage.py`)
was touched. Scope contract: **respected**.

## Gate (re-run by the reviewer, not trusted from the report)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

- On `13a4d6a` as delivered: **pass**, exit 0, 448 passed.
- After the reviewer fix below: **pass**, exit 0, 450 passed (+2 new tests).

## Oracle verification

**O1 — schema violations blocking, valid config clean: MET.**
Verified independently of the test suite. Empty `[gates.*].argv` (`minItems: 1`),
missing `[project].id` / `handoff_globs` (`required`), and a wrong-typed
`[policy]` value all produce `CFG1` / severity `error`, and `has_blocking()` is
true. The findings surface under the config's root-relative path key in
`lint_project`. Beyond the handoff's ask, I validated the schema against **both**
real configs in the repo — `nyxloom/nyxloom-trove/nyxloom.toml` and the
independent `topos/nyxloom-trove/nyxloom.toml` — and both are schema-clean, so
the schema is not overfitted to nyxloom's own file.

**O2 — unresolved `[refs]` flagged, resolving refs clean: MET after the fix
below.** As delivered, the resolving-refs and missing-file cases were correct,
but the containment half of the contract was not implemented (see D1).

Both oracles' `negative` cases were checked and do not reproduce.

## D1 — `CFG3` checked existence but not containment (FOUND, FIXED)

The handoff work item 2(c) requires "every `[refs]` path **resolves under**
`cfg.root`", and the emitted message literally claimed `"does not resolve under
project root"`. The delivered check only tested existence:

```python
if not (cfg.root / ref_path).exists():
```

`pathlib` semantics make this unsound in two ways, both confirmed by direct
probe against the delivered code (not by reading):

| `[refs]` value      | on disk           | delivered | expected |
|---------------------|-------------------|-----------|----------|
| `/etc/passwd`       | exists, outside   | **no finding** | `CFG3` |
| `../outside.md`     | exists, outside   | **no finding** | `CFG3` |

An absolute ref path makes `cfg.root / ref_path` discard the root entirely, and
a `..`-escaping ref resolves outside it. Either way a ref that points clean out
of the project satisfied `.exists()` and lint reported clean — the exact
"caught at runtime, not at lint" failure this package exists to prevent.

**Fix (reviewer, in `lint.py` — an in-scope file):** resolve the target and test
containment with `is_relative_to` before falling back to the existence check,
with the message distinguishing "escapes the project root" from "does not
exist". Both probes now yield `CFG3`; the repo's own relative `docs/*.md` refs
stay clean. Added two regression tests
(`test_absolute_ref_outside_root_is_blocking_cfg3`,
`test_parent_escaping_ref_is_blocking_cfg3`).

Classified small: localized to one loop in an in-scope file, no interface or
architectural change.

## Findings noted, deliberately NOT changed

**N1 — `CFG1` is unreachable for some cases via the real CLI path (pre-existing,
out of scope).** `lint_config(cfg)` takes a `ProjectConfig`, but building one
calls `ProjectConfig.load`, which does `data["project"]["id"]` and
`data["project"]["handoff_globs"]` unguarded. Confirmed by probe: a config
missing `id` raises `KeyError: 'id'` from `load`, and a malformed TOML raises
`TOMLDecodeError` — **before** `lint_config` can report `CFG1`. So for the
missing-required-key and unparseable-TOML cases the user gets a traceback
(swallowed by `except Exception` in `cli.py:170`, surfacing as a bare "clean"),
not a `CFG1` finding.

This is **not** an implementer defect and is not grounds to reject:
- O1 is written against `lint.lint_config(cfg)`, which does behave as specified.
- The only fix is making `ProjectConfig.load` resilient — `config.py` is FROZEN
  CORE and an explicit BLOCKED trigger.

The tests legitimately reach these paths by loading `cfg` from a valid file and
then rewriting it, which matches the handoff's "read the raw config file"
design. Worth a follow-up package that either hardens `load` or has
`lint_config` accept a root path instead of a `ProjectConfig`.

**N2 — `additionalProperties: true` on the root and `[project]`.** The handoff
asked for `additionalProperties: false` "at the section boundaries you can pin";
the implementer pinned `false` on `gates.*`, `mutexes.*`, `policy`, `notify`,
and `redact` but left the root and `[project]` open. I verified tightening both
would still leave the two real configs clean — but left it alone deliberately:
`config.py:158-160` explicitly documents that unknown `[project]`/`[refs]` keys
are dropped by design, so pinning `false` there would contradict the frozen
core's stated contract and could hard-error (severity `error`, blocking) on
consumer projects not visible in this repo. The implementer's choice is
consistent with the core. Tradeoff: a typo'd optional key (`report_dir` for
`reports_dir`) still silently falls back to its default.

**N3 — `doctor.py` labels config findings as "handoff lint" (cosmetic).**
`doctor.py:88` folds every `lint_project` value into
`kind='handoff-lint'` / `message=f'handoff lint: {rule} ...'`, so a `CFG1` shows
as "handoff lint: CFG1 …". Not a functional break, and `doctor.py` is outside
`scope.touch`. Follow-up alongside the SPEC.md codification the handoff already
defers.

## Non-defects confirmed

- **`lint_project` fold is safe for all three consumers.** `daemon.py:581` looks
  findings up by handoff-id keys, so the extra `.toml` key is never read;
  `cli.py:168` prints it and exits 1 on error — the intended "surfaces with no
  cli.py change"; `doctor.py:88` surfaces it (see N3). Existing `lint_project`
  tests and handoff entries are intact.
- **Schema loading and packaging are correct.** `importlib.resources.files(
  "nyxloom.schemas")` mirrors the existing `frontmatter.py:84` pattern exactly;
  `jsonschema>=4` is already declared in `pyproject.toml`, and
  `[tool.setuptools.package-data]` already ships `schemas/*.json`, so the new
  file is packaged with no build change.
- **Tests are not hollow.** Fixtures are built under `tmp_path` per the handoff
  (they do not depend on the repo tree); assertions check rule id, severity,
  `has_blocking`, and message content rather than just non-emptiness.
- **`CFG2`/`worktree_root` correctly excluded from the containment rule** — the
  repo's own `worktree_root = "../.worktrees"` intentionally escapes the root.

## Deviation from the packet

The packet specifies `topos/handoff/reports/<task>-REVIEW.md`. That path does not
fit this repo: `topos/` is a **different project** with its own trove, and
`topos/handoff/` does not exist. This report is filed at the nyxloom project's
declared `reports_dir` (`nyxloom-trove/reports`, per `nyxloom.toml`) using the
`P<NN>-REVIEW.md` convention already established by the sibling
`P<NN>-REPORT.md` files and by pwmcp's `handoff/reports/P01-REVIEW.md`.

## Verdict

**APPROVE.** The package meets its scope, both oracles, and its declared gate,
which I re-ran myself on the delivered commit and after my fix. The schema is
well-formed 2020-12, generalizes to a second real project, and the `lint_project`
fold reaches `nyxloom lint` with no `cli.py` change exactly as designed. The one
real defect (D1, `[refs]` containment) was small, in-scope, and is fixed with
regression tests on this branch. N1 is a genuine reachability gap but is walled
off behind the frozen `config.py` and belongs to a follow-up package, not to this
one. Not merged, per role.
