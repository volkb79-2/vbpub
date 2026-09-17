# ciu-P30 — CIU-48/CIU-49 cockpit-alias guidance (scaffold + docs) — implementation LOG

| | |
|---|---|
| Package | `ciu-P30-ciu48-49-cockpit-alias-guidance` |
| Branch | `feat/ciu-qol-v8prep-wave` (worktree `.worktrees/ciu-qol-v8prep-wave/ciu`) |
| Base HEAD | `5ae5b25766fe50e2e04684f94c960badb3130606` (confirmed via `git log -1` before starting) |
| Gate | `.venv/bin/python run-ciu-tests.py` |
| Status | see §5 |

---

## 1. O1 — scope-decision finding (recorded BEFORE any file was touched)

Ran the exact greps the handoff's O1 oracle specifies, against this session's
own HEAD (`5ae5b257`):

```
$ grep -rn 'hostname:' src/
(zero hits, exit 1)

$ grep -n 'topology' src/ciu/templates/global.defaults.toml.j2
(zero hits, exit 1)
```

Confirmed: ciu's own `ciu init`-shipped scaffold
(`src/ciu/templates/stack.compose.yml.j2`) does **not** set a compose
`hostname:` field anywhere today, and `global.defaults.toml.j2` has **no**
`[topology]` block at all. `src/ciu/secrets/providers.py:62-70`
(`vault_addr_from_config`) only **reads**
`topology.services.vault.internal_host` from the merged config — it supplies
no default value either. `topology.services.*` (S4.16/S7.4) is therefore
entirely consumer-declared configuration that ciu only consumes; ciu ships no
default for either value anywhere in its own templates.

**Conclusion:** the 31 hand-authored compose templates and the hand-maintained
`internal_host` override the CIU-48/CIU-49 filing cites (`infra/db-core/
ciu.compose.yml.j2:59`, dstdns's `test/multistack-v1` override) are ALL in the
DSTDNS repository — not reachable or editable from this vbpub/ciu session.
This package's real scope is narrower than a literal reading of "implement
CIU-48/49": a shipped scaffold-template improvement (§2) plus prescriptive
documentation (§3-5), **not** a fix propagated into every existing consumer's
own already-authored templates — that remains dstdns's own follow-up in its
own repo. The backlog disposition (§6) is worded PARTIAL, not FIXED, to avoid
overclaiming that this ciu-side release alone closes the actual dstdns
operator pain.

## 2. O2 — scaffold `hostname:` line

`src/ciu/templates/stack.compose.yml.j2`'s scaffolded `app` service already had:

```
container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ @@ROOT_KEY@@.app.name }}
```

Added, directly below it, the **identical** expression on a `hostname:` key
(copy-pasted, not retyped, so the two lines cannot silently diverge):

```
hostname: {{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ @@ROOT_KEY@@.app.name }}
```

A stack freshly scaffolded via `ciu init` now gets a correctly-qualified
`hostname:` by default. This does not retroactively touch any existing
consumer's already-authored templates (out of reach, per §1). No compose
service-key allowlist exists anywhere in `src/ciu/` (grepped — nothing
enumerates permitted top-level service keys), so adding `hostname:` cannot
trip a hidden schema check.

**Escalate_if check:** grepped `tests/` (both `tests/tests/` and the
top-level `tests/`) for anything exercising the scaffolded compose template's
exact byte content. `tests/test_init_scaffolding.py::test_init_writes_tree_and_renders_clean`
renders and asserts against `ciu.global.defaults.toml.j2`'s TOML output; it
never renders or byte-compares `applications/api/ciu.compose.yml.j2`'s
content, only that the file is among the written set. `test_spec_contracts.py`
builds its own synthetic compose bodies inline (`_app_compose()`), not a
byte-copy of the real scaffold template. **No existing test asserts exact
content of `stack.compose.yml.j2`** — the escalate_if condition was not hit;
no test needed updating.

## 3. O3 — DESIGN-GUIDE.md hazard section

Added a new `## Why bare hostname: / internal_host defaults are dangerous
(CIU-48/CIU-49, §3.6 cockpit-alias-ambiguity)` section immediately after the
existing "Why there is no compose project without `-p` (CIU-46 cutover)"
section, matching that section's tone/structure. States the mechanism (Docker
independently registers both a container's `hostname:` and its compose
service KEY as network-resolvable aliases), the concrete hazard (two
CIU-deployed instances of the same stack shape sharing a network → a bare
alias resolves non-deterministically), names it "§3.6 cockpit-alias-ambiguity"
matching the filing's own term, and states plainly that Compose's automatic
bare service-key alias itself (CIU-51) is NOT eliminated by anything here —
only the two consumer-controllable value defaults are addressed.

## 4. O4 — CONFIG.md prescription

Extended `docs/CONFIG.md`'s `[topology.services.<name>]` section (S4.16/S7.4)
with a SHOULD-level prescription citing `container_name()`
(`src/ciu/deploy.py:138-151`, read-only) by name, and a link to the new
DESIGN-GUIDE section. Checked the existing worked example
(`internal_host = "ciudemo-dev-vault"`): the three-hyphen-segment shape
matches the `{project}-{env_tag}-{service}` pattern used elsewhere in the docs
(`p-t-postgres`, `project-prod-cache`), but nothing in the surrounding prose
spelled out the split — a reader could plausibly misread "ciudemo-dev" as one
project name rather than `project=ciudemo` / `env_tag=dev`. Made this
explicit with an inline annotation rather than leaving the reader to infer it.

## 5. O5 — CONSUMERS.md worked example

Added new subsection `5e. Qualify a stack's hostname: and internal_host
defaults (avoid the §3.6 cockpit-alias hazard)` showing the paste-able
before/after for both a compose template's `hostname:` line and a
`[topology.services.<name>]` declaration, framed as "what to write when
authoring your own stack" per the handoff. Links to the new DESIGN-GUIDE
section for the WHY rather than re-arguing it (AGENTS.md three-docs rule).

## 6. O6 — backlog + CHANGES.md disposition

**Cross-branch gap found and resolved.** `KNOWN_ISSUES_TODO_BACKLOG.md` on
THIS branch (`feat/ciu-qol-v8prep-wave`, merge-base with `main` at
`27d0d32c`) does **not** contain CIU-48/CIU-49 rows at all — the filing
commit the handoff cites (`vbpub@4ccf7d4d`, "file CIU-48 through CIU-52") was
made directly on `main`, a sibling branch this feature branch has not merged.
Verified: `git diff --stat 27d0d32c HEAD -- KNOWN_ISSUES_TODO_BACKLOG.md` is
empty (this branch never touched the file since the merge-base), while
`git diff --stat 27d0d32c main -- ciu/KNOWN_ISSUES_TODO_BACKLOG.md` shows
main's 338-line addition. Since O6 requires updating "CIU-48 and CIU-49 rows"
and no such rows existed on this branch to update, I brought over the CIU-48
and CIU-49 summary-table rows and full detail sections verbatim from main's
filed text (`git show main:ciu/KNOWN_ISSUES_TODO_BACKLOG.md`, commit
`4ccf7d4d`) — content and citations unchanged — and then applied this
package's PARTIAL disposition on top, in one commit, so the row's history
reads as "filed, then partially addressed" rather than ever falsely claiming
OPEN. CIU-50/CIU-51/CIU-52 (the rest of the filed family) were **not**
brought over — they are out of this package's scope entirely (CIU-52 belongs
to the separate ciu-P31 package; CIU-50/CIU-51 are v8-timed) and remain
tracked only on `main` pending this branch's eventual merge.

Both rows now read: **PARTIAL** — ciu-side scaffold default (`hostname:` in
the `ciu init` scaffold) + prescriptive DESIGN-GUIDE/CONFIG.md/CONSUMERS.md
documentation shipped; propagating the corrected pattern into EXISTING
hand-authored consumer templates (dstdns's 31 compose templates + its
hand-maintained `internal_host` override) is that consumer's own follow-up,
out of reach from a ciu release alone. `CHANGES.md`'s `[Unreleased]` /
`### Added` section states the same distinction plainly.

## 7. Gate

```
$ .venv/bin/python run-ciu-tests.py
[... 2714 tests ...]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/__init__.py                                  3      0      0      0   100%
src/ciu/__main__.py                                  3      0      2      0   100%
src/ciu/_version.py                                 11      0      0      0   100%
src/ciu/activate.py                                119      0     46      0   100%
src/ciu/cli.py                                     750      0    268      0   100%
src/ciu/cli_utils.py                                11      0      0      0   100%
src/ciu/composefile.py                             388      0    180      0   100%
src/ciu/config_constants.py                         29      0      4      0   100%
src/ciu/config_model.py                            276      0    128      0   100%
src/ciu/deploy.py                                 1582      0    686      0   100%
src/ciu/deploy_pkg/__init__.py                       8      0      0      0   100%
src/ciu/deploy_pkg/health.py                       205      0    108      0   100%
src/ciu/deploy_pkg/http_util.py                     24      0      2      0   100%
src/ciu/deploy_pkg/layouts.py                       63      0     24      0   100%
src/ciu/deploy_pkg/phases.py                        76      0     44      0   100%
src/ciu/deploy_pkg/profiles.py                     131      0     64      0   100%
src/ciu/deploy_pkg/registry.py                      38      0     20      0   100%
src/ciu/dev.py                                     196      0     74      0   100%
src/ciu/diagnose.py                                 79      0     34      0   100%
src/ciu/engine.py                                  887      0    292      0   100%
src/ciu/governance.py                              382      0    158      0   100%
src/ciu/hooks/__init__.py                            0      0      0      0   100%
src/ciu/hooks/examples/__init__.py                   0      0      0      0   100%
src/ciu/hooks/examples/post_compose_example.py       5      0      0      0   100%
src/ciu/hooks/examples/pre_compose_example.py        4      0      0      0   100%
src/ciu/hooks_runner.py                            139      0     56      0   100%
src/ciu/hosts.py                                    61      0     28      0   100%
src/ciu/ksm.py                                     180      0     64      0   100%
src/ciu/output.py                                   89      0     34      0   100%
src/ciu/paths.py                                    30      0     12      0   100%
src/ciu/procutil.py                                 17      0      2      0   100%
src/ciu/provisioning.py                            359      0    154      0   100%
src/ciu/scaffold.py                                104      0     36      0   100%
src/ciu/secrets/__init__.py                          3      0      0      0   100%
src/ciu/secrets/directives.py                      140      0     78      0   100%
src/ciu/secrets/materialize.py                     229      0     64      0   100%
src/ciu/secrets/providers.py                       111      0     38      0   100%
src/ciu/transport_ssh.py                           219      0     70      0   100%
src/ciu/warn_policy.py                              32      0     14      0   100%
src/ciu/workspace_env.py                           454      0    190      0   100%
src/ciu/worktree.py                               1128      0    438      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8565      0   3412      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2714 passed in 25.53s =============================
```

No new Python code was added (only a `.j2` template line + prose docs), so
the 100%-coverage floor required no new tests — it was already sitting at
100% before this package and remained there after.

## 8. Commit(s)

Implementation commit: `cdb662bd81c427461f5d4fb58cf6daf23dc20576`
(`feat(ciu): CIU-48/CIU-49 -- qualified hostname: scaffold default +
cockpit-alias guidance`), confirmed via `git log -1 --format=%H` — not
predicted. This LOG file is committed separately per the package's own
`scope.touch` listing it individually, with a `docs(ciu):` prefix.

## 9. Status: COMPLETE

No BLOCKED condition was hit. The `escalate_if` condition (an existing test
asserting byte-for-byte scaffold content) did not trigger — see §2. All six
`Work` items and all six oracles (O1-O6) are addressed above.
