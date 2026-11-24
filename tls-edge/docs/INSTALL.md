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
    consumer.env                   TLS_EDGE_DOMAIN=<your domain>, written by the CLI
    .git, .gitignore               a one-commit repository so ciu can run (see 9)
    .ciu/                          ciu state (survives updates)
```

Containers started by `ciu up` are `tls-edge-<id>-traefik` and `tls-edge-<id>-dockerproxy`; `<id>` is derived from
the path `/srv/tls-edge-instance/stack`, which is why that path is stable. The compose service name `traefik`
stays a DNS name on the networks. Generic defaults publish **80/tcp and 443/tcp**. HTTP/3 optionally adds
UDP on the same public HTTPS port; the optional Mattermost Calls preset adds its own TCP+UDP pair on 8443.

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
sudo ./ciu check
sudo ./ciu up --dry-run
sudo ./ciu up
```

The install command pins the same release tag used to fetch `get.py`. `get.py` bootstraps the instance and installs the bundled CLI;
it does not collect site settings. `tls-edge configure` then asks for the domain, certificate mode, public ports,
HTTP/3, optional Calls media, network/volume adoption, and runtime placement. It displays the exact changes and
inbound firewall ports before writing the instance override and `consumer.env`.

On a root-run system install, bootstrap creates `shared/acme` as `root:root` with mode `0700`. When the override
does not yet set `[tls_edge.acme] store`, `configure` selects `store = "hostdir"` and writes
`[tls_edge.traefik.hostdir.acme] path = "../../shared/acme"`. That source is outside `stack/`: `ciu --reset`,
`docker compose down -v`, and `ciu clean` do not remove it. A `--scope user` install cannot create a root-owned
directory, so it keeps the volume behaviour.

For unattended configuration, prepare a TOML file and pass it to the CLI:

```bash
sudo /srv/tls-edge-instance/bin/tls-edge configure --answers edge.toml --yes
sudo /srv/tls-edge-instance/bin/tls-edge validate
```

```toml
# edge.toml. Unspecified values keep the currently rendered/default value.
domain = "example.test"
tls_mode = "acme-tls"
acme_email = "ops@example.test"
http_enabled = true
http_port = 80
https_port = 443
http3_enabled = true
calls_enabled = false
bind_ip = ""
ingress_external = false
acme_volume_name = ""
cgroup_parent = ""
```

For `acme-dns`, add `dns_provider` and optionally `dns_wildcard`. For `static` or `dev`, add
`static_cert_base`, `static_domains = ["example.test"]`, and `static_source = "bind"` or `"volume"`. The CLI
never asks for or stores DNS credentials; configure those through CIU secrets as described below.

`get.py` verifies the bundle SHA-256 and the manifest (every file, every wheel), installs the wheels offline
with `--require-hashes`, runs the bootstrap adapter, and only then switches `current`. Do not pipe `get.py` into
`python3 -` if you want `bin/tls-edge-installer`: the self-copy needs a file.

Verify the running edge:

```bash
cd /srv/tls-edge-instance/stack
sudo ./ciu env generate     # first time only; ciu up does it too. Needs the Docker group id:
                            # sudo DOCKER_GID=$(stat -c %g /var/run/docker.sock) ./ciu env generate
sudo ./ciu check            # all stages pass on a fresh install
sudo ./scripts/verify.sh --domain app.example.test
```

`verify.sh` discovers the container and volume names of a ciu instance by itself.

## 4. Configure listeners and certificates

Use `/srv/tls-edge-instance/bin/tls-edge configure` for supported settings. It preserves unrelated site
configuration and comments, and updates only `TLS_EDGE_DOMAIN` in `consumer.env`. Use `--dry-run` to review a
change without writing files. Advanced settings can still be edited in `stack/ciu-stack/ciu.toml.j2`; the full
schema and shipped defaults are in `ciu.defaults.toml.j2`.

The site override is plain TOML and is never templated. A `{{`, `{%`, or `{#` marker in it is refused with the
override path. `tls-edge configure` records `[tls_edge] schema = 1`; an override without the key means schema 1,
and a newer schema is refused. Unknown keys are refused by their full dotted path, for example
`unknown override key tls_edge.http3.enable`. The open tables where provider or site-defined keys are expected
are `[tls_edge.acme.dns.environment]`, `[tls_edge.secrets]`, and `[tls_edge.hooks]`. An override that sets
`[tls_edge.hooks] pre_secrets` must keep `hooks/validate_edge.py` in that list, or it is refused.

`ciu check`, the automatic `ciu up` static preflight, `scripts/render_standalone.py --check`, `tls-edge validate`,
and `tls-edge configure --dry-run` all run this same validation. Limit: ciu runs the validator because the shipped
defaults declare it under `[tls_edge.hooks]` `pre_secrets` and `pre_compose`. An override that empties both lists
removes it from `ciu check` and `ciu up`, so keep the shipped entries (tracked upstream as ciu CIU-226).

| `[tls_edge.tls] mode` | How | Port needed | Certificates |
|---|---|---|---|
| `acme-tls` (default) | TLS-ALPN-01 on :443 | 443 | one per host name |
| `acme-http` | HTTP-01 | 80 | one per host name |
| `acme-dns` | DNS-01 via a lego provider | none | per host, or one wildcard |
| `static` | certbot files under `cert_base` | none | yours |
| `dev` | self-signed (`scripts/dev-certs.sh`) | none | local CA |

**Per host vs wildcard.** The HTTPS entrypoint carries the resolver but no fixed domain list, so every consumer
router's `Host()` rule gets its own certificate the first time it is requested. A wildcard certificate exists
only with `acme-dns` and `[tls_edge.acme.dns] wildcard_main = "example.test"`.

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

**Port 80** is on by default (redirect and HTTP-01). `http_enabled = false` turns it off; `acme-http` then
cannot work. **HTTP/3**: set `http3_enabled = true` to publish UDP on the public HTTPS port; keep TCP open for
HTTP/1.1 and HTTP/2 clients and fallback. **Calls**: set `calls_enabled = true` and `calls_port = 8443` only
when the host has a Calls consumer. Enable the corresponding UDP/TCP rules in both firewalls. Use
`tls-edge show` to print the configured inbound port plan.

## 5. Add a consumer

A consumer is a Compose stack on the external network `ingress_public` with Traefik labels. Start from
`consumer-examples/` (explained label by label in [consumer-examples/README.md](../consumer-examples/README.md)):

```bash
cd consumer-examples/pattern-a-public-only
docker compose --env-file /srv/tls-edge-instance/stack/consumer.env config   # offline check
docker compose --env-file /srv/tls-edge-instance/stack/consumer.env up -d
```

Rules: one host name per service, globally unique router/service names (`<stack>-<service>`), no published ports
80/443/8443 on the consumer. Mattermost Calls media attaches with the TCP and UDP routers of `pattern-e-media-udp`
(one media pair per edge).

## 6. Update

```bash
sudo /srv/tls-edge-instance/bin/tls-edge-installer update --variant py313
cd /srv/tls-edge-instance/stack && sudo ./ciu up
```

The update downloads and verifies the new bundle, builds its venv, swaps `current`, and syncs `stack/`. Files
you edited in the synced set (not `ciu.toml.j2`, which is not part of it) stop the update with exit 2 and a list;
keep host edits in `ciu.toml.j2` or pass `--overwrite-local` (the replaced files are saved under
`shared/overwritten/<UTC time>/`). The installer does not touch `ciu.toml.j2`, `consumer.env`, `.ciu/`, volumes
or `shared/acme`. `tls-edge configure` is the supported writer for the site override and domain variable;
`status` shows the installed tag and any locally modified files.

An upgraded 3.1 instance has no `shared/acme` marker and its override normally has no `acme.store`. It therefore
keeps the same `acme-data` volume as before; `configure` writes `store = "volume"` explicitly. If
`acme.volume_name` names an existing external volume, that volume is still used unchanged. `down -v` and
`ciu --reset` preserve an external volume, but `ciu clean` may remove it when it carries this project's labels;
only the hostdir is guaranteed to survive `ciu clean`.

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
   certificates successfully. A rollback to a release older than 3.2 ignores `store`, mounts an empty
   `acme-data` volume, and forces re-issuance; the hostdir remains on disk for a later roll-forward.

## 7. Roll back

```bash
sudo /srv/tls-edge-instance/bin/tls-edge-installer rollback
cd /srv/tls-edge-instance/stack && sudo ./ciu up
```

`rollback` re-points `current` to the previous release, resyncs `stack/` and the stubs. A 3.2 rollback keeps
`shared/acme`, but a rollback to a release older than 3.2 ignores `acme.store`, starts an empty `acme-data` volume,
and re-issues every certificate. The hostdir remains on disk; roll forward again to reuse it.

## 8. Migrate from a hand-made deployment (PLAN, not executed)

State of the live host today (read only): compose project `edge-proxy` from `/root/tls-edge-LIVE`, containers
`edge-traefik` and `edge-dockerproxy`, volume `edge-proxy_acme-data` (holds `acme.json`: the ACME account and every
issued certificate), network `ingress_public` with consumers attached, `traefik_internal`, `cgroup_parent: edge.slice`,
ports 80, 443, and 8443 tcp+udp plus the alternate HTTPS port being removed. HTTP/3 is not currently enabled.
The replacement can optionally enable HTTP/3 on UDP 443; allow that port through both firewalls before doing so.
The two edges cannot run together because both need TCP 443, so the cut-over is a stop plus start of about
15 to 30 seconds.

1. **Prepare (no downtime).** `install -d -m 0755 /srv/tls-edge-instance`, then install the bundle. Create an
   answers file with the live domain and ACME email and these settings, replacing `example.test` and the email:

   ```toml
   domain = "example.test"
   tls_mode = "acme-tls"
   acme_email = "ops@example.test"
   http_enabled = true
   http_port = 80
   https_port = 443
   http3_enabled = true
   calls_enabled = true
   calls_port = 8443
   ingress_external = true
   acme_volume_name = "edge-proxy_acme-data"
   cgroup_parent = "edge.slice"
   ```

   Run `/srv/tls-edge-instance/bin/tls-edge configure --answers /path/to/live.toml --yes` and
   `/srv/tls-edge-instance/bin/tls-edge validate`. Do **not** run `ciu up` yet. The proposed public set is
   80/tcp, 443/tcp, 443/udp (HTTP/3), and 8443/tcp+udp (Calls); allow these in both firewalls.
2. **Review adoption.** Confirm the generated override contains `ingress_external = true`,
   `volume_name = "edge-proxy_acme-data"`, and `cgroup_parent = "edge.slice"`. The network and volume are
   adopted (`external: true`), never created or removed by the instance.
3. **Dry run.** `cd /srv/tls-edge-instance/stack && sudo ./ciu check && sudo ./ciu up --dry-run`. Compare the
   rendered ports, mounts and cgroup parent with `docker inspect edge-traefik`. Note: `ciu env generate` and
   `up --dry-run` create a Docker network and attach the calling container (ciu CIU-162); detach and remove it
   by its exact name afterwards.
4. **Back up.** Copy the ACME volume with one container and exact names:
   `docker run --rm -v edge-proxy_acme-data:/d:ro -v /root/backup:/b alpine tar czf /b/acme-data-$(date +%F).tgz -C /d .`
   and copy `/root/tls-edge-LIVE` aside.
5. **Cut over (downtime starts).** `cd /root/tls-edge-LIVE && docker compose -p edge-proxy down` (never `-v`; compose
   keeps `ingress_public` because consumers are attached and removes the unused `traefik_internal`), then
   `cd /srv/tls-edge-instance/stack && sudo ./ciu up`. Traefik re-reads `acme.json`: no re-issuance.
6. **Verify.** `scripts/verify.sh --domain <host>`, `curl -I https://<each consumer host>`, one certificate check per
   consumer host (not the default certificate, not expired), an HTTP/3 request from a client with HTTP/3 support,
   a Calls test from outside on 8443 tcp and udp, and one real call.
7. **Rollback (any failure in 6).** `cd /srv/tls-edge-instance/stack && sudo ./ciu down` (no `-v`), then
   `cd /root/tls-edge-LIVE && docker compose up -d`. Volume and network were never touched and the ports are the same;
   downtime is seconds.
8. **Later cleanup** (after one soak and a renewal window): archive `/root/tls-edge-LIVE`; remove old compose-project
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
- The release publishes one bundle per interpreter; publishing goes through a project script until cmru has a
  variants handler (cmru KI-115).

### Releasing tls-edge (maintainers)

- **Version 3.0.0 was released on 2026-11-04.** For the next release, select
  the version explicitly to account for the changed generic Calls default and
  mark breaking changes with `!` or a `BREAKING CHANGE:` footer so conventional
  versioning can identify them.
- Stage the bundled ciu reproducibly: `TLS_EDGE_CIU_VERSION=<x.y.z>` (or `scripts/stage-wheels.py --ciu-version`)
  refuses any other ciu release; without it the newest stable ciu is bundled. The staged ciu version and sha256 are
  printed: copy them into the release notes. PyPI wheels are resolved with pip's `--uploaded-prior-to P14D` cutoff,
  then checked against PyPI upload timestamps. Staging fails before replacing existing wheels if its selected pip
  lacks the cutoff option. If a new minimum dependency is not yet old enough, wait for an eligible release or use
  `--allow-fresh`, which omits the cutoff and age check; justify it in the release notes.
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
(`ciu-stack/ciu.toml.j2`) at install time; this is mandatory migration step 2 in section 8, and the plain-compose
fallback (`edge-proxy/`) has no cgroup parent at all: add it by hand if you use that path.
- An update never changes the instance path, so the ciu identity, container names and volumes stay the same.
