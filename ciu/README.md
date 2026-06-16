# CIU

CIU renders and runs Docker Compose stacks from layered templates, with secrets,
host-aware paths, and multi-stack orchestration built in. It ships two console
entrypoints:

- **`ciu`** — render and run a single stack
- **`ciu-deploy`** — orchestrate multi-stack, multi-host deployments

Normative behaviour is defined in [docs/SPEC.md](docs/SPEC.md); the guides under
[docs/](docs/README.md) are the place to start.

## Two ways to ship a stack

A project maintainer can offer **both** deploy paths from the same repository,
side by side:

| Path | File the admin runs | What it gives |
|---|---|---|
| **Plain compose** | a hand-written, committed `docker-compose.yml` | works with `docker compose up` — or `ciu --shipped` to add env/network/preflight |
| **CIU-managed** | `ciu.compose.yml.j2` → rendered `ciu.compose.yml` | secrets, configfiles, hostdirs, host-aware paths, orchestration |

CIU renders its compose to **`ciu.compose.yml`** (gitignored), so it never
touches a hand-written `docker-compose.yml`. An admin who has never heard of CIU
can still `docker compose up` the committed file; the CIU path is additive.

`ciu --shipped` runs the pre-shipped `docker-compose.yml` *through* CIU — loading
`ciu.env`, ensuring/attaching the workspace network, and running the DooD
reachability preflight first — so even the "plain" path gains consistent
machine-identity env interpolation and network wiring that bare `docker compose`
lacks (see [docs/CIU.md](docs/CIU.md)).

## Why CIU over a plain `docker-compose.yml`

1. **Real secrets, never in the file.** Six directives (Vault read, generate-to-Vault, generate-local, ask-external, ask-file, ephemeral; S4.2) resolve secrets, write them as `0440` files, and mount them at `/run/secrets/<name>`. Three layers guarantee values never reach the YAML or logs: stringify-guards (S4.21), a post-render plaintext scan (S4.22), and redacted `--print-context` (S4.23).
2. **Secure-by-default first run.** `GEN_LOCAL`/`GEN_TO_VAULT` mint a strong random secret on first run and reuse it forever — the file *is* the state, idempotent (S4.11). Clone, run, get unique credentials; no default-password footgun.
3. **One template adapts to every host.** Compose and app-config files are Jinja2-rendered against layered TOML + machine facts (UID/GID, docker group, network name, physical paths, FQDN/TLS). Admins tune TOML overrides (S3.3), not the maintainer's compose.
4. **DooD / path correctness for free.** CIU computes physical bind paths so a stack runs identically in a devcontainer, native host, and CI (S1.9). A hand-written `./vol-data` mount silently breaks under docker-outside-of-docker.
5. **Hostdir provisioning & ownership.** Pre-creates volume dirs with correct owner/mode, can seed initial content, and fixes ownership via a helper container even when the operator isn't root (S6.3/S6.5/S6.6) — no chown-init-container boilerplate. Fixed-UID images (postgres 999) just work.
6. **Multi-stack / multi-host orchestration.** `ciu-deploy` runs stacks in numeric phase order, gates on health (`starting` ≠ healthy, S7.7), and supports host profiles with `topology_overrides` for cross-host addressing (S7.4/S7.5a).
7. **Fail-fast before anything starts.** A static validation catalog (S11) and a typed exit-code contract (S10.3) catch errors pre-launch; a vault-backed stack aborts if no token resolves (S7.6).
8. **App config as mounted files, with composite secrets.** Configfile mounts (S5) render a full app config — including DSNs that embed credentials via `secret()` (S5.4) — and mount it read-only, replacing sprawling `APP__*` env blocks.
9. **Declarative bootstrap hooks + clean lifecycle.** Three structured hook points (S9) handle things like "unseal Vault, persist its root token to `[state]`, hand it to later stacks." `--reset` tears down containers, volumes, and rendered outputs, scoped to the stack dir (S6.4).

## When *not* to use `ciu`

- Trivial stacks with no secrets — CIU's model is overhead to learn.
- Environments that need a literal `docker-compose.yml` for other tooling (Swarm, k8s importers, CI that expects that exact filename) — ship one and offer `--shipped`.
- Operators who want to read exactly what runs with no generator in the loop.

## File hierarchy (starter)

Per stack directory, three categories of file coexist. Only the first is
committed; the rest are machine-generated and gitignored.

```
my-stack/
  ciu.defaults.toml.j2     # COMMITTED  — stack config template (the stack marker)
  ciu.compose.yml.j2       # COMMITTED  — compose template
  config.toml.j2           # COMMITTED  — optional app configfile template (S5)
  docker-compose.yml       # COMMITTED  — OPTIONAL hand-written compose (--shipped path)

  ciu.toml.j2              # gitignored — override layer, auto-created, you MAY edit
  ciu.toml                 # gitignored — rendered stack config (keeps [state])
  ciu.compose.yml          # gitignored — rendered compose (what CIU runs)

  .ciu/                    # gitignored — machine-owned, do NOT edit:
    ciu.compose.overlay.yml  #   secrets + configfile mounts (S4.17)
    secrets/<name>           #   materialized secret files (0440)
    rendered/<svc>/<cfg>     #   rendered configfiles
    lock                     #   per-stack run lock
```

At the repo root: `ciu.global.defaults.toml.j2` (committed, the root marker),
`ciu.global.toml.j2` / `ciu.global.toml` (gitignored), and `ciu.env`
(gitignored, the machine-identity layer — generated by `ciu --generate-env`).

The rule of thumb: a `.j2` suffix means *template* (input); strip it to get the
*rendered* output. Everything under `.ciu/` and every rendered output is
gitignored; copy the ready-made rules from [`.gitignored.ciu`](.gitignored.ciu).
Full reference: [docs/CONFIG.md](docs/CONFIG.md#file-roles-and-layering-s31s33).

## Quick start

```bash
pip install -e .                 # install (see docs/README.md for build/wheel)
ciu --generate-env -d <repo>     # detect machine facts → ciu.env (S2.8)
ciu -d <repo>/<stack>            # render + run one stack
ciu-deploy --deploy --profile <host-profile>   # orchestrate many
```

`ciu --help` / `ciu-deploy --help` list every flag.

## Release scripts

Three Python scripts handle the build/release cycle. Run them in order, or let
the parent `release-all.py` pipeline drive them via `release.sample.toml`:

| Script | Purpose | Calls into |
|---|---|---|
| `run-ciu-tests.py` | Run the full pytest suite | `pytest tests/` |
| `build-wheel.py` | Build the wheel into `dist/` (cleans first) | `python -m build`, config: `build-push.toml [steps.build-wheel]` |
| `publish-wheel.py` | Publish to GitHub Releases and validate | `tools/publish-wheel-release.py`, `tools/validate-wheel-latest.py`, config: `build-push.toml [steps.publish-wheel]` |

The `tools/` directory also contains two helper scripts invoked internally:
`cleanup-legacy-releases.sh` deletes the old `ciu-wheel-latest` GitHub release tag,
and `cleanup-and-validate.sh` wraps that cleanup with a post-publish validation pass.

### Release a new version (SemVer)

The publish script at `tools/publish-wheel-release.py:275` has a branch:

- Dev build (2.0.1.dev8.g2f597cf): only moves ciu-latest — no per-commit tag spam
- Clean tagged release (2.0.1): creates both an immutable ciu-v2.0.1 release and updates ciu-latest

So yes, the versioned-tag-plus-latest-alias pattern is already there — you're just not triggering it because
you're on an untagged commit.

Why you're seeing a dev version

The only ciu git tag is ciu-v2.0.0. You're now 8 commits past it (2f597cf). setuptools_scm computes the version
as 2.0.1.dev8.g2f597cf — "next patch after 2.0.0, 8 commits ahead, at this sha." That's why the wheel in
ciu-latest has that name.

How to cut 2.0.1 (or skip to 2.0.2) 

```bash
# On the commit you want to release:
git tag -a ciu-v2.0.1 -m "ciu 2.0.1"
git push origin ciu-v2.0.1   # needs workflow scope — see PAT note below
```

Then run the build+publish pipeline. setuptools_scm will produce clean version 2.0.1, and the publish script
will create:
1. An immutable ciu-v2.0.1 GitHub release with ciu-2.0.1-py3-none-any.whl
2. ciu-latest updated to the same wheel

Version increment is not automatic — you must create the git tag. setuptools_scm reads tags, it doesn't write
them. The .devN suffix is what you get between releases.


## Requirements

- Python 3.11+
- A target repo with CIU templates (at minimum `ciu.global.defaults.toml.j2`)
- Docker Engine + Docker Compose v2

## Documentation

- [docs/README.md](docs/README.md) — index, build/install, running tests, demo repo
- [docs/SPEC.md](docs/SPEC.md) — the normative contract (`S-xx` IDs)
- [docs/CONFIG.md](docs/CONFIG.md) — config files, layering, secret directives
- [docs/CIU.md](docs/CIU.md) — single-stack guide (`ciu`)
- [docs/CIU-DEPLOY.md](docs/CIU-DEPLOY.md) — orchestration guide (`ciu-deploy`)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module map for contributors
- [docs/MIGRATION-V2.md](docs/MIGRATION-V2.md) — migrating a project onto CIU v2

## Market comparison

CIU lives in the "PaaS over Compose on one (or a few) hosts" category. Mature
alternatives overlap with individual pillars:

| CIU pillar | Mature equivalent(s) | Notes |
|---|---|---|
| Jinja2 → compose rendering | Docker-Compose-Templer, jinja-compose, gomplate, Ansible | Multiple drop-in tools do this. |
| Layered defaults/override deep-merge | Compose native `-f` merge, `include`, `extends`, profiles, `${VAR}` | Modern Compose covers much of this. |
| Secret directives (`ASK_VAULT:`, `GEN_*`) | vals (`ref+vault://`), gomplate Vault, Vault Agent, SOPS | `vals` is the canonical inline-reference tool. |
| Pre/post hooks | Compose lifecycle hooks, Kamal hooks, Ansible, Taskfile/Make | Standard. |
| Multi-stack phased orchestration + health + registry auth | Kamal, Coolify, Dokploy, CapRover, Swarm, Nomad | Battle-tested incumbents. |
| Workspace env autodetect (`ciu.env`) | direnv, Compose `env_file`, `.env` conventions | Standard. |
| Define once, target compose + k8s + cloud | Score (CNCF, score-compose) | The standard portable-workload abstraction. |

CIU's edge is the *combination*, tuned for the devcontainer/DooD workflow with
secrets-as-files and dual shipping out of the box.
