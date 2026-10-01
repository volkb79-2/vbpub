# ciu-P20 — hook template library (mechanism + one reference template)

**Handoff:** `nyxloom-trove/handoffs/ciu-P20-hook-template-library.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `c625567c` (ciu-P33's
carve commit, confirmed with `git log -1` before any edit)

LOG filename taken verbatim from the handoff's `scope.touch` line 26 and Work
item 7, which agree with each other and with the dispatch. No discrepancy.

---

## Files changed

| File | What |
|---|---|
| `src/ciu/hook_templates/__init__.py` | new — package docstring only |
| `src/ciu/hook_templates/post_compose_db.py` | new — the one reference template (S9.1 `run`, S9.5 `validate_config`, `template_revision = 1`) |
| `src/ciu/scaffold.py` | `_available_hook_templates`, `_hook_template_source`, `_hook_template_revision`, `_HOOK_TEMPLATE_PACKAGE`/`_HOOK_STAMP_FORMAT`; `collect_plan` gains `--hooks NAME1,NAME2` parsing + validation (unknown name / no `--stacks` target both refuse, exit 2, before any write); `build_files` copies+stamps each requested template into every scaffolded stack's `hooks/` subdir |
| `src/ciu/cli.py` | `_USAGE`'s `init` bullet gains `--hooks` (no dispatch change needed — `init`'s `rest` passthrough to `init_main` was already unconditional; verified, not assumed) |
| `pyproject.toml` | `[tool.setuptools.package-data]` gains `"hook_templates/*"` |
| `tests/tests/test_ciu_scaffold_hooks.py` | new — 17 tests (below) |
| `docs/SPEC.md` | new **S19.1** clause under S19 |
| `docs/FEATURES.md` | new `ciu init` row in the CLI reference table — **see "Blast radius": there was no existing row to "update"** |
| `docs/CONSUMERS.md` | new **§15** worked example |
| `CHANGES.md` | Unreleased → Added entry |
| `docs/BACKLOG-2026-08-24.md` | CIU-QOL-13 → **FIXED-partial** with the O1 scope note |
| `README.md` | **out of scope** — the `ciu init` bullet's one-sentence `--hooks` mention required by O4; see "Blast radius" |

---

## O1 — the scoping decision (restated per the handoff's own instruction)

**Decision, unchanged from the handoff's framing.** The backlog's proposed
structure (CIU-QOL-13) names six consumer-infra hook templates —
`post_compose_db`, `post_compose_vault`, `post_compose_consul`,
`post_compose_redis`, `post_compose_authentik`, `pre_compose_tailscale` —
that mirror dstdns's real stacks, a separate repository this session cannot
read. Inventing business logic for a Vault/Consul/Redis/Authentik
integration this repo has no grounded specification for would ship a
template that *looks* authoritative but does not match the real consumer's
needs — the CIU-45 withdrawal lesson: an invented capability claim is worse
than none.

**What ships:** the mechanism in full —
- `template_revision` stamping (module attribute + copy-time stamp comment,
  S19.1),
- the `ciu init --hooks` copy flow (discovery, validation-before-write,
  never-overwrite, multi-stack fan-out),
- the `run`/`validate_config` contract enforcement (reused verbatim from
  S9.1/S9.5 — nothing template-specific invented),

plus exactly **one** reference template, `post_compose_db.py`, kept
deliberately generic and honest: it demonstrates the S9.3
`wait_healthy`/`secret_file` pattern and an S9.5 `validate_config` example,
and its own docstring says plainly that it is not a production database
bootstrap.

**Re-verified at this commit (not merely assumed from the handoff):**
`grep -rniE "vault|consul|redis|authentik|tailscale" src/ciu/` does turn up
hits — but every one is either (a) CIU's own GENERIC, product-agnostic
mechanism that happens to use these names as vocabulary (`ASK_VAULT`/
`GEN_TO_VAULT` secret directives, the `vault:secret/...` and
`consul:token/...` provisioning-ref grammar, `[registry.consul].
token_vault_path` — all already-shipped CIU features with no per-service
business logic), or (b) an ILLUSTRATIVE example inside a docstring/comment
("e.g. an authentik hook writing its bootstrap token", "a redis ACL hook
does `ctx.wait_healthy(...)`") — never an actual hook implementation. There
is no `run()`/`validate_config()` body anywhere in this repo that bootstraps
a real Vault policy, Consul ACL, Redis user, Authentik application, or
Tailscale node — confirming there is no in-repo specification to ground
those five templates' BUSINESS LOGIC against, which is the actual thing O1
guards against inventing. They remain open backlog items (see the
BACKLOG-2026-08-24.md edit), one small follow-up package each, once a
session with visibility into a real consumer's existing hook can verify a
template against it — mirroring ciu-P11's Tailscale/SSH work, which was
grounded in dstdns's actual bootstrap ask, not an invented one.

---

## Design decisions

### 1. Destination path: `<stack_dir>/hooks/<name>.py`

The `escalate_if` asked me to confirm this convention against
`hooks_runner.py`'s docstring/S9.1 *without* needing to edit
`hooks_runner.py`. S9.1 says hook paths are "lists of script paths relative
to the stack dir" — an arbitrary relative path, resolved by
`ctx.stack_dir / p` (`hooks_runner.run_hooks`, unedited). A `hooks/`
subdirectory needs no runner change: it is exactly as valid a relative path
as the flat `./pre_compose_app.py` the existing test-repo stacks use. I
chose `hooks/` over flat-in-stack-dir specifically because the backlog's own
motivation is a consumer accumulating **several** hook templates over time
(the six-template illustrative list) — a `hooks/` subdirectory keeps that
future from cluttering the stack root next to `ciu.defaults.toml.j2` and
`ciu.compose.yml.j2`. **The `escalate_if` did not fire**: no `hooks_runner.py`
change was needed, confirmed by `git diff --stat` showing it untouched.

### 2. `--hooks` copies into EVERY stack this same `init` invocation scaffolds, not one designated stack

The handoff's flag shape, `ciu init --hooks NAME1,NAME2`, is not
stack-scoped (contrast `--stacks A,B`, which the same invocation already
produces N of). Two designs were possible: (a) copy into a single
"primary"/first stack, or (b) copy into all of them. I chose (b) — simpler,
requires no new "which stack" flag the handoff never asked for, and matches
the mechanism-library framing: a consumer who only wants it in one stack
runs `ciu init` once per stack, or deletes the unwanted copies afterward.
**Consequence, stated plainly:** `--hooks` with **no** `--stacks` given has
nowhere to copy into, so it is a configuration error (exit 2, "requires at
least one `--stacks` target") rather than silently writing nothing or
writing to the repo root. Pinned by
`test_hooks_without_any_stack_refuses` and
`test_hooks_flag_copies_stamped_template_into_every_scaffolded_stack`
(the latter with two stacks, both receiving the copy).

### 3. Template name == file stem, not a separate slug

The handoff's O4 text used `db-core` as an *illustrative* slug
("`ciu init --hooks db-core` (or your chosen template's slug)"). I used the
file stem itself (`post_compose_db`) as the `--hooks` name, with no second
alias/slug-to-filename mapping table to keep in sync — `NAME` maps 1:1 onto
`hook_templates/<NAME>.py`, discovered dynamically
(`_available_hook_templates`, `importlib.resources.files(...).iterdir()`) so
a future template needs no registration anywhere in `scaffold.py`.

### 4. Stamp format, and where `template_revision` is read from

Used the handoff's own suggested format verbatim:
`# ciu-hook-template: <filename> rev=<N>`, documented in SPEC.md S19.1 so a
future revision-comparison feature has a stable line to parse. `N` is read
by **importing** the template module (`_hook_template_revision`,
`importlib.import_module`) rather than regex-scraping the source text — this
guarantees the stamped number always matches the real
`template_revision` the copied file carries (the same text
`_hook_template_source` copies verbatim), with no risk of the two drifting
if a future template's declaration style varies (e.g. a computed default).

### 5. `post_compose_db.py`'s body: what it demonstrates and what it deliberately does not

`run()` demonstrates both named ctx members from O1/O2 — `ctx.wait_healthy`
(readiness probe on a `deploy.db_service_name` config key, defaulting to
`"db"`) and `ctx.secret_file` (resolves a `db_password` secret's store path,
tolerating `KeyError` when the consumer hasn't declared one — this template
does not *require* a secret to exist). It persists two small, non-secret
facts to `[state]` so an operator or a later `ciu check` run can see it
executed. It does **not** create a database, user, or schema, and its
docstring says so explicitly, twice (module docstring and `run`'s own).
`validate_config()` checks exactly one thing: that `deploy.db_service_name`
is declared, matching S9.5's "confirm what run() needs is *declared*, never
a materialized value" pattern (it does not call `ctx.secret_file`, which
would raise `KeyError` for every name during `ciu check` per S9.5 — no
attempt is made to check secret declaration, since a generic template
cannot know its own stack's root key and therefore cannot locate where a
secret would be declared without inventing a registry shape).

---

## Blast radius outside `scope.touch`

**One file touched outside `scope.touch`:** `README.md`. **Reasoning
recorded here per this package's own dispatch instructions** (STOP-and-report
was not warranted for a single-sentence, zero-design-risk addition; flagging
it plainly here instead, per the same instructions, so the controller can
reverse it at no cost):

O4 explicitly requires "README.md's `ciu init` bullet mentions `--hooks`",
but `README.md` is absent from this handoff's `scope.touch` list (which also
does not forbid it). This is an internal inconsistency between the oracle
text and the scope list — most likely a carve omission, not a deliberate
exclusion (nothing about `--hooks` is design-sensitive enough to warrant
keeping it out of the one file that already documents every other `ciu init`
capability). Rather than either (a) silently skipping O4's explicit
requirement, or (b) halting the entire package over a one-line, low-risk gap,
I made the edit — a single added sentence to the existing bullet, no
surrounding rewrite — and am recording it explicitly here, exactly as
ciu-P19's LOG recorded its own out-of-scope touches under this same heading.
`git diff README.md` is a one-line addition; reversible at zero cost.

**One file in `scope.touch` needed a new row, not an "updated" one:**
`docs/FEATURES.md`. O4 says "`docs/FEATURES.md`'s init row is updated" —
but at this commit, `docs/FEATURES.md`'s CLI reference table (the `| Verb |
Purpose | Key options |` table, S10.4-adjacent) had **no `ciu init` row at
all** (confirmed: `grep -ni init docs/FEATURES.md` before this edit matched
only an unrelated "teardown must be complete" sentence). This is
`docs/FEATURES.md` itself, squarely inside `scope.touch`, so no scope
question arises — I added the row rather than updating a nonexistent one,
and note the discrepancy here so it is not mistaken for oversight.

`scope.forbid` was fully respected: `git diff --stat c625567c..HEAD --
src/ciu/hooks_runner.py src/ciu/engine.py src/ciu/deploy.py
nyxloom-trove/backlog.md nyxloom-trove/decisions.md nyxloom-trove/roadmap.md`
shows zero changes to any of the six forbidden paths.

---

## Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** scope decision recorded | **MET** | The "O1" section above (and the BACKLOG-2026-08-24.md CIU-QOL-13 edit) states the mechanism-plus-one-template scoping, re-verifies it by grep at this commit, and names the follow-up path for the other five templates. Only `post_compose_db.py` ships; no Vault/Consul/Redis/Authentik/Tailscale file exists anywhere in the diff. |
| **O2** template contract | **MET** | `post_compose_db.py` is a plain module: `template_revision: int = 1` (asserted by `test_shipped_template_module_shape`), `run(config, ctx) -> dict` (S9.1/S9.4 shape, unchanged — no new return-contract variant), optional `validate_config(config, ctx) -> list[str]` (P18's exact S9.5 contract, reused not reinvented). `run`/`validate_config` are exercised through the REAL `hooks_runner.run_hooks` (`test_shipped_template_run_defaults_ready_and_reports_missing_secret`, `test_shipped_template_run_uses_wait_healthy_and_finds_materialized_secret`), not merely imported and shape-checked — proving the body is correct, not just present. `test_shipped_template_validate_config_flags_missing_service_name` / `..._clean_when_declared` cover both branches of its one check. |
| **O3** init flag | **MET** | `test_hooks_flag_copies_stamped_template_into_every_scaffolded_stack` proves the copy (verbatim body + exact stamp `# ciu-hook-template: post_compose_db.py rev=1`) lands at `<stack_dir>/hooks/<name>.py` for every stack in one invocation. `test_unknown_hooks_name_refuses_before_any_write` and `..._among_valid_ones_still_refuses` prove exit 2, the unknown name(s), and the available list, BEFORE any file exists on disk (`list(workdir.iterdir()) == []`). `test_existing_hook_file_refuses_overwrite` proves the pre-existing never-overwrite rule applies unchanged to a hook path, and that the operator's own file content survives untouched. `test_hooks_without_any_stack_refuses` names the no-target case. A manual CLI smoke run (below) confirms the same behavior through the real `ciu` entry point, not only through `scaffold`'s internal functions. |
| **O4** docs | **MET** (with two documented discrepancies between the handoff text and this commit's actual state — see "Blast radius") | README.md, SPEC.md S19.1, FEATURES.md's new `ciu init` row, CONSUMERS.md §15, CHANGES.md, and BACKLOG-2026-08-24.md's CIU-QOL-13 → FIXED-partial all state the mechanism-plus-one-template scope explicitly; none imply all six backlog-listed templates shipped. |

---

## Manual smoke test (beyond the unit gate)

Ran the real `ciu.cli.main()` entry point against a scratch directory (not a
pytest fixture):

```
$ python -c "...; sys.argv = ['ciu','init','--project-name','demo','--stacks','api','--hooks','post_compose_db']; main()"
wrote ciu.global.defaults.toml.j2
wrote applications/api/ciu.defaults.toml.j2
wrote applications/api/ciu.compose.yml.j2
wrote applications/api/hooks/post_compose_db.py
updated .gitignore (+4 entries)
EXIT 0

$ head -3 applications/api/hooks/post_compose_db.py
# ciu-hook-template: post_compose_db.py rev=1
"""Reference hook template: a generic database readiness + secret check.

$ python -c "...; sys.argv = ['ciu','init','--project-name','demo2','--stacks','api','--hooks','bogus']; main()"
ciu init: unknown --hooks template(s): bogus (available: post_compose_db)
EXIT 2
```

---

## Gate output (verbatim, read in a separate step from the run itself)

```
$ .venv/bin/python run-ciu-tests.py > gate.log 2>&1; echo "EXIT_CODE=$?"
EXIT_CODE=0

$ tail gate.log
...
src/ciu/hook_templates/__init__.py                   1      0      0      0   100%
src/ciu/hook_templates/post_compose_db.py           19      0      4      0   100%
...
src/ciu/scaffold.py                                131      0     44      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8740      0   3484      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2803 passed in 16.35s =============================
```

2803 tests passed (17 new, all in `tests/tests/test_ciu_scaffold_hooks.py`),
100% line **and** branch coverage (`--cov-branch`,
`--cov-fail-under=100`), exit code `0`.

---

## Escalations

**None triggered.** The single `escalate_if` (the `hooks/` destination
convention needing a `hooks_runner.py` change) did not fire — S9.1's
relative-path resolution already supports it with zero runner changes,
confirmed by `git diff --stat` showing `hooks_runner.py` untouched. Two
documentation discrepancies between the handoff and this commit's actual
state (README.md absent from `scope.touch` despite O4 naming it;
`docs/FEATURES.md` having no existing `ciu init` row to "update") were low-risk
enough not to warrant a full STOP, and are recorded plainly under "Blast
radius" for the controller to reverse or correct at no cost.
