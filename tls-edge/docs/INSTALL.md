# Installing and operating tls-edge

tls-edge is one Traefik v3 container (plus a docker-socket-proxy that denies POST) that terminates TLS for
every service on a host. It is installed as a self-contained instance: `get.py` downloads one release bundle,
verifies it, and builds a private venv from the **bundled** `ciu` and its dependency wheels. Nothing is fetched
from PyPI or GitHub after the download, and the instance runs from a path that never changes between updates.

All examples use `example.test`, a reserved placeholder; use your own domain.

## 1. What you get

```text
/srv/tls-edge-instance/            instance root (system scope)
  bin/ciu                          -> ../current/venv/bin/ciu (the bundled ciu)
  bin/tls-edge                     -> ../current/venv/bin/tls-edge (instance configuration CLI)
  bin/tls-edge-installer           copy of get.py for update / status / rollback
  current                          -> releases/<tag>-<sha12>-<variant>   (atomic switch)
  releases/                        previous releases kept for rollback
  stack/                           THE CIU ROOT: always run ciu from here
    ciu                            stub: execs the ciu pinned by `current`
    ciu-stack/                     templates (synced from the release)
      ciu.toml.j2                  YOUR site override (written by `tls-edge configure`)
    consumer.env                   six effective consumer-contract keys, written by the CLI
    .git, .gitignore               a one-commit repository so ciu can run (see 9)
    .ciu/                          ciu state (survives updates)
```

Containers started by `ciu up` are `tls-edge-<id>-traefik` and `tls-edge-<id>-dockerproxy`; `<id>` is derived from
the path `/srv/tls-edge-instance/stack`, which is why that path is stable. The compose service name `traefik`
stays a DNS name on the networks. The public port set a fresh instance publishes is the one
`ciu-stack/ciu.defaults.toml.j2` ships, adjusted by your override; `tls-edge show` prints it. HTTP/3
optionally adds UDP on the same public HTTPS port. Raw TCP/UDP media is published directly by the application;
examples include Jitsi JVB on UDP 10000 and Mattermost Calls on TCP+UDP 8443.

## 2. Requirements

- Linux x86_64 with Docker (the daemon socket must be usable by root), `git`, and `python3` 3.13 or 3.14.
- DNS: one record per service host name (or one wildcard record) pointing at this host.
- Firewall (host and provider): allow the ports shown by `tls-edge configure` or `tls-edge show`.
- The bundle is per interpreter: `--variant py313` for Python 3.13 (Debian 13), `--variant py314` for 3.14.
  `python3 --version` tells you which; a mismatch fails closed at install (the wheels do not match).

## 3. Install

```bash
install -d -m 0755 /srv/tls-edge-instance
# Fetch get.py from the CLI-enabled RELEASE TAG, not the moving `main` branch (it runs as root).
TAG='tls-edge-vX.Y.Z' # replace with the release tag
curl -fsSLO "https://raw.githubusercontent.com/volkb79-2/vbpub/${TAG}/tls-edge/get.py"
sudo python3 get.py install --version "$TAG" --variant py313
sudo /srv/tls-edge-instance/bin/tls-edge configure
cd /srv/tls-edge-instance/stack
# If Docker has no group entry, use: sudo env DOCKER_GID=<gid> ./ciu env generate
sudo ./ciu env generate
sudo ./ciu check
sudo ./ciu up --dry-run
sudo ./ciu up
```

The install command pins the same release tag used to fetch `get.py`. `get.py` bootstraps the instance and installs the bundled CLI;
it does not collect site settings. `tls-edge configure` then asks for the domain, certificate mode, public ports,
HTTP/3, network/volume adoption, and runtime placement. It displays the exact changes and
inbound firewall ports before writing the instance override and `consumer.env`.

An instance cannot be updated in place across a major version boundary: install the new major fresh, as this section
describes. `get.py install` on a root that already holds an instance runs an update instead, so stop the old edge (it
holds ports 80 and 443) and move its instance root aside, or install into the other scope, before the fresh install.

On a root-run system install, bootstrap creates `shared/acme` as `root:root` with mode `0700`. When the override
does not yet set `[tls_edge.acme] store`, `configure` selects `store = "hostdir"`, unless
`[tls_edge.acme] volume_name` is set: an adopted volume keeps `store = "volume"`, and `ciu clean` can still remove
it (see §6). For the hostdir, `configure` writes `[tls_edge.traefik.hostdir.acme] path = "../../shared/acme"`.
That source is outside `stack/`: `ciu --reset`, `docker compose down -v`, and `ciu clean` do not remove it. A
`--scope user` install cannot create a root-owned directory, so it keeps the volume behaviour.

For unattended configuration, prepare a sparse site override in the schema's own shape and pass it to the
CLI. `--answers` changes only the keys named in the file; `configure` still writes every managed field
explicitly. Schema keys that no field row manages are also accepted and written explicitly; the open
`environment`, `secrets`, and `hooks` tables remain refused:

```bash
sudo /srv/tls-edge-instance/bin/tls-edge configure --answers edge.toml --yes
sudo /srv/tls-edge-instance/bin/tls-edge validate
```

```toml
# edge.toml. Unspecified values keep the currently rendered/default value.
[tls_edge]
domain = "example.test"
ingress_external = true

[tls_edge.tls]
mode = "acme-tls"

[tls_edge.acme]
email = "ops@example.test"
volume_name = "<existing ACME volume>"

[tls_edge.ports]
expose_http = true
http = 80
https = 443

[tls_edge.http3]
enabled = true

[tls_edge.runtime]
cgroup_parent = "<slice>.slice"
```

For `acme-dns`, set `[tls_edge.tls] mode = "acme-dns"` and add `[tls_edge.acme.dns] provider` and optionally
`wildcard_main`. For `static` or `dev`, set the mode and add `[tls_edge.static] cert_base`,
`domains = ["example.test"]`, and `source = "bind"` or `"volume"`. The flat answers file of an earlier schema is
refused, and each old name is reported with its schema-shaped replacement. The CLI never asks for or stores DNS
credentials; configure those through CIU secrets as described below.

`get.py` verifies the bundle SHA-256 and the manifest (every file, every wheel), installs the wheels offline
with `--require-hashes`, runs the bootstrap adapter, and only then switches `current`. Do not pipe `get.py` into
`python3 -` if you want `bin/tls-edge-installer`: the self-copy needs a file.

Verify the running edge:

```bash
cd /srv/tls-edge-instance/stack
sudo ./ciu env generate     # first time only; ciu up does it too. Needs the Docker group id:
                            # sudo env DOCKER_GID=$(stat -c %g /var/run/docker.sock) ./ciu env generate
sudo ./ciu check            # all stages pass on a fresh install
sudo ./scripts/verify.sh --domain app.example.test
```

`verify.sh` discovers the container and volume names of a ciu instance by itself.

## 4. Configure listeners and certificates

Use `/srv/tls-edge-instance/bin/tls-edge configure` for supported settings. It preserves unrelated site
configuration and comments. In `consumer.env`, it rewrites the six tls-edge-owned keys and keeps every other
line, including operator-owned values; missing keys are appended in contract order and duplicate owned lines
are removed. An existing file keeps its mode, while a new file is `0644`. Use `--dry-run` to review a change
without writing files. Advanced settings can still be edited in `stack/ciu-stack/ciu.toml.j2`; the full schema
and shipped defaults are in `ciu.defaults.toml.j2`.

The six keys are `TLS_EDGE_DOMAIN`, `TLS_EDGE_NETWORK`, `TLS_EDGE_HTTPS_ENTRYPOINT`,
`TLS_EDGE_HEADERS_MIDDLEWARE`, `TLS_EDGE_HTTP3` and `TLS_EDGE_PUBLIC_PORTS`. Their values are defined once in
[CONSUMERS.md](CONSUMERS.md#rendered-consumer-contract). Read the deployed values with
`tls-edge show --json`; do not infer them from templates.

`tls-edge validate` and the CIU preflight hook refuse an existing `consumer.env` when an owned key is missing
or differs from the effective site configuration:

```text
consumer.env TLS_EDGE_DOMAIN differs from the site override; run tls-edge configure to re-sync
```

`tls-edge show` reports the drift without failing, and `tls-edge configure` re-syncs it. An absent file is not
checked. To re-sync without changing any answer, use an empty answers file:

```bash
printf '' > /tmp/tls-edge-empty.toml
sudo /srv/tls-edge-instance/bin/tls-edge configure --answers /tmp/tls-edge-empty.toml --yes
```

`configure` writes every managed field whose mode applies to the proposed configuration. This keep-list makes
a later change to a shipped default harmless for a configured instance. Prompt defaults are the current
effective values from the rendered defaults plus the site override; there is no second source of literal
defaults.

The CLI's `--root` is a stack directory: any directory that directly contains `ciu-stack/`. On an installed
instance that directory is `/srv/tls-edge-instance/stack`; in a repository checkout it is `tls-edge/`.
`consumer.env` is optional for a checkout, so `validate`, `show`, and `configure --dry-run` work without it. A
directory that does not contain `ciu-stack/` is refused with the expected defaults path in the message.

The site override is plain TOML and is never templated. A `{{`, `{%`, or `{#` marker in it is refused with the
override path. `tls-edge configure` records `[tls_edge] schema = 2`; an override without the key means schema 1,
and a newer schema is refused. Unknown keys are refused by their full dotted path, for example
`unknown override key tls_edge.http3.enable`. The open tables where provider or site-defined keys are expected
are `[tls_edge.acme.dns.environment]`, `[tls_edge.secrets]`, and `[tls_edge.hooks]`. An override that sets
`[tls_edge.hooks] pre_secrets` must keep `hooks/validate_edge.py` in that list, or it is refused.

`ciu check`, the automatic `ciu up` static preflight, the maintainer-only
`tools/render_standalone.py --check`, `tls-edge validate`,
and `tls-edge configure --dry-run` all run this same validation. Limit: ciu runs the validator because the shipped
defaults declare it under `[tls_edge.hooks]` `pre_secrets` and `pre_compose`. An override that empties both lists
removes it from `ciu check` and `ciu up`, so keep the shipped entries (tracked upstream as ciu CIU-226).

`[tls_edge.tls] mode` is one of the five the schema declares
(`ciu-stack/ciu.defaults.toml.j2` carries each mode and its constraints, including which listener it
needs); `tls-edge show` reports the mode and port plan of a configured instance.

**Per host vs wildcard.** The HTTPS entrypoint carries the resolver but no fixed domain list, so every consumer
router's `Host()` rule gets its own certificate the first time it is requested. An automatic wildcard
certificate exists with `acme-dns` and `[tls_edge.acme.dns] wildcard_main = "example.test"`; static mode
supports a manually renewed wildcard instead.

**Static-mode certbot hook.** Install the hook once on the host:

```bash
ln -sf /srv/tls-edge-instance/stack/scripts/certbot-deploy-hook.sh \
  /etc/letsencrypt/renewal-hooks/deploy/01-reload-traefik.sh
certbot renew --dry-run
```

After a renewal the hook finds the Traefik container by its Compose service label and installed-instance name
pattern, then runs `docker restart --time 5 <name>`. Expect about 3 seconds of edge downtime. It does not write
or rewrite any file in the instance; when no matching container is running it prints a message and exits 0.
The stable symlink follows the hook shipped in `stack/scripts/`: an `update` refreshes the file it points at,
so later instance updates take effect without another copied hook.

**Staging CA** while testing: `[tls_edge.acme] ca_server = "https://acme-staging-v02.api.letsencrypt.org/directory"`
(switch back and remove the ACME volume contents before going live, or the staging account is reused).

**DNS credentials (`acme-dns`)** are CIU secrets, never literals in the override:

```toml
[tls_edge.tls]
mode = "acme-dns"
[tls_edge.acme.dns]
provider = "cloudflare"
wildcard_main = "example.test"
[tls_edge.secrets]
dns_api_token = { directive = "ASK_EXTERNAL:CF_DNS_API_TOKEN", mode = "0444" }
[tls_edge.acme.dns.environment]
CF_DNS_API_TOKEN_FILE = "/run/secrets/dns_api_token"
```

`ciu` asks for the token on `ciu up` (or reads `CF_DNS_API_TOKEN` from the environment). Add this block to the
site override after running the CLI; secret references are intentionally kept out of wizard answers.

The HTTP listener is on in the shipped defaults. Turn it off with `[tls_edge.ports] expose_http = false`;
`acme-http` then cannot work. **HTTP/3**: set `[tls_edge.http3] enabled = true` to publish UDP on the public
HTTPS port; keep TCP open for HTTP/1.1 and HTTP/2 clients and fallback. **Raw media** stays outside
tls-edge: the application publishes its own TCP/UDP ports directly and advertises that endpoint to clients.
Use `tls-edge show` to print the edge's configured inbound port plan.

## 5. Add a consumer

A consumer is a Compose stack on the external network `ingress_public` with Traefik labels. Start from
`consumer-examples/` (explained label by label in [consumer-examples/README.md](../consumer-examples/README.md)):

```bash
cd consumer-examples/pattern-a-public-only
docker compose --env-file /srv/tls-edge-instance/stack/consumer.env config   # offline check
docker compose --env-file /srv/tls-edge-instance/stack/consumer.env up -d
```

Rules: one host name per service, globally unique router/service names (`<stack>-<service>`), and no
published HTTP ports 80/443 on the consumer. Raw TCP/UDP media is published directly by the application;
its host port pair must be disjoint from `TLS_EDGE_PUBLIC_PORTS`, as in `pattern-e-media-udp`.

## 6. Update

`bin/tls-edge-installer` is a copy of the release's `get.py`, so `update` targets `/srv/tls-edge-instance` by
default; pass `--scope user` for a user-scope instance. It reuses the installed interpreter variant; pass
`--variant py313` or `--variant py314` only to override that selection.

```bash
printf '' > /tmp/tls-edge-empty.toml
sudo /srv/tls-edge-instance/bin/tls-edge-installer update --variant py313
sudo /srv/tls-edge-instance/bin/tls-edge configure --answers /tmp/tls-edge-empty.toml --yes
cd /srv/tls-edge-instance/stack && sudo ./ciu up
```

The update downloads and verifies the new bundle, builds its venv, swaps `current`, and syncs `stack/`. Files
you edited in the synced set (not `ciu.toml.j2`, which is not part of it) stop the update with exit 2 and a list;
keep host edits in `ciu.toml.j2` or pass `--overwrite-local` (the replaced files are saved under
`shared/overwritten/<UTC time>/`). The installer does not touch `ciu.toml.j2`, `consumer.env`, `.ciu/`, volumes
or `shared/acme`. `tls-edge configure` is the supported writer for the site override and domain variable;
`status` shows the installed tag and any locally modified files. A user-scope instance lives under
`$HOME/.local/share/tls-edge-instance`; its `tls-edge-installer status` also needs the matching scope:

```bash
tls-edge-installer status --scope user
```

Run `tls-edge configure --answers <empty file> --yes` after every update, before the next `ciu up`; otherwise
the preflight hook refuses a `consumer.env` that lacks an owned contract key. `configure` also rewrites the
managed override fields, so the override is written in the schema of the installed release.
An edge that still runs its Calls preset holds 8443 on that host; disable that preset before Mattermost's
`ciu up` publishes Calls media directly (see 8).

An instance whose override adopts an existing volume keeps it: `acme.volume_name` names that exact external
volume and `configure` writes `store = "volume"` explicitly. `down -v` and `ciu --reset` preserve an external
volume, but `ciu clean` may remove it when it carries this project's labels; only the hostdir is guaranteed to
survive `ciu clean`.

To move an existing instance to the durable hostdir manually:

1. `cd /srv/tls-edge-instance/stack && sudo ./ciu down`
2. Create the destination, then copy `acme.json` from the exact existing volume with one exact-name container:

   ```bash
   sudo install -d -m 0700 -o root -g root /srv/tls-edge-instance/shared/acme
   sudo docker run --rm --name tls-edge-acme-copy \
     -v '<current acme volume>:/source:ro' \
     -v /srv/tls-edge-instance/shared/acme:/destination \
     alpine sh -c 'cp -p /source/acme.json /destination/acme.json'
   ```

3. Set this in the site override (clear `volume_name` if it was used):

   ```toml
   [tls_edge.acme]
   store = "hostdir"
   volume_name = ""
   [tls_edge.traefik.hostdir]
   acme = { path = "../../shared/acme", uid = 0, gid = 0, mode = "0700" }
   ```

4. Run `sudo ./ciu check && sudo ./ciu up`. Keep the old volume until the new edge has issued or reused its
   certificates successfully. A rollback keeps `acme.store`, so the hostdir you migrated to stays in use; the
   hostdir also remains on disk for a later roll-forward.

## 7. Roll back

```bash
sudo /srv/tls-edge-instance/bin/tls-edge-installer rollback
cd /srv/tls-edge-instance/stack && sudo ./ciu up
```

`rollback` re-points `current` to the previous release, resyncs `stack/` and the stubs. A rollback keeps
`acme.store`, so a hostdir instance goes on using `shared/acme`; the hostdir remains on disk, so roll forward again
to reuse it.

## 8. Migrate from a hand-made deployment (PLAN, not executed)

State of the live host today (read only): compose project `edge-proxy` from `/root/tls-edge-LIVE`, containers
`edge-traefik` and `edge-dockerproxy`, volume `edge-proxy_acme-data` (holds `acme.json`: the ACME account and every
issued certificate), network `ingress_public` with consumers attached, `traefik_internal`, `cgroup_parent: edge.slice`,
ports 80, 443, and 8443 tcp+udp for Calls plus the alternate HTTPS port being removed. HTTP/3 is not
currently enabled. The replacement can optionally enable HTTP/3 on UDP 443; allow that port through both
firewalls before doing so.
The two edges cannot run together because both need TCP 443, so the cut-over is a stop plus start of about
15 to 30 seconds.

1. **Prepare (no downtime).** `install -d -m 0755 /srv/tls-edge-instance`, then install the bundle. Create an
   answers file with the live domain and ACME email and these settings, replacing `example.test` and the email:

   ```toml
   [tls_edge]
   domain = "example.test"
   ingress_external = true

   [tls_edge.tls]
   mode = "acme-tls"

   [tls_edge.acme]
   email = "ops@example.test"
   volume_name = "edge-proxy_acme-data"

   [tls_edge.ports]
   expose_http = true
   http = 80
   https = 443

   [tls_edge.http3]
   enabled = true

   [tls_edge.runtime]
   cgroup_parent = "edge.slice"
   ```

   Run `/srv/tls-edge-instance/bin/tls-edge configure --answers /path/to/live.toml --yes` and
   `/srv/tls-edge-instance/bin/tls-edge validate`. Do **not** run `ciu up` yet. The proposed edge port set is
   80/tcp, 443/tcp, and 443/udp (HTTP/3); allow these in both firewalls. Mattermost's direct media pair on
   8443/tcp+udp is separate from the edge.
   `ciu` 7.28.0 and later evaluates its S6.5 helper preconditions BEFORE creating any hostdir, and `--dry-run`
   creates none at all, so a refused `ciu up` (missing `CGROUP_PARENT_DEV_INTERACTIVE`, an incompatible existing
   hostdir, a mistyped `seed`) leaves the filesystem untouched; fix the precondition and retry.  Do not reach for
   `ciu up --skip-check` instead: it does not address the refusal and hides the hook's verdict.
2. **Review adoption.** Confirm the generated override contains `ingress_external = true`,
   `volume_name = "edge-proxy_acme-data"`, and `cgroup_parent = "edge.slice"`. The network and volume are
   adopted (`external: true`), never created or removed by the instance.
3. **Dry run.** `cd /srv/tls-edge-instance/stack && sudo ./ciu env generate && sudo ./ciu check && sudo ./ciu up --dry-run`. Compare the
   rendered ports, mounts and cgroup parent with `docker inspect edge-traefik`. Note: `ciu env generate` and
   `up --dry-run` create a Docker network and attach the calling container (ciu CIU-162); detach and remove it
   by its exact name afterwards.
4. **Back up.** Copy the ACME volume with one container and exact names:
   `docker run --rm -v edge-proxy_acme-data:/d:ro -v /root/backup:/b alpine tar czf /b/acme-data-$(date +%F).tgz -C /d .`
   and copy `/root/tls-edge-LIVE` aside.
5. **Cut over (downtime starts).** `cd /root/tls-edge-LIVE && docker compose -p edge-proxy down` (never `-v`; compose
   keeps `ingress_public` because consumers are attached and removes the unused `traefik_internal`), then
   `cd /srv/tls-edge-instance/stack && sudo ./ciu up`. Traefik re-reads `acme.json`: no re-issuance.
6. **Start direct media.** From `voice-screen-share/mattermost-server`, run its `ciu up` so Mattermost binds
   8443 directly. Follow that project's [`CONSUMER.md`](../../voice-screen-share/mattermost-server/CONSUMER.md#calls-cut-over)
   "Calls cut-over" section. Run this only after the old edge is stopped and the new edge is up.
7. **Verify.** `scripts/verify.sh --domain <host>`, `curl -I https://<each consumer host>`, one certificate check per
   consumer host (not the default certificate, not expired), an HTTP/3 request from a client with HTTP/3 support,
   and a Mattermost media test from outside on its directly published 8443 tcp and udp ports.
8. **Rollback (any failure in 7).** Mattermost's Calls media is published by the `mattermost` service itself, so
   there is no separate process to stop: stopping "the process that binds 8443" stops the `mattermost` service, and
   its web UI on 8065 goes down with it. `ciu down` at the stack root (no `-v`) does the same and is the supported
   way; there is no selective per-port stop. `cd /srv/tls-edge-instance/stack && sudo ./ciu down` (no `-v`) stops the
   tls-edge instance. `ciu down` only stops containers; to remove them as well, use
   `docker compose -p <project> down` (never `-v`), where `<project>` comes from
   `docker inspect <container_prefix>-traefik --format '{{index .Config.Labels "com.docker.compose.project"}}'`
   (`container_prefix` is in `ciu-stack/ciu.toml`).
   The old edge publishes 80, 443, and 8443 TCP+UDP, so Mattermost's direct 8443 publication must be stopped before
   restarting it. Run `cd /root/tls-edge-LIVE && docker compose up -d`. Volume and network were never touched;
   downtime is seconds.
9. **Restore Mattermost after rollback.** Check out `voice-screen-share/mattermost-server` at its revision from
   before the cut-over, then run `ciu up --dir .` inside that directory. That revision has no direct 8443
   publication, so its web service can use the restored old edge without a port collision.
10. **Later cleanup** (after one soak and a renewal window): archive `/root/tls-edge-LIVE`; remove old compose-project
   leftovers by exact name. Keep `ingress_external = true` as long as the network predates the instance.

**Impact on consumers.** Networks and labels are unchanged. The container name changes from `edge-traefik` to
`tls-edge-<id>-traefik` (the service name `traefik` still resolves on the networks). In this repository nothing but
tls-edge's own scripts refers to `edge-traefik` by name (`scripts/verify.sh` discovers names;
`scripts/certbot-deploy-hook.sh` takes `TRAEFIK_CONTAINER`); a host script or runbook that runs
`docker exec edge-traefik ...` must be updated. The alternate HTTPS port is gone: confirm no consumer uses
`https://<host>:<former alternate port>` before the cut-over (none is known).

## 9. Notes and limits

- `stack/` is a git repository with one empty commit because ciu derives its identity from git and cannot run in a
  non-git root (ciu backlog CIU-163; the installer adapter creates it). Do not delete `.git`.
- `ciu env generate` and `ciu up --dry-run` create/attach a Docker network (CIU-162): run them on the host, not
  from a devcontainer you care about.
- A non-root CIU run that creates `vol-traefik-acme` through the S6.5 root helper needs
  `CGROUP_PARENT_DEV_INTERACTIVE` exported, naming a LOADED slice unit such as `dev-interactive.slice`
  (`systemctl list-units --type=slice` lists candidates). ciu >= 7.28.0 refuses without it and the refusal
  names the variable, the `.slice` shape and that command (CIU-230); there is no fallback slice.
- `ciu up --dry-run` creates no hostdir from ciu >= 7.28.0: it prints `Would create ...` and resolves every
  declaration (CIU-227). A refused `ciu up` leaves nothing behind on the same version, because the S6.5 helper
  preconditions are checked before the first `mkdir`. Known limit: a failure AFTER `mkdir` that is not a
  precondition — the helper's `docker run`, a seed `copytree` — can still leave a runner-owned directory.
- A `stack/ciu-stack/vol-traefik-acme` owned by root is the legacy case of an instance created by ciu < 7.28.0
  (a refused run there could leave it), or a failure after `mkdir`; delete it with root before removing a
  user-scope instance.
- The release publishes one bundle per interpreter through `cmru handler bundle-build` and
  `cmru handler tarball-publish --variant`.

### Releasing tls-edge (maintainers)

- Version prose belongs in the pending release notes, not in `cmru.toml`'s headline or this document. Mark
  breaking changes with `!` or a `BREAKING CHANGE:` footer so conventional versioning can identify them,
  and select the target version explicitly when releasing.
- Build with `cmru handler bundle-build --bundle-config bundle.toml --cmru-config
  ../cmru.orchestration.toml --name tls-edge --variant all`. The archive names, file allowlist, installer
  manifest and deterministic `xztar` writer come from `bundle.toml`; the first-party fetcher is
  `tools/bundle_wheels.py`. The build step derives `TLS_EDGE_VERSION` from the
  committed `VERSION` file, clears stale `dist/` output first and fails if the
  variable is absent. `TLS_EDGE_CIU_VERSION=<x.y.z>` and
  `TLS_EDGE_CLI_EXTENDED_VERSION=<x.y.z>` optionally pin the GitHub-only first-party wheels.
  Build release bundles only from a clean tree (`cmru release` does); a dirty checkout's untracked
  files under `ciu-stack/` and `consumer-examples/` would be bundled.
  `python3 tools/bundle_wheels.py requirements` resolves the third-party closure under the
  14-day upload buffer and writes exact pins to `requirements-bundle.txt`; cmru vendors
  that lock for each declared interpreter.
- CMRU's archive writer uses the runner's `SOURCE_DATE_EPOCH` and normalised owner, mode and
  ordering. Identical inputs and epoch produce byte-identical archives. The upload-age policy
  applies once, when the committed lock is generated, so a bundle build resolves no floating
  third-party versions.
- Tests: `run-gate tls-edge tests` (lane `tests` in `run-gate.toml`, tester-unified). A missing cmru/jinja2/docker
  fails the lane; only `TLS_EDGE_ALLOW_INSTALLER_SKIP=1` skips the installer test, deliberately, outside the lane.

### Trust and signing

Releases are currently **unsigned**: `get.py` ships with an empty `MANIFEST_PUBKEY`, so no minisign signature is
checked (as for pwmcp). What is verified: the bundle SHA-256 sidecar, then the in-bundle `manifest.json` (every
file and every wheel), then `pip --require-hashes`. The sidecar and manifest come from the same GitHub release as the
bundle, so they detect corruption and partial tampering but are **not an independent trust anchor**: whoever can
replace the release assets can replace all three. Likewise the first `curl` of `get.py` is trust-on-first-use
(HTTPS to GitHub; pinned to the release tag above, not to a hash). Because `get.py` then runs as root and starts
Docker, read it before running it (it is one file). Decision: a minisign
key (cmru signature policy) is wanted before tls-edge is used beyond the operator's own hosts; until it exists,
treat the release page as the trust boundary.

### Resource governance

The ciu default is `[tls_edge.runtime] cgroup_parent = ""` so an install works on hosts without the slice. On a host
that runs under the shared governance rule, set `cgroup_parent = "edge.slice"` in the override
(`ciu-stack/ciu.toml.j2`) at install time; this is mandatory migration step 2 in section 8. The generated
`edge-proxy/` trial tree is not the supported deployment and has no cgroup parent by default.
- An update never changes the instance path, so the ciu identity, container names and volumes stay the same.
