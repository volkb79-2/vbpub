# ciu-P31 — CIU-52 `shared_infra.ref_services` — implementer LOG

Package: `nyxloom-trove/handoffs/ciu-P31-ciu52-shared-infra-ref-services.md`
Branch: `feat/ciu-qol-v8prep-wave` (worktree `.worktrees/ciu-qol-v8prep-wave`)
Base HEAD at start: `e61df823` (ciu-P30 LOG commit) — confirmed before starting.
Implementation commit: `75f9fda2229bb74ce93e6b4b52826d1c100f2df1`
Gate: **2771 passed, 100.00% line+branch coverage, exit 0.**

Not BLOCKED. Both `escalate_if` triggers were checked first and neither fired
(see §1).

---

## 0. The §0 correction — independently re-derived, not taken on trust

The handoff asked me to verify its own load-bearing claim before writing
anything. I did, from the shipped code rather than from the handoff:

- `connect_shared_infra_after_up`'s target-discovery loop
  (`src/ciu/worktree.py`, the `for service in intent.services:` block) issues
  `docker ps --no-trunc --filter label=com.docker.compose.project=<compose_project>
  --filter label=com.docker.compose.service=<service>`, where `compose_project`
  is **this** (joining) instance's own project, passed in by the caller. So
  `services` are the JOINER's own containers.
- `ref_projects` is consumed in exactly two places, both about the reference:
  `_check_reference_network_and_projects` (AND-combined liveness, scoped to
  network + the exact project label) and the "a reference project must belong
  to the reference instance" refusal. Never per-service, never indexed against
  `services`.
- The shipped fixture
  `TestAddSharedInfra::test_success_records_all_four_fields_in_order` uses
  `services="api,worker"` (2) against `ref_projects="idp-dev-idp,vault-dev-vault"`
  (2), and `test_success_round_trips_through_parse_shared_infra_config` uses
  `services="api"` (1) against `ref_projects="idp-dev-idp"` (1) — while
  `test_empty_list_item_in_services_fails` freely mixes 2-vs-1. Different
  lengths are legal; there is no index correspondence anywhere.

**Confirmed: the backlog filing's `[[shared_infra.services]] name="vault"
ref_project="dstdns" aliases=["vault"]` misreads the schema.** A
`services[*].aliases` sub-key could only ever have named the JOINER's own copy
of a service. Implementing the reservation literally would have shipped a
default that points this instance's own `vault` at the reference's `vault` —
the CIU-49 bug relocated, not fixed. I implemented the third independent axis
the handoff specifies and withdrew the S12 reservation instead.

The reviewer-facing note in the SPEC, CONFIG.md, CHANGES.md and the backlog
disposition all state this explicitly, so a future reader cannot repeat the
misreading from the filed text alone.

## 1. `escalate_if` pre-checks (both clear)

**`render_global_chain` parameters.** Verified read-only at
`src/ciu/config_model.py:411-418`: the signature is
`render_global_chain(working_dir, repo_root, *, write_rendered=True,
environ=None, ciu_context=None)`, and both parameters do exactly what the
design assumes — `write_rendered=False` returns the merged mapping without
writing `<repo_root>/ciu.global.toml`, and `environ=` replaces `os.environ` for
BOTH the Jinja `env` context and `$VAR` expansion at every template in the
chain (`expand_env_vars_or_fail`, `config_model.py:136-141`: "when given,
expansion consults ONLY this mapping — never `os.environ`"). No escalation.

**Forbidden files.** Nothing outside `scope.touch` was edited. `deploy.py`
(`container_name`), `config_model.py` (`render_global_chain`, `deep_merge`) and
`secrets/providers.py` were read and are consumed READ-ONLY via import.
`composefile.py` and `hooks_runner.py` were not involved at all. No escalation.

**Precedents cited for the read-only render** (the handoff asked me to name
them): `resolve_worktree_cap` (`worktree.py`, `render_global_chain(root, root,
write_rendered=False)`) and `_resolve_budget_candidates` (`worktree.py`,
`render_global_chain(candidate_stack, candidate_ciu_root, write_rendered=False,
environ=candidate_env)`). The second is the exact shape I mirrored: read
ANOTHER checkout's config, under THAT checkout's own `ciu.env`, writing
nothing.

## 2. Schema decisions (§1 — no deviations)

Shipped exactly as specified: `SharedInfraRefService(alias, service, container,
port: int | None = None)` frozen dataclass; `SharedInfraIntent.ref_services:
tuple[...] = ()`; required-plus-optional closed-shape check preserving both
halves of the original error message (the pre-existing
`test_partial_intent_raises_naming_missing_fields` passes unmodified).

Grammar validators as named:

- `_parse_ref_services_arg` — delegates the split to the existing
  `_split_unique_list`, so blank items, an empty value and verbatim duplicates
  are refused with the wording the sibling flags already use, and then enforces
  uniqueness on the ALIAS specifically (`vault,vault=vault` is two distinct
  items but one alias — `_split_unique_list` alone would not catch it).
- `_config_ref_services` — table-of-tables, per-entry required
  `{service, container}` + optional `port`, unknown sub-key refused, alias key
  itself regex-checked.
- Regexes: alias `^[A-Za-z_][A-Za-z0-9_-]*$` (also guarantees a bare TOML key,
  since the alias becomes `[topology.services.<alias>]`), service
  `^[a-z0-9][a-z0-9_.-]*$`, and I added container
  `^[A-Za-z0-9][A-Za-z0-9_.-]*$`. `$` and `{` are structurally impossible in
  all three, which is what actually keeps the recorded values safe through
  `expand_env_vars_or_fail` and the secret scan — enforced, not hoped for.

### Degree-of-freedom / disclosed reconciliation: ordering

§1 says `_config_ref_services` returns "a deterministic tuple sorted by
alias"; §2's overlay paragraph says "(declaration order)". Those two are only
consistent if declaration order IS sorted order, and Oracle 4 requires an
EXACT round-trip. I resolved it by **canonicalising to alias-sorted order at
both ends** — `_resolve_ref_services` sorts at intent construction, so the
overlay's write order and the parse order always coincide and
`--shared-infra-ref-services zulu=vault,alpha=vault` round-trips exactly.
Pinned by `test_declaration_order_is_canonicalised_so_the_round_trip_is_exact`.
This is the only place I departed from a literal reading of the handoff prose,
and it departs from the weaker of two conflicting statements in service of a
normative oracle.

### Container-name sanity check (added, not requested)

`_resolve_ref_services` rejects a derived name that fails the container regex
(a reference whose `deploy.project_name` is e.g. `"not a name"`). `container_name()`
is pure string interpolation and validates neither half; without this, garbage
from the reference's config would be written into this instance's addressing
and only fail much later. Refuses loudly instead.

## 3. Resolution decisions (§2 — no deviations)

Inserted in `_preflight_shared_infra_for_add` after
`_check_reference_network_and_projects` and before the `SharedInfraIntent`
return, exactly as specified. Two placement details worth stating:

- **Grammar parsing sits with its two siblings** (beside the `_split_unique_list`
  calls), BEFORE the Docker liveness check. Oracle 11 requires each grammar
  refusal "before any side effect"; this makes it before any Docker call at
  all, which the tests assert as `fake.calls == []`.
- **Authentication is NOT additionally scoped by
  `label=com.docker.compose.project`**, per the handoff's reasoning, which I
  independently agree with: the container NAME already carries the reference's
  `project_name`/`environment_tag`, and that IS the authenticating fact. Adding
  a project filter would refuse a correct configuration whenever the reference
  runs the shared service under a project the operator did not need to declare
  for liveness. Recorded here as the named degree of freedom; choice = follow
  the handoff.

**Degree of freedom taken: one shared helper, not duplicated inline.**
`_live_ref_service_names(network, service)` issues the query;
`_authenticate_ref_services(network, entries, *, recorded)` applies it. Both
add-time and join-time call the same pair, so the two can never drift into
disagreeing about what "live" means; `recorded` selects
"resolved"-vs-"recorded" phrasing and appends the join-time remedy sentence
(the same phrasing `connect_shared_infra_after_up` already uses for its other
staleness refusals).

**Three outcomes, never two** (the reviewer's named probe). `_live_ref_service_names`
raises a *distinct* `[S16.1] could not query reference service ...` error for
`FileNotFoundError`/`OSError` **and** for a non-zero `docker ps`. It never
returns `[]` for an unanswered question. This is deliberately stricter than the
neighbouring `_check_reference_network_and_projects`, which conflates
`ps.returncode != 0 or not ids` into one "does not look live" message: for an
addressing decision, "CIU could not determine this" and "CIU determined the
container is absent" are different facts and must not print the same sentence.
Three tests pin it, including one asserting the failure message does NOT
contain `found: []`.

**Port.** `_ref_service_port` reads the reference's own
`topology.services.<service>.internal_port`, accepts only a non-bool `int`, and
otherwise returns `None` — so a missing or non-integer reference port results in
no `internal_port` key at all, never a coerced or invented one.

## 4. CLI decisions (§4 — no deviations)

One flag, `--shared-infra-ref-services` (metavar `A1,A2=S2`), registered on
`p_add`, the shared `add_create_options` (covering `create` and `ensure`) and
`p_adopt`, and forwarded at all three dispatch sites — mirroring the existing
three flags exactly. It is optional but joins the existing all-or-nothing
group in both `create` and `adopt`, so supplying it alone is a partial-group
refusal before any git or Docker call. No `--shared-infra-aliases`, no
positional pairing.

**Known trap handled as expected in-scope work:** `test_ciu_cli_worktree.py`
fakes `wt_mod.add` with an exact keyword-only signature and literal expectation
dicts in three places; all three were updated in the same change. `create`,
`ensure` and `adopt` fakes use `**kwargs` and needed no signature change — I
added forwarding assertions for them anyway.

## 5. Oracle-by-oracle evidence

All in `tests/tests/test_ciu_worktree_shared_infra.py` unless noted. Every new
Docker interaction goes through the existing strict `ScriptedDocker` fake (an
unscripted call raises), and the new predicate `_is_ref_service_ps` explicitly
excludes any project-labelled call so it can never accidentally match the
pre-existing target-discovery query.

| # | Oracle | Test | What it actually proves |
|---|---|---|---|
| 1 | Headline contract | `TestRefServicesAddTimeResolution::test_headline_contract_resolves_qualified_host_and_port` | Reference config `project_name="dstdns"` + `environment_tag="${INSTANCE_ID}"`(=`aaaaaa`) + `internal_port=8200`; `--shared-infra-ref-services vault` ⇒ real `tomllib` parse of the overlay gives `topology.services.vault == {"internal_host": "dstdns-aaaaaa-vault", "internal_port": 8200}`. No hand-written override anywhere in the fixture. |
| 2 | Controlled wrong implementation | `...::test_controlled_wrong_derivation_is_refused_before_any_git_mutation` | `deploy.container_name` monkeypatched to return the BARE `"vault"` (the CIU-49 bug relocated). Add-time authentication refuses, naming both `'vault'` and the live `dstdns-aaaaaa-vault`; `track_git_add_calls == []` and `.worktrees/child` does not exist. Without the authentication step this mutant ships. |
| 3 | Three-instance non-interference | `...::test_three_instance_non_interference` **and** `...::test_three_instance_impostor_alone_is_refused_not_adopted` | First: unrelated C (`dstdns-cccccc-vault`) is ADVERSARIALLY on A's network carrying the identical `com.docker.compose.service=vault` label, so the live query returns BOTH names — B still resolves A's container, and `dstdns-cccccc-vault` appears nowhere in the overlay. Second (the sharp edge): A's vault is DOWN and only C's answers the label — a resolver that picked "the vault-labelled container on this network" would silently address C; authentication refuses instead, naming both. |
| 4 | Round-trip symmetry | `TestRefServicesSchemaRoundTrip::test_overlay_round_trips_to_the_exact_intent` (+ `test_declaration_order_is_canonicalised...`) | overlay text → `tomllib.loads` → `parse_shared_infra_config` `==` the exact `SharedInfraIntent`, `ref_services` included. |
| 5 | Backward compatibility | `TestRefServicesBackwardCompatibility::test_omitting_the_flag_is_byte_identical_and_costs_zero_docker_calls` + `...::test_omitting_the_flag_costs_zero_extra_docker_calls_at_join_time` | Add half: the overlay text is compared with `==` against the pre-CIU-52 four-line shape written out literally (a real byte comparison, not "still passes"), AND the strict fake's `calls` list is compared with `==` against the exact two-call pre-CIU-52 sequence. `ref_services == ()`. Join half: the join issues no `_is_ref_service_ps` call at all and ends on the same connect. |
| 6 | Closed-shape widening, not opening | `TestRefServicesClosedShape` (9 tests, incl. a 9-case parametrize) | Unknown top-level key still refused BY NAME (`unknown=['aliases']` — the filing's own key); missing required key still named; `ref_services` optional; sub-table with missing `service`, missing `container`, unknown key, string `port`, bool `port`, non-string/bad-regex `service`, non-string/`$`-bearing `container`, non-table value, empty table, illegal alias key — each refused. |
| 7 | Join-time precondition ordering | `TestRefServicesJoinTimeReverification` (3 tests) | Absent recorded container ⇒ `WorktreeError` with ZERO `["network","connect",...]` calls and no target-discovery call either. Present ⇒ index assertion `auth_at < target_at < connect_at` and the pre-existing connect behaviour unchanged. Unanswerable query ⇒ refusal, still zero connects. |
| 8 | Render isolation | `TestRefServicesRenderIsolation::test_reference_checkout_gains_no_rendered_config_and_ignores_ambient_env` | The reference's directory listing is captured before and after the add and compared with `==` (no new `ciu.global.toml`, no new file of any kind). The reference's `environment_tag` interpolates `$INSTANCE_ID`, so the poisoned ambient `INSTANCE_ID=poison`/`REPO_ROOT=/nowhere` would visibly rewrite the derived name; the overlay still contains `dstdns-aaaaaa-vault` and the string `poison` appears nowhere in it. |
| 9 | Rename escape hatch | `...::test_rename_escape_hatch_writes_only_the_alias_block` | `secrets=vault` ⇒ `topology.services.secrets.internal_host == dstdns-aaaaaa-vault`; `"vault" not in topology.services` and the literal `[topology.services.vault]` is absent from the text. |
| 10 | Merge order | `TestRefServicesMergeOrder::test_overlay_wins_internal_host_while_committed_port_survives` | End-to-end through the REAL `config_model.render_global_chain` on the joining checkout: a committed `ciu.global.defaults.toml.j2` declaring `internal_host="vault", internal_port=8200`, a reference that declares NO port ⇒ merged `internal_host` is `dstdns-aaaaaa-vault` (overlay wins) and `internal_port` is `8200` (committed default survives). Also asserts the read-only render wrote no `ciu.global.toml`. |
| 11 | Grammar refusals | `TestRefServicesGrammarRefusals::test_refusal_before_any_side_effect` (11-case parametrize) | Empty value, blank item, duplicate item, duplicate alias (`vault,vault=vault`), alias regex failures, service regex failures, `$` in either component, `a=b=c`. Each asserts `track_git_add_calls == []` AND `fake.calls == []`. |
| 12 | Partial-group refusal | `...::test_ref_services_alone_is_a_partial_group_refusal`, `...::test_adopt_ref_services_alone_is_a_partial_group_refusal`, and `test_ciu_cli_worktree.py::...::test_ref_services_alone_reaches_the_all_or_nothing_refusal` | `--shared-infra-ref-services` alone on `add` and on `adopt` refuses before any git or Docker call; the CLI half proves the flag reaches `wt_mod.add` (rather than being silently dropped) and surfaces as exit 2. |
| 13 | Port omission | `...::test_port_omission_invents_nothing` (+ `test_non_integer_reference_port_is_not_recorded`) | Reference declares no `internal_port` ⇒ overlay has `internal_host` only, the literal `internal_port` is absent from the text, and no `port` key lands in the recorded sub-table. A non-integer reference port is treated as none, never coerced. |

Additional coverage beyond the 13, driven by the reviewer's named probes:
`TestRefServicesAuthenticationFailureModes` (5 tests — missing binary,
unreachable daemon, non-zero `ps`, genuinely-empty live set, comma-joined
`{{.Names}}` output), plus reference-side resolution failures (no
`deploy.project_name`, no global config at all, illegal derived name) and
`test_two_aliases_may_share_one_reference_service`.

### Reviewer probes, answered directly

- **A config written under the old (incorrect) positionally-paired
  assumption.** There is no such shipped config — `services[*].aliases` was
  only ever a S12 reservation, never implemented, and `services` is a
  `list[str]` that `_config_string_list` has always required to be a non-empty
  string array. A hand-written `[[shared_infra.services]]` table-array fails
  that check; a top-level `aliases = [...]` key hits the closed-shape check and
  is refused BY NAME (pinned by `test_unknown_top_level_key_is_still_named`,
  which uses `aliases` as the key precisely for this reason).
- **Write-once record vs later Docker drift.** The record is deliberately
  write-once (resolution is add-time, per §2 — not a degree of freedom), and
  drift is caught by the join-time re-verification, before any connect, with a
  refusal naming the recorded container, what was actually found, and the
  remedy. Pinned by `test_drifted_container_refuses_before_any_connect`.
- **Is alias-resolution failure loud or a silent fallback?** Loud, always.
  There is no fallback path anywhere: an unresolvable service, an illegal
  derived name, an unauthenticated container and an unanswerable query each
  raise `WorktreeError` before the git worktree is created. Nothing is ever
  written on a failed resolution.
- **Does "authenticated against live Docker" degrade safely when Docker is
  unreachable?** Yes — as a clear determination failure, never an
  empty/permissive result. See §3 above and the three
  `TestRefServicesAuthenticationFailureModes` query-failure tests.

## 6. Docs and backlog (Work item 6)

- `docs/SPEC.md` S12: reservation **withdrawn** with the reason stated (the
  premise does not hold), pointing at S16.1.
- `docs/SPEC.md` new **S16.1a**: normative — the unpaired
  `services`/`ref_projects` statement first and explicitly, the schema, add-time
  derivation + authentication (including why the query is not project-scoped
  and the three-outcome rule), join-time re-verification and its position in
  the precondition region, and the merge-order consequence. S16.1's opening
  paragraph now names the optional fourth flag.
- `docs/CONFIG.md`: the shared-infra worked example gains the `ref_services`
  sub-table, the emitted `[topology.services.vault]` block, a blockquote
  stating the unpaired rule inline where the two lists appear, the flag in the
  `console` example, and a new `--shared-infra-ref-services` subsection.
- `CHANGES.md` `[Unreleased] / ### Added`: one entry, including the
  misreading note for readers of the filing.
- `KNOWN_ISSUES_TODO_BACKLOG.md`: **cross-branch gap again, same as ciu-P30.**
  This branch's copy predates `vbpub@4ccf7d4d` and contained no CIU-52 row at
  all (only CIU-49's passing reference to it). Following the pattern P30
  established and recorded, I brought CIU-52's filed text over verbatim from
  `main` (`git show main:ciu/KNOWN_ISSUES_TODO_BACKLOG.md`) and applied the
  FIXED disposition on top **in the same commit**, so the row's history reads
  "filed, then fixed" and never falsely appears OPEN. Summary-table row and the
  file's "Last updated" header updated to match. CIU-50/CIU-51 were **not**
  brought over — out of scope, v8-timed, still tracked on `main` pending merge.

## 7. Files changed

| File | Change |
|---|---|
| `src/ciu/worktree.py` | `SharedInfraRefService`; `SharedInfraIntent.ref_services`; three regexes; `_parse_ref_services_arg`; `_config_ref_services`; closed-shape widening; overlay emission; `_live_ref_service_names`; `_authenticate_ref_services`; `_resolve_ref_services`; `_ref_service_port`; preflight wiring; join-time re-verification + docstring renumber; `create`/`adopt` parameter and group check |
| `src/ciu/cli.py` | flag on `add` / `add_create_options` (`create`+`ensure`) / `adopt`, forwarded at all three dispatch sites |
| `tests/tests/test_ciu_worktree_shared_infra.py` | new CIU-52 section: 7 test classes, ~40 tests |
| `tests/tests/test_ciu_cli_worktree.py` | known-trap fake signatures/expectations updated ×3; 4 new dispatch tests |
| `docs/SPEC.md` | S12 withdrawal; new S16.1a; S16.1 opening paragraph |
| `docs/CONFIG.md` | worked example extended; new subsection |
| `CHANGES.md` | `[Unreleased] / ### Added` entry |
| `KNOWN_ISSUES_TODO_BACKLOG.md` | CIU-52 filed text brought over from `main` + FIXED disposition; summary row; header |

No forbidden file was modified. `git status` before the commit listed exactly
the eight files above, all inside `scope.touch`.

## 8. Gate — real output, verbatim

```
$ .venv/bin/python run-ciu-tests.py
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/__init__.py                                  3      0      0      0   100%
src/ciu/__main__.py                                  3      0      2      0   100%
src/ciu/_version.py                                 11      0      0      0   100%
src/ciu/activate.py                                119      0     46      0   100%
src/ciu/cli.py                                     753      0    268      0   100%
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
src/ciu/worktree.py                               1241      0    492      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8681      0   3466      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
====================== 2771 passed, 6 warnings in 20.73s =======================
```

Exit code 0. The 6 warnings are pre-existing and environmental — `[S4.10]
insufficient privilege to chown secret file ... to 1000:999` raised from
`src/ciu/secrets/materialize.py:204` under
`tests/tests/test_ciu_render_selection_context.py`, i.e. this unprivileged
devcontainer cannot `chown`. Nothing to do with this package (neither file is
in `scope.touch`); the count varies run to run with xdist worker scheduling
(an earlier identical-result run of the same suite reported 0).

## 9. Notes for the adversarial reviewer

1. The §0 correction is re-derived from the shipped code in §0 above — please
   re-derive it a third time rather than trusting either the handoff or me.
   The specific things to look at are the `compose_project` argument threaded
   into `connect_shared_infra_after_up`'s service query and the two (only two)
   consumers of `ref_projects`.
2. Ordering canonicalisation (§2) is my one departure from the handoff's prose;
   it is disclosed there with the reasoning. If the controller prefers literal
   declaration order in the overlay, Oracle 4's exactness has to weaken
   correspondingly — I chose the oracle.
3. The container-regex check on the DERIVED name (§2) is an addition the
   handoff did not ask for. It is defensive; it is not load-bearing for any
   oracle. Removing it would not break the design, only widen what can be
   written.
4. `_authenticate_ref_services`'s stricter three-outcome behaviour (§3)
   intentionally differs from the neighbouring
   `_check_reference_network_and_projects`, which still conflates
   query-failure with not-live. I did not "fix" that pre-existing function —
   it is outside this package's scope and its conflation is not a defect for
   its own (liveness) purpose. Worth a backlog note if the controller wants
   consistency.
