# CMRU and CIU default-input audit

**Status:** 2026-08-12 initial strictness audit. This is an implementation
backlog, not a claim that every existing policy knob should become mandatory.

## Rule used

An absent value is safe only when it expresses a policy that is correct without
any further fact. A value which names a release identity, a resource budget, an
image, a host, a filesystem location, a deployment target, or a destructive
scope must be derived from an authoritative source, explicitly declared, or
rejected. It must not be guessed by source code.

CLI display choices such as `resolve --format=json`, terminal colour only on a
TTY, and an opt-in `--log-prefix-time-short` are intentionally retained as
policy defaults: they do not select or alter an external resource. Empty
optional per-device I/O caps likewise mean "use the already explicit parent
cgroup policy", rather than inventing a device/rate.

CMRU's default project release history path (`CHANGES.md`) is also retained as
an intentional estate policy: every managed project receives generated history
unless it explicitly declares `changelog = false`, as required by the release
contract. It is not a guessed external input.

## Fixed in this change

| Product | Former implicit input | Strict result |
|---|---|---|
| CMRU `tester-gate` | CPU limit `1.5`; host-systemd probe image; `docker:dind` sidecar image | `CMRU_TESTER_CPUS` and `CMRU_TESTER_CGROUP_PROBE_IMAGE` are required; a Docker-enabled gate also requires `CMRU_TESTER_DIND_IMAGE`. All in-tree project contracts declare the inputs. |
| CMRU `wheel-build` | Local cockpit Python build when no builder image was configured | `CMRU_WHEEL_BUILDER_IMAGE` and Docker are required. The local build path is retired. |
| CMRU packaging | `setuptools_scm.fallback_version` could manufacture a static release identity without Git | Removed from CMRU and CIU. Wheel builds require a resolvable Git worktree. |
| CMRU cleanup | Empty release/package selectors became an all-target wildcard | Empty now selects nothing; only the explicit `"*"` selector is broad. |
| CIU remote verbs | An unreadable global config silently became `{}` before SSH/activation | `render/up/down/health/ssh --host` now exit 2 before opening a transport. |

The CMRU project template is now revision 4. `cmru standards` verifies the new
tester inputs and wheel-builder requirement from command usage, so a project
cannot merely claim adoption with a revision marker.

## CMRU: remaining candidates

### Require an explicit project release identity (high)

`cmru/src/cmru/version.py` and `cmru/src/cmru/changelog.py` still choose
`0.1.0` for a first non-external release; counter releases use `1.0.0` when
`base_version` is absent. `cmru/src/cmru/cli.py:VersionSpec` still carries
those historical values too.

Add conditional `[project.version]` grammar in the next schema revision:

- `initial_version` required for `scm` and `file:<path>` strategies;
- `base_version` required for `counter`;
- neither permitted for `external:<VAR>` or `none` unless its meaning is
  explicitly specified.

Every project template and project `cmru.toml` must then state its own initial
identity. This is a release-identity migration, so it should land with parser,
spec, template, and first-release regression tests together rather than by
silently retaining `0.1.0`.

### Make optional configuration grammars strict (medium)

`cmru/src/cmru/config.py:_parse_installer` supplies `.tar.xz`,
`manifest.json`, and `manifest.json.minisig`; `cmru/src/cmru/bundle.py` supplies
output directories, `python3`, and `gztar`. These values influence published
artifact layout. The installer/bundle config grammars should require them when
their respective feature is used, with a separate migration for the existing
tarball/bundle consumers.

`cmru.handlers` also has command-line defaults for tarball suffix and OCI
repack size/compression. Those should become required handler flags (therefore
visible in a project step argv), not inferred handler policy. This needs a
small handler-contract migration; it is deliberately not folded into the
tester resource change.

### Separate control-plane configuration (high, separate product surface)

`cmru-agent` and `cmru-controller` default Consul to
`http://127.0.0.1:8500` and default state/log settings. These are not CMRU
release-orchestration inputs and do not belong in project `cmru.toml`. They
need their own explicit node/controller config document (or required CLI
arguments) before those binaries are promoted as a production control plane.

## CIU: remaining candidates

### Deployment-affecting fallbacks requiring a schema migration (high)

| Source area | Current behavior | Required direction |
|---|---|---|
| `deploy.py:_seconds`; health call sites | Missing health timeout means `30s`; an invalid value warns and also becomes `30s`. | Require a valid explicit `[deploy.health].timeout` wherever health gating is enabled. Invalid input must be exit 2, never a different timeout. |
| `hosts.py`; remote CLI | Inventory lookup can fall through from explicit `CIU_HOSTS_FILE` to repo then `~/.ciu/hosts.toml`; remote `bundle_dir` defaults to `/opt/ciu/current`; transport defaults include root/port 22. | Make the inventory repository-local or an exact explicit path only; a named missing `CIU_HOSTS_FILE` must fail. Define and validate the remote-host schema, including connection identity and `bundle_dir` for sync/thin operations. This is a breaking but portable-host-config migration. |
| `cli.py` remote verb paths | Missing `REPO_ROOT` falls back to the process current directory before host lookup/config load. | Require the workspace identity (or an explicit `--define-root`) for remote operations; never let the caller's incidental shell directory choose an inventory. |
| `secrets/providers.py` | Vault state defaults to `infra/vault`. | Require `[vault].stack_path` only when the selected deployment resolves a Vault token through local stack state; do not force unrelated stacks to declare Vault. |
| `governance.py` | `FALLBACK_READ_IOPS=200` applies when no measured baseline resolves. | Require either `governance.read_iops` or an existing explicit `baseline_path` when governance needs a read cap. Retain `0` only if it has a separately documented, safe no-cap meaning. |
| `engine.py` / config model | `auto_connect_network=true`, `require_fqdn=false`, `require_certs=false`, service enable/health defaults, and log level are deployment scope choices. | Decide the safe absence policy per key, then make it explicit in the global/stack schema and generated templates. Do not mechanically copy today's values: `true` can expand a deployment while `false` can disable an expected service. |
| `dev.py` | `/app`, build context `.`, Dockerfile `Dockerfile`, and derived dev tag are source literals. | Require these in `[<stack>.dev]` when that feature is used, or define a clearly named reusable dev-profile template; avoid an invisible per-image contract. |

### Legacy and ambient compatibility paths to retire (high)

The estate decision is no fallback/legacy support. The next CIU major should
remove, rather than preserve behind environment toggles:

- user-global `~/.ciu/hosts.toml` and missing-file fallthrough;
- `CIU_ADOPT_LEGACY_PROJECT` legacy Compose adoption;
- `SKIP_DEPENDENCY_CHECK` and `CIU_SKIP_DOOD_PREFLIGHT` bypasses, unless they
  become explicit, audited `--break-glass` CLI flags with a recorded reason;
- workspace path/identity fallbacks in `workspace_env.py` that substitute the
  current process's UID/GID, current working directory, hostname, or
  `localhost` for a required published/deployment fact.

Some of these are derivations rather than defaults (for example a native
host's physical path may equal its repository path). Keep an exact derivation
only where the source relation is proved; reject all remaining guesses.

## Deliberately not changed blindly

CIU governs live infrastructure. Replacing defaults such as profile selection,
service health enablement, network attachment, or governance KSM strategy
without a declared replacement changes what starts on a host. The right next
change is a versioned CIU config grammar plus consumer-template migration and
fixture coverage, not a source edit that happens to make the local demo green.

For each candidate above, the migration gate must demonstrate all three:

1. absence is rejected before Docker/SSH/host mutation;
2. each current consumer declares the formerly implicit value explicitly;
3. a misspelled or invalid value cannot degrade to a different operational
   behavior.
