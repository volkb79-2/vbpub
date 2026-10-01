---
schema_version: 1
id: ciu-P31-ciu52-shared-infra-ref-services
project: ciu
component: worktree
title: "CIU-52: implement S12's reserved shared_infra addressing as a new optional ref_services table (alias-keyed, additive to the shipped closed shared_infra shape), CIU-resolving the reference instance's qualified container name into the joining instance's own topology.services.<alias>.internal_host at worktree-add time, authenticated against live Docker state, re-verified at join time"
tier: implement-4
input_revision: "27d0d32c"
source: {kind: research, ref: "CIU-52 design fork, controller session 2026-08-25, grounded in KNOWN_ISSUES_TODO_BACKLOG.md#CIU-52 and live src/ciu/worktree.py S16.1 shared_infra implementation"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/worktree.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_worktree_shared_infra.py"
    - "tests/tests/test_ciu_cli_worktree.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P31-ciu52-shared-infra-ref-services-LOG.md"
  forbid:
    - "src/ciu/deploy.py"
    - "src/ciu/composefile.py"
    - "src/ciu/config_model.py"
    - "src/ciu/hooks_runner.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-schema-and-backward-compat
    observable: "New optional `ref_services` table-of-tables (keyed by local alias, each `{service, container, port?}`) widens the closed `[ciu.instance.shared_infra]` shape additively; `SharedInfraIntent.ref_services` defaults to `()`. See body 'Oracles' 4/5/6/13 for the full round-trip, byte-identical-when-omitted, closed-shape-widening, and port-omission proofs."
    negative: "any behavior change when ref_services is omitted; a closed-shape check that silently accepts an unrelated unknown key"
    gate: "tester-unified"
  - id: O2-add-time-derivation-and-authentication
    observable: "The reference's qualified container name is derived from the REFERENCE's OWN rendered global config (read-only, environ-isolated) via `container_name()`, then AUTHENTICATED against live Docker state on the reference's network before being trusted/written. See body Oracles 1/2/3/8 for the headline contract, the controlled-wrong mutant, three-instance non-interference, and render-isolation proofs."
    negative: "trusting the derivation without live authentication; deriving from ref_projects instead of the reference's own config; leaking this process's ambient env or writing into the reference's checkout"
    gate: "tester-unified"
  - id: O3-join-time-reverification
    observable: "connect_shared_infra_after_up re-checks each ref_services entry's container is still live BEFORE any network connect call; see body Oracle 7."
    negative: "a connect attempted before the re-check, or the re-check silently skipped"
    gate: "tester-unified"
  - id: O4-cli-and-docs
    observable: "New optional `--shared-infra-ref-services` flag (bare-alias or alias=service form), joining the existing all-or-nothing group check; SPEC S12/S16.1 and CONFIG.md updated per Work item 6; backlog CIU-52 row -> FIXED naming the corrected services/ref_projects understanding. See body Oracles 9/11/12."
    negative: "a separate positionally-paired aliases flag reproducing the services/ref_projects ambiguity this package exists to avoid"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "config_model.render_global_chain does not actually support write_rendered=False / environ= parameters as assumed -- BLOCKED naming what you find, do not invent a workaround that writes to the reference or leaks ambient environment"
  - "the derivation or authentication cannot avoid touching a forbidden file -- BLOCKED naming the exact incompatibility"
mutexes: [merge-lane]
review_focus:
  - "the section 0 correction (services/ref_projects are unpaired) actually holds -- independently re-derive it, don't trust this handoff or the implementer uncritically"
  - "add-time authentication (oracle 2) refuses BEFORE any git worktree mutation"
  - "the three-instance non-interference fixture (oracle 3) is genuinely adversarial (C actually connected to A's network with a colliding label)"
  - "backward compatibility (oracle 5) is a real byte-identical comparison, not just 'still passes'"
  - "no ambient environment or write leaks into the reference's checkout during resolution (oracle 8)"
---

# ciu-P31 — CIU-52: `shared_infra.ref_services` reference-service addressing

## The correction that shapes this whole package — read this before anything else

The backlog filing's own illustrative TOML (`[[shared_infra.services]] name="vault"
ref_project="dstdns"`) **misreads the shipped schema**. Verify this yourself before
writing any code (it is the single most important fact in this handoff):

- `services` (in `[ciu.instance.shared_infra]`, `SharedInfraIntent.services`) names
  THIS (joining) instance's OWN diverging-tier containers to connect — NOT the
  reference's shared services. Confirmed at `connect_shared_infra_after_up`'s
  target-discovery loop (search `worktree.py` for it): it filters
  `docker ps --filter label=com.docker.compose.project=<THIS instance's compose_project>
  --filter label=com.docker.compose.service=<service>`.
- `ref_projects` names the REFERENCE's compose projects, used only for AND-combined
  liveness checking (`_check_reference_network_and_projects`) and a refusal that no
  ref project equals this instance's own project.
- **They are NOT paired** — not positionally, not as a cross-product. Confirm via the
  shipped test fixture in `tests/tests/test_ciu_worktree_shared_infra.py` using
  `services=("api","worker")` with `ref_projects=("idp-dev-idp",)` — different
  lengths, no index correspondence, and the connect loop never consults
  `ref_projects` per service.

**Consequence:** the thing CIU-52 needs a name for — a REFERENCE-side service
(e.g. `vault`) — is a third, entirely new axis. Do not attempt to infer an alias
default from `services` (that would point THIS instance's own diverging container at
the reference's copy of it — actively wrong) or restructure `services`/`ref_projects`
into a paired shape (unnecessary churn to an already-shipped, hardened shape; they
are not ambiguous, they are simply about different things).

## Context to read first

1. `KNOWN_ISSUES_TODO_BACKLOG.md#CIU-52` (full filed text) — the ask and its
   (partially incorrect, per above) illustrative contract.
2. `docs/SPEC.md` — S12 (the reserved-but-unimplemented `aliases` field — search for
   it) and S16.1/CIU-22 (the full normative join-mechanism text).
3. `src/ciu/worktree.py` — READ IN FULL the entire shared_infra surface: the
   `SharedInfraIntent` dataclass definition and its neighbors (`_split_unique_list`,
   `_config_string_list` — your grammar-helper precedents), `parse_shared_infra_config`
   (the closed-shape validator you extend), `_worktree_overlay_text`/
   `_write_worktree_overlay` (the overlay writer you extend), `_preflight_shared_infra_for_add`
   (where the NEW derivation+authentication logic is inserted), `create`/`adopt`
   (where the new CLI parameter threads through), and `connect_shared_infra_after_up`
   in full including its numbered-steps docstring (the join-time re-verification you
   extend).
4. `src/ciu/cli.py` — the three existing `--shared-infra*` flag registrations (on
   `add`, the shared `create`/`ensure` options function, and `adopt`) and their
   forwarding into `worktree.add`/`create`/`adopt` — mirror this exactly for the new
   flag.
5. `src/ciu/deploy.py:138-153` (`container_name()`, READ-ONLY, forbidden) — the exact
   qualified-name derivation you call (via `deploy.container_name`, lazily imported —
   see Work item 3's import-cycle note).
6. `src/ciu/secrets/providers.py:56-70` (READ-ONLY) — confirms how CIU itself already
   consumes `topology.services.<name>.internal_host`/`internal_port` for Vault
   addressing; your new `[topology.services.<alias>]` block written by this package
   feeds this exact same consumer.
7. `src/ciu/config_model.py` (READ-ONLY, forbidden) — `render_global_chain`'s
   signature (confirm `write_rendered`/`environ` parameters exist as this handoff
   assumes) and `deep_merge` (confirm the overlay's merge-last-wins property, so the
   emitted `[topology.services.<alias>]` block genuinely overrides any committed
   default of the same key).
8. `tests/tests/test_ciu_worktree_shared_infra.py` in full — the `ScriptedDocker`
   strict fake (exact-call-list assertion style — an unmatched call raises) and the
   existing intent-construction/overlay-roundtrip tests you extend.
9. `tests/tests/test_ciu_cli_worktree.py` — **known trap**: it fakes `wt_mod.add`
   with an EXACT keyword-only signature and literal expectation dicts. Adding the new
   kwarg WILL break these fakes with `TypeError` unless updated in the same change —
   this is expected in-scope work, not a surprise BLOCKED trigger.

## Implementation packet (normative)

### 1. Schema addition

New OPTIONAL key `ref_services` on the existing closed `[ciu.instance.shared_infra]`
shape — a table-of-tables keyed by the LOCAL ALIAS (not a flat list):

```toml
[ciu.instance.shared_infra.ref_services.vault]   # "vault" is the alias
service = "vault"                                # the REFERENCE's compose/service key
container = "dstdns-98535c-vault"                # CIU-derived, NEVER hand-typed
port = 8200                                       # optional; only when the reference declares one
```

Why a table keyed by alias rather than a flat `aliases = {vault = "vault"}` mapping
or the filing's table-list: the alias is the value's identity (it becomes
`topology.services.<alias>`) and must be unique — a TOML table key enforces this
structurally. Each entry carries three facts (reference service key, resolved
container name, optional port) that belong together, matching how `network` (also a
copied reference fact) already lives directly in the intent table.

New frozen dataclass:
```python
@dataclass(frozen=True)
class SharedInfraRefService:
    alias: str       # key in THIS instance's topology.services
    service: str     # the reference instance's compose/service key
    container: str   # the reference's qualified container name (CIU-derived)
    port: int | None # the reference's declared internal_port, when it has one
```

`SharedInfraIntent` gains `ref_services: tuple[SharedInfraRefService, ...] = ()`
(defaulted — omitting it reproduces 100% of today's behavior, byte-for-byte overlay
text, zero extra Docker calls; this is the backward-compatibility invariant, O5).

`parse_shared_infra_config`'s closed-shape check becomes required-plus-optional:
```python
required = {"ref_path", "network", "services", "ref_projects"}
optional = {"ref_services"}
missing, unknown = required - set(raw), set(raw) - (required | optional)
if missing or unknown:
    raise WorktreeError(
        "[S16.1] malformed [ciu.instance.shared_infra]: "
        f"missing={sorted(missing)}, unknown={sorted(unknown)}"
    )
```
(Preserves both halves of today's error message; the existing
`test_partial_intent_raises_naming_missing_fields` test must still pass unmodified.)

New grammar validators, styled on `_split_unique_list`/`_config_string_list`:
- `_parse_ref_services_arg(raw: str, *, label: str)` — CLI grammar: comma-separated
  items, each either `alias` (bare — alias equals reference service name) or
  `alias=ref_service` (rename form). Alias regex `^[A-Za-z_][A-Za-z0-9_-]*$`; service
  regex `^[a-z0-9][a-z0-9_.-]*$`; duplicate aliases refused. Two different aliases MAY
  point at the same reference service.
- `_config_ref_services(value, *, label: str)` — stored-TOML shape: a table of
  tables, each requiring `{service, container}` (str) and optionally `port` (a
  non-bool `int`); reject any other sub-key. Return a deterministic tuple sorted by
  alias.
- No `$` in any recorded value (the overlay passes through `expand_env_vars_or_fail`
  and a secret-scan) — enforce via the regexes, not by hoping.

### 2. Resolution mechanism: add-time, write-once into the overlay

Resolve inside `_preflight_shared_infra_for_add`, inserted AFTER the existing
`_check_reference_network_and_projects` call and BEFORE the `SharedInfraIntent(...)`
return:

```python
# lazy import — deploy.py imports engine, which imports worktree: a module-level
# import here would cycle. Mirror the existing lazy `from . import engine` pattern
# already used elsewhere in this file for the identical reason.
from . import deploy as deploy_mod
from . import config_model

ref_ciu_root = ref.path / _ciu_root_offset(repo_root)   # same expression already used
ref_global = config_model.render_global_chain(
    ref_ciu_root, ref_ciu_root, write_rendered=False, environ=ref_env,
)
for entry in requested_ref_services:
    try:
        container = deploy_mod.container_name(ref_global, entry.service)
    except ValueError as exc:
        raise WorktreeError(f"[S16.1] could not resolve reference service {entry.service!r}: {exc}") from exc
    port = (
        ref_global.get("topology", {}).get("services", {}).get(entry.service, {}).get("internal_port")
    )
    port = port if isinstance(port, int) and not isinstance(port, bool) else None
    # AUTHENTICATE against live Docker state before trusting the derived name —
    # this is what makes a stale/wrong derivation refuse instead of silently write:
    live_names = <docker ps --no-trunc --filter network=<intent's network>
                   --filter label=com.docker.compose.service=<entry.service> --format '{{.Names}}'>
    if container not in live_names:
        raise WorktreeError(
            f"[S16.1] resolved reference container {container!r} for service "
            f"{entry.service!r} is not live on network {network!r} (found: {live_names}). "
            "The reference instance may be stopped, or its identity may have changed."
        )
```

`write_rendered=False` and `environ=ref_env` are BOTH mandatory (mirror the existing
`resolve_worktree_cap`/`_resolve_budget_candidates` precedents in this same file —
find and cite the exact functions in your LOG): never write `ciu.global.toml` into
the reference's checkout, and never let the reference's templates see THIS process's
ambient environment.

Do NOT scope the authentication query additionally by `label=com.docker.compose.project`
matching `ref_projects` — the container name itself already carries the reference's
`project_name`/`environment_tag`, which IS the authenticating fact; a reference may
legitimately run the shared service under a project the operator didn't need to
declare via `--shared-infra-ref-projects` for liveness. (If you disagree after your
own investigation, this is a named degree of freedom — document your choice.)

`_worktree_overlay_text` emits, after the four existing lines, one
`[ciu.instance.shared_infra.ref_services.<alias>]` sub-table per entry (declaration
order), THEN one commented `[topology.services.<alias>]` block per entry with
`internal_host = "<container>"` and (only when known) `internal_port = <port>`:

```
# S16.1/CIU-52 — CIU-resolved addressing for the reference instance's shared
# services. Do not hand-edit; re-run `ciu worktree add --shared-infra ...`.
[topology.services.vault]
internal_host = "dstdns-98535c-vault"
internal_port = 8200
```

TOML ordering matters: parent shared_infra sub-tables first, then the top-level
`[topology.*]` blocks, matching how a human reads top-to-bottom.

### 3. Join-time re-verification (`connect_shared_infra_after_up`)

Insert one new precondition block AFTER the existing
`_check_reference_network_and_projects` call and BEFORE the target-discovery loop —
i.e. inside the "every precondition before any side effect" region. For each
`entry in intent.ref_services`, re-run the SAME live-name query as step 2's
authentication and require `entry.container` still present; on failure, refuse
naming the recorded container, the names actually found, and the same remedy
phrasing this function already uses for its other staleness refusals ("Restore it,
re-run `ciu worktree add --shared-infra` to update the recorded reference, or
`ciu down` this instance"). No connects are attempted on failure — nothing to roll
back.

### 4. CLI surface

One new optional flag: `--shared-infra-ref-services vault[,consul=consul-server]`.
- Bare item = alias equals reference-service name (the common case, matching the
  filing's own `aliases=["vault"]` example with zero extra syntax).
- `alias=ref_service` = the rename escape hatch.
- Optional, but joins the EXISTING all-or-nothing group check (find it near
  `_preflight_shared_infra_for_add`'s call site in `create`/`add`): supplying this
  flag WITHOUT `--shared-infra`/`--shared-infra-services`/`--shared-infra-ref-projects`
  is a partial-group refusal, before any git or Docker call.
- Register and forward on `add`, `create` (and `ensure`, which shares `create`'s
  options function), and `adopt` — mirror the existing three `--shared-infra*` flags'
  exact registration/forwarding pattern in `cli.py`.
- Do NOT invent a `--shared-infra-aliases` flag with positional pairing to
  `services` — that reproduces exactly the two-independent-lists ambiguity this
  package's own §0 correction identifies as the filing's mistake.

### Degrees of freedom
Exact private helper names; whether the live-name authentication query is factored
into one shared helper called from both step 2 (add-time) and step 3 (join-time) or
duplicated inline (prefer a shared helper — same query, same failure shape, DRY is
correct here, not premature). NOT a degree of freedom: the schema shape (§1), the
resolution timing (add-time write-once, §2), the derivation source (the reference's
OWN rendered config via `container_name()`, never string surgery on `ref_projects`),
or the CLI flag shape (§4).

## Work

1. Schema addition: dataclass, grammar validators, closed-shape widening (§1).
2. Resolution + authentication at add-time preflight (§2).
3. Join-time re-verification (§3).
4. CLI flag (§4).
5. Tests per the 13 oracles below.
6. Docs: SPEC S12 (strike the bare reservation, point at S16.1's now-implemented
   `ref_services`) and S16.1 (normative paragraphs: the schema, add-time
   derivation+authentication, join-time re-verification, and an EXPLICIT statement
   that `services` are the joiner's own containers while `ref_services` are the
   reference's — the exact ambiguity this filing's own illustrative example fell
   into). docs/CONFIG.md: extend the shared-infra worked example with `ref_services`
   and the emitted `[topology.services.vault]` block. CHANGES.md Unreleased entry.
   KNOWN_ISSUES_TODO_BACKLOG.md CIU-52 row -> FIXED with evidence, naming the
   corrected understanding of `services`/`ref_projects` explicitly so a future reader
   doesn't repeat the filing's own misreading.

## Oracles

1. **Headline contract.** `add(..., shared_infra_ref_services="vault")` against a
   reference whose rendered global config has `project_name="dstdns"`,
   `environment_tag="98535c"`, `topology.services.vault.internal_port=8200` ⇒
   overlay parses (real `tomllib`) to `topology.services.vault.internal_host ==
   "dstdns-98535c-vault"` and `internal_port == 8200`, with NO hand-written override
   anywhere in the fixture.
2. **Controlled wrong implementation.** Derivation returns the bare `"vault"` instead
   of the qualified form ⇒ add-time authentication refuses naming both the computed
   and the live names found; `git worktree add` is never called.
3. **Three-instance non-interference** (the filing's own named fixture): reference A
   (project `dstdns`/tag `aaaaaa`, vault container `dstdns-aaaaaa-vault`), joining B,
   and an unrelated C running its OWN vault (`dstdns-cccccc-vault`) that is
   ADVERSARIALLY also connected to A's network carrying the identical
   `com.docker.compose.service=vault` label — B's resolution is `dstdns-aaaaaa-vault`
   always, unaffected by C's presence.
4. **Round-trip schema symmetry**: overlay text → `tomllib.loads` →
   `parse_shared_infra_config` reproduces the exact `SharedInfraIntent` including
   `ref_services`.
5. **Backward-compatibility byte-test**: the same add WITHOUT
   `--shared-infra-ref-services` produces byte-IDENTICAL overlay text to today's,
   `ref_services == ()`, and the strict `ScriptedDocker` fake records ZERO extra
   Docker calls at both add and join.
6. **Closed-shape widening, not opening**: an unknown top-level `shared_infra` key
   still refuses naming it; a `ref_services.<alias>` sub-table with an unknown key,
   missing `service`/`container`, or non-int `port` refuses.
7. **Join-time precondition ordering**: recorded container absent from the live
   query at join time ⇒ `WorktreeError` BEFORE any `network connect` call (assert the
   fake's call list contains zero `["network","connect",...]` entries); present ⇒
   pre-existing connect behavior is bit-for-bit unchanged.
8. **Render isolation**: the reference's checkout gains no new `ciu.global.toml`
   file after an add, and a poisoned ambient `$REPO_ROOT`/`$INSTANCE_ID` in the
   calling process does not change the resolved container name.
9. **Rename escape hatch**: `--shared-infra-ref-services secrets=vault` ⇒
   `topology.services.secrets.internal_host` resolves to A's vault container; no
   `topology.services.vault` block is written.
10. **Merge order**: a committed `ciu.global.defaults.toml.j2` declaring
    `[topology.services.vault] internal_host="vault", internal_port=8200` ⇒ after
    `render_global_chain`, `internal_host` is the reference's qualified name
    (overlay wins) and `internal_port` survives from the committed default when the
    reference itself declares none.
11. **Grammar refusals**, each before any side effect: blank item, duplicate alias
    (`vault,vault=vault`), alias failing its regex, service failing its regex, a `$`
    in any component, an empty flag value.
12. **Partial-group refusal**: `--shared-infra-ref-services` alone (without the other
    three) refuses before any git/Docker call.
13. **Port omission**: reference declares no `internal_port` for the service ⇒
    overlay writes `internal_host` only, no invented `internal_port` key.

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

No live Docker in tests — the `ScriptedDocker` strict fake already used by this
file's existing tests; mirror it exactly for the new Docker calls.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file
(especially: if `config_model.render_global_chain` does not actually accept
`write_rendered`/`environ` parameters as this handoff assumes — re-verify this
yourself first, read-only, before assuming it's wrong), STOP: write
`BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P31-ciu52-shared-infra-ref-services-LOG.md`, commit what
you have, exit. Additional triggers (also in frontmatter `escalate_if`): if you
discover a real blast-radius issue outside `scope.touch` beyond the already-flagged
`test_ciu_cli_worktree.py` trap, follow this wave's established pattern — stop,
document evidence and options in the LOG, do not ship red or silently widen scope;
the controller reviews and authorizes.
