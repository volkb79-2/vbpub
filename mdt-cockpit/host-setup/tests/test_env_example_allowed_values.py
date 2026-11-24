"""Every variable in host-setup.env.example states its allowed values.

The example is the operator's only reference while editing /etc/mdt/host-setup.env,
so a variable without a stated grammar sends them to the validator source. The
convention is one machine-checkable line per variable or group of variables:

    # Allowed KEY[, KEY...]: <grammar>

Keys may use one level of bash-style brace expansion per group (``{A,B}``, the
empty alternative ``{,X}`` is allowed) so the twenty IO pins stay one line.
"""
from __future__ import annotations

import itertools
import re
import unittest
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "host-setup.env.example"
ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)
ALLOWED = re.compile(r"^# Allowed ([^:]+): (\S.*)$", re.MULTILINE)
BRACES = re.compile(r"\{([^{}]*)\}")


def expand(spec: str) -> list[str]:
    """Expand every ``{a,b}`` group in ``spec`` (cartesian product, left to right)."""
    parts = BRACES.split(spec)  # literal, alternatives, literal, alternatives, ...
    literals, groups = parts[0::2], [alternatives.split(",") for alternatives in parts[1::2]]
    result = []
    for choice in itertools.product(*groups):
        pieces = [literals[0]]
        for alternative, literal in zip(choice, literals[1:]):
            pieces.extend((alternative, literal))
        result.append("".join(pieces))
    return result


def documented_keys(text: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for spec_list, grammar in ALLOWED.findall(text):
        for spec in re.split(r",\s+", spec_list):
            for key in expand(spec.strip()):
                assert key not in keys, f"{key} has two Allowed lines"
                keys[key] = grammar
    return keys


class EnvExampleAllowedValues(unittest.TestCase):
    def setUp(self) -> None:
        self.text = EXAMPLE.read_text(encoding="utf-8")
        self.assigned = ASSIGNMENT.findall(self.text)
        self.documented = documented_keys(self.text)

    def test_expander_handles_groups_and_the_empty_alternative(self) -> None:
        self.assertEqual(expand("A_{R,W}{X,Y}"), ["A_RX", "A_RY", "A_WX", "A_WY"])
        self.assertEqual(expand("A_{,B_}C"), ["A_C", "A_B_C"])
        self.assertEqual(expand("PLAIN"), ["PLAIN"])

    def test_every_variable_has_an_allowed_line(self) -> None:
        undocumented = sorted(set(self.assigned) - set(self.documented))
        self.assertEqual(
            undocumented, [],
            "add a '# Allowed KEY: <grammar>' line above each (see the module docstring)",
        )

    def test_no_allowed_line_names_a_variable_the_example_does_not_set(self) -> None:
        stale = sorted(set(self.documented) - set(self.assigned))
        self.assertEqual(stale, [], "an Allowed line names a variable that is not assigned")

    def test_closed_vocabulary_lines_list_every_validator_value(self) -> None:
        # The enumerated policies are the ones an operator is most likely to mistype.
        for key, values in (
            ("DEVCONTAINER_MISSING_BIND_SOURCE_POLICY", ("create-by-spelling", "fail")),
            ("BUILDX_ACCIDENTAL_CONTAINER_POLICY", ("report-only", "terminate")),
            ("WATCHER_MEMORY_POLICY", ("enabled", "disabled")),
            ("CGROUP2_FLAGS", ("fix", "warn")),
            ("DEV_ZSWAP_WRITEBACK", ("yes", "no")),
        ):
            for value in values:
                self.assertIn(value, self.documented[key], key)


if __name__ == "__main__":
    unittest.main()
