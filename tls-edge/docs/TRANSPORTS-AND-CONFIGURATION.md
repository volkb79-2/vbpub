# Transports, live events, and instance configuration

This guide records the transport choices discussed for `tls-edge`, shows how
consumer stacks opt in with Traefik labels, and explains where each setting is
owned. `tls-edge` is a reusable template. Its current Mattermost Calls example
and defaults are preconfiguration, not a requirement that every deployment
must serve Mattermost.

## Current shape

The intended request path is:

```text
Browser -- HTTPS over TCP (HTTP/2 or HTTP/1.1) --> Traefik :443
        -- plain HTTP over the Docker ingress network --> application
```

The tracked `websecure` entrypoint is a shared HTTPS listener on TCP 443. TLS
and certificates terminate at Traefik. The generic default keeps HTTP/3 off;
an operator can enable it for the shared endpoint with `tls-edge configure`,
which also publishes UDP on the public HTTPS port. HTTP/2 and HTTP/1.1 stay
available on TCP. The app-facing connection is plain HTTP by default; Traefik
does not make a second TLS connection to the app unless the service is
explicitly configured to use HTTPS.

The generic shipped public port set is 80/TCP (HTTP redirect and, when
selected, HTTP-01) and 443/TCP (HTTPS). Enabling HTTP/3 adds 443/UDP by default.
Enabling the Calls preset adds 8443/TCP+UDP. Every HTTP consumer shares the
same HTTPS listener and Traefik selects the app by host name.

The templates contain an optional Mattermost Calls preset: a raw TCP and UDP
media pair on public port 8443, disabled by default. Enable it only for a Calls
consumer. It is separate from HTTPS and does not change how HTTP consumers are
routed.

With HTTP/3 enabled, the request path becomes:

```text
Browser -- HTTP/3 over QUIC/UDP + TLS --> Traefik
        -- plain HTTP over a pooled TCP connection --> application
```

## HTTP/1.1, HTTP/2, HTTP/3, and QUIC

| Client to edge | Network transport | What it means for this setup |
|---|---|---|
| HTTP/1.1 | TCP + TLS | Broad fallback and compatibility path. |
| HTTP/2 (`h2`) | TCP + TLS | Multiple request streams share a TCP connection; this is already available on the HTTPS entrypoint unless TLS options explicitly remove `h2` from ALPN. |
| HTTP/3 (`h3`) | QUIC over UDP + TLS 1.3 | Optional client-to-Traefik transport, disabled in generic defaults. |

HTTP/3 uses QUIC over UDP and requires TLS 1.3 or newer. It is not ordinary
cleartext HTTP over UDP. The browser and Traefik still use the normal HTTPS
origin, certificate, host name, cookies, and HTTP headers.

HTTP/2 and HTTP/3 both multiplex requests. HTTP/3's QUIC transport avoids
TCP's connection-wide loss blocking across independent HTTP streams and can
resume or migrate a connection more smoothly when a client network changes.
Those properties can help on lossy or mobile networks. They do not guarantee
lower latency for every request, and they do not make the same-host backend
leg faster by themselves. The useful default is HTTP/2 and HTTP/1.1 over TCP,
with HTTP/3 as an optional edge transport when UDP reachability and measured
client benefit justify it.

### Enabling HTTP/3 at the edge

Traefik configures HTTP/3 on a TCP entrypoint. When enabled for `websecure`, it
also listens for QUIC on UDP using the entrypoint's internal port. Docker
publishes UDP on the same public port as TCP, and the host firewall and any
provider firewall must permit it. The TCP mapping stays in place for HTTP/2
and HTTP/1.1 clients and for fallback when a client network blocks UDP.

A rendered Traefik config would look like this:

```yaml
entryPoints:
  websecure:
    address: ":443"
    http3: {}
    http:
      tls:
        certResolver: letsencrypt
```

The Compose service would publish both protocol sockets:

```yaml
ports:
  - "443:443/tcp"
  - "443:443/udp"
```

The configuration derives `http3.advertisedPort` from the public HTTPS port.
This supports a host mapping such as public UDP 9443 to Traefik's internal 443.
The CLI checks conflicts between TCP and UDP listeners before writing changes;
for example, a Calls pair on 9443 cannot share the public HTTPS port 9443.

Traefik advertises HTTP/3 through `Alt-Svc` responses when HTTP/3 is enabled.
For a browser with no cached alternative and no usable HTTPS DNS record, the
usual sequence is an initial TCP HTTPS request, receipt of `Alt-Svc: h3=...`,
then a separate QUIC connection attempt. The browser does not convert the
existing TCP connection into UDP. It can keep using TCP or fall back to it if
QUIC cannot connect. A browser with a usable cached `Alt-Svc` entry or HTTPS
DNS record may try QUIC before making that first TCP request.

DNS A/AAAA records only map a name to an address; they do not announce an HTTP
transport. An HTTPS DNS record (type 65) can advertise ALPN values such as
`h3` and a port before the HTTP request begins, for example:

```dns
app.example.test. 3600 IN HTTPS 1 . alpn="h3,h2" port=443
```

This is an optional discovery hint, not a command that forces the browser to
use QUIC. The DNS provider must support HTTPS records, the client's resolver
and browser must use them, and the advertised listener must really be
reachable. Resolver behavior varies: some clients use platform DNS, while
others can use browser-managed DNS-over-HTTPS. Keep TCP 443 available for
compatibility and fallback.

### Can the public endpoint be UDP-only?

It is possible to build a private or controlled client deployment around an
HTTP/3-only public policy, but it is a poor default for a public browser URL.
Traefik's HTTP/3 option is attached to a TCP entrypoint; an operator could
filter TCP at the firewall and allow UDP, but that removes fallback rather
than creating a separate simpler listener. A browser opening `https://host/`
normally needs a TCP HTTPS path to discover an `Alt-Svc` alternative unless
it already has a usable cached alternative or HTTPS DNS record. HTTPS DNS
records can let supporting clients try HTTP/3 first, but they do not guarantee
that every browser or network will do so. If UDP is blocked or discovery is
unsupported, there is no TCP path to fall back to and the site fails. Keep TCP
443 for the normal public service.

### Is HTTP/3 a per-service label?

No. In this design `websecure` is one static Traefik entrypoint shared by all
HTTP routers. HTTP/3 is configured on that entrypoint, not on each Docker
container. Consumer labels select a host, route, middleware, and backend; they
do not open UDP 443 or enable QUIC. Once enabled on `websecure`, all TLS routers
using that entrypoint can be served over HTTP/3. A response-header middleware
can suppress `Alt-Svc` for an exceptional route, but it does not disable the
shared UDP listener and is not the normal configuration mechanism.

### Does an app need to change?

Not for HTTP/3 at the edge. Traefik accepts HTTP/3 from the browser, decrypts
the request, and forwards ordinary HTTP to the app. The app continues to
handle HTTP requests and the same authentication headers, cookies, and
sessions as before. The public QUIC connection is not exposed to the app as a
QUIC socket.

Traefik handles each HTTP request or stream independently and reuses upstream
connections in pools for each backend server. One browser QUIC connection
does not imply one backend TCP connection per browser or per user. A pooled
backend connection can carry requests from different client connections; the
app's identity is normally established by application data such as cookies
or authorization headers, not by which upstream TCP connection carried a
request. HTTP/2 and HTTP/3 can multiplex independent requests; upstream
pooling and concurrency are managed separately by the proxy.

The default upstream is plain HTTP/1.1 over TCP on the Docker network. If an
app explicitly supports cleartext HTTP/2 with prior knowledge, Traefik can be
configured to use `h2c` for that backend, for example with
`traefik.http.services.<service>.loadbalancer.server.scheme=h2c`. That is an
optional app-to-edge setting and is not required to serve HTTP/2 or HTTP/3 to
browsers. Traefik's documented standard HTTP service schemes are HTTP, HTTPS,
and h2c; do not assume that a normal HTTP service label creates a QUIC/HTTP/3
upstream. Native app QUIC would need an app server that speaks it and a proxy
or network design that explicitly supports the desired QUIC upstream. For
these same-host Docker backends, the extra protocol and deployment complexity
has no clear default performance win over a reused TCP connection.

### If an application needs native QUIC

There are two distinct designs, and neither happens automatically from HTTP
router labels:

1. **Expose the app's QUIC listener directly.** The app owns QUIC and TLS, and
   the host publishes its UDP port. This keeps the client session at the app,
   but each service needs a distinct public UDP port or IP unless another
   QUIC-aware component can multiplex it. The normal Traefik HTTP middleware,
   host routing, and TLS termination path no longer applies to that traffic.
2. **Use a QUIC-aware reverse proxy.** It terminates browser QUIC and opens a
   separate QUIC connection to the app. This keeps QUIC on both network legs,
   but they are two independent encrypted connections and the proxy/app must
   explicitly support the chosen upstream protocol.

The gain is a QUIC transport on the app-facing leg or an app-owned QUIC
session; it is not the same client QUIC connection transparently continuing
through Traefik. For apps on the same Docker host, this adds a second transport
stack without an obvious improvement to the browser-to-edge portion. Keep
Traefik terminating browser HTTP/3 and using its normal pooled HTTP backend
unless a measured requirement justifies one of these topologies.

## Consumer labels

The edge discovers only opted-in containers attached to `ingress_public`.
Consumers publish no host ports for their web UI. Router and service names
must be unique across all stacks on the host.

```yaml
services:
  webapp:
    image: your-image:tag
    expose:
      - "8080" # documentation only; this does not publish a host port
    networks:
      - ingress_public
      - private
    labels:
      - traefik.enable=true
      - traefik.docker.network=ingress_public
      - traefik.http.routers.webapp.rule=Host(`app.${TLS_EDGE_DOMAIN:?set TLS_EDGE_DOMAIN}`)
      - traefik.http.routers.webapp.entrypoints=websecure
      - traefik.http.services.webapp.loadbalancer.server.port=8080

networks:
  ingress_public:
    external: true
  private:
    internal: true
```

There is no `tls=true`, `certresolver`, or HTTP/3 label in this example:
`websecure` already enables TLS and the certificate resolver in the edge's
static configuration. The Host rule supplies the name for routing and
certificate issuance. The backend port is the container's HTTP listener.

The same pattern applies when exposing
`dstdns/applications/webapp-server` or `netcup-api-filter`: put the labels on
the Compose service that owns the intended public HTTP listener, use a
distinct router/service name and host name for each, and set
`loadbalancer.server.port` to that service's configured container port. Those
port values and the decision to enable a public route remain consumer-owned;
do not add ingress labels to workers, databases, or other containers that do
not serve the HTTP application.

HTTP/3 is transparent to this consumer. It keeps the same labels whether the
browser arrives over HTTP/1.1, HTTP/2, or HTTP/3. WebSocket and SSE endpoints
can also use the same HTTP router; they do not need separate TCP/UDP router
labels. The existing [consumer examples](../consumer-examples/README.md) show
public routing, middleware labels, and the distinct raw TCP/UDP labels needed
only for non-HTTP media traffic.

## Server-originated events

Choose the application protocol from the direction and shape of the messages.
The proxy does not make an app event stream bidirectional or add event
semantics; it forwards the HTTP request or upgraded connection.

| Mechanism | Direction and behavior | Efficiency and capabilities | Use it when |
|---|---|---|---|
| Server-Sent Events (SSE) | One long-lived HTTP response from server to browser. The browser `EventSource` API reconnects and supports event IDs / `Last-Event-ID`. Events are UTF-8 text. | A normal HTTP request and response; easy to route, inspect, authenticate with normal web session cookies, and operate through HTTP infrastructure. One live connection per stream consumes server connection and application state, but messages need no WebSocket framing. With HTTP/2 or HTTP/3, the stream shares a multiplexed connection; HTTP/1.1 has a low per-origin browser connection limit. The client sends commands separately with `fetch` or ordinary forms. | Notifications, progress, logs, live status, or server updates where the browser mostly listens. |
| WebSocket | One long-lived full-duplex message connection after an HTTP opening handshake. Either side can send text or binary messages. | Efficient for frequent two-way messages after setup; avoids repeated HTTP request headers. The application must define message types, heartbeats, reconnect/backoff, replay, authorization and scaling behavior. Long-lived sockets also hold server and proxy state. | Chat, collaborative editing, interactive control, or a protocol with sustained client and server messages. |
| Long polling | Browser makes an HTTP request that the server holds until an event or timeout, then repeats. | Very compatible and straightforward, but repeats requests and headers and adds a round trip between events. It can be a fallback where streaming is unavailable. | Infrequent updates or a simple compatibility fallback. |
| WebTransport | A newer HTTP/3 transport API with multiple reliable streams and datagrams. | More transport choices than SSE or WebSocket, including unreliable datagrams and independent streams, at the cost of a more specialized client/server and proxy path. Do not assume this Traefik deployment supports WebTransport end to end just because its browser-facing entrypoint supports HTTP/3. | Specialized real-time apps that need independent streams or datagrams and can require HTTP/3-capable clients and servers. |

### Real-time search as the user types

For ordinary typeahead search, the usual implementation is a debounced
request/response endpoint (`fetch`), not WebSocket. Send a request after the
input pauses briefly, cancel or ignore an older request when a newer query is
sent, and render the newest response. HTTP/2 or HTTP/3 multiplexes concurrent
requests without requiring a long-lived application protocol. This works
through the current Traefik labels and requires no QUIC support in the app.

Use SSE if the server needs to continue sending results, progress, or updates
after the initial query while the user is idle. Use WebSocket if the client
and server need ongoing two-way messages, such as interactive suggestions
driven by server state. For simple search results returned once per query,
WebSocket adds connection and message-protocol state without solving a need
that ordinary HTTP lacks.

## Where operators configure the edge

The public `websecure` endpoint is shared, but its configuration belongs to
the **tls-edge application schema**. CIU should render and apply it; generic
CIU root defaults should not become the place where Traefik-specific policy
is defined. The tracked `ciu.defaults.toml.j2` is the tls-edge schema/defaults,
the instance's `ciu.toml.j2` is the operator's sparse override, and the
Traefik/Compose templates consume the merged values.

| Concern | Operator input / source | What it affects |
|---|---|---|
| Base domain for consumer Compose files | `tls-edge configure` writes `TLS_EDGE_DOMAIN` to instance `consumer.env`. | Consumer Host rules and DNS names. It is not itself the Traefik listener configuration. |
| Domain, TLS/ACME mode, ACME email, DNS provider, wildcard, static cert paths | `tls-edge configure` writes supported fields to instance `ciu-stack/ciu.toml.j2`; defaults/schema live in `ciu-stack/ciu.defaults.toml.j2`. Secrets use CIU's secret mechanism. | Rendered Traefik static config, certificate resolver, mounts, or dynamic cert file. Listener/certificate-resolver changes require CIU rendering and a container restart. |
| Public HTTP/HTTPS ports, bind IP, port 80 on/off | `tls-edge configure` writes `[tls_edge.ports]` values in the site override; templates are `ciu-stack/traefik.yml.j2` and `ciu-stack/ciu.compose.yml.j2`. | Traefik listeners and Compose host port publications. Update host and provider firewall rules to match. |
| HTTP/3 / UDP on the public HTTPS port | `tls-edge configure` writes `[tls_edge.http3] enabled = true`; the public UDP port and Traefik Alt-Svc advertised port derive from `ports.https`. | Enables QUIC on shared `websecure`, publishes UDP, and includes it in the firewall plan. TCP remains enabled for HTTP/1.1 and HTTP/2 fallback. |
| Calls media preset | `tls-edge configure` writes `[tls_edge.calls]`; it is disabled by default. | Adds/removes the separate TCP/UDP entrypoints and Compose port publications, currently 8443. A consumer also needs app-specific TCP/UDP labels and its own advertised media address. |
| Default TLS options and secure-header policy | `ciu-stack/conf.d/options.yml` and `ciu-stack/conf.d/middlewares.yml`. | The file-provider contents are watched and hot-reloaded. Changing `[tls_edge.headers] enabled` also attaches/detaches the middleware in static `traefik.yml.j2`, so that toggle requires render and restart. Consumer-specific middleware can instead be declared with labels. |
| Docker network integration and migration | `[tls_edge] ingress_network`, `ingress_external`, and `join_workspace_network` in the site override. | Controls which shared/workspace networks Traefik joins and whether it creates or adopts the public ingress network. Consumer stacks still own their external network declarations. |
| Runtime and diagnostics | `[tls_edge.traefik]`, `[tls_edge.runtime]`, `[tls_edge.health]`, and `[tls_edge.logging]` in the site override. | Sets Traefik log/dashboard behavior, cgroup parent, health-check timing, and bounded container logs. Static Traefik settings need a render/restart; Compose-only settings need a Compose update. |
| CIU root identity, stack selection and generic deployment environment | `ciu.global.defaults.toml.j2`. | CIU's deployment envelope, not the tls-edge public protocol policy. Keep `websecure`, ports, and H3 out of this generic root file. |

TLS mode and port choices are linked: `acme-tls` uses TCP 443 for the
TLS-ALPN challenge; `acme-http` needs port 80 enabled; `acme-dns` needs a DNS
provider credential and can issue a wildcard; `static` and `dev` need the
configured certificate files or generated development certificates. These
relationships are validated together by the configuration CLI.

HTTP/3 is an instance-wide option because all web routers share `websecure`.
`tls-edge configure` validates it with the other listeners, shows the UDP rule
in the firewall plan, and writes the single `[tls_edge.http3] enabled` setting
consumed by Traefik and Compose. Consumer labels do not enable or disable
HTTP/3 for individual host names.

For the released `get.py` installation, use
`/srv/tls-edge-instance/bin/tls-edge configure`. The command writes the
persistent site override and updates only `TLS_EDGE_DOMAIN` in
`/srv/tls-edge-instance/stack/consumer.env`. Review with `tls-edge validate`
and `sudo ./ciu up --dry-run`, then apply from `/srv/tls-edge-instance/stack`
with `sudo ./ciu up`.

An instance that opts into both optional media and HTTP/3 could have a sparse
override like this. Generic defaults leave both listeners disabled.

```toml
[tls_edge]
domain = "example.test"

[tls_edge.tls]
mode = "acme-tls"

[tls_edge.acme]
email = "ops@example.test"

[tls_edge.ports]
expose_http = true
https = 443

[tls_edge.calls]
enabled = true

[tls_edge.http3]
enabled = true
```

The keys omitted from this file continue to come from
`ciu-stack/ciu.defaults.toml.j2`. The CLI writes the same supported settings
while preserving other site overrides and comments.

For a repo checkout deployed with the standalone renderer, put the sparse
override at `ciu-stack/ciu.toml.j2`, then run `scripts/render.sh`. For the
manually managed host copy at `/root/tls-edge-LIVE`, make the change in that
host copy and rerender that copy. Do not edit the devcontainer checkout and
expect it to change the separately deployed host service. The generated files
under `edge-proxy/` are output and may be overwritten by the next render. The
one-off plain Compose quickstart permits directly editing rendered
`edge-proxy/traefik.yml`, but that is not a durable configuration source.

The tracked file that carries default values is not the normal place for a
single instance's changes: edit the site override. Maintainers adding a
supported feature should add its typed default and comments to
`ciu-stack/ciu.defaults.toml.j2`, render it into the needed templates, and
then expose it through the installer or operator CLI.

## Configuration CLI and wizard

The generated `get.py` remains the small bootstrapper: it verifies the offline
bundle, installs CIU and the `tls-edge` CLI into the release venv, and creates
the stable CIU git root. It does not ask site questions or write site
configuration. After installation, `tls-edge configure` uses `cli-extended`
for terminal prompts and owns tls-edge validation and persistence. It writes
the site override and `consumer.env`, displays the TOML diff and inbound port
plan, and asks for confirmation. `--dry-run` previews; `--answers FILE --yes`
supports unattended configuration. CIU rendering and deployment remain an
explicit next step.

The CLI supports certificate mode and ACME settings, wildcard/static
certificates, public HTTP/HTTPS ports, bind address, HTTP/3, optional Calls
media, adoption of an ingress network and ACME volume, and cgroup parent. It
does not collect secret values: DNS credentials belong in CIU secrets. The
interactive Questionary dependency is bundled for terminal configuration;
non-interactive commands such as `show` and `validate` do not load it.

## References

- [Traefik v3.7 EntryPoints and HTTP/3](https://doc.traefik.io/traefik/v3.7/reference/install-configuration/entrypoints/)
- [Traefik v3.7 HTTP/3 response-header implementation](https://github.com/traefik/traefik/blob/v3.7/pkg/server/server_entrypoint_tcp_http3.go)
- [Traefik v3.7 TLS and ALPN defaults](https://doc.traefik.io/traefik/v3.7/reference/routing-configuration/http/tls/tls-options/)
- [Traefik v3.7.13 h2c backend configuration](https://doc.traefik.io/traefik/v3.7/migrate/v3/#h2c-upgrade-requests)
- [RFC 9114: HTTP/3, Alt-Svc, and QUIC/TLS](https://datatracker.ietf.org/doc/html/rfc9114)
- [RFC 9460: HTTPS DNS records](https://datatracker.ietf.org/doc/html/rfc9460)
- [WHATWG HTML: Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- [RFC 6455: WebSocket](https://datatracker.ietf.org/doc/html/rfc6455)
- [W3C: WebTransport](https://www.w3.org/TR/webtransport/)
- [tls-edge consumer examples](../consumer-examples/README.md)
- [tls-edge installer and operations guide](INSTALL.md)
- [`cli-extended` prompt API](../../libraries/cli-extended/README.md#optional-interactive-prompts)
