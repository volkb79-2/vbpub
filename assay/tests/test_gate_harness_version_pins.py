"""B069 — the gate harnesses' version pins are visible to the LOCAL suite.

`gate/python/*.py` are the qualification harnesses the registered gate runs
INSIDE the `tester-unified` container. They are collected by no local pytest
run: every test that drives one is skipped outside the image (twenty of them,
measured on this branch), so a hardcoded contract version inside a harness has
historically been discovered only by a ~25-minute red gate run. Generation 8 of
Wave D lost two runs to exactly that — first `qualify_topos.py`'s
`schema_version != 9`, then its `carve-assets/W5/expected` root.

**The pins themselves are a FEATURE and this test does not remove them.** Per
DA-R24 the harnesses pin deliberately, so that advancing a verdict schema or a
carve-asset generation is a conscious edit rather than something a glob quietly
follows. What was missing is a red that fires in the ordinary suite. This file
is that red: it scans the harness TEXT and asserts each pin still names the
current contract.

Two pin families, and the boundary between them is load-bearing:

* **Verdict-document `schema_version`** — a subscript or `.get()` against the
  string key, compared to an integer literal. This must equal
  `assay.verdict.VERDICT_SCHEMA_VERSION`.
* **`carve-assets/W<n>` path construction** — the frozen drift-guard
  generation a harness reads its locked templates from. This must be the
  NEWEST `W<n>` directory under `nyxloom-trove/carve-assets/`.

Deliberately NOT scanned:

* The lane-file form `schema_version = 2` inside a TOML template string.
  `LANE_SCHEMA_VERSION` is a separate contract on its own version line (it
  stays 2 across this cut), and folding the two together would make this test
  demand a lane bump every time a verdict bumps.
* Prose references to earlier generations (``carve-assets/W4/…`` in a
  docstring, `W1/migrate_v5_to_v6.py` in a comment). Every earlier generation
  stays frozen and unedited — that is the `carve-assets/*/MANIFEST.md` rule —
  so a harness that NARRATES W4's history is correct, and only a harness that
  READS from a stale generation is stale.

Red-first is expressible without touching the real tree:
`test_the_scanner_reports_a_pre_cut_harness_as_stale` runs the same scanner
over a fixture copy of the pre-cut harness text (literal `9`, root `W5`) and
requires both findings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from conftest import PROJECT_ROOT

from assay.verdict import VERDICT_SCHEMA_VERSION

GATE_PYTHON = PROJECT_ROOT / "gate" / "python"
CARVE_ASSETS = PROJECT_ROOT / "nyxloom-trove" / "carve-assets"

# `doc["schema_version"] != 9` / `doc.get("schema_version") != 9`, either
# quoting, either comparison. The TOML assignment form is not matched.
_VERDICT_SCHEMA_PIN = re.compile(
    r"""(?:\[\s*|\.get\(\s*)(?P<q>["'])schema_version(?P=q)\s*(?:\]|\))\s*"""
    r"""(?:!=|==)\s*(?P<value>\d+)"""
)

# `… / "carve-assets" / "W6" / …` — the path-construction form only.
_CARVE_ASSET_GENERATION_PIN = re.compile(
    r"""(?P<q>["'])carve-assets(?P=q)\s*/\s*(?P<q2>["'])W(?P<value>\d+)(?P=q2)"""
)

_GENERATION_DIR = re.compile(r"W(\d+)")


@dataclass(frozen=True)
class Pin:
    """One hardcoded contract version found in a gate harness."""

    path: str
    lineno: int
    kind: str
    value: int
    text: str


def scan_pins(text: str, *, path: str) -> list[Pin]:
    """Every pinned contract version in one harness's source text."""
    pins: list[Pin] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in _VERDICT_SCHEMA_PIN.finditer(line):
            pins.append(
                Pin(
                    path=path,
                    lineno=lineno,
                    kind="verdict_schema_version",
                    value=int(match.group("value")),
                    text=line.strip(),
                )
            )
        for match in _CARVE_ASSET_GENERATION_PIN.finditer(line):
            pins.append(
                Pin(
                    path=path,
                    lineno=lineno,
                    kind="carve_asset_generation",
                    value=int(match.group("value")),
                    text=line.strip(),
                )
            )
    return pins


def stale_pins(text: str, *, path: str, schema_version: int, generation: int) -> list[Pin]:
    """The subset of :func:`scan_pins` that names something other than current."""
    current = {
        "verdict_schema_version": schema_version,
        "carve_asset_generation": generation,
    }
    return [pin for pin in scan_pins(text, path=path) if pin.value != current[pin.kind]]


def newest_carve_asset_generation() -> int:
    """The highest `W<n>` generation directory under `nyxloom-trove/carve-assets/`."""
    generations = [
        int(match.group(1))
        for entry in CARVE_ASSETS.iterdir()
        if entry.is_dir() and (match := _GENERATION_DIR.fullmatch(entry.name))
    ]
    assert generations, f"no W<n> drift-guard generation under {CARVE_ASSETS}"
    return max(generations)


def _harness_sources() -> list[tuple[str, str]]:
    sources = [
        (path.name, path.read_text(encoding="utf-8"))
        for path in sorted(GATE_PYTHON.glob("*.py"))
    ]
    assert sources, f"no gate harnesses under {GATE_PYTHON}"
    return sources


def _describe(pins: list[Pin]) -> str:
    return "\n".join(f"  {pin.path}:{pin.lineno} -> {pin.value}: {pin.text}" for pin in pins)


def test_gate_harnesses_pin_the_current_verdict_schema_version() -> None:
    """A harness that still checks the previous schema version fails HERE, not in the gate."""
    generation = newest_carve_asset_generation()
    found: list[Pin] = []
    stale: list[Pin] = []
    for name, text in _harness_sources():
        found.extend(
            pin for pin in scan_pins(text, path=name) if pin.kind == "verdict_schema_version"
        )
        stale.extend(
            pin
            for pin in stale_pins(
                text,
                path=name,
                schema_version=VERDICT_SCHEMA_VERSION,
                generation=generation,
            )
            if pin.kind == "verdict_schema_version"
        )
    assert found, (
        "no gate harness pins a verdict `schema_version` any more — either the "
        "harnesses stopped checking the contract, or this scanner's pattern has "
        "rotted; both are regressions, neither is a pass"
    )
    assert not stale, (
        f"gate harnesses pin a verdict schema_version other than the current "
        f"{VERDICT_SCHEMA_VERSION}; the registered gate would go red ~25 minutes "
        f"from now:\n{_describe(stale)}"
    )


def test_gate_harnesses_pin_the_newest_carve_asset_generation() -> None:
    """A harness reading a frozen earlier generation's templates fails HERE."""
    generation = newest_carve_asset_generation()
    found: list[Pin] = []
    stale: list[Pin] = []
    for name, text in _harness_sources():
        found.extend(
            pin for pin in scan_pins(text, path=name) if pin.kind == "carve_asset_generation"
        )
        stale.extend(
            pin
            for pin in stale_pins(
                text,
                path=name,
                schema_version=VERDICT_SCHEMA_VERSION,
                generation=generation,
            )
            if pin.kind == "carve_asset_generation"
        )
    assert found, (
        "no gate harness constructs a `carve-assets/W<n>` path any more — either "
        "the locked-template comparison was dropped, or this scanner's pattern "
        "has rotted"
    )
    assert not stale, (
        f"gate harnesses read a carve-asset generation other than the newest "
        f"W{generation}:\n{_describe(stale)}"
    )


# The pre-cut text of `gate/python/qualify_topos.py`, reduced to the two lines
# that made generation 8's gate runs red (`git show b2fd09f3^:gate/python/
# qualify_topos.py` carries them verbatim at :102 and :858). Keeping it as a
# fixture rather than a checkout keeps the red expressible for every future cut.
_PRE_CUT_HARNESS = '''\
_EXPECTED_ROOT = _PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "W5" / "expected"
_MANIFEST = _PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P25" / "manifest.json"


def normalize_artifact(document):
    normalized = copy.deepcopy(dict(document))
    if normalized.get("schema_version") != 9:
        raise QualificationError("artifact schema_version is not the current v9 contract")
    return normalized


_LANE_TEMPLATE = """schema_version = 2
[lane]
"""

# History, deliberately unpinned: `carve-assets/W4/test_acceptance_v8.py`.
'''


def test_the_scanner_reports_a_pre_cut_harness_as_stale() -> None:
    """Red-first: the same scanner over the pre-cut text finds both pins."""
    stale = stale_pins(
        _PRE_CUT_HARNESS,
        path="qualify_topos.py",
        schema_version=10,
        generation=6,
    )
    assert [(pin.kind, pin.value) for pin in stale] == [
        ("carve_asset_generation", 5),
        ("verdict_schema_version", 9),
    ], _describe(stale)


def test_the_scanner_ignores_the_lane_schema_version_and_prose() -> None:
    """The two deliberate non-subjects stay out of the result, on their own."""
    pins = scan_pins(_PRE_CUT_HARNESS, path="qualify_topos.py")
    assert not [pin for pin in pins if "_LANE_TEMPLATE" in pin.text], (
        "the lane-file `schema_version = 2` was matched; LANE_SCHEMA_VERSION is "
        "a separate contract and must not be dragged along by a verdict bump"
    )
    assert not [pin for pin in pins if pin.text.startswith("#")], (
        "a prose reference to a frozen earlier generation was matched as a pin"
    )
    # `P25` is a carve-asset directory but not a W<n> generation: it must not be
    # read as generation 25.
    assert not [pin for pin in pins if pin.value == 25]
