"""Private staged invocations of Augeas' command-line editor."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from . import PlanRefused, Problem
from . import shadow

_DIST_LENSES = "/usr/share/augeas/lenses/dist"
_TIMEOUT = 30.0
# The HC-3 live smoke still has to verify this spelling with Debian's 1.14.1.
# Keep the choice here so callers and script builders cannot drift separately.
AUGTOOL_TRANSFORM_LENS_SPELLING = "module"


@dataclass(frozen=True)
class AugtoolOutput:
    """Output from a private-root augtool invocation."""

    stdout: str
    stderr: str
    saved: bytes | None = None


def require_available(path: str) -> None:
    """Fail with the documented package remedy when either Augeas tool is absent."""

    missing = [name for name in ("augtool", "augparse") if shutil.which(name) is None]
    if missing:
        raise PlanRefused(
            [
                Problem(
                    "edit.augtool.missing",
                    "install augeas-tools",
                    path=path,
                )
            ]
        )


def quote(value: str) -> str:
    """Quote one augtool string or path expression without allowing script lines."""

    if not isinstance(value, str) or any(char in value for char in ("\n", "\r", "\x00")):
        raise ValueError("augtool strings must be strings without newline or NUL")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def transform_lens(lens: str) -> str:
    """Return the lens token used by the single HC-3 transform spelling choice."""

    if AUGTOOL_TRANSFORM_LENS_SPELLING == "module":
        return lens.removesuffix(".lns")
    if AUGTOOL_TRANSFORM_LENS_SPELLING == "lns":
        return lens if lens.endswith(".lns") else lens + ".lns"
    else:
        raise ValueError("unsupported augtool transform lens spelling")


def transform_spec(lens: str, path: str) -> str:
    """Build the argv value for ``-t``; the path is quoted in Augeas syntax."""

    return f"{transform_lens(lens)} incl {quote(path)}"


def _copy_tree(source: Any, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_dir():
            _copy_tree(entry, target)
        elif entry.is_file():
            target.write_bytes(entry.read_bytes())
            target.chmod(0o600)


def _error_text(stdout: str, stderr: str, returncode: int) -> str | None:
    diagnostics = [
        line.strip()
        for line in (stdout + "\n" + stderr).splitlines()
        if "/augeas/" in line and "/error" in line
    ]
    if diagnostics:
        return "; ".join(diagnostics)
    if returncode:
        detail = (stderr or stdout).strip()
        return detail or f"augtool exited {returncode} without a diagnostic"
    return None


def run(
    *,
    path: str,
    lens: str,
    source: bytes,
    script: str,
    save_new: bool = False,
) -> AugtoolOutput:
    """Run augtool on a private copy and return its output and optional ``.augnew``."""

    if not isinstance(source, bytes):
        raise TypeError("augtool source must be bytes")
    with tempfile.TemporaryDirectory(prefix="augeaswrap-aug-") as temporary:
        private = Path(temporary)
        private.chmod(0o700)
        root = private / "root"
        root.mkdir(mode=0o700)
        lenses = private / "lenses"
        _copy_tree(resources.files("augeaswrap").joinpath("lenses"), lenses)
        target = root / path.lstrip("/")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(source)
        target.chmod(0o600)
        command_file = private / "commands.aug"
        command_file.write_text(script, encoding="utf-8")
        command_file.chmod(0o600)
        argv = [
            "augtool",
            f"--root={root}",
            "--noautoload",
            "--nostdinc",
            "-I",
            str(lenses),
            "-I",
            _DIST_LENSES,
            "-t",
            transform_spec(lens, path),
        ]
        if save_new:
            argv.append("--new")
        argv.extend(("-f", str(command_file)))
        try:
            result = shadow.run_command(tuple(argv), timeout=_TIMEOUT)
        except Exception as exc:
            raise PlanRefused(
                [Problem("edit.augtool.failed", f"augtool failed for {path}: {exc}", path=path)]
            ) from exc
        diagnostic = _error_text(result.stdout, result.stderr, result.returncode)
        if diagnostic is not None:
            raise PlanRefused(
                [Problem("edit.augtool.failed", f"augtool failed for {path}: {diagnostic}", path=path)]
            )
        saved_path = target.with_name(target.name + ".augnew")
        saved = saved_path.read_bytes() if save_new and saved_path.exists() else None
        return AugtoolOutput(result.stdout, result.stderr, saved)


def tree_script(path: str) -> str:
    return f"load\nprint {quote('/files' + path)}\nprint /augeas//error\n"


def match_script(expression: str) -> str:
    return f"load\nmatch {quote(expression)}\nprint /augeas//error\n"


def edit_script(lines: list[str]) -> str:
    return "load\n" + "".join(line + "\n" for line in lines) + "print /augeas//error\nsave\n"


def tree_values(stdout: str, file_root: str) -> dict[str, list[tuple[str, str]]]:
    """Parse printed direct children and leaf values under one loaded file root."""

    result: dict[str, list[tuple[str, str]]] = {}
    prefix = file_root.rstrip("/") + "/"
    for line in stdout.splitlines():
        if " = " not in line:
            continue
        node, value = line.split(" = ", 1)
        if value.startswith('"') or value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("augtool printed an invalid quoted value") from exc
        if not node.startswith(prefix):
            continue
        relative = node[len(prefix) :].split("/")
        if len(relative) != 2:
            continue
        record, field = relative
        result.setdefault(record, []).append((field, value))
    return result


def match_paths(stdout: str, file_root: str) -> tuple[str, ...]:
    """Extract node paths returned by one ``match`` command."""

    prefix = file_root.rstrip("/")
    paths: list[str] = []
    for line in stdout.splitlines():
        value = line.strip().split(" = ", 1)[0]
        if value == prefix or value.startswith(prefix + "/"):
            paths.append(value)
    return tuple(paths)


__all__ = [
    "AUGTOOL_TRANSFORM_LENS_SPELLING",
    "AugtoolOutput",
    "edit_script",
    "match_paths",
    "match_script",
    "quote",
    "require_available",
    "run",
    "transform_spec",
    "tree_script",
    "tree_values",
]
