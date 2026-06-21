# SPEC C — CIU Multi-Profile (`CIU_SERVICES_PROFILE`)

| Field | Value |
|---|---|
| **Spec ID** | C |
| **Repo** | `vbpub/ciu` (generic — no dstdns knowledge) |
| **Owns** | Seam 4 — CIU service-profile selection |
| **Consumed by** | SPEC F (the dstdns CIU adapter runs `ciu up --profile … --profile …`) |
| **Depends on** | Nothing |
| **Release impact** | **Breaking → CIU major version bump** (`ciu-vN.0.0`) |
| **Policy** | Greenfield. `CIU_HOST_PROFILE` is **retired**, not aliased. No dual-read fallback. |
| **Master plan** | `/workspaces/dstdns/docs/plan-cmru-remote-deployment.md` (§3.4, §5 Seam 4, §6) |

---

## Worktree directive

```
Worktree: create a git worktree for branch `feat/ciu-multi-profile` at
/tmp/vbpub-ciu-multi-profile and do all work there — never modify /workspaces/vbpub directly:
  git worktree add -b feat/ciu-multi-profile /tmp/vbpub-ciu-multi-profile main
```

All edits, the test run, and the commit happen inside `/tmp/vbpub-ciu-multi-profile`. Base on
the current **local** `main` (it carries unreleased CIU work the released tag does not).

---

## 1. Goal

Let a single CIU invocation activate an **ordered list** of service profiles
(`core`, `db`, `worker-io`, …) instead of exactly one host profile. Each remote host in the
landscape is assigned an ordered profile list; the deployment adapter (SPEC F) passes that
list to CIU. A second, smaller goal: give CIU a **dynamic per-instance configfile selector**
so a project can render *N* worker instances (each with a unique ID and its own configfile
mount) instead of hardcoding a fixed two.

This is a **breaking** change: the env var name changes, the CLI flag becomes repeatable,
and `resolve_profile` is superseded by a composite resolver.

---

## 2. Background / ground truth (verified)

- **Single-valued today.** `src/ciu/deploy.py:1155`:
  ```python
  control.add_argument("--profile", default=None, metavar="NAME",
                       help="Host profile to activate (default: env CIU_HOST_PROFILE) (S7.5)")
  ```
  `deploy.py:1245` calls `resolve_profile(global_cfg, args.profile)`; the thin wrapper at
  `deploy.py:185` delegates to `deploy_pkg/profiles.py:resolve_profile`.
- **`resolve_profile`** (`src/ciu/deploy_pkg/profiles.py:64–162`) resolves **one** name:
  argument → `env["CIU_HOST_PROFILE"]` (line 88) → default (all phases). It already:
  - validates `phases` against `PHASE_KEY_RE` and builds a `set[str]` of `phase_<n>` keys;
  - collects `stacks` → `extra_stacks`, `compose_profiles`, `env_overrides` (dict);
  - deep-merges `topology_overrides` over a **deep copy** of `global_cfg["topology"]` via
    `config_model.deep_merge` (S7.5a) — **reuse this merge primitive**.
  Returns a `Profile` dataclass (`profiles.py:21–40`): `name`, `phase_keys`, `extra_stacks`,
  `compose_profiles`, `env_overrides`, `config`.
- **`CIU_HOST_PROFILE` references** to migrate:
  `deploy_pkg/profiles.py:73,88`; `deploy.py:186,1156`; `workspace_env.py:633,635` (the
  generated `ciu.env` placeholder comment); `dev.py:330` (comment). `SPEC.md` S7.5.
- **Phases are numeric-ordered** by `deploy_pkg/phases.py:ordered_phases` (`phase_2 < phase_10`).
  Phase *selection* is a `set`, but execution order is numeric — preserve that; union of
  profile `phase_keys` is still just a set, ordering is decided by `ordered_phases`.
- **Configfiles are generic in CIU.** `src/ciu/composefile.py:render_configfiles` discovers
  `[<root_key>.<service>.configfile.<cfgname>]` tables per service (S5). **There is no
  worker-1/worker-2 hardcoding inside CIU** — that lives in the dstdns worker compose
  templates. The CIU-side gap is the lack of a way to declare *one* configfile section that
  renders once **per generated instance**. See §6.
- **Action scoping** (existing behaviour to preserve): when `--profile`/`--phases` is
  supplied, *all* actions in the command apply only to that selection.

---

## 3. Frozen contract — Seam 4 (implement exactly)

> This block is the authoritative contract. SPEC F depends on it verbatim.

- **Env var:** `CIU_SERVICES_PROFILE=core,db,worker-io` — a comma-separated **ordered list**.
  It **replaces** `CIU_HOST_PROFILE`, which is **retired**: if `CIU_HOST_PROFILE` is set it is
  **ignored and never used as a fallback**; emit a one-line deprecation error to stderr so an
  operator with a stale env notices (do not silently honour it).
- **CLI:** `ciu up --profile core --profile db --profile worker-io` — `--profile` becomes
  **repeatable** (`action="append"`). A single `--profile core,db` comma form is also
  accepted and split, so CLI and env are symmetric.
- **Precedence:** if any `--profile` is given on the CLI, the **CLI list fully overrides** the
  env list (they are **not** merged).
- **Resolution (union, order-preserving, deduped):** the selected profiles' `phases`,
  `stacks`, and `compose_profiles` are unioned **preserving first-seen order** and
  **de-duplicating** repeated entries. (Phase execution order remains numeric via
  `ordered_phases`; stacks/compose-profiles keep first-seen order.)
- **Override merge + conflict rule:** `env_overrides` and `topology_overrides` from all
  selected profiles are deep-merged in list order. If two profiles set the **same key** to
  **different** values → **fail before any render or Docker mutation**, exit code **2**, with a
  message naming the key and the two conflicting profiles. **Equal** repeated values are
  accepted silently.
- **Explicit per-host overrides** (anything already applied after profile defaults today)
  continue to apply **after** the combined profile defaults.

Profile-table shape is unchanged (`[deploy.profiles.<name>]` with
`phases`/`stacks`/`compose_profiles`/`env_overrides`/`topology_overrides`).

---

## 4. Implementation tasks (file-by-file)

1. **`src/ciu/deploy.py`**
   - argparse (~line 1155): `--profile` → `action="append", default=None`. Update help to
     reference `CIU_SERVICES_PROFILE`. Accept comma forms (split each appended value on `,`).
   - `resolve_profile` wrapper (line 185) → `resolve_profiles(global_cfg, names: list[str] | None)`.
   - call site (~line 1245): pass the resolved CLI list (or `None` to fall through to env).
   - Preserve action-scoping semantics over the **composite** selection.

2. **`src/ciu/deploy_pkg/profiles.py`**
   - Add `resolve_profiles(global_cfg, names: list[str] | None, env=None) -> Profile`:
     - if `names` is falsy, read `env.get("CIU_SERVICES_PROFILE")` and comma-split (strip
       blanks); if still empty → default `Profile` (all phases), unchanged.
     - **Reject** `CIU_HOST_PROFILE` if present (raise `ValueError` → exit 2) with the
       deprecation message.
     - Resolve each name through the existing single-profile logic (refactor the per-name body
       of `resolve_profile` into a private `_resolve_one`), then **compose**:
       - `phase_keys` = union of sets (None means "all" — if any profile is None/all, treat as
         all; document this).
       - `extra_stacks`, `compose_profiles` = order-preserving dedupe across profiles.
       - `env_overrides` = deep-merge with conflict detection.
       - `topology_overrides` = deep-merge over one deep copy of `global_cfg["topology"]`,
         with conflict detection (reuse `config_model.deep_merge`; add a `deep_merge_strict`
         variant or a pre-check that walks both dicts and raises on differing leaf values).
     - Keep `resolve_profile` as a thin `resolve_profiles([name])` shim **only if** other
       call sites need it; otherwise remove it (greenfield — prefer removal + update callers).
   - Add an order-preserving dedupe helper: `dedupe_keep_order(items) -> list`.
   - Add a strict conflict helper used for both env and topology overrides.

3. **`src/ciu/workspace_env.py`** (lines ~633–635): regenerate the `ciu.env` placeholder
   comment to `# export CIU_SERVICES_PROFILE="core,db,worker-io"  # ordered profile list for
   this host`. Remove the `CIU_HOST_PROFILE` placeholder.

4. **`src/ciu/dev.py`** (line ~330): update the comment referencing `CIU_HOST_PROFILE`.

5. **Dynamic worker configfile selector** (the §6 capability) in
   `src/ciu/composefile.py:render_configfiles` (+ config model if needed).

6. **`vbpub/ciu/docs/SPEC.md`** S7: rewrite S7.5 for `CIU_SERVICES_PROFILE` + repeatable
   `--profile`; add the union/dedup/conflict rules and the configfile-selector behaviour;
   bump the documented CIU major version.

7. **`vbpub/ciu/test-repo/`** fixtures: add ≥3 profiles whose union exercises dedup, plus a
   pair with a conflicting `topology_overrides`/`env_overrides` value for the conflict test.

---

## 5. Profile composition — worked example

```toml
[deploy.profiles.core]        ; phases = ["phase_1", "phase_2"]
[deploy.profiles.db]          ; phases = ["phase_2"]      ; (dup phase_2 → deduped)
[deploy.profiles.worker-io]   ; phases = ["phase_4"] ; compose_profiles = ["workers"]
```
`ciu up --profile core --profile db --profile worker-io` → effective phase set
`{phase_1, phase_2, phase_4}` (executed in numeric order), `compose_profiles = ["workers"]`,
overrides merged. If `core` set `topology.services.redis.internal_host = "a"` and `db` set it
to `"b"`, resolution **fails with exit 2** before rendering.

---

## 6. Dynamic per-instance configfile selector

**Problem:** a project that runs *N* worker instances must today declare a separate
`[<root>.<service>.configfile.<name>]` per instance (the dstdns templates hardcode worker-1
and worker-2). CIU should let one declaration fan out to *N* instances.

**Required capability (keep it generic — no "worker" knowledge in CIU):** allow a configfile
section (or the service block that owns it) to declare an **instance count / index variable**
so `render_configfiles` emits one rendered configfile + mount **per instance**, each with a
unique target path and a stable per-instance identifier exposed to the template (e.g. an
`instance_index` / `instance_id` the template can interpolate). The exact TOML shape is the
implementer's choice but MUST:
- render correctly for **1, 2, and many** instances;
- give every instance a **unique configfile mount** and **unique ID**;
- leave existing single-instance configfile sections behaving identically (no regression).

The dstdns-side template change that consumes this lives in the dstdns repo (the worker
sizing work package / SPEC F), **out of scope here** — this spec only delivers the CIU
capability and tests it against `test-repo` fixtures.

---

## 7. Out of scope

- dstdns profile decomposition into `core`/`db`/`worker-io`/`worker-db` (SPEC F).
- Rootless Docker (deferred → mTLS-gated alternative release).
- The worker-io rate-limiter / sizing app code (separate OPEN-WORKSTREAMS work package).
- Any agent/controller or release/bundle logic.

---

## 8. Acceptance criteria & tests

Add tests under `vbpub/ciu/tests/tests/` (extend `test_ciu_deploy_pkg.py` and the CLI parser
test). All must pass:

1. **Ordered union + dedup:** three profiles with overlapping phases/stacks/compose_profiles
   → effective lists are deduped and first-seen-ordered.
2. **Conflict rejection:** two profiles with differing values for the same
   `topology_overrides`/`env_overrides` key → `ValueError`, **exit 2**, **before** any render
   or Docker call; message names the key + both profiles.
3. **Equal repeated values accepted:** two profiles setting the same key to the **same** value
   → no error.
4. **CLI precedence:** with `CIU_SERVICES_PROFILE=a,b` in env and `--profile c` on the CLI,
   only `c` is used (CLI overrides env, not merged).
5. **Env list parsing:** `CIU_SERVICES_PROFILE=core, db ,worker-io` (with spaces) parses to
   `["core","db","worker-io"]`.
6. **`CIU_HOST_PROFILE` retired:** setting it raises/exits 2 with the deprecation message;
   it is **never** used as a fallback.
7. **Comma CLI form:** `--profile core,db` == `--profile core --profile db`.
8. **Dynamic configfiles:** a `test-repo` service rendering 1, 2, and N instances yields N
   unique configfile mounts + unique IDs; single-instance configfiles unchanged.
9. **Unknown profile** still errors with the available-profiles list (existing behaviour).

**Run the suite:**
```bash
cd /tmp/vbpub-ciu-multi-profile
python run-ciu-tests.py
```

---

## 9. Done definition

- `--profile` repeatable; `CIU_SERVICES_PROFILE` is the only env var read; `CIU_HOST_PROFILE`
  fully removed from code + docs (only the deprecation guard mentions it).
- `resolve_profiles` composes an ordered, deduped, conflict-checked composite `Profile`.
- Dynamic configfile selector renders N instances; no single-instance regression.
- `SPEC.md` S7 updated and the CIU major version bumped.
- All §8 tests pass via `run-ciu-tests.py`; commit on `feat/ciu-multi-profile` with the
  worktree's path + hash listed in the final report.
