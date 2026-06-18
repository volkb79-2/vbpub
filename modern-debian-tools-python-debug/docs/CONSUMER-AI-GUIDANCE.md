# Consumer AI Guidance

> What a repo that *consumes* this base image should put into its own AI-agent instruction files
> (`AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`). This file is intentionally **not**
> named like those canonical files — copy the relevant parts into yours, adapted to your services.

The companion to this doc is [CONTAINER-DOCTRINE.md](CONTAINER-DOCTRINE.md), which explains *why*. This
one is operational: the cheat-sheet, the one boundary, and copy-paste blocks.

## 1. The cockpit's role (state this first)

The devcontainer built on `modern-debian-tools-python-debug(-vsc-devcontainer)` is a **cockpit**: a
lean, secret-bearing place to **inspect** running services and **drive** deploys. It is *not* where
your app runs and *not* where the gating test suite runs. Two reasons: it holds tokens/keys (keep the
surface small), and its Python venv is a *debug toolkit*, not your app's dependency closure (so its
pass/fail can't be trusted as a ship signal).

## 2. Inspection cheat-sheet (positive affordances)

Tell your agent what *is* available, by protocol — agents inspect best when pointed at the right tool:

| Talk to… | Use |
|---|---|
| PostgreSQL / TimescaleDB | `psql` |
| Redis | `redis-cli` |
| Vault | `vault` CLI (not python `hvac`) |
| Consul | `consul` CLI |
| S3 / MinIO | `aws --endpoint-url http://<host>:<port> s3 …` |
| HTTP / REST | `http` (httpie), `curl` |
| gRPC (e.g. SkyWalking/OTel) | `grpcurl` |
| DNS | `dig` / `nslookup` |
| **Static HTML** (read a page instead of curl) | `w3m` |
| **JS-rendered UI** | **drive `pwmcp`** (Playwright-as-a-Service) — never a browser engine in the cockpit |

For richer-than-CLI probing, quick Python in `ipython` is fine and encouraged:
`asyncpg`, `redis`, `hvac`, `httpx`, `sqlalchemy`, `dnspython`, `websockets`, `requests`, `boto3` ship
in the venv. Ad-hoc `pip install <x>` for a debug session is fine — **this venv is scratch**, not a
shipped artifact.

Always resolve service hostnames from your config (e.g. a `config_helper`/`url_builder`), never
hardcode — and address services by **container name on the shared network**, never `localhost`.

## 3. The one boundary (the only prohibition you need)

> **The gating test suite runs in a test image, not in the cockpit.** Build it `FROM <your-app-base>`
> (the image that carries your runtime dependency closure) plus test-only extras, and run it as a
> peer on the app network. "Green in the devcontainer venv" is **not** a ship signal — that venv
> carries the base image's library pins, not your app's. Unit tests belong in the test image too
> (run it with the stack *down*): the image is what makes the dependency closure honest, not the
> services.

Do **not** write "python libs are unavailable on purpose" — they're present for inspection, and
fighting your agent's natural reach for Python wastes turns. One positive kit + one boundary is enough.

## 4. Copy-paste block for your AGENTS.md / CLAUDE.md

```markdown
## Cockpit vs. ship-gate
The devcontainer is a COCKPIT — inspect the running stack and drive deploys, never run the
gating suite here. Inspect with CLIs (psql, redis-cli, vault, consul, `aws --endpoint-url` for
MinIO, dig, grpcurl, http/curl, w3m for static HTML) or quick Python in ipython
(asyncpg/redis/hvac/httpx/sqlalchemy). JS-rendered UI → drive pwmcp; never put a browser
engine in the cockpit. Resolve service names from config (never hardcode; never localhost).

The GATING suite runs in the `<test-runner>` image (FROM <app-base> → identical runtime
closure), launched via `<your test wrapper>`. "Green in the devcontainer venv" is NOT a ship
signal — it carries the base image's pins, not the app's. Ad-hoc `pip install` here is fine;
it's a scratch venv.
```

## 5. Test-image template

Minimal gating test image (closure ≡ runtime closure) and a compose peer on the app network:

```dockerfile
# tests/Dockerfile  (or tools/test-runner/Dockerfile)
FROM <your-app-base>:latest          # inherits the EXACT runtime pins
RUN pip install --no-cache-dir \
    pytest pytest-asyncio pytest-cov pytest-mock httpx fakeredis   # test-only extras
WORKDIR /workspace
ENV PYTHONPATH=/workspace
ENTRYPOINT ["/bin/bash", "-lc"]
CMD ["pytest"]
```

```yaml
# a test-runner service on the SAME external app network
services:
  test-runner:
    image: <ns>/test-runner:latest
    networks: [<app-network>]              # external; address peers by container name
    environment: [RUN_LIVE_TESTS, MOCK_MODE]
    volumes: ["<host-repo>:/workspace:rw"]  # mount source; data lives in the services, not here
    working_dir: /workspace
    profiles: [test]                        # never starts with the normal stack
```

Run it (don't run pytest in the cockpit):

```bash
# integration/e2e against the live stack
RUN_LIVE_TESTS=1 docker compose --profile test run --rm test-runner pytest -m "integration or e2e"
# unit tests — no services needed, still in the image (truthful closure)
docker compose run --rm --no-deps test-runner pytest tests/unit -m unit
```

**Don't mount service data dirs** (Postgres `pgdata`, Redis AOF, MinIO `/data`) into the test image —
reach that data through the service protocol (SQL/RESP/S3), not the filesystem. The only mount the
test image needs is your **source**; the only exception is a volume that is itself the app's contract
(e.g. a shared spool dir). Keep test data isolated (a test DB/schema or a separate Redis db-index) so
runs against shared instances don't pollute live data.
