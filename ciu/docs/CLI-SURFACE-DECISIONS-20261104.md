# ciu CLI surface decisions, 2026-11-04

Branch `feat-ciu-clix-20261104` (from main `3d7e34cc6`, ciu 7.19.0). Carves CIU-114 (adopt cli-extended).
Not merged. Written while the operator was AFK for ~8 hours: every question the interview would have
asked was decided with the recommended default of `CGPCTL-PLAN-2026-11-04.md` section 6 and is recorded here
so it can be reviewed and vetoed. **Each decision group is one commit (or one group of commits) on the
branch, so a veto is `git revert <commit>` of that group.** Operator rules applied: adopt cli-extended with all
its features; surface changes free; no backwards-compat shims or aliases (rename outright, update every caller
in this repository); dstdns migrates later.

Legend for "Revisit": what the interview would have asked.

## 0. How the adoption is built (architecture decision, D-ARCH)

`ciu/src/ciu/cli_registry.py` declares every verb, argument and option once with `CliRegistry`/`VerbSpec`/
`OptionSpec` (identity, groups, constraints, `--json`, `--yes`, `--dry-run`, `--traceback`, exact option
spellings, surface export, skills, doctor). Each handler is an adapter: it re-spells the parsed namespace into the
token list the implementation layer (`deploy.main`, `engine.main`, `worktree`, ...) already understood and calls
it through `cli._run_legacy`. The implementation parsers stay behind the registry as an internal layer; they only
receive tokens the registry validated. The old front end is gone: the `if verb ==` chain no longer parses anything
(it is `_legacy_dispatch`, a token router), `_USAGE`/`_VERB_HELP`, per-verb help interception, the `--ksm` strip,
the hand version reader and the `--host`/`--layout`/`--dir` abbreviation routing (SPEC S10.4).
- Why not inline the implementations: the `deploy.py` forwarding is the hard part (CGPCTL plan C1); inlining 6k lines of
  parsing risks every one of the ~257 test files. The adapter gets 100% of the user-visible adoption now and
  lets the internal parsers be retired verb by verb (follow-up, not blocking).
- Revisit: whether to retire the internal parsers before v8.

## 1. Forced by the library (not a free choice; no alias is kept)

| Change | Old | New | Why | Callers updated |
|---|---|---|---|---|
| F1 | `-y` | `--yes` | the library has long options only; `-y` is not a library control and AC-05 forbids a consumer copy | see section 5 sweep |
| F2 | `-d PATH` (secrets) | `--dir PATH` | long options only | sweep |
| F3 | `-f NAME` (up --dir) | `--file NAME` | long options only | sweep |
| F4 | `-h`, `--help` interception per verb | `--help` only (library rejects `-h`) | library contract | sweep |
| F5 | unambiguous prefix spellings (`--prof`, `--ho`, `--root`) | exact spellings only | library default; kills S10.4 routing | none found in repo |
| F6 | `ciu version` / `ciu --version` | unchanged names, library output `ciu <version>`; `ciu help [verb]` added | library | n/a |
| F7 | literal version fallbacks (`unknown`, `0.0.0+unknown`) | `CliIdentity.resolve`: installed metadata and/or build-time `_version.py`, must agree; a source tree that is neither built nor installed exits 3 with a clear message | AC-01, AGENTS "defaults are hazards" | tests (conftest stand-in dist-info) |
| F8 | bare `ciu env` (deprecated ciu.env dump, CIU-75) | **removed**; `env` is a group: `env generate`, `env print` | the library cannot register a verb that is both a handler and a group; the verb was already deprecated (CGPCTL C11) | repo grep: see sweep |
| F9 | `up --check` | removed (use `ciu check`) | duplicate of a verb; flag soup on `up` | sweep |
| F10 | `up` engine flags `--update-cert-permission`, `--auto-connect-network`/`--no-auto-connect-network`, `--generate-env` | not declared on the registered surface | zero callers outside src/tests (verified by `git grep`) | n/a |
| F11 | `provenance --force` (alias of `--ignore-mismatch`) | removed | no aliases | n/a |
| F12 | `graph --format json` | `graph --json` (`--format mermaid\|dot`) | CGPCTL C2: duplicate JSON spelling | sweep |
| F13 | `resolve ... --json` required | `resolve` always emits its JSON document (`--json` accepted, library-owned) | a read verb with one output shape should not demand the flag | sweep |
| F14 | `--ksm` / `--no-ksm`, `--log-prefix-time-short` | global options of the registry (accepted before or after any verb), same semantics | CGPCTL C3 (globals) | n/a |

Revisit: F1 is the biggest blast for dstdns (`clean -y` alone is 255 lines there); the library has no short-option
mechanism, so the alternative would be leaving cli-extended out, not an alias.

## 2. Exit codes (D-EXIT, CGPCTL CQ3)

- v7 keeps SPEC S10.3: 0 success, 1 runtime failure, 2 usage/validation, 3 environment/bootstrap. The library's
  0/1/2 coincide; 3 is raised as `CliFailure(exit_code=3)` / `VersionLookupError` in `main()`.
- **4 (contention) and 5 (remote failure) are reserved for v8 and never emitted by v7.**
- The v8 gate table (PASS 0, FAIL 1, ERROR 2, NOT_RUN 3, BUDGET 4) collided with 3/4 above. Decision: the five gate
  buckets live **inside the gate's JSON document**; the gate process exits 0 (PASS), 1 (FAIL), 2 (ERROR/usage), 3
  (environment, incl. NOT_RUN for an unavailable environment) and uses 4 only for budget/contention in the v8 sense.
  Recorded as a settled note in `SPEC-V8.md` (S18). Revisit: the exact NOT_RUN/BUDGET mapping when run-gate merges
  into ciu in v8 (operator wants run-gate integration considered: this keeps the table free for it).

## 3. Voluntary surface changes (CQ2: C4-C8, C11-C13 now; C3, C9, C10 wait for v8)

Status column is filled as commits land.

| # | Change | Old -> new | Rationale | Blast (vbpub/dstdns, from the plan) | Status |
|---|---|---|---|---|---|
| C4 | doctor + skills | new `ciu doctor`, `ciu skills install\|uninstall\|check\|list` | free from the library; v8 S18 already names both; skills move to package data (AC-19) | none | adoption commit |
| C5 | status absorbs health | `ciu health [--preflight [--strict]] [--host H --thin]` -> `ciu status --gate`, `ciu status --preflight [--strict]`, `ciu status --gate --host H --thin` | v8 parity (S18 "status absorbs health") | 0/10 | done (commit "status absorbs health") |
| C6 | show absorbs profiles+layouts | `ciu profiles` -> `ciu show profiles`; `ciu layouts` -> `ciu show layouts` (both gain `--json`) | v8 parity; fixes the CIU-109 smell (listing verb vs writer) | 0/35 | done (commit "show absorbs profiles and layouts") |
| C7 | governance / schema / migrate | `ksm build` -> `governance ksm`; `iops-baseline` -> `governance iops-baseline`; `capabilities` -> `schema capabilities`; `migration-check` -> `migrate --check` (`--check` mandatory in v7) | v8 parity (S18: `governance ksm \| iops-baseline`, `schema`, `migrate [--check]`), all ~0 external use | ~0 | done (commit "governance, schema, migrate, secrets host") |
| C8 | secrets absorbs host-secrets | `host-secrets HOST --materialize\|--list\|--path N` -> `secrets host HOST ...` | v8 parity | 0/0 | done (same commit as C7) |
| C11 | remove bare `env` | see F8 | deprecated since CIU-75 | small | adoption commit |
| C12 | confirmation / dry-run honesty | `down`/`clean` keep the `[deploy] protected = true` gate (CIU-105: `--yes` AND `--i-understand-this-is-protected`), now declared as a first-class option; library `--yes` replaces `-y`; `--dry-run` is library-owned on `up` | closes CIU-105 for flagged instances without a new prompt for ordinary scripts (`clean` has 18/255 callers; an unconditional prompt would break them) | `clean` 18/255 | adoption commit (declaration); CIU-162/CIU-103 NOT in scope (they are implementation side effects, not grammar) |
| C13 | `ciu run` (CIU-141) | `run [--detach --json] ...` plus `run reap` | v7 lifecycle primitive for one-shot execution from the rendered service; v8 `gate run`/`gate exec` can delegate to it | - | added in v7 by RG-102 part 1; see SPEC S8.9/S10.8 |
| C14 | keep the `gate` group name unused | `ciu gate` is NOT registered in v7 | keeps v8's run-gate merge clean | - | kept |
| C3 | `--root` global rename | **not done**; `--root-folder` stays per verb | renames a flag every script uses; v8 | not counted | deferred to v8 |
| C9 | `build` for `bake` | **not done** | "build" overloaded with cmru; v8 | 8/23 | deferred to v8 |
| C10 | `instance` for `env generate` + `worktree *` | **not done** | largest blast; dstdns dominates (75/400+) | | deferred to v8 |

Open design points inside the voluntary changes (decided, flagged for veto):
- C5: v8 says `status` absorbs `health`, but status is a read-only report while `health` is a gate with an exit
  status. Decision: `status` stays a report; `--gate` makes the exit status the verdict (the S7.7 gate), `--preflight`
  is the image-tool probe. `--host/--thin` ride along on `--gate` only. Revisit: whether `--preflight` belongs under `doctor`.
- C6: `show` is read-only and `--json` capable; it never writes generated config (CIU-109 stays fixed).
- C7: `governance` is a group; `capabilities` becomes `schema capabilities` because v8 S18 owns the `schema` group.

## 4. CQ answers recorded

- CQ1 adopt in v7 with v8 names where a name changes: **yes** (this branch).
- CQ2 which renames now: C4-C8 and C11-C13 (above; C13 was amended by RG-102 part 1).
- CQ3 exit codes: section 2.
- CQ4 `worktree add` callers: sweep, no alias. `worktree add` was removed in `d1eb98770`; callers in vbpub are
  fixed in the sweep commit; dstdns is NOT touched (migrated later). Verify dstdns's ciu version when it migrates.
- CQ5 `ciu run`: amended by RG-102 part 1 — ship the CIU lifecycle primitive in v7; the v8 `gate` surface may build on it.
- CQ6 v8 sign-off: still pending. This branch does not edit v8 semantics except the exit-table note.

## 5. Callers swept

Patterns grepped over the whole monorepo (`git grep`, docs, skills, scripts, tests, internal subprocess spawns,
`.claude/skills`, mdt-cockpit, nyxloom, voice-screen-share, scripts/netcup): `ciu .* -y`, `secrets .* -d`,
`ciu (health|profiles|layouts|ksm|iops-baseline|capabilities|migration-check|host-secrets)`, bare `ciu env`,
`up .*--check`, `graph .*--format json`, `provenance .*--force`, `worktree add`, `"-m", "ciu.cli"` spawns.

- Internal spawns fixed: `worktree.py` (`clean -y` -> `--yes`, three sites), the remote `health` command
  (`ciu health` -> `ciu status --gate`).
- Docs/skills/READMEs fixed: ciu docs (README, CIU, CIU-DEPLOY, CONFIG, CONSUMERS, DESIGN-GUIDE, DESIGN-NOTES,
  FEATURES, MIGRATION-V2, SPEC), ciu skills (now `ciu/src/ciu/skills`), `ciu/test-repo/README.md`,
  `nyxloom/.claude/skills/nyxloom-merge-p`, `nyxloom/ntfy/README.md`, `mdt-cockpit/docs/BTRFS-COW-OPTIONS.md`,
  voice-screen-share (jitsi, fluxer, mattermost x4 files, mattermore).
- `worktree add` (CQ4): the vbpub hits are `git worktree add` (git, not ciu); no ciu caller exists in vbpub. dstdns
  is not touched (it migrates later); when it does: `git grep -n 'ciu worktree add'` and verify its ciu version.
- Left alone on purpose: historical records (`CHANGES.md` history, `KNOWN_ISSUES_TODO_BACKLOG.md` bodies, reports,
  `nyxloom-trove/archive`, `ciu8` design docs): they describe what was true when written.
- Not found anywhere outside ciu: scripts/netcup/runner and mdt-cockpit scripts do not invoke the changed verbs.

## 6. Adoption checklist status (AC-01..AC-25), audit at branch tip

`cli-extended audit`: 13 pass, 2 manual, 0 fail.
- AC-01 identity (`CliIdentity.resolve`, no literal fallback), AC-04, AC-05, AC-07 (no `configure` callbacks),
  AC-10 (`unexpected_exceptions="report"`), AC-16/17/18 (manifest + spec region + 669-case reviewed catalog,
  `surface check` passes, syntax complete), AC-19 (skills packaged, validated), AC-20 (`doctor`), AC-22 (pytest
  plugin enabled), AC-24 (`cli-extended>=0.5.0`), AC-25 (no library source on any path: assay.toml and
  run-ciu-tests.py dropped it).
- AC-08 (manual): the one hidden option is `env generate --identity-only`, internal plumbing for the worktree
  allocator (recorded in `cli_registry._env_cli`).
- AC-12 (manual): 17 mutating verbs have neither `--yes` nor `--dry-run`. Each one is either idempotent and
  non-destructive (`env generate`, `render`, `init`, `worktree create|ensure|adopt|lease|refork|up`,
  `governance ksm|iops-baseline`, `bake`, `host enroll` step 1), interactive by nature (`ssh`, `dev`, `exec`,
  `worktree exec`) or already confirms itself (`secrets host --materialize` prompts unless `--yes`, `host enroll`
  step 2 asks for the fingerprint). The destructive verbs all have `--yes`: `down`, `clean`, `secrets reset`,
  `worktree rm|branches|reap`; `up`/`worktree reap` have `--dry-run`. Revisit: whether `render`/`init` should
  take the library `--dry-run`.
- AC-21: `assert_cli_contract` against a real launcher (test_ciu_cli_review_cases.py).
- AC-23: not applicable (no per-project subprocess wrapper was replaced; the 22 test files that shell out keep
  their own helpers; follow-up).
- AC-02/03/06/09/11/13/14/15 are `manual` rows in the checklist: AC-03 holds (every verb is registered; the
  internal implementation parsers remain behind the adapters, see D-ARCH), AC-06 holds for the declared
  constraints, AC-09 (`SelectorList`) is NOT used (profile lists stay comma strings parsed by the
  implementation), AC-11/13/14/15: domain failures stay printed by the implementation layer (`[ERROR] ...`
  + exit code), prompts stay the implementation's (`runtime.confirm` is not wired into `up`/`clean`), and the
  implementation still prints directly instead of `runtime.output`; revisit when the internal parsers go.

## 7. Further decisions made while building (for review)

- D-HEADLINE: a usage/config diagnostic now leads with `[ERROR] ...`, then the CIU headline and usage (the library's
  block order); help screens still begin with the headline. SPEC S10.1 rewritten; tests adapted.
- D-GLOBALS: `--root-folder` stays a per-verb option (only on verbs that accept it); `--log-prefix-time-short`,
  `--ksm`, `--no-ksm` are registry globals.
- D-VOCAB: `show profiles` keeps the v7 word "profiles" (v8's S18 calls the same thing `show bundles`); the rename
  belongs to the v8 vocabulary change, not to this surface adoption.
- D-REMAINDER: `exec`, `ssh` and `worktree exec` take their command after `--` as a registered remainder argument;
  the child argv is passed through byte for byte.
- D-NO-COMPLETION: cli-extended 0.5.0 provides no shell-completion generator (SPEC mentions it only as a category),
  so there is nothing to adopt; no completion script was invented.
- D-CATALOG: the reviewed catalog (669 rows) is a characterization generated by `tools/gen-cli-review-catalog.py`
  from the exported surface; each row runs through the real registry in a sandbox (implementation recorded, nothing
  executed). It pins grammar, not semantics. A semantic LLM review of the high-risk verbs (`clean`, `down`, `up`,
  `worktree rm`) with `cli-extended surface pack` is still to do. Revisit: whether that review is required before
  release.
- D-INSTALLER: `ciu/cmru.toml` lists `vendor/cli_extended-*.whl` before ciu's wheel and `cmru.orchestration.toml`
  gives ciu a `depends_on = ["cli-extended"]` release edge; `get.py` was re-rendered (one-line diff). The release
  bundle that `get.py install` consumes (CIU-99: not built yet) must carry the cli-extended wheel.
- D-TESTENV: the tester image installs cli-extended but not ciu; `tests/conftest.py` provides a throw-away
  `ciu-0.0.0+tests` dist-info (on `sys.path` and `PYTHONPATH`) so `CliIdentity.resolve` finds a version. Without it
  the CLI exits 3 by design.

## 8. RG-102 amendment (2026-11-05)

CIU-141 adds the v7 `ciu run` lifecycle and `ciu run reap`; it does not register
`ciu gate` (C14 remains in force). The recommended answers to RG-102 plan
questions 1–6 were accepted. The real-Compose spike amended question 1's
foreground mechanism: foreground uses `docker compose run --name N` without
`--rm`, exact-name inspection, explicit signal forwarding, inspected status,
and exact-name removal. Run-Gate uses `--detach --json`, then owns observer,
status collection, and removal by the returned ID. V1 has no first-instruction
readiness barrier; early-sample coverage is not promised. SQL-mutation keeps
operator-exported DSNs through `--env-name`, and `[.ciu]` environments retain
exec-mode service-mount git-hardening parity.
