# Container Doctrine

> Everything heavy or specialized is its own consumable container. The devcontainer is a lean
> cockpit that *drives* those containers — never the place they *run*.

This image (`modern-debian-tools-python-debug` and its `-vsc-devcontainer` sibling) is the
**universal** base for that cockpit, shared across many projects. This document defines what belongs
in it, what must be pushed out, and why. It is general; each consuming project applies it in its own
repository.

## Why the devcontainer stays lean

Two independent reasons converge on one rule.

**Security — the trust boundary.** The devcontainer holds secrets: tokens, SSH keys, cloud
credentials. Every package installed beside them widens the surface that can reach them. Large or
risky surfaces — browsers above all — must live *outside* that boundary and be reached over the wire.

**Fidelity — the dependency closure.** The devcontainer's language environment is a *toolkit*, not a
runtime. Code does not ship from here; it ships from per-service images. If tests run in the toolkit,
they pass or fail against whatever the toolkit happens to contain — not against the image that
deploys. A dependency present in the toolkit but absent from the image yields a **green test and a
production crash**.

> The toolkit being *incomplete* relative to any app is correct by design. The only environment whose
> completeness matters is the container that runs the code, and validation must happen there.

Making the toolkit "complete" re-couples results to the devcontainer and reintroduces exactly this
class of bug. Don't.

## The layers

| Layer | Scope | Holds | Must never hold |
|---|---|---|---|
| **This image (the base)** | Universal — every project | generic dev/debug tooling, the language runtime, the `docker` CLI, editors; a curated **toolkit** venv | any project's app or test dependencies |
| **Devcontainer venv** | Per project | launchers and orchestration only (deploy CLI, the test *launcher*) | the app or full-test dependency closure |
| **Consumable services** | Cross-project, over the wire | one specialized capability each | — *(e.g. browsers → `pwmcp`)* |
| **App images** | Per service | that service's full runtime dependencies | — |
| **CLI image** | Per project | the CLI tool and its dependencies | — |
| **Test image** | Per project | the test runner **plus the runtime dependency closure** | — |

Everything below the base is built and managed by the project's own deployment tooling. The
devcontainer *consumes*; it does not *contain*.

## Applications of the doctrine

### Browsers live in a service, never in the image — *security*

**Do not add browser packages to this image** — no `playwright` / `chromium` / `selenium` /
`puppeteer`, no browser OS libraries (`libnss3`, `libgbm-dev`, `fonts-*`, `libatk*`, `libxss1`, …),
nothing `playwright install` would pull. Browser tests connect to an external
Playwright-as-a-Service (`pwmcp`) over a shared Docker network, addressed by container name (never
`localhost`):

| Purpose | Variable | Value |
|---|---|---|
| Test-runner WebSocket | `PLAYWRIGHT_SERVER_WS` | `ws://<pwmcp-playwright>:3000/` |
| AI / MCP endpoint | *(direct URL)* | `http://<pwmcp-mcp>:8931/mcp` |

The runner needs only the client library, pinned to the service's image tag — never the browser
binaries:

```bash
pip install playwright==<version>   # match the pwmcp image tag
# never: playwright install
```

### Tests run in a test container, never in the toolkit — *fidelity*

Build a per-project **test image** from this base + the project's own packages + test-only extras
(`pytest`, …), and run the suite *inside* it. Then the test dependency closure is identical to the
runtime closure: a missing top-level dependency fails at import in CI rather than as a crash after
deploy, and a test that exercises an optional or lazily-imported feature catches that feature's
missing dependency too. The devcontainer's only role is to *launch* the run.

### Multi-version Python: `uv` + `tox` — *fidelity across interpreters*

This image ships `uv` for hermetic interpreters. Install the targets once per devcontainer lifetime
and run matrices for fast local reproduction:

```bash
uv python install 3.9 3.11 3.13
uv run --python 3.9 pytest tests/
uv run tox                          # envlist = py39, py311, py313
```

The local matrix is a *convenience* for chasing a version-specific failure — not a ship signal. The
authoritative gate is the project's CI matrix.

### Dev Python ≠ prod Python

Develop on the modern interpreter the image ships (better tooling, clearer errors, faster editor
inference); build and test *release artifacts* on each declared target version via the `uv` / `tox`
flow above. **"Green on 3.13" never implies "ships on 3.9"** — syntax, stdlib, and backport gaps
surface only when the real interpreter actually runs.

## Keeping this image universal

Because every project consumes this base, it must stay general. **Never** add a project's app or test
dependencies here to make something work — push them down into that project's app or test image.
"Slimming the base" means removing anything project- or test-specific that has crept in. It is the
same instinct that made browsers a separate service rather than a base-image feature.
