# ciu-P34 — CONSUMERS.md: migrating a hand-rolled `internal_host` override to `ref_services`

**Handoff:** `nyxloom-trove/handoffs/ciu-P34-consumers-ref-services-migration-note.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `baa91ae5` (confirmed
with `git status --porcelain && git log --oneline -3` before any edit — tree
was clean).

**Status: COMPLETE.** Docs-only. Full gate green (3261 passed, 100.00%
line+branch coverage), zero source or test file changes. No `escalate_if`
fired.

---

## 1. Reading, before writing anything

Read the handoff in full, then in order: `KNOWN_ISSUES_TODO_BACKLOG.md`'s
CIU-49 entry (the filed BEFORE text and its "PARTIAL" disposition) and its
CIU-52 entry (the shipped S16.1a mechanism and the explicit note that the
filing's own illustrative TOML was wrong about how `services`/`ref_projects`
pair — a corroborating example of why this package re-verifies rather than
trusts prior citations), `docs/CONFIG.md`'s current shared-infra section, and
`docs/CONSUMERS.md` in full (to find its actual current highest section
number) plus `tests/tests/test_ciu_worktree_shared_infra.py`'s
`TestRefServicesAddTimeResolution` fixtures.

## 2. Two citations in the handoff had drifted — both self-corrected per the
   outer task's own instruction, neither is an `escalate_if` condition

**CONFIG.md line numbers.** The handoff cited "around line 899-954" for the
S16.1a worked example. Current location is `docs/CONFIG.md:1039-1126`
(`### Shared-infra join example [S16.1]` through the end of the
`--shared-infra-ref-services` subsection) — the mechanism content itself
(flag name `--shared-infra-ref-services ALIAS[,ALIAS=SERVICE]`, TOML table
`[ciu.instance.shared_infra.ref_services.<alias>]`, emitted
`[topology.services.<alias>]` block) is unchanged from what the handoff
describes. This is a pure line-shift from packages landing since the handoff
was carved (P31/P32/P33 all touched files above it) — not a shape drift, so
the `escalate_if` ("the CONFIG.md worked example ... has drifted from
CIU-52's actual shipped CLI flag/TOML shape") does not fire.

**CONSUMERS.md section numbering.** The handoff's own frontmatter/oracle text
assumed the highest existing section was `## 15. Adopt a shipped hook
template...` and that the new section becomes `## 16`. Checked directly:

```
$ grep -n "^## [0-9]" docs/CONSUMERS.md | tail -3
756:## 15. Adopt a shipped hook template instead of hand-writing one (`ciu init --hooks`, S19.1)
803:## 16. Fan a service out by `instances` instead of hand-rolling a compose loop (V8-PREP-6)
```

Section 16 already exists — V8-PREP-6 (`ciu.instances`, unrelated topic)
landed after this handoff was carved and claimed the number the handoff
expected to use. Per the outer task's explicit instruction to re-verify this
rather than trust the handoff, the new section is numbered **17**, appended
after §16 at the end of the file (previously 871 lines). This is exactly the
kind of staleness the task briefing warned about, not a scope or shape
problem — no escalation needed.

## 3. The real BEFORE text, re-found rather than trusted from the handoff's quote

`KNOWN_ISSUES_TODO_BACKLOG.md:1210` (CIU-49 entry), quoted verbatim,
character-for-character:

```
internal_host = "dstdns-mstest-f2d1cb-vault"  # instance config: scoped (GUIDE 3.6)
```

This matches the handoff's own quotation exactly — re-grepped directly
against the current file rather than copied from the handoff, per this
package's own review_focus item.

## 4. The AFTER example — executed against a real fixture, not typed from memory

Per O2, the CLI invocation and resulting TOML block published in CONSUMERS.md
§17 were proven by actually running them, reusing (not reinventing) the exact
fixture construction `test_ciu_worktree_shared_infra.py`'s
`TestRefServicesAddTimeResolution.test_headline_contract_resolves_qualified_host_and_port`
already uses: a throwaway git repo (mirroring the `tmp_repo` fixture), a
reference instance created via an ordinary `worktree.add` with `ciu.env`
pinned to `INSTANCE_ID=aaaaaa` and a `ciu.global.defaults.toml.j2` declaring
`project_name = "dstdns"` / `environment_tag = "${INSTANCE_ID}"` /
`[topology.services.vault] internal_port = 8200` (mirroring the `ref_instance`
fixture), and a `ScriptedDocker` fake assigned to `worktree.procutil.docker`
answering exactly the three Docker calls this path makes (network-exists,
ref-project liveness, ref-service liveness) — the identical predicate
functions the test file defines (`_is_network_inspect_exists`,
`_is_ref_project_ps`, `_is_ref_service_ps`).

The one deliberate difference from the test file: instead of calling
`worktree.add(...)` (the internal Python API the pytest fixtures call
directly), this verification drives `ciu.cli._worktree(rest)` — the same
function `ciu`'s real `main()` dispatches `verb == "worktree"` to — with the
literal argv list published in the docs, so the proof covers the exact
flag spelling a reader would type, not just the underlying function
signature. Script:
`/tmp/claude-1003/-workspaces-vbpub/73531854-0a40-4598-8b44-a207b6a1b698/scratchpad/verify_ref_services_example.py`
(scratchpad-only, not part of this commit — `scope.forbid` excludes
`tests/tests/*.py` and this is not a test file at all, just an ad hoc
reproduction run once for this evidence).

Real output, pasted verbatim:

```
$ .venv/bin/python verify_ref_services_example.py
=== Step 1: create the REFERENCE instance (ordinary `ciu worktree add`) ===
worktree ready: /tmp/ciu-p34-verify-61h6bg_i/repo/.worktrees/primary-ref
  next: cd /tmp/ciu-p34-verify-61h6bg_i/repo/.worktrees/primary-ref && ciu up
(exit 0)

=== Step 2: join it, addressing its vault by alias (THE DOCS COMMAND) ===
$ ciu worktree add mstest --base main --profile core --shared-infra primary-ref --shared-infra-services api --shared-infra-ref-projects idp-dev-idp --shared-infra-ref-services vault
worktree ready: /tmp/ciu-p34-verify-61h6bg_i/repo/.worktrees/mstest
  next: cd /tmp/ciu-p34-verify-61h6bg_i/repo/.worktrees/mstest && ciu up
(exit 0)

=== Step 3: the resulting ciu.global.worktree.toml.j2 overlay ===
# Worktree-local sparse global override (S3.1b / S16).
# Durable configuration: preserved by `ciu clean` and `ciu env generate`.
[ciu.instance]
service_profiles = ["core"]

[ciu.instance.shared_infra]
ref_path = "/tmp/ciu-p34-verify-61h6bg_i/repo/.worktrees/primary-ref"
network = "net-eee75c05"
services = ["api"]
ref_projects = ["idp-dev-idp"]

[ciu.instance.shared_infra.ref_services.vault]
service = "vault"
container = "dstdns-aaaaaa-vault"
port = 8200

# S16.1/CIU-52 — CIU-resolved addressing for the reference instance's shared
# services. Do not hand-edit; re-run `ciu worktree add --shared-infra ...`.

[topology.services.vault]
internal_host = "dstdns-aaaaaa-vault"
internal_port = 8200

=== Parsed [topology.services.vault] = {'internal_host': 'dstdns-aaaaaa-vault', 'internal_port': 8200} ===
=== MATCH: docs example AFTER block is accurate. ===
(scratch repo: /tmp/ciu-p34-verify-61h6bg_i/repo)
```

The published `internal_host`/`internal_port` values in CONSUMERS.md §17 are
copied verbatim from this real, parsed output. The docs example explicitly
notes the project/instance values are illustrative (this run's fixture
identity, `dstdns`/`aaaaaa`, not the real mstest environment's own — that
identity is dstdns's own and not reconstructable from the frozen BEFORE
string alone), so the docs make no claim of reproducing the exact frozen
`f2d1cb` string; what is proven is the MECHANISM — the CLI flag grammar and
the exact shape of the emitted `[topology.services.vault]` block.

## 5. Files changed

| File | What |
|---|---|
| `docs/CONSUMERS.md` | New `## 17. Migrate a hand-rolled internal_host override to --shared-infra-ref-services (S16.1a, CIU-49/CIU-52)` section: the real CIU-49 BEFORE quote, the staleness hazard named concretely (a reference re-created under a new identity leaves the frozen literal silently pointing at nothing / a different container), the AFTER invocation + resulting native block (values proven per §4 above), a callout box naming exactly what the migration buys (re-derivation + re-authentication at every add/create/ensure/adopt and every up, vs. checked-once-and-never-again), a cross-reference to CONFIG.md's S16.1a worked example (not a duplication of its mechanism explanation), and a one-line disambiguation against §5e (the separate, non-shared-infra "qualify your OWN internal_host" case) |
| `CHANGES.md` | New `### Documentation` subsection under the existing `## [Unreleased]` header (no duplicate `[Unreleased]` created), one `docs(ciu):` entry, no `!` marker — nothing behavioral changes |
| `nyxloom-trove/reports/ciu-P34-consumers-ref-services-migration-note-LOG.md` | This file |

No `scope.forbid` file was touched — confirmed both before writing and again
just before committing:

```
$ git diff --stat -- ciu/src/ciu/ ciu/tests/tests/ ciu/docs/SPEC.md \
    ciu/docs/CONFIG.md ciu/docs/DESIGN-GUIDE.md ciu/nyxloom-trove/backlog.md \
    ciu/nyxloom-trove/decisions.md ciu/nyxloom-trove/roadmap.md
(empty)
```

## 6. Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** new section shows a real before/after | **MET** | §17 (numbered 17, not the handoff's assumed 16, per §2 above) shows: (a) the verbatim CIU-49 BEFORE quote (§3 above); (b) the AFTER `--shared-infra-ref-services vault` invocation and the resulting `[topology.services.vault]` block, cross-referencing `CONFIG.md#shared-infra-join-example-s161` rather than restating its mechanism; (c) an explicit callout naming the staleness risk concretely: a hand-typed override survives a reference re-created under a new `INSTANCE_ID`/network unchanged and unchecked, while `ref_services` re-derives and re-authenticates it at every add/create/ensure/adopt and every `ciu up`. |
| **O2** worked example executed, not just prose | **MET** | §4 above — real fixture built by mirroring `test_ciu_worktree_shared_infra.py`'s `TestRefServicesAddTimeResolution` construction, driven through the real `ciu.cli._worktree` dispatch with the literal published argv, real captured output pasted verbatim, values in CONSUMERS.md copied from that real output. |
| **O3** gate stays green | **MET** | §7 below — 3261 passed, 100.00% line+branch coverage, zero source/test file changes (`git diff --stat` over `src/` and `tests/tests/` is empty). |

## 7. Gate output (verbatim, read in a separate step from the run itself)

```
$ .venv/bin/python run-ciu-tests.py
...
--------------------------------------------------------------------------------------------
TOTAL                                             9688      0   3948      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
====================== 3261 passed, 6 warnings in 22.67s =======================
```

3261 tests passed, exit code `0`, zero source or test edits (`docs/CONSUMERS.md`
and `CHANGES.md` are the only diffed files — confirmed by `git diff --stat`
before this run). The 6 warnings are pre-existing chown-privilege
`UserWarning`s unrelated to this package (secrets materialization under the
sandboxed test UID), not new.

## 8. Commits

1. `docs/CONSUMERS.md` + `CHANGES.md` — one commit, per
   `git commit --only -F - -- ciu/docs/CONSUMERS.md ciu/CHANGES.md`.
2. This LOG file — a separate commit.

Exact hashes are in this package's final report (read back via `git log
--format=%H`, not predicted ahead of the actual commit).
