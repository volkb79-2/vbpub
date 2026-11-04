"""Generate Fluxer's secrets once and write the per-role env files (ciu `pre_compose` hook).

WHY A HOOK AND NOT `[fluxer.secrets]` DIRECTIVES
------------------------------------------------
The Fluxer images read every secret from the ENVIRONMENT (no `*_FILE` forms),
and several secrets have a mandatory FORMAT that ciu's `GEN_LOCAL` (always
`token_urlsafe(32)`) cannot produce: the upload-relay secret is standard
base64, the VAPID pair is a P-256 key pair in base64url (the config loader
rejects a malformed one). `expose_env` would cover only the plain tokens.
So one hook owns all of it, and every value lives in exactly one place:

    <stack>/.ciu/secrets/            (0700, gitignored via `**/.ciu/`)
        fluxer-secrets.json          canonical store, 0600, generated ONCE
        accounts.json                admin + dummy-user passwords, 0600, ONCE
        env.fluxer env.api env.postgres env.meilisearch env.livekit env.s3init
                                     derived per-role env files, 0600,
                                     rewritten every run from the store

Compose loads the derived files with `env_file` (see ciu.compose.yml.j2). No
secret value is ever printed, logged, put in a template, or put in
`ciu.toml`: the hook prints only file PATHS and key COUNTS.

Idempotent: a key already in the store is never regenerated (rotating the
Postgres password, the signing keys or the VAPID pair would break the running
instance; see USERS.md for how to rotate deliberately). Only missing keys are
added.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
from pathlib import Path

# Keys that are plain random tokens (hex, like `openssl rand -hex 32`).
_HEX_KEYS = (
    "postgres_password",
    "meili_master_key",
    "s3_secret_key",
    "livekit_api_secret",
    "sudo_mode_secret",
    "connection_initiation_secret",
    "profile_pseudonym_secret",
    "gateway_rpc_auth_token",
    "erlang_cookie",
    "media_proxy_secret_key",
    "admin_secret_key_base",
    "admin_oauth_client_secret",
    "setup_token",
)

STORE_NAME = "fluxer-secrets.json"
ACCOUNTS_NAME = "accounts.json"


class SecretsError(RuntimeError):
    """Generation failed in a way that must stop the deploy."""


def _root(config: dict) -> dict:
    root = config.get("fluxer")
    if not isinstance(root, dict):
        raise SecretsError("no [fluxer] table in the merged config")
    return root


def secrets_dir(ctx) -> Path:
    return Path(ctx.stack_dir) / ".ciu" / "secrets"


def _write_private(path: Path, text: str) -> None:
    """Atomic write, mode 0600 from the first byte."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SecretsError(f"cannot read {path.name}: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise SecretsError(f"{path.name} is not a JSON object")
    return data


def _openssl(*args: str, stdin: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(
            ["openssl", *args], input=stdin, capture_output=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SecretsError(f"openssl is required to generate the VAPID key pair ({type(exc).__name__})") from exc
    if proc.returncode != 0:
        raise SecretsError("openssl failed while generating the VAPID key pair")
    return proc.stdout


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_vapid_pair() -> tuple[str, str]:
    """P-256 pair as unpadded base64url: (65-byte uncompressed point, 32-byte scalar).

    Same derivation as the upstream .env.example comments (`openssl ec ...
    -outform DER | tail -c 65` for the public key, bytes 7..39 of the private
    DER for the scalar).
    """
    pem = _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout")
    pub_der = _openssl("ec", "-pubout", "-outform", "DER", stdin=pem)
    priv_der = _openssl("ec", "-outform", "DER", stdin=pem)
    public, private = pub_der[-65:], priv_der[7:39]
    if len(public) != 65 or public[0] != 4 or len(private) != 32:
        raise SecretsError("generated VAPID pair has an unexpected shape")
    return _b64url(public), _b64url(private)


def ensure_store(directory: Path) -> tuple[dict, int]:
    """Return (store, number of keys added). Existing keys are never touched."""
    path = directory / STORE_NAME
    store = _load_json(path)
    added = 0
    for key in _HEX_KEYS:
        if not store.get(key):
            store[key] = secrets.token_hex(32)
            added += 1
    if not store.get("upload_relay_secret_b64"):
        store["upload_relay_secret_b64"] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        added += 1
    if not (store.get("vapid_public") and store.get("vapid_private")):
        store["vapid_public"], store["vapid_private"] = generate_vapid_pair()
        added += 2
    if added:
        _write_private(path, json.dumps(store, indent=2, sort_keys=True) + "\n")
    return store, added


def account_names(root: dict) -> list[str]:
    accounts = root.get("accounts") or {}
    admin = accounts.get("admin")
    users = accounts.get("users") or []
    names = [admin, *users]
    return [str(name) for name in names if name]


def ensure_accounts(directory: Path, names: list[str]) -> int:
    path = directory / ACCOUNTS_NAME
    data = _load_json(path)
    added = 0
    for name in names:
        if not data.get(name):
            data[name] = secrets.token_urlsafe(18)
            added += 1
    if added:
        _write_private(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return added


def derived_env_files(store: dict, livekit_key: str, s3_access_key: str) -> dict[str, dict[str, str]]:
    """File name -> {ENV: value}. The mapping mirrors the upstream compose bundle."""
    return {
        "env.fluxer": {
            "FLUXER_POSTGRES_PASSWORD": store["postgres_password"],
            "FLUXER_SEARCH_API_KEY": store["meili_master_key"],
            "FLUXER_S3_SECRET_ACCESS_KEY": store["s3_secret_key"],
            "FLUXER_LIVEKIT_API_SECRET": store["livekit_api_secret"],
            "FLUXER_SUDO_MODE_SECRET": store["sudo_mode_secret"],
            "FLUXER_CONNECTION_INITIATION_SECRET": store["connection_initiation_secret"],
            "FLUXER_PROFILE_PSEUDONYM_SECRET": store["profile_pseudonym_secret"],
            "FLUXER_GATEWAY_RPC_AUTH_TOKEN": store["gateway_rpc_auth_token"],
            "FLUXER_ERLANG_COOKIE": store["erlang_cookie"],
            "FLUXER_MEDIA_PROXY_SECRET_KEY": store["media_proxy_secret_key"],
            "FLUXER_ADMIN_SECRET_KEY_BASE": store["admin_secret_key_base"],
            "FLUXER_ADMIN_OAUTH_CLIENT_SECRET": store["admin_oauth_client_secret"],
            "FLUXER_MEDIA_PROXY_UPLOAD_RELAY_SECRET_BASE64": store["upload_relay_secret_b64"],
            "FLUXER_VAPID_PUBLIC_KEY": store["vapid_public"],
            "FLUXER_VAPID_PRIVATE_KEY": store["vapid_private"],
        },
        "env.api": {"FLUXER_SETUP_TOKEN": store["setup_token"]},
        "env.postgres": {"POSTGRES_PASSWORD": store["postgres_password"]},
        "env.meilisearch": {"MEILI_MASTER_KEY": store["meili_master_key"]},
        "env.livekit": {"LIVEKIT_KEYS": f"{livekit_key}: {store['livekit_api_secret']}"},
        "env.s3init": {
            "FLUXER_S3_ACCESS_KEY": s3_access_key,
            "FLUXER_S3_SECRET_KEY": store["s3_secret_key"],
        },
    }


def write_env_files(directory: Path, files: dict[str, dict[str, str]]) -> None:
    for name, pairs in files.items():
        text = "".join(f"{key}={value}\n" for key, value in pairs.items())
        _write_private(directory / name, text)


def validate_config(config: dict, ctx) -> list:
    """Side-effect-free preflight (S9.5)."""
    findings: list = []
    root = config.get("fluxer")
    if not isinstance(root, dict):
        return ["no [fluxer] table in the merged config"]
    names = account_names(root)
    if len(names) < 2:
        findings.append("[fluxer.accounts] needs an admin and at least one user")
    if len(set(names)) != len(names):
        findings.append("[fluxer.accounts] usernames must be unique")
    for name in names:
        if not (1 <= len(name) <= 32) or not all(c.isalnum() or c == "_" for c in name):
            findings.append(f"account name {name!r}: 1-32 letters, digits or underscore")
    livekit = root.get("livekit") or {}
    for key in ("tcp_port", "udp_port"):
        port = livekit.get(key)
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            findings.append(f"[fluxer.livekit] {key} must be an integer in 1024..65535")
    return findings


def run(config: dict, ctx) -> dict:
    root = _root(config)
    directory = secrets_dir(ctx)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    store, added = ensure_store(directory)
    accounts_added = ensure_accounts(directory, account_names(root))
    livekit_key = (root.get("livekit") or {}).get("api_key", "fluxer")
    s3_access_key = (root.get("s3") or {}).get("access_key", "fluxer")
    write_env_files(directory, derived_env_files(store, livekit_key, s3_access_key))
    # Paths and counts only, never a value.
    print(
        f"[FLUXER-SECRETS] store={directory / STORE_NAME} keys_added={added} "
        f"accounts_file={directory / ACCOUNTS_NAME} accounts_added={accounts_added}",
        flush=True,
    )
    return {}
