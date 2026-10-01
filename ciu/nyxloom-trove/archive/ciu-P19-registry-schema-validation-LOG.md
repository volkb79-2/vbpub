# ciu-P19 — `[registry.*]` schema validation, CIU-consumed fields only

**Handoff:** `nyxloom-trove/handoffs/ciu-P19-registry-schema-validation.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `b24595f3` (ciu-P18's
LOG commit, confirmed with `git log -1` before any edit)
**Implementation commit:** `a3bc88fb0e2f913e8a569aeb057b391342826686`

LOG filename taken from the handoff's own `scope.touch` line 24 and Work item
6, which agree with each other and with the dispatch: `ciu-P19-registry-
schema-validation-LOG.md`. No discrepancy this time.

---

## Files changed

| File | What |
|---|---|
| `src/ciu/provisioning.py` | `CONSUL_TOKEN_VAULT_PATH_DEFAULT`, `REGISTRY_VALIDATED_TABLES`, `REGISTRY_VALIDATOR_KEY`, `_load_pydantic`, `_svc_template_problem`, `_build_registry_models` (`RegistryPostgresql`/`RegistryConsul`), `_load_consumer_validator`, `_run_consumer_validator`, `validate_registries`; `_probe_consul` now reads the default from the new constant instead of two literals |
| `src/ciu/deploy.py` | `"registry"` added to `CHECK_STAGES` between `configfile` and `hooks-load`; stage-7 call in `action_check`; ciu-P18's insertion-point comment rewritten as a pointer; three docstrings corrected |
| `pyproject.toml` | new `registry = ["pydantic>=2"]` optional extra + `pydantic>=2` in the `test` closure |
| `tests/tests/test_ciu_provisioning.py` | +600 lines — the S13.4b suite |
| `tests/tests/test_ciu_deploy_actions.py` | **out of scope**, ciu-P18's forward-marker assertion flipped — see "Blast radius" |
| `docs/SPEC.md` | new **S13.4b**; S13.4a's stage table gains the `registry` row; its "Not yet implemented" paragraph replaced |
| `docs/CONFIG.md` | `[registry.*]` section gains the two-key constraint table and the `validate_registry` extension point |
| `docs/CONSUMERS.md` | **out of scope**, §14's now-false "Registry validation is not implemented yet" bullet — see "Blast radius" |
| `docs/FEATURES.md` | **out of scope**, the now-false "Registry (Pydantic-model) validation is NOT yet included" clause — see "Blast radius" |
| `CHANGES.md` | Unreleased → Added entry + upgrade note; ciu-P18's entry loses its stale "deliberately NOT included yet" tail |
| `docs/BACKLOG-2026-08-24.md` | CIU-V8-PREP-8 → **FIXED** with the scope note; CIU-QOL-12 → **FIXED**; implementation-plan item 3 updated |

---

## O1 — the scoping decision, and what re-verifying it actually found

**Decision.** This package ships Pydantic models for **two fields**:
`[registry.postgresql].database` and `[registry.consul].token_vault_path`. It
ships **no** model for Redis, MinIO, Vault, or PostgreSQL-users registry
tables — the other three of the V8 proposal §2.6 "five built-in kinds".

### What the re-verification found (grepped at this commit, not assumed)

`grep -rn "registry" src/ciu/` returns exactly **two** reads of a
`[registry.*]` value anywhere in CIU's source:

```
src/ciu/provisioning.py:310   db_name = (config.get('registry', {}) or {}).get('postgresql', {}).get('database')
src/ciu/provisioning.py:369   consul_cfg = (config.get("registry", {}) or {}).get("consul", {}) or {}
src/ciu/provisioning.py:370   template = consul_cfg.get("token_vault_path", …)
```

Every other `registry` hit in `src/` is a **different namespace** —
`deploy.registry.url` (S7.9 Docker-registry auth, `deploy_pkg/registry.py`)
— which is not `[registry.*]` and is not in scope here. `docs/CONFIG.md`
agrees: its `[registry.*]` section says "Two sub-keys are read by CIU's
provisioning probes (S13.2)" and shows exactly those two.

**The handoff's O1 baseline is accurate at this commit. No discrepancy, so
the `escalate_if` did not fire.** Neither more nor fewer fields; the two
models cover precisely what CIU consumes.

### Why not invent the other three

Beyond the handoff's own reasoning, the real dstdns workspace supplied
direct empirical support (found by accident — see "Real-world verification",
where a smoke run resolved `REPO_ROOT` to the live workspace). Its global
config carries a large, entirely consumer-owned registry block:

- `registry.postgresql` — `database`, plus `authentik_database`,
  `superuser`, and a `users` table of six roles
- `registry.minio.users.*` — three users with `access_key_secret` /
  `secret_key_secret` indirections
- `registry.vault.roles.*` — roles with `paths` / `runtime_paths` lists
- `registry.redis.acl` + `registry.redis.users.*` — five users with
  `permissions` lists
- `registry.consul.acl`, `.policies.*` (nine policies with raw HCL
  `rules` strings), `.deploy.auto_config.*` — and **no `token_vault_path` at
  all**

`ciu check` stage 7 passes clean against all of it. A model set built from
the proposal's five-kinds language would have had to guess the shape of
every one of those tables — `redis.users.<n>.permissions` is a list of ACL
strings here, `minio.users.<n>` is a secret-name indirection rather than a
bucket list, `consul.policies.<n>.rules` is embedded HCL. Any of those
guesses landing wrong turns a working consumer config red for no reason.
`extra="allow"` on the two real models is what keeps this block untouched,
and `test_registry_extra_keys_are_never_rejected` pins exactly that shape
(including a `redis`, `minio` and `vault` table) as a **passing** config.

### Where the missing three go instead

The proposal's own Option C, as one additive extension point:
`[ciu].registry_validator` names a module whose
`validate_registry(config) -> list[str]` polices whatever CIU does not.

**Mechanism chosen, and why `[ciu]` rather than `[registry]`:** `[ciu]` is
CIU's own workspace-switch namespace, documented in CONFIG.md as carrying
keys CIU acts on. Putting the key inside `[registry]` would place a
CIU-reserved name in exactly the free-form space this feature exists to hand
to the consumer — a `[registry.validator]` table of their own would then
collide with it. A hook file was the other candidate (P18's
`load_hook_for_check` already loads hooks) and was rejected: hooks are
per-stack and `[registry]` is global, so a registry validator declared on a
hook would run once per declaring stack and would be unreachable for a
workspace whose stacks declare no hooks. Loading reuses ciu-P18's extracted
`hooks_runner._load_hook_module` (same `[S9.2]` missing-file semantics)
rather than a second `spec_from_file_location` block; `load_hook` /
`load_hook_for_check` are the wrong entry points because both require a
`run`, which a validator module has no reason to define.

---

## Design decisions

### 1. Stage 7 runs at GLOBAL scope in `action_check`, not at ciu-P18's per-stack insertion point

ciu-P18 left a marked insertion point inside `_check_stack_config` saying
P19 would "validate `merged[root_key]["registry"]` here". Implementing it
there would have been wrong, for three findings that only surface when you
look at where `[registry]` can actually live:

1. `registry` is in `config_model.RESERVED_GLOBAL_NAMESPACES` (S3.7), and
   `_STACK_RESERVED` is `frozenset({"state"})` — so a stack config carrying
   its own top-level `[registry]` table has **two** non-reserved root keys
   and already fails S3.5 at stage 2. A stack-level registry table is not a
   thing that can exist.
2. `probe_ref` — the only consumer of these values — is called with
   `profile.config`, never with `merged`. Validating `merged` would validate
   a view the real code never reads.
3. `merged = deep_merge(profile.config, stack_cfg)` carries the **same**
   global table for every stack, so per-stack validation would emit N
   identical copies of one finding for an N-stack workspace.

So the call sits in `action_check` beside QOL-11's
`validate_declared_features` — the other global-scope check, for the same
reason — and runs even when `selection` is empty. **ciu-P18's machinery is
reused, not re-derived**: `_CheckReport.fail`, `CHECK_STAGES`,
`_emit_check_report`, and its exit-code contract; no stage-walking or
aggregation logic is duplicated (O3's negative). The insertion-point comment
was **not** deleted — it was rewritten in place as a pointer carrying the
three findings above, so the next reader finds the reasoning where they look
for it. Pinned by `test_check_stage7_reports_one_finding_not_one_per_stack`
(three selected stacks, one finding) and
`test_check_stage7_runs_with_an_empty_selection`.

### 2. Which constraints are real, and the one deliberately NOT imposed

O4's negative warns specifically against inventing a `token_vault_path`
constraint stricter than the code. Every constraint shipped is traced to a
line in `_probe_consul` / `_probe_pg`:

| Constraint | What the probe does without it |
|---|---|
| `database` must be a **string** | `cmd = [… , str(db_name), …]` — a non-string is *coerced*, so `psql -d 12345` runs against a nonsense database name |
| `database` must be **non-empty** | `if db_name:` — `""` is falsy, silently skipped, and the probe targets the default `postgres` db instead of the app one |
| `token_vault_path` must be a **valid format template** | `template.format(...)` raises `ValueError` on an unbalanced brace, and the probe's `except (KeyError, IndexError)` does **not** catch it — the whole probe run dies with a traceback |
| `token_vault_path`'s only placeholder is **`{svc}`** | `{service}`/`{}`/`{0}` raise `KeyError`/`IndexError`, which the probe **does** catch and then silently substitutes `consul/acl/tokens/{svc}` — a different Vault path than the operator wrote, with no warning |

**Not imposed: `{svc}` must be present.** A constant path substitutes
cleanly; `str.format` is happy, the probe is happy, and one shared ACL token
is a legitimate (if degenerate) deployment. The code requires nothing of the
sort, so neither does the model.
`test_svc_template_accepts_a_template_with_no_placeholder_at_all` pins the
non-constraint so a later reader cannot "tighten" it by accident.

The unknown-placeholder constraint is not asserted *about* — it is asserted
*against the real probe*. `test_svc_template_rejection_matches_the_probe_it_
protects` runs `probe_ref("consul:token/api", …{"token_vault_path":
"consul/{service}/token"}…)` with a recording Vault client and asserts the
path actually read was `consul/acl/tokens/api`, **not** `consul/api/token`.
A pre-existing test
(`test_ciu_provisioning_remaining.py::test_consul_probe_uses_safe_default_
for_malformed_path_template`) already pinned that silent fallback, which is
what makes it a documented behaviour worth surfacing rather than a bug to
fix inside a `scope.forbid`-adjacent probe.

### 3. `extra="allow"` + `strict=True`

`extra="allow"` is load-bearing (see O1 above). `strict=True` stops pydantic
from lax-coercing a wrong-typed value into a plausible string and calling it
valid — the failure mode being guarded against is precisely a silent
coercion. Verified in a REPL against pydantic 2.13.4 before use:
`{"database": 123}`, `True`, `["a"]` and `""` are all rejected, and
`model_validate("scalar")` raises `ValidationError` rather than
`AttributeError`, which is why a non-table sub-table needs no separate
`isinstance` branch.

### 4. Models are rebuilt per call, never memoized

A module-global model cache would be a correctness hazard, not just a
micro-optimisation: a cached set built by an earlier call would satisfy a
later call whose entire point is that pydantic is unavailable — and under
`-n auto` the test order that exposes it is nondeterministic. The cost is
one class-pair construction per `ciu check` run.

### 5. `validate_registries` raises nothing

`_load_pydantic` raises `ValueError` (mirroring `_load_jsonschema`'s exact
shape), but `validate_registries` converts it at its own boundary into a
finding. One contract for the caller — findings ⇒ stage failed ⇒ exit 2 —
and a missing extra therefore fails **loudly without aborting** the other
eleven stages, which a propagating exception would have done.

---

## Blast radius outside the originally-scoped files

Three files outside `scope.touch` were touched. Each is recorded here rather
than made silently, per the dispatch; all three are mechanical and the
controller may reverse any of them at no cost. `scope.forbid` was fully
respected — `engine.py`, `composefile.py`, `hooks_runner.py` and the three
`nyxloom-trove/*.md` files are **unmodified** (`hooks_runner` is consumed
read-only via import; confirm with `git diff --stat b24595f3..HEAD`).

### 1. `tests/tests/test_ciu_deploy_actions.py` — ciu-P18's forward marker (a RED gate without this)

The full suite was run **without `-x`** first to enumerate every out-of-scope
failure. There was exactly one:

```
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_json_envelope_is_versioned_and_ordered
1 failed, 2608 passed
```

The failing line is ciu-P18's own forward marker, comment included:

```python
# Stage 7 (registry) is deliberately absent — ciu-P19 owns it.
assert "registry" not in deploy.CHECK_STAGES
```

i.e. an assertion whose comment names **this package** as the thing that
retires it. Flipped to `assert "registry" in deploy.CHECK_STAGES` with a
comment recording the handover and pointing at the positional contract now
pinned in `test_ciu_provisioning.py`. **Alternatives rejected:** (a) shipping
red — not acceptable against a 0-failure gate; (b) stopping without the
one-line flip — leaves the package permanently unshippable over an assertion
its predecessor wrote to be flipped here; (c) keeping stage 7 out of
`CHECK_STAGES` to appease the assertion — contorts production code around a
test.

### 2 & 3. `docs/FEATURES.md`, `docs/CONSUMERS.md` — statements this package makes FALSE

ciu-P18 documented the stage-7 gap in four places. `scope.touch` covers two
of them (SPEC.md, BACKLOG); it does not cover the other two, which now assert
something untrue:

- `docs/FEATURES.md:41` — "Registry (Pydantic-model) validation is NOT yet
  included."
- `docs/CONSUMERS.md` §14 — "**Registry validation is not implemented yet.**
  … Do not read a green `ciu check` as 'my registry shape is correct'."

Both were corrected, minimally and in place. The CONSUMERS.md replacement
keeps the *true* half of the original warning — a green stage 7 still does
not mean your whole registry shape is correct, because CIU models two fields
— and points at `[ciu].registry_validator` for the rest. **Alternatives
rejected:** leaving them — knowingly shipping documentation that contradicts
the code, in the two documents a consumer actually reads, is a worse outcome
than a documented out-of-scope edit; filing a follow-up instead — same
problem, just deferred, and the carve's own O4 spirit is that the gap
statements get retired with the gap.

---

## Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** scope decision recorded | **MET** | The "O1" section above records the decision, the grep output that grounds it at this commit (two reads, `provisioning.py:310` and `:369-370`), the confirmation that CONFIG.md still matches, and the real-workspace registry block that would have been at risk from invented models. Two models only. **O1's negative honoured:** no PostgreSQL-users / Redis-ACL / MinIO-bucket / Vault-mount model exists in the diff; `test_registry_extra_keys_are_never_rejected` pins a config carrying all four as **passing**. `REGISTRY_VALIDATED_TABLES`' doc-comment carries the same reasoning at the code. The `validate_registry` extension point ships and is exercised end-to-end (`test_check_stage7_wires_the_consumer_validator`). |
| **O2** pydantic dependency | **MET** | `pyproject.toml`: `registry = ["pydantic>=2"]`, declared in the same shape and adjacent to `schema = ["jsonschema>=4.18"]`, with the same style of comment naming the spec ID and the version reason; `pydantic>=2` added to the `test` closure exactly as `jsonschema` is, for the same stated reason. Not a hard dependency: the import is function-local in `_load_pydantic`, and `test_no_registry_table_never_imports_pydantic` proves it is never attempted when neither table is declared (mirroring CIU-37's `test_no_schema_key_never_imports_jsonschema`). Loud-when-absent: `test_absent_pydantic_fails_loud_with_install_hint` mirrors CIU-37's `test_absent_jsonschema_fails_loud_with_install_hint` (`monkeypatch.setitem(sys.modules, "pydantic", None)`) and asserts `[S13.4b]`, `ciu[registry]` and `pip install` in the message; `test_absent_pydantic_never_silently_skips_a_malformed_table` pins the anti-pattern directly (a wrong config must not come back clean). **Also verified for real, not only by monkeypatch:** with pydantic genuinely `pip uninstall`ed, `ciu check` returned rc **2** with `[x] registry: fail` and the install hint, against both the smoke workspace and the real dstdns one (transcript below). **O2's negative honoured** on both halves. |
| **O3** wiring | **MET** | `provisioning.py` gains `RegistryPostgresql`/`RegistryConsul` and `validate_registries(config, repo_root) -> list[str]`. `deploy.py` adds `"registry"` to `CHECK_STAGES` between `configfile` and `hooks-load` — the exact slot ciu-P18 predicted, pinned by `test_check_stage7_is_between_configfile_and_hooks_load` — and calls the function through ciu-P18's `_CheckReport`/`_emit_check_report`. Exit-code contract: `test_check_stage7_failure_is_exit_2_even_with_live` runs with `live=True` and `probe_ref` wired to `pytest.fail` — rc is **2** and the live probe is never reached. **O3's negative honoured:** no stage-walking or aggregation machinery is duplicated; the deviation from the literal insertion point is the *location* only, is argued from three verifiable facts, and is documented at the insertion point itself. |
| **O4** docs | **MET** | `docs/CONFIG.md`'s `[registry.*]` section gains a per-key constraint table (type, constraint, and the probe behaviour each constraint prevents), the `ciu[registry]` install line, the loud-when-absent rule, and a worked `[ciu].registry_validator` example. `docs/SPEC.md`: **S13.4b** — chosen over S17 because stage 7 is a `ciu check` stage and S13.4a is the check pipeline (S13.5 was already taken by `ciu graph`, so S13.4b keeps the numbering unperturbed); S13.4a's stage table gains the `registry` row and its "Not yet implemented" paragraph is retired. `CHANGES.md` Unreleased → Added names pydantic as a new optional extra, states the narrowing, and carries an upgrade note. `docs/BACKLOG-2026-08-24.md`: CIU-V8-PREP-8 → ✅ FIXED with a ⚠️ scope note stating the narrowing plainly ("this is NOT 'all five kinds got Pydantic models'"), CIU-QOL-12 → ✅ FIXED with stage 7 named, and implementation-plan item 3 rewritten. **O4's negative honoured:** the `{svc}`-presence constraint was checked against `_probe_consul` first, found NOT to be required, and is documented as deliberately not enforced in CONFIG.md, SPEC.md and in a named test. |

---

## Gate output (verbatim)

```
$ .venv/bin/python run-ciu-tests.py
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
configfile: pyproject.toml
plugins: xdist-3.8.0, cov-7.1.0
created: 8/8 workers
8 workers [2609 items]
...
Name                                             Stmts   Miss Branch BrPart  Cover   Missing
src/ciu/deploy.py                                 1583      0    686      0   100%
src/ciu/provisioning.py                            358      0    154      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8532      0   3400      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2609 passed in 22.21s =============================
```

Exit status `0`, read in a separate step (not off a pipe tail). 100% **line
and branch** coverage (`--cov-branch`, `--cov-fail-under=100`). 2609 tests, up
from 2557 at ciu-P18 (+52).

Three `UserWarning`s appear in some runs
(`test_ciu_host_secrets.py`, "[S4.10] insufficient privilege to chown secret
file") — **pre-existing and unrelated**: they come from
`secrets/materialize.py`'s chown fallback in this unprivileged container and
touch nothing in this package.

---

## Real-world verification (beyond the unit gate)

Ran the real CLI against the real `/workspaces/dstdns` workspace (~16 stacks,
6 real hooks, and the large real `[registry.*]` block described under O1):

```
$ ciu check   →   rc 2
  [-] render: pass        [-] shape: pass          [-] secrets: pass
  [x] provisioning: fail  [-] governance: pass     [-] configfile: pass
  [-] registry: pass      [-] hooks-load: pass     [-] hooks-preflight: pass
  [-] compose-render: pass [-] leak-scan: pass     [-] consumption: pass
```

The three `provisioning` findings are the same genuine, pre-existing graph
gaps ciu-P18 reported, from the unchanged stage-4 path. **Stage 7 passes on a
real, entirely consumer-shaped registry block** — the single most useful
piece of evidence for O1.

Negative path, same CLI, a copy of `test-repo` with three deliberate defects
appended (`database = 12345`, `token_vault_path = "consul/{service}/token"`,
plus an untouched `[registry.redis.users.worker]`):

```
$ ciu check   →   rc 2
  [x] registry: fail
    [S13.4b] [registry.postgresql].database: Input should be a valid string
    [S13.4b] [registry.consul].token_vault_path: Value error, references
             {service}, but the consul:token probe substitutes only {svc};
             the substitution fails and the probe SILENTLY falls back to
             'consul/acl/tokens/{svc}'
```

Two findings, not three: the `redis` table passed through untouched, which is
the O1 negative holding in a real run.

With `pydantic` genuinely `pip uninstall`ed (not monkeypatched), the same
command against both workspaces:

```
$ ciu check   →   rc 2
  [x] registry: fail
    [S13.4b] a [registry.postgresql] or [registry.consul] table is declared,
    but validating it requires the optional 'pydantic' dependency. Install it
    with: pip install 'ciu[registry]' (or remove the table if nothing reads
    it).
```

pydantic was reinstalled and the full gate re-run green afterwards.

**One user-visible consequence, recorded rather than glossed:** the real
dstdns workspace declares both validated tables, so on upgrading CIU without
`pip install 'ciu[registry]'` its `ciu check` goes red at stage 7. That is
the intended fail-loud contract (O2 forbids the silent alternative), it does
not affect `ciu up`, and it is called out as an **upgrade note** in
CHANGES.md so nobody meets it as a surprise.

A smoke-run byproduct worth recording for the next package: `ciu.global.toml`
is `GLOBAL_CONFIG_RENDERED`, regenerated from `ciu.global.defaults.toml.j2`
on every run — editing it to build a fixture silently loses the edit. Edit
the `.j2` instead.

---

## Escalations

**None triggered.** The single `escalate_if` (CIU reading more or fewer than
the two documented `registry.*` fields) was re-checked by grep at this commit
and did **not** fire: exactly two, matching the handoff's O1 baseline. No
`BLOCKED` condition arose. Three out-of-scope files were touched and are
documented under "Blast radius" with alternatives and reversal cost.
