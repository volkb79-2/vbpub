---
schema_version: 1
id: ciu-P20-hook-template-library
project: ciu
component: scaffold
title: "Hook template library mechanism: template_revision-stamped hook files under src/ciu/hook_templates/, `ciu init --hooks NAME1,NAME2` copies+stamps them into a consumer stack, each implementing run(config, ctx) + optional validate_config(config, ctx) -> list[str]"
tier: implement-2
input_revision: "370ea8141f7f69399a751f2d5731a8ccf5419921"
source: {kind: backlog, ref: "docs/BACKLOG-2026-08-24.md#CIU-QOL-13"}
stack: none
depends_on: [P19]
session: fresh
scope:
  touch:
    - "src/ciu/hook_templates/__init__.py"
    - "src/ciu/hook_templates/post_compose_db.py"
    - "src/ciu/scaffold.py"
    - "src/ciu/cli.py"
    - "pyproject.toml"
    - "tests/tests/test_ciu_scaffold_hooks.py"
    - "docs/SPEC.md"
    - "docs/FEATURES.md"
    - "docs/CONSUMERS.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P20-hook-template-library-LOG.md"
  forbid:
    - "src/ciu/hooks_runner.py"
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-scope-decision-recorded
    observable: "The LOG explicitly records this scoping decision: the backlog's proposed structure names specific consumer-infra hook templates (post_compose_db, post_compose_vault, post_compose_consul, post_compose_redis, post_compose_authentik, pre_compose_tailscale) that mirror dstdns's actual stacks -- a SEPARATE repository this session cannot read. Inventing business logic for a Vault/Consul/Authentik integration this repo has no grounded specification for would risk shipping a template that looks authoritative but doesn't match the real consumer's needs (the exact anti-pattern named in this repo's own CIU-45 withdrawal lesson: an invented capability claim is worse than none). This package therefore ships the MECHANISM in full (template_revision stamping, the ciu init --hooks copy/stamp flow, the run+validate_config contract enforcement) plus exactly ONE reference template -- post_compose_db.py -- kept deliberately generic and honestly minimal (demonstrates the S9.3 wait_healthy/secret_file pattern and a validate_config example, without claiming to be a production PostgreSQL bootstrap). Additional named templates (vault/consul/redis/authentik/tailscale) are explicitly left as future work, one small follow-up package each, once a session with visibility into a real consumer's existing hook can verify the template against it (mirroring how ciu-P11's host-secrets work was grounded in dstdns's ACTUAL Tailscale/SSH bootstrap ask, not an invented one)."
    negative: "shipping five confident-looking but ungrounded hook templates for services this repo has no specification for; skipping the reference template entirely (the mechanism needs at least one working example proving it end-to-end)"
    gate: "tester-unified"
  - id: O2-template-contract
    observable: "Every file under src/ciu/hook_templates/ is a plain module (no package __init__ complexity beyond marking it a package) exposing: a module-level `template_revision: int` (starts at 1, increments on every behavioral change -- document this rule in the module's own docstring so a future template author sees it); `run(config, ctx) -> dict` (S9 contract, unchanged from today's hook shape); an OPTIONAL `validate_config(config, ctx) -> list[str]` (P18's contract -- reuse its exact shape, do not invent a different one). post_compose_db.py's run() and validate_config() bodies are simple and CORRECT for what they claim (e.g. waiting for a database service to be healthy via ctx.wait_healthy before returning a config value) -- do not pad it with unverified business logic to look more complete than it is."
    negative: "a template with a validate_config signature that doesn't match P18's contract; template_revision missing or not an int; a run() implementation that silently assumes registry/secret shapes this handoff has no grounding for"
    gate: "tester-unified"
  - id: O3-init-flag
    observable: "`ciu init --hooks NAME1,NAME2` (composable with init's existing flags, e.g. --project-name/--stacks) copies each named template's file into the target stack directory (decide the destination path -- likely `<stack_dir>/hooks/<template_name>.py`, matching S9.1's 'script paths relative to the stack dir' convention; verify this against hooks_runner.py's docstring, which you may READ though it's in scope.forbid for edits) verbatim PLUS a stamped header comment naming the template's identity and the CURRENT template_revision at copy time (exact format your choice -- e.g. `# ciu-hook-template: post_compose_db.py rev=1` -- document it in SPEC.md so a future revision-comparison feature has a stable format to parse). An unknown template name in --hooks is a configuration error (exit 2, naming the unknown name and the available list) BEFORE any file is written. init's existing 'never overwrite an existing file' rule (build_files's `existing` check) applies to copied hook files too -- do not special-case them into a silent overwrite."
    negative: "silently ignoring an unknown --hooks name; overwriting an existing hook file at the destination path; a stamp format so unstructured a future revision-comparison feature couldn't parse it back out"
    gate: "tester-unified"
  - id: O4-docs
    observable: "README.md's `ciu init` bullet mentions --hooks. docs/FEATURES.md's init row is updated. docs/SPEC.md documents the hook-template mechanism (new S19-adjacent clause: template_revision semantics, the stamp comment format, the run+validate_config contract) under S19 (repository scaffolding) with a cross-reference to S9 (hooks). docs/CONSUMERS.md gets a worked example: `ciu init --hooks db-core` (or your chosen template's slug) then reading the stamped comment. CHANGES.md Unreleased entry explicitly names the O1 scoping decision (mechanism + one reference template, not five). docs/BACKLOG-2026-08-24.md's CIU-QOL-13 row -> FIXED-partial (mechanism + one template; remaining named templates are follow-up candidates) with evidence."
    negative: "documenting this as if all six named templates from the backlog shipped"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "the hook-file destination path convention (relative-to-stack-dir per S9.1) cannot be confirmed by reading hooks_runner.py's docstring/S9 in SPEC.md without ALSO needing to edit hooks_runner.py itself to make templates work -- BLOCKED naming the exact gap; this package copies files into an EXISTING, already-working hook-loading contract and must not need to change it"
mutexes: [merge-lane]
review_focus:
  - "post_compose_db.py's run()/validate_config() bodies are honest about what they demonstrate -- not padded with unverified claims about a real PostgreSQL bootstrap flow"
  - "an unknown --hooks name refuses before any file write (no partial scaffold on a typo)"
  - "the stamp comment format is documented in SPEC.md, not just invented ad hoc in code with no normative record"
---

# ciu-P20 — hook template library (mechanism + one reference template) (CIU-QOL-13)

## Context to read first
1. `docs/BACKLOG-2026-08-24.md#CIU-QOL-13` (already in your context via
   `source`) — the full proposed structure. Read `## O1-scope-decision-recorded`
   above before doing anything else; it narrows this package's actual
   deliverable relative to the backlog's illustrative list.
2. `src/ciu/scaffold.py` — READ IN FULL (~230 lines). `_template`/
   `collect_plan`/`build_files`/`init_main` are the existing scaffolding
   machinery your `--hooks` flag extends. Note `build_files`'s
   validation-first discipline (render + parse + shape-validate BEFORE
   writing anything, `existing`-file refusal) — your hook-copy path follows
   the same discipline (a hook file is simpler — no Jinja2 substitution
   needed, it's copied close to verbatim plus a stamp header — but the
   never-overwrite and validate-before-write principles still apply).
3. `pyproject.toml` — `[tool.setuptools.package-data]` (~line 43): `ciu =
   ["data/*.c", "templates/*"]` — add `"hook_templates/*"` here so the new
   directory ships inside the wheel (mirrors how `templates/*` already
   ships `ciu init`'s scaffold templates).
4. `docs/SPEC.md` S9 (Hooks, ~995-1034) — the exact `run(config, ctx) ->
   dict` contract; S19 (Repository scaffolding, `ciu init`, search `## S19`)
   — where you document the new mechanism.
5. `nyxloom-trove/handoffs/ciu-P18-config-check-hook-preflight.md` — P18's
   `validate_config(config, ctx) -> list[str]` contract (lands before this
   package runs); your template's optional `validate_config` must match it
   exactly, not a similar-but-different shape.

## Work
1. `src/ciu/hook_templates/__init__.py` (empty or a short docstring) +
   `post_compose_db.py` (the one reference template, per O1/O2).
2. `pyproject.toml`: package-data entry for `hook_templates/*`.
3. `scaffold.py`: `--hooks NAME1,NAME2` flag in `collect_plan`, copy+stamp
   logic in `build_files`/`init_main` (O3).
4. `cli.py`: confirm/extend `init`'s argv passthrough if needed (it likely
   already forwards `rest` to `init_main` unchanged — verify, don't assume).
5. Tests: valid `--hooks` copy + stamp format, unknown name refusal,
   never-overwrite-existing enforcement, `template_revision`/`validate_config`
   contract shape asserted on the shipped `post_compose_db.py` itself (a
   test that imports it and checks the contract, not just that the file
   exists).
6. Docs per O4.
7. LOG at `nyxloom-trove/reports/ciu-P20-hook-template-library-LOG.md`,
   restating the O1 scoping decision.

## Environment setup
Same worktree/venv as prior packages:
`cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu && .venv/bin/python run-ciu-tests.py`

## BLOCKED rule
Per `escalate_if` above. Forbidden workaround: inventing hook business logic
for a named consumer service (Vault/Consul/Redis/Authentik/Tailscale) this
repo has no grounded specification for, just to match the backlog's
illustrative list.
