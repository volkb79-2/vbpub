#!/usr/bin/env python3
"""Render the tls-edge templates without ciu.

Reads ciu-stack/ciu.defaults.toml.j2 (+ optional gitignored ciu-stack/ciu.toml.j2
override, same schema) through the same pipeline ciu uses — Jinja render, TOML
parse, deep merge — then renders docker-compose.yml, traefik.yml and
conf.d/certs.yml into edge-proxy/ with a standalone context (no `deploy`
namespace; `tls_edge.standalone` forced true).

DooD-aware: when running inside a container whose Docker daemon is the host's
(devcontainer), bind-mount sources are emitted as physical host paths.
"""

import argparse
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

try:
    import jinja2
except ImportError:
    sys.exit("error: python3-jinja2 is required (pip install jinja2 / apt install python3-jinja2)")

ROOT = Path(__file__).resolve().parent.parent
ACME_MODES = ("acme-tls", "acme-http", "acme-dns")
MODES = ACME_MODES + ("static", "dev")
TEMPLATES = ("ciu.compose.yml.j2", "traefik.yml.j2", "conf.d/certs.yml.j2")
STATIC_FILES = ("conf.d/options.yml", "conf.d/middlewares.yml")

# ciu v2 renders ciu.compose.yml; the standalone renderer targets edge-proxy/
# which ships a committed docker-compose.yml for plain `docker compose up`.
STANDALONE_OUTPUT_NAMES = {
    "ciu.compose.yml.j2": "docker-compose.yml",
}


def jinja_env() -> jinja2.Environment:
    return jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)


def render_toml(path: Path, context: dict) -> dict:
    """Jinja-render a .toml.j2 file, then parse it (mirrors ciu S3.2)."""
    try:
        text = jinja_env().from_string(path.read_text()).render(**context)
    except jinja2.UndefinedError as exc:
        sys.exit(f"error: {path}: {exc} (standalone renders have no ciu namespaces)")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        sys.exit(f"error: {path}: invalid TOML after render: {exc}")


def deep_merge(base: dict, override: dict) -> dict:
    """Dicts merge recursively; scalars and lists replace (ciu S3.3)."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def physical_path(logical: Path) -> Path:
    """Map a path inside this container to the host path the daemon sees.

    Native host (no /.dockerenv): identity.  DooD: resolve via this
    container's own mount table.  DinD or uninspectable: identity.
    """
    if not Path("/.dockerenv").exists():
        return logical
    override = os.environ.get("TLS_EDGE_PHYSICAL_DIR")
    if override:
        return Path(override) / logical.resolve().relative_to(ROOT)
    result = subprocess.run(
        ["docker", "inspect", socket.gethostname()],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return logical  # daemon cannot see us: assume shared filesystem namespace
    resolved = logical.resolve()
    best_dest = best_src = None
    for mount in json.loads(result.stdout)[0].get("Mounts", []):
        dest = Path(mount["Destination"])
        if (dest == resolved or dest in resolved.parents) and (
            best_dest is None or len(dest.parts) > len(best_dest.parts)
        ):
            best_dest, best_src = dest, Path(mount["Source"])
    if best_dest is None:
        sys.exit(
            f"error: DooD detected but {resolved} is not bind-mounted from the host —\n"
            "the daemon cannot reach it. Options: run on the host, set\n"
            "TLS_EDGE_PHYSICAL_DIR to the host path of the tls-edge directory,\n"
            "or use dev mode with [tls_edge.static] source = \"volume\"."
        )
    return best_src / resolved.relative_to(best_dest)


def load_config(stack_dir: Path, defaults_only: bool) -> dict:
    context = {"env": dict(os.environ)}
    config = render_toml(stack_dir / "ciu.defaults.toml.j2", context)
    override_file = stack_dir / "ciu.toml.j2"
    if not defaults_only and override_file.exists():
        config = deep_merge(config, render_toml(override_file, context))
    if "tls_edge" not in config:
        sys.exit("error: configuration is missing the [tls_edge] root table")
    return config["tls_edge"]


def validate(cfg: dict) -> None:
    mode = cfg["tls"]["mode"]
    if mode not in MODES:
        sys.exit(f"error: tls.mode {mode!r} is not one of {', '.join(MODES)}")
    if mode == "acme-http" and not cfg["ports"]["expose_http"]:
        sys.exit("error: tls.mode acme-http needs ports.expose_http = true "
                 "(the HTTP-01 challenge arrives on port 80)")
    if mode in ("static", "dev") and not cfg["static"]["domains"]:
        sys.exit(f"error: tls.mode {mode} needs [tls_edge.static] domains = [...]")
    if cfg.get("secrets"):
        sys.exit("error: [tls_edge.secrets] directives require ciu; in standalone "
                 "mode put credentials in the gitignored edge-proxy/.env instead")


def render_outputs(stack_dir: Path, out_dir: Path, cfg: dict,
                   certs_only: bool) -> list[Path]:
    env = jinja_env()
    context = {"tls_edge": cfg, "env": dict(os.environ)}
    written = []
    templates = ("conf.d/certs.yml.j2",) if certs_only else TEMPLATES
    for rel in templates:
        out_name = STANDALONE_OUTPUT_NAMES.get(rel, rel[: -len(".j2")])
        target = out_dir / out_name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            rendered = env.from_string((stack_dir / rel).read_text()).render(**context)
        except jinja2.UndefinedError as exc:
            sys.exit(f"error: {rel}: {exc}")
        # Cosmetic: collapse the blank-line runs Jinja block tags leave behind.
        while "\n\n\n" in rendered:
            rendered = rendered.replace("\n\n\n", "\n\n")
        target.write_text(rendered)
        written.append(target)
    if not certs_only:
        for rel in STATIC_FILES:
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stack_dir / rel, target)
            written.append(target)
    return written


def compose_check(out_dir: Path, cfg: dict) -> None:
    if cfg["tls"]["mode"] == "acme-dns" and not (out_dir / ".env").exists():
        (out_dir / ".env").write_text("# dummy for --check\n")
    result = subprocess.run(
        ["docker", "compose", "-f", str(out_dir / "docker-compose.yml"), "config", "-q"],
        cwd=out_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"error: docker compose config failed:\n{result.stderr.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack-dir", type=Path, default=ROOT / "ciu-stack")
    parser.add_argument("--out", type=Path, default=ROOT / "edge-proxy")
    parser.add_argument("--defaults-only", action="store_true",
                        help="ignore ciu.toml.j2 (maintainer regeneration of the "
                             "committed default artifacts)")
    parser.add_argument("--stamp", action="store_true",
                        help="write a real timestamp into conf.d/certs.yml "
                             "(used by the certbot deploy hook)")
    parser.add_argument("--certs-only", action="store_true",
                        help="render only conf.d/certs.yml")
    parser.add_argument("--check", action="store_true",
                        help="render to a temp dir and validate with "
                             "`docker compose config -q` instead of writing")
    args = parser.parse_args()

    cfg = load_config(args.stack_dir, args.defaults_only)
    validate(cfg)
    cfg["standalone"] = True
    if args.stamp:
        cfg["render_stamp"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            cfg["base_dir"] = "."
            render_outputs(args.stack_dir, out, cfg, certs_only=False)
            compose_check(out, cfg)
        print(f"OK: mode={cfg['tls']['mode']} renders and validates")
        return

    # base_dir points at the DEPLOYMENT directory (where compose runs and the
    # bind-mount sources live) — always edge-proxy/, independent of --out, so
    # drift-check renders to a temp dir produce identical mount lines.
    deploy_dir = ROOT / "edge-proxy"
    if args.defaults_only:
        # Committed default artifacts stay machine-neutral; DooD users re-render.
        base = deploy_dir
    else:
        base = physical_path(deploy_dir)
    cfg["base_dir"] = "." if base in (deploy_dir, deploy_dir.resolve()) else str(base)
    written = render_outputs(args.stack_dir, args.out, cfg, args.certs_only)
    for path in written:
        print(f"rendered: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    if cfg["base_dir"] != ".":
        print(f"note: DooD detected — bind mounts use the host path {cfg['base_dir']}")


if __name__ == "__main__":
    main()
