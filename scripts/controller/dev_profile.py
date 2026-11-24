#!/usr/bin/env python3
"""Resolve a dev profile and a verb to NUL-separated records for scripts/controller/dev.

usage: dev_profile.py WORKTREE PROJECT VERB [ARGS...]      (contract: docs/design/agent-dev/DESIGN.md section 2)
Every user word stays exactly one `cmd=` record. Exit 2 with a message on stderr for anything unknown or malformed.
"""
from __future__ import annotations

import os
import posixpath
import re
import shlex
import sys
import tomllib
from pathlib import Path

PROFILES = Path(os.environ.get("AGENT_DEV_TEST_PROFILES") or Path(__file__).resolve().with_name("dev-profiles.toml"))
NAME = re.compile(r"^[A-Za-z0-9_-]+$")
# Container properties live in dev-profiles.toml; the safety invariants (network none, cap-drop ALL, no-new-privileges,
# private PID namespace, --init, exact names, dev-gates.slice, the time limit) are applied by `dev` itself and are not keys.
SETTINGS = {"memory", "memory_max", "cpus", "pids", "max_s", "user", "ulimits", "stop_timeout", "mounts", "prefix", "env"}
KIND_KEYS = SETTINGS | {"python", "verbs", "default_args", "caps"}
PROFILE_KEYS = SETTINGS | {"kind", "lane", "environment", "dev_image", "workdir", "cache", "setup", "partial_args"}
# `dev` interpolates these into scripts that run on the HOST, outside the sandbox: strict allow-lists, then %q on top
MOUNT = re.compile(r"^\{(work|cache)\}:/[A-Za-z0-9_./-]+(:ro)?$")
IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]*$")
PROJECT_KEY = re.compile(r"^[A-Za-z0-9._/-]+$")


class Refusal(Exception):
    pass


def load(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise Refusal(f"cannot read {path}: {exc}") from exc


def check_keys(where: str, table: dict, allowed: set) -> None:
    for key in table:
        if key not in allowed:
            raise Refusal(f"{where}: '{key}' is not a configurable setting (safety invariants cannot be overridden)")


def settings(cfg: dict, profile: dict, kind: dict) -> dict:
    check_keys("[defaults]", cfg.get("defaults", {}), SETTINGS)
    check_keys(f"[kinds.{profile['kind']}]", kind, KIND_KEYS)
    check_keys("profile", profile, PROFILE_KEYS)
    out: dict = {}
    for layer in (cfg.get("defaults", {}), kind, profile):
        for key in SETTINGS:
            if key == "env":
                out["env"] = out.get("env", []) + list(layer.get("env", []))
            elif key in layer:
                out[key] = layer[key]
    if profile.get("cache"):
        out["mounts"] = out["mounts"] + ["{cache}:" + profile["cache"]]
    for mount in out["mounts"]:
        if not MOUNT.fullmatch(mount):
            raise Refusal(f"mount '{mount}' must be a {{work}} or {{cache}} path inside the container")
        dest = posixpath.normpath(mount.split(":")[1])
        if dest == "/work/.git" or dest.startswith("/work/.git/"):
            raise Refusal(f"mount '{mount}': /work/.git is always the read-only host .git, added by dev itself")
    return out


def lane_facts(worktree: Path, project: str, profile: dict) -> dict:
    """The environment image, and for kind="assay" lanes the env and interpreter, from the project's own lane."""
    cfg = load(worktree / project / "run-gate.toml")
    lane = cfg.get("lanes", {}).get(profile["lane"])
    if lane is None:
        raise Refusal(f"{project}/run-gate.toml has no lane '{profile['lane']}'")
    env_name = lane.get("environment")
    if env_name != profile["environment"]:
        raise Refusal(f"profile environment '{profile['environment']}' differs from lane '{profile['lane']}' environment '{env_name}'")
    envs = {**load(worktree / "run-gate.root.toml").get("environments", {}), **cfg.get("environments", {})}
    image = envs.get(env_name, {}).get("image")
    if not image:
        raise Refusal(f"environment '{env_name}' declares no image")
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        raise Refusal(f"environment '{env_name}': image {image!r} must match {IMAGE.pattern}")
    facts = {"image": image, "env": {}, "python": None}
    if lane.get("kind") == "assay":
        assay_lane = load(worktree / project / "assay.toml").get("lanes", {}).get(lane.get("assay_lane"))
        if assay_lane is None:
            raise Refusal(f"{project}/assay.toml has no lane '{lane.get('assay_lane')}'")
        facts["env"] = dict(assay_lane.get("env", {}))
        argv = assay_lane.get("argv") or []
        facts["python"] = argv[0] if argv else None
    return facts


def expand(template: list, args: list, python: str, partial: list) -> list:
    out: list = []
    for word in template:
        if word == "{args}":
            out += args
        elif word == "{partial}":
            out += partial if args else []
        else:
            out.append(word.replace("{python}", python))
    return out


def resolve(worktree: Path, project: str, verb: str, args: list) -> list:
    kinds_cfg = load(PROFILES)
    if not PROJECT_KEY.fullmatch(project):
        raise Refusal(f"project key '{project}' must match {PROJECT_KEY.pattern}")
    profile = kinds_cfg.get("profiles", {}).get(project)
    if profile is None:
        raise Refusal(f"unknown project '{project}' (known: {', '.join(sorted(kinds_cfg.get('profiles', {})))})")
    kind = kinds_cfg["kinds"][profile["kind"]]
    conf = settings(kinds_cfg, profile, kind)
    dev_image = profile.get("dev_image", "")
    if dev_image and (not isinstance(dev_image, str) or not IMAGE.fullmatch(dev_image)):
        raise Refusal(f"profile '{project}': dev_image {dev_image!r} must match {IMAGE.pattern}")
    facts = lane_facts(worktree, project, profile)
    env = dict(facts["env"])
    for pair in conf["env"]:
        key, _, value = pair.partition("=")
        if key in env and env[key] != value:
            raise Refusal(f"profile env {key} may only add keys: the lane already sets {key}={env[key]}")
        env[key] = value
    python = facts["python"] or kind.get("python", "")
    records = [("image", facts["image"]), ("dev_image", dev_image), ("workdir", "/work/" + profile["workdir"])]
    records += [(k, str(conf[k])) for k in ("memory", "memory_max", "cpus", "pids", "max_s", "user", "stop_timeout")]
    records += [("ulimit", u) for u in conf["ulimits"]] + [("mount", m) for m in conf["mounts"]] + [("prefix", w) for w in conf["prefix"]]
    records += [("cap", c) for c in kind.get("caps", {}).get(verb, [])]
    records += [("env", f"{k}={v}") for k, v in env.items()]
    verbs = kind["verbs"]
    if verb in ("status", "stop"):
        if args:
            raise Refusal(f"{verb} takes no arguments")
        return records
    if verb not in verbs or verb == "test-plain":
        raise Refusal(f"unknown verb '{verb}' for a {profile['kind']} project (known: {', '.join(sorted(v for v in verbs if v != 'test-plain'))}, status, stop)")
    template = verbs[verb]
    if verb == "shell" and args:
        raise Refusal("shell takes no arguments")
    if verb == "run":
        if not args or args[0] != "--" or len(args) < 2:
            raise Refusal("usage: dev -p PROJECT run -- CMD... (a command is required)")
        args = args[1:]
    elif verb == "test" and profile["kind"] == "rust" and args[:1] == ["--no-nextest"]:
        template, args = verbs["test-plain"], args[1:]
    elif verb == "test" and profile["kind"] == "python" and args[:1] == ["--"]:
        args = args[1:]
    elif verb == "clippy" and not args:
        args = kind["default_args"]["clippy"]
    elif verb == "example":
        if len(args) < 2 or not NAME.fullmatch(args[0]) or not NAME.fullmatch(args[1]):
            raise Refusal("usage: dev -p PROJECT example CRATE NAME [ARGS...] (crate and example names must be plain identifiers)")
        rest = args[3:] if args[2:3] == ["--"] else args[2:]
        template = [w for w in template if w != "{rest}"]
        template = [args[0] if w == "{1}" else args[1] if w == "{2}" else w for w in template]
        args = (["--"] + rest) if rest else []
        template = template + ["{args}"]
    cmd = expand(template, args, python, profile.get("partial_args", []))
    if verb == "test" and profile["kind"] == "python" and profile.get("setup"):
        script = "set -eu\n" + "".join(shlex.join(step) + "\n" for step in profile["setup"]) + 'exec "$@"'
        cmd = ["bash", "-c", script, "dev"] + cmd
    return records + [("cmd", word) for word in cmd]


def main(argv: list) -> int:
    if len(argv) < 4:
        print("usage: dev_profile.py WORKTREE PROJECT VERB [ARGS...]", file=sys.stderr)
        return 2
    try:
        records = resolve(Path(argv[1]), argv[2], argv[3], argv[4:])
    except Refusal as exc:
        print(f"dev: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(b"".join(f"{k}={v}".encode() + b"\0" for k, v in records))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
