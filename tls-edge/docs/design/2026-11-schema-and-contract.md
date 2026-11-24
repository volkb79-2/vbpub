# tls-edge: schema, answers, consumer contract, ACME state (P-edge-D1)

Decisions for P-edge-04, -04b, -05 and -06. Bound by PLAN-edge D1-D12. Builds on P-edge-02's
`load_site`, `check_override`, `migrate`, `validate_config`, `SCHEMA` and the `validate_edge.py` hook.
Keys are written below `[tls_edge]` unless they are spelled out in full.

## 1. Field table and answers grammar

**1.1 Shape** (`src/tls_edge_cli/fields.py`):

```python
@dataclass(frozen=True)
class Field:
    key: str                          # dotted path below [tls_edge], e.g. "http3.enabled"
    kind: str                         # "text" | "bool" | "port" | "choice" | "list"
    prompt: str | None                # None: never asked (answers file or current value only)
    choices: tuple[str, ...] = ()     # kind "choice"
    when: Callable[[Mapping], bool] = ALWAYS   # over the proposed [tls_edge] table
    when_text: str = ""               # names the controlling key in refusals
    check: Callable[[Any], Any] | None = None  # normalise one value or raise ConfigError
    required: bool = False            # empty text refused
    initial: Callable[[Layout], Any] | None = None  # value when the override lacks the key
```

`FIELDS` (table order is the prompt order):

| key | kind | when | check / initial |
|---|---|---|---|
| `domain` | text, required | always | `normalize_domain` |
| `tls.mode` | choice `TLS_MODES` | always | |
| `acme.email` | text | mode `acme-*` | e-mail or `""` |
| `acme.dns.provider`, `acme.dns.wildcard_main` | text | mode `acme-dns` | lego name; DNS name or `""` |
| `ports.expose_http`, `http3.enabled`, `ingress_external` | bool | always | |
| `ports.http`, `ports.https` | port | always | 1-65535, not bool |
| `ports.bind_ip`, `acme.volume_name`, `runtime.cgroup_parent` | text | always | IP / volume name / cgroup path, or `""` |
| `acme.store` | choice `volume`,`hostdir`, prompt None | always | initial: section 4 |
| `traefik.hostdir.acme.path` | text, prompt None | `acme.store == "hostdir"` | initial `"../../shared/acme"` on an instance layout only; in a checkout `store = "hostdir"` without a path is refused |
| `static.cert_base`, `static.domains`, `static.source` | text / list / choice | mode `static` or `dev` | path; lineages, initial `[domain]`; `bind`,`volume` |
| `calls.enabled`, `calls.public_port` | bool / port | always / `calls.enabled` | rows deleted in P-edge-06 |

To add a setting, add one row and the key in `ciu.defaults.toml.j2`, for example
`Field("traefik.log_level", "choice", "Traefik log level", choices=("ERROR", "WARN", "INFO", "DEBUG"))`.
Prompt defaults are the current effective values, which come from the rendered defaults plus the
override. There are no literal fallbacks (D2).

**1.2 What configure always writes:** `schema`, every field whose `when` holds for the proposed
config, and every key in the answers file. These are written as explicit values with tomlkit, and
comments and operator keys are kept. A field whose `when` is false is not written, and an existing
value for it stays in the file.
Why: explicit writes keep default flips harmless (keep-list). Rejected: writing only changed keys
(a later default change would silently move configured instances).

**1.3 Answers grammar and merge.** An answers file is a sparse override in the schema's own shape,
for example `[tls_edge.http3] enabled = true`, parsed as plain TOML. `deep_merge` is ciu's rule:
tables merge recursively, and scalars and lists replace (ciu SPEC S3.1a clause 3, S3.3).

```text
defaults, overlay, _ = load_site(stack_dir)
answers  = migrate(read_toml(answers_file))
check_override(defaults, answers)       # same refusals as the override
refuse open tables in answers           # secrets, acme.dns.environment, hooks
proposed_overlay = deep_merge(overlay, answers)
for f in FIELDS:                        # in table order
    edge = deep_merge(defaults, proposed_overlay)["tls_edge"]
    if not f.when(edge):
        refuse if f.key in answers      # "tls_edge.static.domains applies only when tls_edge.tls.mode is static or dev"
        continue
    v = answers[f.key] if set
        else prompt(f, default=edge[f.key]) when interactive (no --answers) and f.prompt is set
        else f.initial(layout) if the override lacks the key and f.initial
        else edge[f.key]
    proposed_overlay[f.key] = f.check(typecheck(f, v))
proposed_overlay["tls_edge"]["schema"] = SCHEMA
proposed = deep_merge(defaults, proposed_overlay)
plan = validate_config(proposed["tls_edge"], stack_dir=stack_dir)
```

Prompts see earlier answers through `edge`, so the mode chosen first decides which ACME or static
questions follow. Type errors name the full key, for example
`tls_edge.http3.enabled must be true or false (answers file edge.toml)`.
Rejected: a separate answers vocabulary (two names for every setting, H2).

**1.4 3.1 flat keys.** Any top-level answers key other than `tls_edge` is refused. The 18 names
of 3.1 are kept in a frozen `LEGACY_ANSWER_KEYS` map, which is used only to build the refusal
message: `answers key 'http_enabled' is the 3.1 flat form; write [tls_edge.ports] expose_http (answers file P)`.
Other top-level keys get the message `unknown answers key 'x'; answers use the site-override shape [tls_edge.<table>]`.
Rejected: accepting both forms for one release (that keeps H2's second vocabulary alive).

**1.5 INSTALL section 8 answers example** (placeholders only):

```toml
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

## 2. Consumer contract

**2.1 API** (`src/tls_edge_cli/contract.py`). It imports only the stdlib and `tls_edge_cli.config`,
so it follows P-edge-02's rule for the validation core: stdlib and Jinja2 only.

```python
CONTRACT_KEYS = ("TLS_EDGE_DOMAIN", "TLS_EDGE_NETWORK", "TLS_EDGE_HTTPS_ENTRYPOINT",
                 "TLS_EDGE_HEADERS_MIDDLEWARE", "TLS_EDGE_HTTP3", "TLS_EDGE_PUBLIC_PORTS")
def consumer_contract(edge: Mapping) -> dict[str, str]  # edge = effective [tls_edge], as validate_config takes
def contract_for(stack_dir: Path, overlay: Mapping | None = None) -> dict[str, str]
def render_env(contract: Mapping[str, str], existing: str = "") -> str
```

| key | value | example |
|---|---|---|
| `TLS_EDGE_DOMAIN` | normalised `domain`, `""` when unset | `example.test` |
| `TLS_EDGE_NETWORK` | `ingress_network` | `ingress_public` |
| `TLS_EDGE_HTTPS_ENTRYPOINT` | constant `HTTPS_ENTRYPOINT` (`traefik.yml.j2:52`) | `websecure` |
| `TLS_EDGE_HEADERS_MIDDLEWARE` | constant `HEADERS_MIDDLEWARE = "secure-headers@file"` | `secure-headers@file` |
| `TLS_EDGE_HTTP3` | `true` / `false` | `false` |
| `TLS_EDGE_PUBLIC_PORTS` | `<port>/<proto>` from the validator's port plan, comma-joined, sorted by port and then `tcp` before `udp`, no spaces | `80/tcp,443/tcp,443/udp` |

`validate_config` formats its text plan from a structured `port_plan(edge) -> list[(port, proto, purpose)]`,
so the contract and the firewall text have one source. Every value matches `[A-Za-z0-9._@,/-]*`,
which `render_env` asserts, so `consumer.env` never needs quoting.
A tls-edge render test pins `HTTPS_ENTRYPOINT` to the entrypoint in the rendered `traefik.yml` that
carries the resolver, and `HEADERS_MIDDLEWARE` to the middleware that entrypoint names.
Rejected: making the entrypoint name a defaults key (it would invite renaming a name that every
consumer label uses).

**2.2 `consumer.env`.** `configure` is its only writer. tls-edge owns the six keys:
- owned keys are rewritten in place;
- missing owned keys are appended in `CONTRACT_KEYS` order;
- duplicate owned lines are removed;
- every other line is kept, as `updated_consumer_env` keeps them today (`config.py:353-376`);
- an existing file keeps its mode, and a new file gets `0644`.

**2.3 Drift.** The file has drifted when it exists and an owned key is missing or differs from
`consumer_contract(effective)`.
- `configure` re-syncs it: it always rewrites the owned keys, and `configure --answers <empty file> --yes`
  does that and rewrites the managed override fields with unchanged values.
- `tls-edge validate` (exit 2) and the ciu hook (an ERROR finding) refuse, naming the keys:
  `consumer.env TLS_EDGE_DOMAIN differs from the site override; run tls-edge configure to re-sync`.
- `show` reports drift and exits 0.
- An absent `consumer.env`, as in a checkout, is not checked.
- 4.0 upgrade step: a 3.2 `consumer.env` lacks the new keys; run
  `tls-edge configure --answers <empty> --yes` before `ciu up`.

Why the hook refuses too: a hand edit of `domain` followed by `ciu up` would otherwise drift with no
message. Rejected: re-syncing from `validate` or `ciu check` (both are read-only; `ciu check` is
side-effect-free per ciu SPEC S13.4a).

**2.4 `tls-edge show --json`** uses cli-extended's `include_json` and `runtime.output.primary_json`.

```json
{"kind": "tls-edge.show", "format": 1, "version": "<tls-edge-cli version>", "schema": 1,
 "stack_dir": "/srv/tls-edge-instance/stack/ciu-stack", "override": true,
 "contract": {"TLS_EDGE_DOMAIN": "example.test", "TLS_EDGE_HTTP3": "false", "...": "..."},
 "ports": [{"port": 443, "protocol": "tcp", "purpose": "HTTPS"}],
 "consumer_env": {"state": "in-sync", "drift": []}}
```

`contract` holds the same strings as `consumer.env`. `consumer_env.state` is `in-sync`, `drift` or
`absent`. Adding a field does not bump `format`; removing, renaming or retyping a field does.

**2.5 Consumer import.** A consumer test finds `tls-edge/src/tls_edge_cli` by walking up its parent
directories, as `mattermost-server/tests/_render.py:27-33` does, and puts that `src` first on
`sys.path`. A sparse checkout without tls-edge skips the test. Then:
`contract_for(edge / "ciu-stack", {"tls_edge": {"http3": {"enabled": True}}})`.
- `contract_for` renders the defaults only. It never reads `ciu.toml.j2`, because in the main
  checkout that file is an ignored site override that holds a real domain.
- It runs `migrate` and `check_override` on the given overlay, then
  `validate_config(require_domain=False, require_certificates=False)`.
- Consumers assert their labels against `TLS_EDGE_NETWORK` and `TLS_EDGE_HTTPS_ENTRYPOINT`, and
  check that their published ports are disjoint from `TLS_EDGE_PUBLIC_PORTS`.
- Rendered `traefik.yml` details (`:443`, `advertisedPort`) belong to tls-edge's own tests (P-edge-03).

Rejected: installing tls-edge-cli into consumer venvs, or reading defaults files and `render_standalone.py`.

## 3. Schema 2 `migrate()` (4.0.0)

- **When the schema goes up:** only when a key is removed or renamed, or changes meaning. A new key
  stays in the current schema, so 3.2.0 (with section 4's keys) is still `SCHEMA = 1`.
- **Reading `tls_edge.schema`:** absent means 1. A non-integer, a bool or a value below 1 is refused
  (`tls_edge.schema must be an integer from 1 to 2`). A value above `SCHEMA` is refused (P-edge-02).
- **Rule 1 to 2, the only rule:**
  - `[tls_edge.calls]` whose `enabled` is `false` or absent (absent was `false` in 3.1), and whose
    other keys come only from `{public_port, tcp_internal_port, udp_internal_port}`: the whole
    table is dropped silently.
  - `enabled = true` is refused: `tls_edge.calls.enabled = true: tls-edge 4.0 no longer forwards
    media; publish the media port from the application stack (Mattermost Calls:
    voice-screen-share/mattermost-server publishes TCP+UDP directly), then remove [tls_edge.calls]
    from <override path>`.
  - Any other content under `calls` (another key, a non-bool `enabled`, a non-table) is left in
    place. `check_override` then refuses it as an unknown key (R-1).
- **`schema = 2` with a `[tls_edge.calls]` table:** no rule applies, so the result is
  `unknown override key tls_edge.calls.enabled`.
- **Where migration runs:** on the override and on answers files, in memory, on every path (hook,
  standalone, `validate`, `configure`). Only `configure` writes the result: it removes the dropped
  table and writes `schema = 2`, and both changes show in its diff.

Why drop-only: ciu renders the raw override file (S3.2, S3.3), never `migrate()`'s output. So a
migration may only drop keys that no 4.0 template reads, or refuse. A rename or a default flip is
not a migration rule. Rejected: the hook rewriting the override (`ciu check` writes nothing, S13.4a).

The hook validates the migrated effective config, not ciu's raw merged config (which still holds
`[tls_edge.calls]`). P-edge-06 adds a test through ciu's library render with a 3.1 override carrying
`[tls_edge.calls] enabled = false, public_port = 8443`: `check` passes and the compose does not
publish 8443.

## 4. ACME state for new instances (D7, TE-10)

**Decision:** a ciu hostdir at `<instance root>/shared/acme`, chosen by a new key `acme.store`.

**What ciu SPEC S6 allows:**
- S6.1: a hostdir value "non-empty [is] used as given (absolute allowed)". A relative value
  resolves against the stack dir (`ciu/src/ciu/engine.py:946-948`, `deploy.py:3518-3521`).
- S6.4: `--reset` "removes `vol-*` dirs **of the stack directory**" and must "prove each resolved
  directory remains within the resolved stack directory" (`engine.py:1263` globs `stack_dir/vol-*`).
- S6.4a, S6.4b: `clean` removes containers, named volumes, networks and, with `--vanilla`, exactly
  four workspace files. None of these is a declared hostdir.
- Docker: `compose down -v` never removes a bind source.

**Defaults** (3.2.0, schema 1, additive):

```toml
[tls_edge.acme]
store = "volume"   # "volume": compose volume acme-data (the 3.1 behaviour); "hostdir": the hostdir below
[tls_edge.traefik.hostdir]
acme = { path = "", uid = 0, gid = 0, mode = "0700" }
```

**Compose:** when `acme.volume_name` is set, the external volume is used (unchanged). Otherwise,
`store = "hostdir"` mounts `{{ tls_edge.traefik.hostdir.acme }}:/var/lib/traefik`, and the
fallback is `acme-data`. The top-level `acme-data` volume is declared only when `store = "volume"`
or `volume_name` is set.

**Owner and mode: `0:0`, `0700`.**
- Traefik runs as root: `ciu.compose.yml.j2` has no `user:`.
- Traefik has `cap_drop: ALL` and gets back only `NET_BIND_SERVICE` (`ciu.compose.yml.j2:148-151`,
  `ciu.defaults.toml.j2:189-190`), so it has no `DAC_OVERRIDE`.
- With S6.3's default owner `CONTAINER_UID:DOCKER_GID` and mode `0775`, uid 0 would be "other" and
  could not create `acme.json`. Traefik creates that file itself with mode 0600 (`traefik.yml.j2:97`).
- UID 0 is a valid value (S2.5).

**New-instance signal:**
- Adapter `bootstrap`, which get.py runs only on a first install (`get.py:1653`), creates
  `<root>/shared/acme` as root:root `0700`. `apply` and `rollback` do not create it.
- `bootstrap` creates `<root>/shared/acme` only when euid is 0. Under `--scope user` it creates
  nothing, and `store` stays `volume`. Reason: get.py `--scope user` (`get.py:2475`); ciu S6.3
  needs owner 0:0 here (`engine.py:898-915`).
- `configure` sets the initial `acme.store` to `hostdir` when that directory exists and the override
  does not set `store` yet. With it, configure writes `traefik.hostdir.acme.path = "../../shared/acme"`.
  That path is relative to `stack/ciu-stack/`, so it keeps working if the instance root moves.
- ciu S6.3 accepts the existing directory because owner and group match (`engine.py:902-904`).

**Validator** (when `store = "hostdir"`), via a new `stack_dir` keyword of `validate_config`:
- the path is not empty;
- the path resolves outside `stack_dir.parent` (the instance's `stack/`, per D7);
- uid is 0;
- `volume_name` is empty;
- the standalone renderer refuses `hostdir`, because there is no ciu to create the directory.

In `ciu check` the hook receives the hostdir already resolved to an absolute string
(`deploy.py:3979`, before the hooks at `:4033`), so the uid, empty-path and `volume_name` rules read
the `load_site` effective config, not ciu's merged config.

With `store = "volume"`, ciu still creates an inert `ciu-stack/vol-traefik-acme` (the S6.1 auto path).
ciu creates every hostdir it finds declared, and D1's key check needs the key declared in the defaults.

**Checkout** (no instance root, no marker directory): `store = "volume"` uses `acme-data`, as today.
`hostdir` needs an explicit path that passes the validator rules below; without one it is refused.

**Upgrading a 3.1 instance:**
- Nothing changes on update. The override sets no `store`, so the instance keeps the same `acme-data` volume.
- Adoption: set `[tls_edge.acme] volume_name = "<exact existing volume name>"`. `volume_name` makes
  the volume external, so `down -v` and `--reset` keep it; `ciu clean` still removes a volume compose
  created for this project (S6.4a item 6); only the hostdir survives `ciu clean`. There is no
  automatic copy.
- Moving to the hostdir is an optional manual step in INSTALL section 6: stop the edge, copy
  `acme.json` with one exact-name container, then set `store` and `path`.

**`get.py update` and rollback:**
- Update never touches the directory. The sync writes or deletes only recorded manifest files under
  `stack/` (`get.py:1774-1813`). In `shared/`, the installer touches only `host.toml` and
  `overwritten/` (`get.py:1246-1250`, `1815-1820`).
- A rollback to a release older than 3.2 ignores `store` and starts an empty `acme-data`, so every
  certificate is issued again. INSTALL documents this.

Rejected: an adapter-created external volume. Its name `tls-edge-<id>-acme` matches ciu clean's
`{project}-{env}-` volume pass (`deploy.py:6309-6343`).

## 5. What each package implements from this note

- **P-edge-02:** nothing beyond its brief. Section 3's rule keeps `SCHEMA = 1` for 3.2.0.
- **P-edge-04b (3.2.0):** section 4:
  - the two defaults keys and the compose branch;
  - the validator rules and the `stack_dir` keyword;
  - the adapter `bootstrap` mkdir;
  - `configure` writes `acme.store` explicitly on every run (`volume` or `hostdir`), and `path`
    by the new-instance signal;
  - the backlog limitation "acme-data volume loss" stays, reworded to cover `store = "volume"` only;
  - a render test through ciu's library render;
  - INSTALL sections 3 and 6.
- **P-edge-04:** section 1:
  - `fields.py` with `Field` and `FIELDS`, including 04b's rows;
  - the answers grammar and merge;
  - the `LEGACY_ANSWER_KEYS` refusals;
  - public `render_defaults` and `deep_merge`;
  - INSTALL sections 3, 4 and 8.
- **P-edge-05:** section 2:
  - `contract.py` and `port_plan`;
  - ownership of `consumer.env`;
  - drift checks in `validate`, the hook and `show`;
  - `show --json`;
  - the Jitsi and Mattermost tests moved to `contract_for`.
- **P-edge-06:** section 3: `SCHEMA = 2`, the migrate rule, persistence in `configure`, and deletion
  of the Calls rows. If P-edge-05 has not landed, also drop `test_calls_media_contract.py:119`
  (`edge_config["calls"]`).

## Decided conflicts

- **(a) Volume adoption and `ciu clean`.** `ciu clean`'s label pass (`deploy.py:6346-6392`, owner
  verdict `:4592-4613`, external volumes not filtered `:6428-6441`) removes a volume compose created
  for this project; adoption does not change its labels. P-edge-04b keeps `volume_name` adoption
  (D7) and documents it, and never claims an adopted volume survives `ciu clean`. Only the hostdir
  does.
- **(b) Unknown-key refusal in 3.2.0.** Accepted as a documented validation tightening. CHANGES
  pending note: "Changed: unknown keys in the site override are refused (`unknown override key
  tls_edge.<path>`); 3.1 overrides written by `configure` are unaffected."
- **(c) Explicit writes against a sparse override.** Compatible: ciu S3.1a clause 2 is a SHOULD.
  INSTALL section 4 gets one sentence: "`configure` writes every managed field explicitly so that a
  later change to a default never moves a configured instance."
