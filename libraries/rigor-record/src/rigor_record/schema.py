"""A small JSON Schema validator for exactly the keyword subset the rigor schemas use (SPEC section 9).

The schemas are valid JSON Schema 2020-12 documents, so any external validator accepts them. This module exists
so the wheel stays stdlib-only. It refuses a schema that uses a keyword it does not implement: a silently
ignored keyword would make a constraint look enforced when it is not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

#: Keywords that constrain an instance and are implemented below.
CONSTRAINT_KEYWORDS = frozenset(
    {
        "type", "enum", "const", "pattern", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
        "properties", "required", "additionalProperties", "propertyNames", "items", "minItems", "maxItems",
        "oneOf", "$ref",
    }
)
#: Keywords that carry no constraint.
ANNOTATION_KEYWORDS = frozenset({"$schema", "$id", "$comment", "title", "description", "$defs"})

SCHEMA_FILES = (
    "rigor-common-1.json",
    "rigor-fragment-1.json",
    "rigor-record-1.json",
    "rigor-waivers-1.json",
)


@dataclass(frozen=True)
class Violation:
    """One failed constraint. ``path`` is a ``$.a.b[0]`` style location in the instance."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@lru_cache(maxsize=None)
def load_schema(filename: str) -> dict:
    """Load one packaged schema document by file name."""
    if filename not in SCHEMA_FILES:
        raise KeyError(f"unknown schema file {filename!r}")
    text = resources.files("rigor_record").joinpath("schemas", filename).read_text(encoding="utf-8")
    return json.loads(text)


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    # JSON Schema (ECMA-262) `$` matches only at the very end; Python's also matches before a trailing newline.
    # \Z is the strict form. The schemas use `$` solely as a final anchor (checked by the test suite).
    if pattern.endswith("$"):
        pattern = pattern[:-1] + r"\Z"
    return re.compile(pattern)


_compile_pattern = lru_cache(maxsize=None)(_compile_pattern)


def _type_ok(value: Any, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    raise ValueError(f"unsupported type name {name!r}")


class Validator:
    """Validate instances against a packaged schema document."""

    def __init__(self, filename: str):
        self._root_name = filename

    def validate(self, instance: Any, pointer: str = "") -> list[Violation]:
        """Validate against the document root, or against ``#<pointer>`` (for example ``/$defs/assay_lane``)."""
        node = load_schema(self._root_name)
        if pointer:
            node = self._pointer(self._root_name, pointer)
        return self._check(instance, node, self._root_name, "$")

    # -- reference resolution -------------------------------------------------------------------------------
    def _pointer(self, filename: str, pointer: str) -> Any:
        node: Any = load_schema(filename)
        for part in pointer.lstrip("/").split("/"):
            node = node[part]
        return node

    def _resolve(self, ref: str, filename: str) -> tuple[Any, str]:
        target_file, _, pointer = ref.partition("#")
        target_file = target_file or filename
        return self._pointer(target_file, pointer), target_file

    # -- the checker ----------------------------------------------------------------------------------------
    def _check(self, value: Any, schema: dict, filename: str, path: str) -> list[Violation]:
        unknown = set(schema) - CONSTRAINT_KEYWORDS - ANNOTATION_KEYWORDS
        if unknown:
            raise ValueError(f"unsupported schema keyword(s) {sorted(unknown)} in {filename}")
        out: list[Violation] = []
        if "$ref" in schema:
            target, target_file = self._resolve(schema["$ref"], filename)
            out += self._check(value, target, target_file, path)
        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_type_ok(value, n) for n in names):
                return out + [Violation(path, f"expected type {'|'.join(names)}, got {_describe(value)}")]
        if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
            out.append(Violation(path, f"must equal {json.dumps(schema['const'])}"))
        if "enum" in schema and not any(
            value == member and type(value) is type(member) for member in schema["enum"]
        ):
            out.append(Violation(path, f"must be one of {json.dumps(schema['enum'])}"))
        if "oneOf" in schema:
            results = [self._check(value, branch, filename, path) for branch in schema["oneOf"]]
            matches = sum(1 for result in results if not result)
            # Alternatives that fail only on the root type are not what the author meant ("null or an object"
            # reports the object's own violations); among the plausible ones the alternative with strictly the
            # fewest violations is the one the author meant, and its violations are reported.
            plausible = [r for r in results if r and not (len(r) == 1 and r[0].path == path and r[0].message.startswith("expected type"))]
            fewest = min((len(r) for r in plausible), default=0)
            closest = [r for r in plausible if len(r) == fewest]
            if matches == 0 and len(closest) == 1:
                out += closest[0]
            elif matches != 1:
                out.append(Violation(path, f"must match exactly one alternative, matched {matches}"))
        if isinstance(value, str):
            out += self._check_string(value, schema, path)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out += self._check_number(value, schema, path)
        if isinstance(value, list):
            out += self._check_array(value, schema, filename, path)
        if isinstance(value, dict):
            out += self._check_object(value, schema, filename, path)
        return out

    def _check_string(self, value: str, schema: dict, path: str) -> list[Violation]:
        out: list[Violation] = []
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(Violation(path, f"shorter than {schema['minLength']} characters"))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(Violation(path, f"longer than {schema['maxLength']} characters"))
        if "pattern" in schema and not _compile_pattern(schema["pattern"]).search(value):
            out.append(Violation(path, f"does not match pattern {schema['pattern']}"))
        return out

    def _check_number(self, value: float, schema: dict, path: str) -> list[Violation]:
        out: list[Violation] = []
        if "minimum" in schema and value < schema["minimum"]:
            out.append(Violation(path, f"below minimum {schema['minimum']}"))
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            out.append(Violation(path, f"must be above {schema['exclusiveMinimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            out.append(Violation(path, f"above maximum {schema['maximum']}"))
        return out

    def _check_array(self, value: list, schema: dict, filename: str, path: str) -> list[Violation]:
        out: list[Violation] = []
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(Violation(path, f"fewer than {schema['minItems']} items"))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(Violation(path, f"more than {schema['maxItems']} items"))
        if "items" in schema:
            for i, item in enumerate(value):
                out += self._check(item, schema["items"], filename, f"{path}[{i}]")
        return out

    def _check_object(self, value: dict, schema: dict, filename: str, path: str) -> list[Violation]:
        out: list[Violation] = []
        for key in schema.get("required", []):
            if key not in value:
                out.append(Violation(path, f"missing required key {key!r}"))
        properties = schema.get("properties", {})
        for key, item in value.items():
            child = f"{path}.{key}"
            if "propertyNames" in schema:
                out += [Violation(child, f"key {v.message}") for v in
                        self._check(key, schema["propertyNames"], filename, child)]
            if key in properties:
                out += self._check(item, properties[key], filename, child)
            elif "additionalProperties" in schema:
                extra = schema["additionalProperties"]
                if extra is False:
                    out.append(Violation(child, "unexpected key"))
                else:
                    out += self._check(item, extra, filename, child)
        return out


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    return type(value).__name__
