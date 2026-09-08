"""CIU host inventory loader — render-safe hosts file (SPEC J §4 / §4.1).

Reads [deploy.hosts.*] from a dedicated file that ciu render/clean never
touches. Precedence (first found wins):
  1. $CIU_HOSTS_FILE environment variable
  2. <repo_root>/.ciu.hosts.toml
  3. ~/.ciu/hosts.toml

S14.7c (CIU-93) adds the file's first WRITER — ``write_host_row`` — a
round-trip edit that appends or updates exactly one ``[deploy.hosts.<name>]``
row and leaves every other table, key, comment and blank line in the file
byte-for-byte identical. It lives here, beside the reader, deliberately: the
two are one contract (what ``write_host_row`` emits, ``load_hosts`` must read
back), and the ``[deploy.hosts.*]``-vs-top-level-``[hosts.*]`` precedence rule
is stated once, in this module, rather than twice in two files.
"""
from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path

import tomli_w

from .config_constants import MACHINE_DIR
from .secrets.directives import SecretSpec, parse_value

# S14.3a — the only directive kinds legal at host scope (S4.2 six kinds).
# Vault-dependent (ASK_VAULT, GEN_TO_VAULT), ephemeral (GEN_EPHEMERAL) and
# in-place-referenced (ASK_FILE) kinds are meaningless before a host is
# adopted, so they are refused here rather than silently mis-resolved.
HOST_SCOPE_KINDS = frozenset({"ASK_EXTERNAL", "GEN_LOCAL"})


def _hosts_candidates(repo_root: Path) -> list[Path]:
    """The inventory lookup order (S14.3): env override > repo-local > user-global.

    Stated ONCE so the reader and the S14.7c writer can never disagree about
    which file an inventory row lives in.
    """
    candidates = []
    env_file = os.environ.get("CIU_HOSTS_FILE")
    if env_file:
        candidates.append(Path(env_file))
    candidates.append(Path(repo_root) / ".ciu.hosts.toml")
    candidates.append(Path.home() / MACHINE_DIR / "hosts.toml")
    return candidates


def resolve_hosts_file(repo_root: Path) -> Path:
    """The file a new inventory row must be written to (S14.7c).

    The first candidate that EXISTS wins — the same precedence ``load_hosts``
    reads by, so a row is always written to the file the reader will actually
    consult. When none exists yet, the repo-local ``.ciu.hosts.toml`` is
    created (never the user-global file: a per-repo enrollment must not leak
    into every other checkout on the machine).
    """
    candidates = _hosts_candidates(repo_root)
    for path in candidates:
        if path.exists():
            return path
    return Path(repo_root) / ".ciu.hosts.toml"


def load_hosts(repo_root: Path) -> dict:
    """Load the host inventory. Returns {} if no hosts file found."""
    # Precedence: env override > repo-local > user-global
    candidates = _hosts_candidates(repo_root)

    for path in candidates:
        if path.exists():
            with path.open("rb") as fh:
                doc = tomllib.load(fh)
            # Support both [deploy.hosts.*] and top-level [hosts.*]
            hosts = doc.get("deploy", {}).get("hosts")
            if hosts is None:
                hosts = doc.get("hosts")
            return hosts if isinstance(hosts, dict) else {}
    return {}


def _parse_host_secrets(host_name: str, secrets_table: dict) -> dict[str, SecretSpec]:
    """Parse + validate one host's [deploy.hosts.<host>.secrets] subtable (S14.3a).

    Each entry is parsed with the EXISTING ``directives.parse_value`` (never
    reimplemented) and only ``HOST_SCOPE_KINDS`` (ASK_EXTERNAL, GEN_LOCAL) are
    accepted. Any other directive — or any grammar violation — raises a tagged
    ``[S14.3a]`` error naming host, entry and the reason.
    """
    specs: dict[str, SecretSpec] = {}
    for entry_name, raw_value in secrets_table.items():
        try:
            spec = parse_value(entry_name, raw_value, f"deploy.hosts.{host_name}.secrets")
        except ValueError:
            # Never interpolate the upstream message: parse_value's grammar
            # errors echo the raw token back verbatim (e.g. "[S4.2] Unknown
            # directive '<token>'" where <token> IS the pasted secret value
            # when an operator writes a value instead of a directive). That
            # message must never reach stderr — see cli.py's `[ERROR] {exc}`
            # printers and every get_host() caller (S14.3a / P11-B1).
            raise ValueError(
                f"[S14.3a] host '{host_name}', entry '{entry_name}': not a "
                f"recognized secret directive — value not shown"
            ) from None
        if spec.kind not in HOST_SCOPE_KINDS:
            raise ValueError(
                f"[S14.3a] host '{host_name}', entry '{entry_name}': directive "
                f"'{spec.kind}' is not allowed at host scope; only ASK_EXTERNAL "
                f"and GEN_LOCAL resolve before a host is adopted"
            )
        specs[entry_name] = spec
    return specs


def get_host(repo_root: Path, name: str, *, admin: bool = False) -> dict:
    """Return the config dict for a named host (merged with .admin if admin=True).

    Raises ValueError if the host or hosts file is missing.
    """
    hosts = load_hosts(repo_root)
    if not hosts:
        raise ValueError(
            f"[SPEC J] No hosts file found. Create <repo>/.ciu.hosts.toml or "
            f"~/.ciu/hosts.toml with [deploy.hosts.{name}] entries."
        )
    if name not in hosts:
        available = sorted(hosts.keys())
        raise ValueError(
            f"[SPEC J] Host '{name}' not found in the hosts inventory. "
            f"Available hosts: {available or '(none)'}"
        )

    host_cfg = dict(hosts[name])

    if admin:
        admin_cfg = host_cfg.pop("admin", None)
        if admin_cfg and isinstance(admin_cfg, dict):
            host_cfg.update(admin_cfg)
    else:
        # Remove admin sub-table from the base config to avoid confusion
        host_cfg.pop("admin", None)

    # S14.3a — host-scoped secret directives are validated here (so a malformed
    # table aborts any flow that touches the host) but are POPPED before return:
    # a caller asking for connection facts never receives secret directives.
    if "secrets" in host_cfg:
        _parse_host_secrets(name, host_cfg.pop("secrets"))

    return host_cfg


def get_host_secrets(repo_root: Path, name: str) -> dict[str, SecretSpec]:
    """Return the host's parsed, validated secret directives (S14.3a).

    ``{}`` when the host declares no ``[deploy.hosts.<name>.secrets>`` table.
    Raises ValueError (same contract as ``get_host``) when the hosts file or
    the host is missing, or a tagged ``[S14.3a]`` error on a bad entry.
    """
    hosts = load_hosts(repo_root)
    if not hosts:
        raise ValueError(
            f"[SPEC J] No hosts file found. Create <repo>/.ciu.hosts.toml or "
            f"~/.ciu/hosts.toml with [deploy.hosts.{name}] entries."
        )
    if name not in hosts:
        available = sorted(hosts.keys())
        raise ValueError(
            f"[SPEC J] Host '{name}' not found in the hosts inventory. "
            f"Available hosts: {available or '(none)'}"
        )
    host_data = hosts[name]
    if not isinstance(host_data, dict) or not isinstance(host_data.get("secrets"), dict):
        return {}
    return _parse_host_secrets(name, host_data["secrets"])


# ─────────────────────────────────────────────────────────────────────────────
# S14.7c — the round-trip inventory writer (CIU-93)
#
# Design decision (see nyxloom-trove/reports/ciu-P52-REPORT.md): this is
# STDLIB-ONLY targeted text surgery, not `tomlkit`. tomlkit is the purpose-built
# tool for round-trip TOML, but it is absent from ciu's dependency closure AND
# from the gate's own `tester-unified:local` image, whose venv is built
# elsewhere (cmru — a forbidden path for this package). Adding a hard runtime
# dependency ciu's own gate could not import is not a trade this package may
# make, so the surgery route is taken and made safe by construction plus a
# semantic post-check (`_verify_round_trip`) that REFUSES rather than writing
# anything it cannot prove is a pure single-row edit.
#
# The three grammar hazards a naive per-key regex would hit — a similarly named
# table, a multi-line string whose body contains a `[table]`-looking line, and
# quoted/dotted key spellings — are handled by (a) a line scanner that tracks
# multi-line basic/literal string state, and (b) handing every header and key
# spelling to `tomllib` itself rather than parsing TOML by hand.
# ─────────────────────────────────────────────────────────────────────────────

#: The only keys `ciu host enroll` owns in an inventory row (S14.7c). Everything
#: else in the table — bundle_dir, docker_optional, activate, admin, secrets —
#: stays the operator's, exactly as for a hand-written row.
ENROLL_ROW_KEYS = ("ssh_host", "ssh_user", "ssh_port", "ssh_key", "known_host")


def _scan_line_strings(text: str, ml: str | None) -> str | None:
    """Advance the multi-line-string state across one physical line.

    *ml* is ``None`` outside a multi-line string, else the opening delimiter
    (``'\"\"\"'`` or ``\"'''\"``). Returns the state at end of line. Comments end
    the scan (a `#` outside a string makes the rest of the line inert).
    """
    i, n = 0, len(text)
    while i < n:
        if ml is not None:
            j = text.find(ml, i)
            if j < 0:
                return ml
            i = j + 3
            ml = None
            continue
        ch = text[i]
        if ch == "#":
            return None
        if text.startswith('"""', i) or text.startswith("'''", i):
            ml = text[i:i + 3]
            i += 3
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < n:
                if quote == '"' and text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        i += 1
    return ml


def _toml_path_of(fragment: str) -> list[str] | None:
    """Return the dotted key path *fragment* denotes, per TOML's own grammar.

    ``tomllib`` is the parser — never a hand-rolled splitter — so quoted
    (``[deploy.hosts."rs 1002"]``), dotted and whitespace-padded spellings all
    resolve exactly as the reader will resolve them. ``None`` when the fragment
    is not a single well-formed key path.
    """
    try:
        doc = tomllib.loads(f"[{fragment}]\n")
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    path: list[str] = []
    cur: object = doc
    while isinstance(cur, dict) and len(cur) == 1:
        key, value = next(iter(cur.items()))
        path.append(key)
        cur = value
    return path if isinstance(cur, dict) and not cur else None


def _classify_lines(text: str) -> list[tuple[str, list[str] | None]]:
    """Classify every physical line as ``("header", path)``/``("key", path)``/
    ``("other", None)``.

    Lines inside a multi-line string are always ``other`` — that is the whole
    point of tracking the state — so a `[deploy.hosts.x]`-shaped line inside a
    ``\"\"\"…\"\"\"`` value can never be mistaken for a table header.
    """
    out: list[tuple[str, list[str] | None]] = []
    ml: str | None = None
    for raw in text.splitlines():
        if ml is not None:
            out.append(("other", None))
            ml = _scan_line_strings(raw, ml)
            continue
        stripped = raw.strip()
        kind: str = "other"
        path: list[str] | None = None
        if stripped.startswith("["):
            inner = stripped[2:] if stripped.startswith("[[") else stripped[1:]
            closer = "]]" if stripped.startswith("[[") else "]"
            end = inner.find(closer)
            if end >= 0:
                candidate = _toml_path_of(inner[:end])
                if candidate is not None:
                    kind, path = "header", candidate
        elif stripped and not stripped.startswith("#") and "=" in raw:
            candidate = _toml_path_of(raw.split("=", 1)[0])
            if candidate is not None:
                kind, path = "key", candidate
        out.append((kind, path))
        ml = _scan_line_strings(raw, None)
    return out


def _inventory_prefix(doc: dict) -> list[str]:
    """The table prefix a row must be written under for THIS file.

    Mirrors ``load_hosts``' own precedence: a file that already uses the
    top-level ``[hosts.*]`` form (the user-global spelling) keeps it. Writing
    ``[deploy.hosts.x]`` into such a file would not merely add a row — it would
    make ``load_hosts`` prefer the new ``deploy.hosts`` table and stop seeing
    every existing ``[hosts.*]`` row.
    """
    deploy = doc.get("deploy")
    if isinstance(deploy, dict) and isinstance(deploy.get("hosts"), dict):
        return ["deploy", "hosts"]
    if isinstance(doc.get("hosts"), dict):
        return ["hosts"]
    return ["deploy", "hosts"]


def _row_at(doc: dict, path: list[str]) -> dict | None:
    cur: object = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur if isinstance(cur, dict) else None


def _without_path(doc: dict, path: list[str]) -> dict:
    """A deep copy of *doc* with *path* removed, EMPTY ancestors pruned.

    The pruning is what makes the "everything else is unchanged" comparison
    meaningful for a first row: appending ``[deploy.hosts.h]`` to a file that
    had no ``[deploy]`` table at all creates the two intermediate tables, and
    without pruning the comparison would report that legitimate creation as a
    change outside the row. Applied identically to both sides, so a table the
    operator really did have and really did lose still shows up as a change.
    """
    import copy

    out = copy.deepcopy(doc)
    chain: list[dict] = []
    cur: object = out
    for key in path[:-1]:
        if not isinstance(cur, dict) or key not in cur:
            return out
        chain.append(cur)
        cur = cur[key]
    if isinstance(cur, dict):
        cur.pop(path[-1], None)
        chain.append(cur)
    for depth in range(len(chain) - 1, 0, -1):
        if chain[depth]:
            break
        chain[depth - 1].pop(path[depth - 1], None)
    return out


def render_host_row(name: str, row: dict, prefix: list[str]) -> str:
    """Render one inventory table exactly as it will be written.

    ``tomli_w`` (already a ciu dependency) owns the escaping and the header
    spelling, so a host name needing quotes is quoted the way the reader
    expects rather than the way a format string guesses.
    """
    nested: dict = {name: dict(row)}
    for key in reversed(prefix):
        nested = {key: nested}
    return tomli_w.dumps(nested)


def _verify_round_trip(
    before_text: str, after_text: str, path: list[str], expected_row: dict
) -> None:
    """Refuse the write unless it is provably a single-row edit.

    Three independent checks, any of which failing raises rather than writing:
    the result parses; the target row is exactly what was asked for (managed
    keys) with the operator's own keys still present; and the ENTIRE rest of
    the document — every other table and key — is deep-equal to before.
    """
    before = tomllib.loads(before_text)
    try:
        after = tomllib.loads(after_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"[S14.7] refusing to write the inventory: the edited file would not "
            f"parse as TOML ({exc}). Nothing was written."
        ) from exc
    written = _row_at(after, path)
    if written is None:
        raise ValueError(
            "[S14.7] refusing to write the inventory: the edited file does not "
            "contain the row that was being written. Nothing was written."
        )
    for key, value in expected_row.items():
        if written.get(key) != value:
            raise ValueError(
                f"[S14.7] refusing to write the inventory: key '{key}' did not "
                f"survive the edit as written. Nothing was written."
            )
    prior = _row_at(before, path) or {}
    for key, value in prior.items():
        # ENROLL_ROW_KEYS are CIU's to set AND to unset: a rotation that no
        # longer needs `ssh_port` (back to 22) deliberately drops it. Only the
        # operator's OWN keys are protected here.
        if key in expected_row or key in ENROLL_ROW_KEYS:
            continue
        if written.get(key) != value:
            raise ValueError(
                f"[S14.7] refusing to write the inventory: the operator's own key "
                f"'{key}' on this host would be lost. Nothing was written."
            )
    if _without_path(before, path) != _without_path(after, path):
        raise ValueError(
            "[S14.7] refusing to write the inventory: the edit would change "
            "something outside this host's own row. Nothing was written."
        )


def _atomic_write_text(path: Path, text: str) -> None:
    """tmp sibling + ``os.replace`` (S8.4 convention, as engine/composefile do).

    An interrupted write can never leave a truncated or half-parsed inventory:
    the rename is atomic within the directory, and the pre-existing file mode is
    carried over so a 0600 inventory stays 0600.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        # mkstemp already created the file, and nothing above can succeed past
        # the rename, so the temp sibling is always still there to remove.
        os.unlink(tmp_path)
        raise


def _replace_row_body(
    lines: list[str],
    classified: list[tuple[str, list[str] | None]],
    header_idx: int,
    row_lines: list[str],
) -> list[str]:
    """Substitute the managed keys inside an EXISTING table's direct body.

    The body runs from just after its header to the next header line of any
    kind (so ``[deploy.hosts.<name>.admin]`` and every later table are outside
    it and untouched). Managed key lines are rewritten in place — preserving a
    trailing comment on the line — unmanaged keys, comments and blank lines are
    copied verbatim, and managed keys the row no longer carries are dropped.
    """
    end = len(lines)
    for idx in range(header_idx + 1, len(lines)):
        if classified[idx][0] == "header":
            end = idx
            break

    new_by_key = {line.split(" = ", 1)[0]: line for line in row_lines}
    body: list[str] = []
    seen: list[str] = []
    for idx in range(header_idx + 1, end):
        kind, path = classified[idx]
        key = path[0] if (kind == "key" and path and len(path) == 1) else None
        if key is None or key not in ENROLL_ROW_KEYS:
            body.append(lines[idx])
            continue
        seen.append(key)
        if key not in new_by_key:
            continue  # a managed key this row no longer sets (e.g. ssh_port 22)
        comment = ""
        stripped = lines[idx].rstrip()
        hash_at = _comment_start(stripped)
        if hash_at is not None:
            comment = "  " + stripped[hash_at:]
        body.append(new_by_key[key] + comment)

    trailing = 0
    while trailing < len(body) and not body[len(body) - 1 - trailing].strip():
        trailing += 1
    insert_at = len(body) - trailing
    additions = [line for key, line in new_by_key.items() if key not in seen]
    body[insert_at:insert_at] = additions
    return lines[:header_idx + 1] + body + lines[end:]


def _comment_start(text: str) -> int | None:
    """Index of the `#` that starts a trailing comment, or ``None``."""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            return i
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < n:
                if quote == '"' and text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    break
                i += 1
        i += 1
    return None


def write_host_row(
    hosts_path: Path, name: str, row: dict, *, replace: bool = False
) -> str:
    """Write ONE ``[deploy.hosts.<name>]`` row round-trip (S14.7c).

    A host that is not present yet is APPENDED at end of file — the strongest
    possible preservation guarantee, since every prior byte is copied verbatim
    and a table header at EOF is valid TOML after any complete document. An
    existing host is refused unless *replace*, in which case only the managed
    keys inside its own direct body change.

    Returns the rendered row text (for printing). Raises ``ValueError`` — tagged
    ``[S14.7]`` — and writes NOTHING on any refusal.
    """
    hosts_path = Path(hosts_path)
    before_text = hosts_path.read_text(encoding="utf-8") if hosts_path.exists() else ""
    try:
        doc = tomllib.loads(before_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"[S14.7] {hosts_path} is not valid TOML ({exc}); refusing to edit it. "
            f"Fix the file by hand first."
        ) from exc

    prefix = _inventory_prefix(doc)
    path = prefix + [name]
    existing = _row_at(doc, path)
    if existing is not None and not replace:
        raise ValueError(
            f"[S14.7] host '{name}' already has a row in {hosts_path}. "
            f"Re-run with --replace to rotate its key and pinned host key."
        )

    rendered = render_host_row(name, row, prefix)
    row_lines = rendered.splitlines()[1:]  # drop the table header line

    if existing is None:
        prefix_text = before_text
        if prefix_text and not prefix_text.endswith("\n"):
            prefix_text += "\n"
        separator = "\n" if prefix_text.strip() else ""
        after_text = prefix_text + separator + rendered
    else:
        lines = before_text.splitlines()
        classified = _classify_lines(before_text)
        header_idx = next(
            (
                idx
                for idx, (kind, hpath) in enumerate(classified)
                if kind == "header" and hpath == path
            ),
            None,
        )
        if header_idx is None:
            raise ValueError(
                f"[S14.7] host '{name}' is defined in {hosts_path} but its table "
                f"header could not be located for an in-place edit (a dotted-key "
                f"or inline-table spelling). Edit the row by hand, or remove it "
                f"and re-run."
            )
        after_lines = _replace_row_body(lines, classified, header_idx, row_lines)
        after_text = "\n".join(after_lines) + "\n"

    _verify_round_trip(before_text, after_text, path, row)
    _atomic_write_text(hosts_path, after_text)
    return rendered
