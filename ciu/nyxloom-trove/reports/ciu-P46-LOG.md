# ciu-P46 — LOG

Package: `nyxloom-trove/handoffs/ciu-P46-persist-secret-hooks-migration-check.md`
— A1 (`persist:"secret"`), A2 (Vault bootstrap off `[state]`), A3
(`vault-presence` static stage / F7), A4 (`state-secrets` static stage), A5
(`ciu migration-check` verb + rule registry). Harness-provided worktree
`/workspaces/vbpub/.claude/worktrees/agent-a3aef243215d54b0d`, branch
`worktree-agent-a3aef243215d54b0d`, based on vbpub main `754cf35c`
(`docs(run-gate): clear the post-23.2.2-release stale Unreleased block` —
the tip when the worktree was created, carrying ciu 7.8.0).

Four commits. Docs (SPEC/CONFIG/CONSUMERS/CHANGES) landed in the SAME commit
as the code, per this repo's own "FIXED means code, behavioral tests, SPEC,
and user documentation landed together" rule
(`KNOWN_ISSUES_TODO_BACKLOG.md`'s own opening paragraph); only the backlog
bookkeeping was split out, matching ciu-P44/P45's precedent for that file
(its header paragraph shares diff hunks with adjacent unrelated content, so a
clean per-item split is not mechanically available there).

**The real gate** (`./run-gate.py ciu --worktree
/workspaces/vbpub/.claude/worktrees/agent-a3aef243215d54b0d`, run from
`<worktree>/ciu`) was run TWICE: once at `af334ee8` where it FAILED, and once
at the final HEAD `bc23f15d` where it PASSED. Verdict read in a separate
step from the run, off the preserved log and the verdict artifact, never a
piped tail. See `ciu-P46-REPORT.md` for the verbatim verdict.

---

## Commit 1 — `8ece3eb5` — `feat(ciu)!:` A1-A5, code + tests + SPEC/CONFIG/CONSUMERS/CHANGES

28 files, +2614/-186.

### A1 — `persist:"secret"` (S9.4a)

`hooks_runner.run_hooks` gained a keyword-only `declared_secret_names`
(defaulting to `None`, so every existing positional call keeps working) and a
`persist == "secret"` branch delegating to a new `_persist_secret`. Storage
goes through a new `materialize.write_hook_secret`, which reuses
`_write_store_file`/`_ensure_dir_mode`/`_flock` verbatim rather than
reinventing a second writer — same `<stack>/.ciu/secrets/<name>` path, same
0440 mode, same 0700 store dirs, same atomic `mkstemp` + `os.replace`, same
per-stack lock. `_write_store_file` needs only `spec.mode`/`spec.uid`, so the
call constructs a synthetic `SecretSpec(kind="HOOK", locator=None)` with the
default `"0440"` mode instead of refactoring that function's signature.

Refusal order in `_persist_secret`, each raised BEFORE any write and each
naming the KEY only (S4.23, asserted per-case in the tests): dotted path →
S4.6-grammar violation → non-string value → collision with a
directive-declared name. The `apply_to_config` + `persist:"secret"` refusal
is raised earlier still, before the `apply` mutation itself, so a refused
return can never half-apply — pinned by its own test.

Engine wiring: `declared_secret_names = frozenset(spec.name for spec in
specs)` computed once, from the SAME `specs` Step 5 already discovered (so the
two channels cannot disagree about what is declared), and passed at all three
hook points.

### A1 — discoverability

`ciu secrets list` had nothing to enumerate a hook-persisted secret FROM:
`list_secrets` walks specs, and a hook-persisted name has no spec. Two shapes
were considered:

- **scan the store dir for undeclared files.** Rejected: a stale file left by
  a since-removed declaration is indistinguishable from a hook-persisted one,
  so the listing would confidently mislabel it, and the handoff's requested
  `source: hook:<script>` annotation is unavailable at all.
- **a provenance sidecar** — chosen. `<stack>/.ciu/secrets/.hook-persisted.toml`
  maps `name -> "hook:<script>"`, mode 0600, written atomically under the same
  lock. It holds names and paths only, never a value (asserted). Rows are
  reported as kind `HOOK` with the hook path as the locator, and a row for a
  name a directive HAS since declared is suppressed rather than mislabelled.
  An unreadable/corrupt sidecar degrades to `{}` — it is metadata for a
  listing and must never fail a deployment.

`reset_hook_secrets` is its counterpart; `secrets_command` calls it after
`reset_secrets` and unions both name sets for `--name` validation, so
`ciu secrets reset --name <hook-persisted>` no longer reports "no such
secret".

### A2 — S4.16 source #3

`resolve_vault_token`'s `[state].root_token` read is deleted outright (with
its `tomllib`/`STACK_CONFIG_RENDERED` imports, now unused). Source #3 reads
`hook_secret_store(repo_root / stack_path, VAULT_BOOTSTRAP_SECRET)`; the name
`root_token` is a new module constant so the well-known name has one
spelling. `FileNotFoundError` falls through (the stack may not be
bootstrapped here — the same semantics an absent `token_file` already had);
any other `OSError`/`UnicodeDecodeError` is a typed `[S4.16]` error naming the
PATH, because present-but-unreadable is indeterminacy, not emptiness.
`engine`'s own no-token abort message was updated to stop naming
`[state].root_token`.

### A2 — the reference fixture, and the one judgment call the handoff left open

The handoff's pre-analysis was followed exactly for steps 1-3:
`post_compose_vault.py` stops returning `root_token` (keeping only
`initialized`), `[state].root_token` is deleted from
`test-repo/infra/vault/ciu.defaults.toml.j2`, and the hook's docstring now
explains the corrected flow.

**A consequence the handoff did not name, found while implementing:** with
`root_token` removed from `[state]` AND unable to use `persist:"secret"` (its
name is directive-declared as `GEN_LOCAL:demo/vault_root_token`, which A1's
own uniqueness rule refuses), the demo's later `GEN_TO_VAULT` stacks
(redis-core, db-core) would resolve NO token at all — source #1 is unset,
#2 was unconfigured, #3 is legitimately absent. Leaving CIU's canonical
reference fixture knowingly broken was not acceptable, and inventing new
mechanism for it would have contradicted the handoff. Resolved with the
existing, already-documented, already-sanctioned mechanism and no new code:
`[vault].token_file = ".ciu/secrets/demo/vault_root_token"` added to
`test-repo/ciu.global.defaults.toml.j2` — S4.16 source #2 pointed at the SAME
0440 project-store file the `GEN_LOCAL` directive materializes. Called out
explicitly in CHANGES.md's adoption notes and CONSUMERS.md #20 as the
prescribed migration for any consumer with a directive-backed vault token.

The handoff also asked for a second, genuinely-minting worked example: added
as `docs/SPEC.md` **§B.2a**, with §B.2's own prose corrected (it still
described `[state]` carrying `root_token`/`unseal_key`).

### A3 / A4 — the two static stages

Both live in `_check_stack_config` (per-stack scope), appended to
`CHECK_STAGES` after `service-registry` — never interleaved, so stages 1-13
keep the positions `test_check_stage7_is_between_configfile_and_hooks_load`
pins. `vault-presence` reuses `discover` + `vault_addr_from_config` (the same
functions S4.16 itself calls) and stays silent when the `secrets` stage
already failed for that stack: one root cause, one finding, and the specs it
would otherwise read are untrustworthy.

`state-secrets` reads `stack_cfg`, deliberately NOT `merged`: `[state]` is a
per-stack top-level table, and merging could only widen the scan to keys the
stack does not own and emit the same global finding once per selected stack.

**The heuristic — a real judgment call.** The handoff said to reuse "the exact
heuristic already implemented for S2.4.1". S2.4.1 is a **SPEC-V8 clause and is
not implemented in v7 at all** (verified by grep: `S2.4.1` appears only in
`docs/SPEC-V8.md`). What exists in v7 is S3.1a's
`config_model.scan_override_for_secrets`. So the two halves were taken from
where each actually lives: the KEY test is S2.4.1's normative text verbatim
(last `_`-separated component in the eight-name list), the VALUE test is
S3.1a's existing implementation, factored out into a shared
`is_secret_shaped` that `scan_override_for_secrets` now calls the constant of
— one implementation, not two that can drift.

One S2.4.1 exclusion was deliberately NOT adopted: its KV-path-shape test
`^[a-z0-9_./-]+$` would exclude `s.demo-token` and most dev-mode tokens,
defeating the rule in exactly the case it exists for. S3.1a's own path
exclusion (`"/" in value`) is used instead — it covers Vault KV paths and
`/run/secrets/` mounts without swallowing a real token. The full truth table
is pinned by an 18-case parametrized test.

`find_secret_shaped_keys` walks nested tables, because `persist:"state"`
accepts a dotted path — without that, the rule would be side-steppable by
returning `vault.root_token` instead of `root_token`.

### A5 — `migration_check.py`

A plain `RULES` tuple of `Rule(name, description, detector)`, each detector
`(repo_root) -> list[Finding]`, `Finding(rule, severity, message,
remediation)` reusing S9.5's `WARN`/`ERROR` vocabulary. Two entry points over
the one registry: `main()` (verb, any finding → non-zero) and
`deploy._check_migration` (stage, WARN → note / ERROR → fail). The stage
walks `run_migration_check`; a test monkeypatches it and asserts the stage
called it, so a future reimplementation inside `deploy.py` fails loudly.

**Rule 1's wording problem, and how it was resolved.** The handoff states the
`retired-overlay-file` detector "will find nothing today, since P46 does not
rename anything" — but `ciu.global.worktree.toml.j2` is the LIVE overlay and
exists in every real checkout, so a literal existence check would WARN on
every `ciu check` everywhere, about the current file. The detector instead
keeps a `RETIRED_OVERLAY_NAMES` history literal and filters it against the
live `GLOBAL_CONFIG_WORKTREE_OVERRIDES` constant. Today the two are equal →
the rule is silent, exactly as the handoff requires; the moment ciu-P47 flips
that one constant the rule goes live with no edit to the detector or the
registry. Both halves are pinned by tests (the P47 shape is proven now by
monkeypatching the constant).

Rule 3 (`gitignore-gaps`) is silent when `.gitignore` is ABSENT — a
deliberate narrowing, documented in the detector and in S13.7: a checkout that
never ran `ciu init` is a first-run state, not a stale artifact, and this
verb's whole subject is stale artifacts. Firing there would have put a WARN
note on essentially every `ciu check` in the test suite for no signal.

The fourth candidate rule (secret-shaped `[state]`) stays dropped, and a test
pins the registry to exactly the three names so re-adding it is a deliberate
act rather than drift.

### Test-fixture fallout (expected, not defects)

Five `run_hooks` monkeypatch stubs took exactly five positional parameters and
broke on the new keyword — `**_kw` added to each. Nine provider/hook tests
asserted the OLD `[state].root_token` source #3; each was rewritten onto the
store file rather than deleted, and three gained a positive assertion that the
legacy `[state]` copy now resolves NOTHING (the hard cutover, tested as a
contract rather than left to inference).

### Controlled-wrong-implementation caught before landing

`_persist_secret`'s first version imported `directives._NAME_RE` (a private
name across a module boundary). Replaced with the public `SECRET_NAME_RE` +
`re.match` before commit.

## Commit 2 — `af334ee8` — `backlog(ciu):` F4/F7 backported early; CIU-38 untouched

`KNOWN_ISSUES_TODO_BACKLOG.md`'s "Last updated" running-history paragraph,
with ciu-P45's pushed down to a `Previously, 2026-08-31 —` entry beneath it
(the file's own same-day-stacking convention). Records what F4/F7 are, why
they were not held for the v8 cutover, and — explicitly, as the handoff's own
correction requires — that **CIU-38 stays OPEN/deferred and is unrelated**,
with the reason (AppRole credentials route through Vault itself and need no
new CIU mechanism). CIU-38's own row and detail section were not touched.

## Commit 3 — `bc23f15d` — `fix(ciu):` drop the sidecar-writer pragma

The real gate's FIRST run (at `af334ee8`) came back **FAIL/EXCLUDED_LINES**:
assay's R1 claim runs with `allow_excluded: false`, and the single
`# pragma: no cover` on `_write_hook_manifest`'s temp-file cleanup
(materialize.py:624-627) was therefore an unjudged line, not an excused one.
This is precisely the `assay-gate-vs-pytest-gap` this estate has a name for:
`pytest`/`run-ciu-tests.py` had reported 100.00% WITH that pragma honoured,
and would never have surfaced it. Pragma removed; the branch is now covered
for real by monkeypatching `tomli_w.dump` to raise mid-write and asserting no
stray `.tmp-hookman-*` survives in the store dir.

## Commit 4 — this LOG/REPORT
