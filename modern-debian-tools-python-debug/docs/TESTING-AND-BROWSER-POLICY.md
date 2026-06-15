# Testing and Browser Policy

## Browser-free policy

**Do not add browser packages to this image.** This means no:
- `playwright`, `chromium`, `selenium`, `puppeteer` Python packages
- Browser OS libraries: `libnss3`, `libgbm-dev`, `fonts-*`, `libatk*`, `libxss1`, etc.
- Any package installed by `playwright install` or `playwright install-deps`

Rationale: browsers are a large attack surface and pull in hundreds of OS packages.
The devcontainer holds secrets (tokens, SSH keys, cloud credentials); browser code
must not share that trust boundary.

### UI / browser tests — use external Playwright-as-a-Service

Browser tests connect to an external **pwmcp** service (see `../pwmcp`) over the
wire. The service exposes two endpoints, addressed by container name on a shared
Docker network (never `localhost`):

| Purpose | Env var | Value |
|---------|---------|-------|
| Test runner WebSocket | `PLAYWRIGHT_SERVER_WS` | `ws://<pwmcp-playwright-container>:3000/` |
| AI / MCP endpoint | *(direct URL)* | `http://<pwmcp-mcp-container>:8931/mcp` |

The test runner only needs the Playwright client library — **no browser binaries**:

```bash
# Match the version pinned in the pwmcp image
pip install playwright==<version>
# Do NOT run: playwright install
```

The `playwright` package connects to `PLAYWRIGHT_SERVER_WS` and streams test
execution to the remote browser process.

---

## Local multi-version Python workflow

The image ships `uv` for hermetic interpreter management. Install the target
interpreters once per devcontainer lifetime:

```bash
uv python install 3.9 3.11 3.13
```

### One-off runs against a specific version

```bash
uv run --python 3.9 pytest tests/
```

### Matrix runs with tox

Minimal `tox.ini` covering the common target set:

```ini
[tox]
envlist = py39, py311, py313

[testenv]
deps = .[test]
commands = pytest {posargs}
```

Run the full matrix:

```bash
uv run tox
```

The local matrix exists for **fast reproduction** (e.g. chase a 3.9-only failure
without waiting for CI). The authoritative gate is the project's CI matrix — a
local green run is a convenience, not a ship signal.

---

## Why dev Python ≠ prod Python

Develop on the modern Python (3.13/3.14) that ships in the image — modern tooling,
better error messages, faster type inference in editors.

Build and test *release artifacts* on the real target versions (3.9, 3.11, …) using
the `uv`/`tox` workflow above.

**"Green on 3.13" never implies "ships on 3.9."**
Version-specific failures (syntax, stdlib changes, backport gaps) only surface when
you actually run the target interpreter. CI must cover each declared minimum.
