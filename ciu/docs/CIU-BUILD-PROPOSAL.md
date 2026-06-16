# Proposal: `ciu-build` — Image Build/Push Runner

**Status:** Draft — not implemented  
**Proposed SPEC section:** S10.4  
**Entry point:** `ciu-build = "ciu.build_runner:main"`

---

## Problem

Projects in a CIU ecosystem need to build and push Docker images as part of their release
cycle. Each project currently writes its own wrapper script (`build-push.py`) to:

- Load environment variables from `.release-vars` or `release.toml`
- Authenticate to a container registry
- Run `docker buildx bake` or similar commands
- Check required credentials before running

The `build-push.toml` format was designed to capture this configuration, but nothing
reads it. Every project re-implements the same logic in a different wrapper.

`ciu-build` is the missing reader for `build-push.toml`.

---

## Proposed CLI

```
ciu-build [-d PATH] <step-name>
```

`-d PATH` — directory containing `build-push.toml` (default: `.`)  
`<step-name>` — name of the step to run (e.g. `build-images`, `push-images`)

**Exit codes** (same contract as `ciu`, S10.3):  
`0` success · `1` runtime failure · `2` configuration error · `3` environment/bootstrap error

---

## `build-push.toml` format

```toml
project_root = "."          # project root relative to this file (for cwd resolution)
log_dir = "logs"            # optional: where to write run logs

[steps.<name>]
required_env = ["VAR1", "VAR2"]   # fail with exit 3 if any are unset
commands = [
  { label = "human-readable label", argv = ["cmd", "arg1", "arg2"], cwd = "." },
]

[steps.<name>.login]        # optional: docker registry auth before commands run
registry = "ghcr.io"
username_env = "GITHUB_USERNAME"
token_env = "GITHUB_PUSH_PAT"
required = true             # fail with exit 3 if creds are missing
```

Multiple steps can be defined; `ciu-build -d . build-images` runs only that step.

### Example: pwmcp

```toml
project_root = "."
log_dir = "logs"

[steps.build-images]
commands = [
  { label = "pwmcp: build playwright-server image", argv = ["docker", "buildx", "bake", "all", "--load"], cwd = "." },
]

[steps.push-images]
required_env = ["GITHUB_USERNAME", "GITHUB_PUSH_PAT"]
commands = [
  { label = "pwmcp: push playwright-server image", argv = ["docker", "buildx", "bake", "all", "--push"], cwd = "." },
]
[steps.push-images.login]
registry = "ghcr.io"
username_env = "GITHUB_USERNAME"
token_env = "GITHUB_PUSH_PAT"
required = true
```

---

## How it fits in the SPEC

The three CIU entry points cover the full artifact lifecycle:

| CLI | Scope | Config file | Reads env from |
|---|---|---|---|
| `ciu` | Stack runtime (render, secrets, up/down) | `ciu.*.toml.j2` | `ciu.env` |
| `ciu-deploy` | Multi-stack deployment orchestration | `ciu-deploy.toml` | `ciu.env` |
| `ciu-build` | Image build/push pipeline | `build-push.toml` | shell env / `release.toml` |

The separation is intentional: `ciu` manages running stacks; `ciu-build` manages the
images those stacks pull. Neither depends on the other.

**Proposed S10.4 language:**

> - **S10.4** `ciu-build`: reads `build-push.toml` in the project directory (`-d`).
>   Executes a named step's commands in sequence. Before execution: validates
>   `required_env`; if a `[steps.<name>.login]` block is present, runs
>   `docker login <registry>` using the named env vars. All sub-processes are run
>   via `procutil.run_cmd` (no `shell=True`). Exit codes follow S10.3.

---

## Implementation sketch

New file: `ciu/src/ciu/build_runner.py`

```python
# Reads build-push.toml, validates env, optional docker login, runs commands.
# Uses existing procutil.run_cmd — no new subprocess plumbing.
```

New entry point in `ciu/pyproject.toml`:

```toml
[project.scripts]
ciu       = "ciu.cli:main"
ciu-deploy = "ciu.deploy:main"
ciu-build  = "ciu.build_runner:main"   # new
```

### Data model

```python
@dataclass
class StepCommand:
    label: str
    argv: list[str]
    cwd: str = "."

@dataclass
class RegistryLogin:
    registry: str
    username_env: str
    token_env: str
    required: bool = True

@dataclass
class BuildStep:
    name: str
    required_env: list[str]
    commands: list[StepCommand]
    login: RegistryLogin | None
```

### Execution order

1. Parse `build-push.toml` (fail with exit 2 on missing/malformed)
2. Look up `<step-name>` (fail with exit 2 if not found; list available steps)
3. Validate `required_env` (fail with exit 3 on any missing var)
4. If `login` block present: `docker login <registry>` via stdin pipe (fail with exit 3 if `required` and creds missing)
5. Run each command in `commands` via `procutil.run_cmd` (fail with exit 1 on non-zero return)

### Credential resolution (optional extension)

`ciu-build` could optionally read `GITHUB_USERNAME` / `GITHUB_PUSH_PAT` from
`../release.toml[github]` when not set in environment — same pattern as
`pwmcp/build-push.py` implements today. This avoids needing to `export` secrets
in the shell before running.

---

## What replaces the per-project wrappers

Once `ciu-build` is implemented:

- `pwmcp/build-push.py` → deleted; `release.toml` steps use `ciu-build -d . build-images/push-images`
- Any future CIU-managed project with Docker images follows the same pattern without writing a wrapper

The `build-push.toml` format is already in use. `ciu-build` would be a zero-config drop-in
for all projects that already have one.

---

## Open questions

1. **Credential sourcing**: Should `ciu-build` read `release.toml` automatically, or require
   the caller to export env vars? Auto-read is convenient but couples `ciu-build` to the
   `release.toml` schema.

2. **SPEC bump**: Adding S10.4 is a backwards-compatible SPEC change. Does it warrant a minor
   version bump (`ciu-v2.1.0`) per the versioning convention (MINOR = new feature)?

3. **Log file**: `build-push.toml` has `log_dir`; should `ciu-build` write a structured log
   (like `ciu-deploy` does) or just pass stdout/stderr through?

4. **Parallel steps**: Out of scope for now; all commands within a step run sequentially.
   Cross-step parallelism would be an S12 extension.
