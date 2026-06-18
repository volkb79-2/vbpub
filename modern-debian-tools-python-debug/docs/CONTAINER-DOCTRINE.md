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

## Lean ≠ bare: this is a *debug* cockpit

The image is named `…-python-debug` on purpose. "Lean" is about the **trust boundary and the
ship-gate** — not about starving the cockpit of inspection tooling. Two activities look alike but have
opposite requirements:

| | **Debugging** (this image's mission) | **Ship-gate testing** |
|---|---|---|
| Mode | interactive, human-in-the-loop | automated pass/fail |
| Runs against | a **running** deployed stack | the test image's own closure |
| A wrong library version | is *seen immediately* by the human | silently flips red→green |
| Rich inspection libs | **help** — keep them | irrelevant; must equal prod |

So the fidelity rule governs **where the gating suite runs**, never what the debug image *contains*.
"Green in this cockpit" is never a ship signal; the gating suite runs in a test image built
`FROM <app-base>` (see *Tests run in a test container*). Given that boundary, shipping a curated set
of inspection libraries in the venv is correct, not a violation.

### Inspection client ≠ app library

The cockpit talks to services two ways; keep them distinct:

- **Inspection CLIs** — `psql`, `redis-cli`, `vault`, `consul`, `aws` (→ S3 / MinIO), `dig`,
  `grpcurl`, `http`/`curl`, `w3m`. These *are* the cockpit driving services over the wire — pure
  doctrine, and most of a stack's services already have one.
- **Python inspection libs** — `asyncpg`, `redis`, `hvac`, `httpx`, `sqlalchemy`, … in the venv, used
  from `ipython` for richer-than-CLI probing (decode a Redis Stream envelope, pretty-print a
  TimescaleDB hypertable query).

Note the trap this dissolves: there is no `hvac` *command* — you inspect Vault with the `vault` CLI;
python `hvac` is an *app library*, useful only as a scratch convenience. "We can talk to Redis" is
satisfied by `redis-tools` (the CLI), independent of whether python `redis` is in the venv.

### The AI-CLI corollary

This image ships AI coding agents (Claude Code, Codex). They reach for `python -c "import asyncpg…"`
**by default** — so libs-present makes their default correct by construction. "Ship the AI CLIs but
forbid Python / CLI-only" is the inefficient incoherent corner: you pay the agents' (real) attack
surface *and* kneecap them, while relying on probabilistic adherence to negative instructions and
burning turns on failed imports + `pip install` retries (which succeed anyway in a networked cockpit,
falsifying "unavailable on purpose"). Guidance to consuming repos must therefore be **positive**
(here's the inspection kit; ad-hoc `pip install` is fine — this venv is scratch) plus exactly **one
boundary** (the gating suite runs in the test image). Never write "python libs unavailable on
purpose." See [CONSUMER-AI-GUIDANCE.md](CONSUMER-AI-GUIDANCE.md) for the block consumers paste into
their AI instruction files.

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

**Text browsers are fine; JS engines are not.** A no-JS terminal browser (`w3m`, `links2`) is just
"`curl` + an HTML renderer" — low surface, and a real upgrade over staring at `curl` output for
static pages; `w3m` ships in the image. But any **JS-capable** browser (`browsh` → Firefox,
`carbonyl` → Chromium, engine-backed `elinks`) *is* a browser engine — exactly the surface this
section pushes out. "JS must be supported" and "no engine in the cockpit" are mutually exclusive by
construction, so JS-rendered pages go to `pwmcp`, which returns the post-JS DOM / a screenshot while
the engine stays outside the trust boundary.

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
