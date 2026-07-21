"""Handoff file parsing: YAML frontmatter + Markdown body. PACKAGE P01.

INTERFACE CONTRACT (frozen — implement exactly; do not change signatures):

- The handoff file format: first line exactly '---'; YAML mapping until the
  next line that is exactly '---'; everything after is the Markdown body.
- Schema: src/nyxloom/schemas/handoff-frontmatter.schema.json, loaded via
  importlib.resources from package 'nyxloom.schemas'.
- Errors carry file path and (where known) 1-based line numbers.
"""

from __future__ import annotations

import importlib.resources
import json
import re
from pathlib import Path

import jsonschema
import yaml

from .config import ProjectConfig
from .log import get_logger
from .types import Frontmatter

log = get_logger("frontmatter")


class HandoffParseError(Exception):
    """Raised on malformed frontmatter or schema violation.

    Attributes: path (str), errors (list[str]), line (int|None).
    """

    def __init__(self, path: str, errors: list[str], line: int | None = None):
        self.path, self.errors, self.line = path, errors, line
        super().__init__(f"{path}: " + "; ".join(errors))


def split_frontmatter(text: str) -> tuple[dict, str, int]:
    """Return (frontmatter_mapping, body, body_start_line).

    body_start_line is the 1-based line number of the first body line.
    Raises HandoffParseError(path='<text>') on: missing leading '---',
    unterminated frontmatter, YAML that is not a mapping, YAML parse errors
    (include the yaml error message).
    """
    lines = text.split("\n")

    # Check for leading ---
    if not lines or lines[0] != "---":
        log.debug("frontmatter split failed", reason="missing leading ---")
        raise HandoffParseError("<text>", ["missing leading '---'"])

    # Find closing ---
    closing_idx = None
    for i in range(1, len(lines)):
        if lines[i] == "---":
            closing_idx = i
            break

    if closing_idx is None:
        log.debug("frontmatter split failed", reason="unterminated frontmatter")
        raise HandoffParseError("<text>", ["unterminated frontmatter"])

    # Parse YAML
    fm_text = "\n".join(lines[1:closing_idx])
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        log.debug("frontmatter split failed", reason="yaml parse error")
        raise HandoffParseError("<text>", [f"YAML parse error: {e}"])

    # Ensure it's a mapping (dict)
    if not isinstance(data, dict):
        log.debug("frontmatter split failed", reason="not a mapping")
        raise HandoffParseError("<text>", ["frontmatter YAML is not a mapping"])

    # Body starts after the closing ---
    body_start_line = closing_idx + 2  # 1-based: +1 for 0-indexing, +1 for next line
    body = "\n".join(lines[closing_idx + 1:])

    log.debug("frontmatter split", body_start_line=body_start_line)
    return data, body, body_start_line


def schema_errors(data: dict) -> list[str]:
    """Validate against the packaged JSON schema; return human-readable
    error strings ('<json-path>: <message>'), empty when valid. Uses
    jsonschema.Draft202012Validator; errors sorted by path for determinism."""
    # Load schema from package resources
    schema_text = importlib.resources.files("nyxloom.schemas").joinpath(
        "handoff-frontmatter.schema.json"
    ).read_text(encoding="utf-8")
    schema = json.loads(schema_text)

    validator = jsonschema.Draft202012Validator(schema)
    errors = []

    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        # Build JSON path
        path_parts = list(error.absolute_path)
        if path_parts:
            json_path = ".".join(str(p) for p in path_parts)
        else:
            json_path = "$"

        errors.append(f"{json_path}: {error.message}")

    return errors


def parse_handoff(path: Path) -> tuple[Frontmatter, str]:
    """Read, split, schema-validate, construct types.Frontmatter.

    Raises HandoffParseError with ALL schema errors at once (not first-only).
    """
    text = path.read_text(encoding="utf-8")

    try:
        data, body, body_start_line = split_frontmatter(text)
    except HandoffParseError as e:
        e.path = str(path)
        raise

    # Validate schema
    errs = schema_errors(data)
    if errs:
        log.warning("handoff schema invalid", path=str(path), error_count=len(errs))
        raise HandoffParseError(str(path), errs)

    # Construct Frontmatter
    fm = Frontmatter.from_dict(data)

    log.debug("handoff parsed", path=str(path), id=fm.id)
    return fm, body


def discover_handoffs(cfg: ProjectConfig) -> list[Path]:
    """All files matching cfg.handoff_globs relative to cfg.root, sorted.
    Files under cfg.reports_dir are excluded."""
    results = []
    reports_dir = (cfg.root / cfg.reports_dir).resolve()

    for glob_pattern in cfg.handoff_globs:
        for match in cfg.root.glob(glob_pattern):
            if match.is_file():
                # Exclude files under reports_dir
                try:
                    match.resolve().relative_to(reports_dir)
                    # If we get here, it's under reports_dir, skip it
                    continue
                except ValueError:
                    # Not under reports_dir, include it
                    results.append(match)

    discovered = sorted(set(results))
    log.debug("handoffs discovered", count=len(discovered))
    return discovered


def convert_legacy_header(text: str) -> str:
    """Best-effort conversion of a v2 §7 blockquote header to frontmatter.

    Input: a handoff whose header lines look like
        > **Tier:** flash-high
        > **Depends-on:** app-P03 (merged), D-012 | none
        > **Base:** main after P53 merge
        > **Stack:** none|readonly|exclusive
        > **Session-hint:** fresh | resume <area> session
        > **Serialize-with:** P02 (shared files: ...)
        > **Escalate-if:** trigger a; trigger b
    Output: the same document with a generated frontmatter block prepended
    (fields it cannot infer get placeholders: input_revision '0000000',
    oracles [] -> a single TODO oracle, gates ['TODO'], scope.touch ['TODO'])
    and the original blockquote header left in place in the body.
    'none' values map to absent/empty. Depends-on '(merged)' suffixes are
    stripped. Serialize-with maps to mutexes: ['serialize-<peer-id>'].
    This is an evolution aid; its output intentionally FAILS lint until a
    human fills the TODOs — it must parse, not pass.
    """
    # Parse blockquote headers
    header_map = {}
    for line in text.split("\n"):
        if line.startswith("> **"):
            # Extract key and value
            match = re.match(r">\s*\*\*([^:]+):\*\*\s*(.*)", line)
            if match:
                key = match.group(1).lower().replace("-", "_")
                value = match.group(2).strip()
                header_map[key] = value

    # Build frontmatter
    tier = header_map.get("tier", "TODO").replace("none", "")
    session_hint = header_map.get("session_hint", "fresh")
    session = "fresh"
    if "resume" in session_hint.lower():
        parts = session_hint.split()
        area = parts[-1] if len(parts) > 1 else "TODO"
        session = f"resume:{area}"

    # Parse depends_on
    depends_on = []
    dep_str = header_map.get("depends_on", "")
    if dep_str and dep_str.lower() != "none":
        for part in re.split(r"[|,]", dep_str):
            part = part.strip()
            # Remove (merged) suffix
            part = re.sub(r"\s*\(merged\)\s*", "", part)
            if part:
                depends_on.append(part)

    # Parse mutexes from serialize-with
    mutexes = []
    serialize_with = header_map.get("serialize_with", "")
    if serialize_with and serialize_with.lower() != "none":
        # Extract peer ids
        for peer_id in re.findall(r"P\d{2,4}", serialize_with):
            mutexes.append(f"serialize-{peer_id}")

    # Parse stack
    stack = header_map.get("stack", "none").lower()
    if stack not in ("none", "readonly", "exclusive"):
        stack = "none"

    # Parse escalate_if
    escalate_if = []
    escalate_str = header_map.get("escalate_if", "")
    if escalate_str:
        for trigger in re.split(r"[;,]", escalate_str):
            trigger = trigger.strip()
            if trigger:
                escalate_if.append(trigger)

    if not escalate_if:
        escalate_if = ["TODO"]

    # Parse base
    base = None
    base_str = header_map.get("base", "")
    if base_str:
        match = re.match(r"(\S+)\s+after\s+(\S+)\s+merge", base_str)
        if match:
            base = {"branch": match.group(1), "after": match.group(2)}
        else:
            base = {"branch": base_str.split()[0] if base_str else "main"}

    # Build YAML frontmatter
    fm_dict = {
        "schema_version": 1,
        "id": "demo-P00-converted",
        "project": "demo",
        "title": "TODO",
        "tier": tier or "TODO",
        "input_revision": "0000000",
        "source": {"kind": "review"},
        "scope": {"touch": ["TODO"]},
        "oracles": [{"id": "O1", "observable": "TODO", "negative": "TODO", "gate": "TODO"}],
        "gates": ["TODO"],
        "escalate_if": escalate_if,
    }

    if depends_on:
        fm_dict["depends_on"] = depends_on
    if mutexes:
        fm_dict["mutexes"] = mutexes
    if stack != "none":
        fm_dict["stack"] = stack
    if session != "fresh":
        fm_dict["session"] = session
    if base:
        fm_dict["base"] = base

    # Convert to YAML
    fm_text = yaml.dump(fm_dict, default_flow_style=False, sort_keys=False)

    # Remove (merged) suffix from the preserved body text
    body_text = re.sub(r"\s*\(merged\)\s*", " ", text)

    log.debug("legacy header converted", tier=tier or "TODO")
    return f"---\n{fm_text}---\n\n{body_text}"
