# LOG — ciu-P11-host-scoped-secrets

- Package: `ciu-P11-host-scoped-secrets`
- Branch: `docs/ciu-P11-host-scoped-secrets` (forked from origin/main @ `98549075`)
- Worktree: `/workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog`
- Handoff input_revision: `3639b18c`
- Status: COMPLETE (no BLOCKED)

## Environment / gate notes

**Evidence ladder (brief rev 3-5):** the pytest result below is the
**iteration signal** — same suite, same 100% line+branch fail-under, in a
LOCAL venv. Recorded as "venv run", never as "the gate". Checkpoint evidence =
trove `[gates.tester-unified]` argv run by the operator/controller at merge
review; no docker/tester-unified container started by the implementer.

**Environment caveat (unchanged):** the devcontainer's ambient
`REPO_ROOT` / `PHYSICAL_REPO_ROOT` / `CIU_GOV_READ_IOPS` leak into a handful
of engine tests; the venv run scrubbed them.

## Iteration-signal result (venv run)

```
env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u CIU_GOV_READ_IOPS \
  .venv/bin/python run-ciu-tests.py
TOTAL   7358  0  2882  0  100%   (line+branch, --cov-fail-under=100)
2232 passed in ~13.2s
```

The run went red once before green — a coverage-only gap (cli.py:1298
"no secrets declared" materialize branch), closed by a targeted CLI test; no
source workaround, no exclusion.

## Work done

Scope.touch only (directives.py / providers.py / transport_ssh.py / engine.py
byte-identical to origin/main — verified empty diff; directives consumed
READ-ONLY):

1. `src/ciu/hosts.py` (S14.3a):
   - `HOST_SCOPE_KINDS = {ASK_EXTERNAL, GEN_LOCAL}` — the only legal host-scope
     kinds (Vault-dependent, ephemeral and in-place kinds are meaningless
     before a host is adopted).
   - `_parse_host_secrets(host_name, secrets_table)` — parses each entry with
     the EXISTING `directives.parse_value` (imported, never reimplemented);
     a grammar violation or a non-host-scope kind raises a tagged `[S14.3a]`
     error naming host + entry + reason.
   - `get_host` — validates the `secrets` subtable (a malformed table aborts
     any flow touching the host) but **pops** it before returning: transport
     callers never receive secret directives.
   - `get_host_secrets(repo_root, host_name) -> dict[str, SecretSpec]` — the
     CLI-facing parse; `{}` when the host declares no subtable.
2. `src/ciu/secrets/materialize.py` (O2):
   - Extracted a `store_path` seam on `_materialize_one` (defaults to the
     existing `_store_file`) so the SAME resolution core persists into a
     different namespace — no resolution-order divergence.
   - `host_secret_store(repo_root, host_name, name)` — the
     `project_store/hosts/<host>/<name>` path.
   - `materialize_host_secrets(repo_root, host_name, entries, *, assume_yes,
     env, chown_fn, prompt_fn)` — the existing machinery on the host
     namespace: ASK_EXTERNAL env→CIU_SECRET_→store-file-reuse→prompt→[S4.13]
     abort; GEN_LOCAL generate-then-reuse. 0700 dirs, atomic write, flock
     (project lock) reused verbatim. Two hosts may declare the same entry
     name without collision (S4.6 does not apply across host namespaces).
3. `src/ciu/cli.py` (O3):
   - `ciu host-secrets <host> [--materialize | --list | --path <name>] [-y]`
     verb (inline-argparse pattern): `--materialize` resolves all declared
     entries and prints store file paths (never values); `--list` prints
     names + store-file existence; `--path` prints one store path (for
     feeding a bootstrap command over `ciu ssh`). Values never printed;
     materialization is explicit-only (nothing inside ssh/up --host).
     Exactly one mode required; missing host or unknown `--path` entry → exit
     2 with actionable text. `_USAGE` + `_VERB_HELP` updated.
4. Tests — `tests/tests/test_ciu_host_secrets.py` (31 tests): closed-kind
   refusal (all four forbidden kinds, parametrized), grammar violation tagged
   error, pop-before-return, host/nonexistence errors, store namespace,
   ASK_EXTERNAL env / CIU_SECRET_ / store-reuse / prompt / non-interactive
   abort, GEN_LOCAL generate-then-reuse, two-host same-entry no-collision,
   empty noop, and the CLI surface: list without values, path, unknown entry,
   materialize prints paths not values, S4.13 abort exit 2, unknown host,
   missing host, exactly-one-mode, and a no-implicit-materialization probe
   (up --host with a secrets-bearing host runs the push and writes nothing).
   Prompt paths fake `sys.stdin.isatty` + inject `prompt_fn` (existing
   test_ciu_secrets_materialize precedent); store paths use tmp_path.
5. Docs (O4):
   - `docs/SPEC.md` — S14.3a normative clause: closed set, store namespace,
     resolution order, inert GEN_LOCAL locator (shared grammar), explicit-
     only + never-printed, transport isolation; `secrets` row added to the
     S14.3 inventory key table.
   - `docs/CONFIG.md` — `[deploy.hosts.<name>.secrets]` section with the
     **pre-Vault rationale** (the whole point of the ask), the two allowed
     kinds, the store namespace + non-uniqueness rule, and the worked
     consumer example from the ask (Tailscale single-use authkey + SSH
     bootstrap key, materialized then consumed by a bootstrap command over
     `ciu ssh`).
   - `CHANGES.md` — Unreleased entry (CIU-35, S14.3a).
   - `KNOWN_ISSUES_TODO_BACKLOG.md` — CIU-35 row + detail block → **FIXED**
     with evidence.

## Controlled red during development

The full venv run was observed red once, a real coverage gap closed by a
targeted CLI test (cli.py:1298 materialize-with-no-secrets branch). During
test authoring, host-scope `GEN_LOCAL` was initially declared bare in
fixtures; the shared `parse_value` grammar requires a locator payload
(`GEN_LOCAL:<locator>`), which surfaced and is now documented as **inert** at
host scope (the store path is the entry name per O2) — directives.py was not
touched.

## Deviations

1. Host-scope `GEN_LOCAL` carries the shared grammar's required locator
   payload, but the store path is the entry name (`hosts/<host>/<entry_name>`
   per O2) — the locator is documented inert. Rationale: directives.py is
   forbidden; the shared grammar is reused verbatim.
2. `materialize_host_secrets` locks on the project lock (the host namespace
   lives under the project store), not a stack lock.
3. Escalate_if #2 not triggered: the worktree-automation branch HAS merged;
   `test_ciu_documentation_contract.py` passes unmodified against the new
   docs shape (checked in the same targeted run).
