"""IOB-DOC-01/02: ids, anchors, and the examples in README / DESIGN-GUIDE / CONSUMERS."""
import ast
import json
import pathlib
import re
import shlex

import pytest
from cli_extended import CliIdentity

from iobench import cli, plausibility, proposals as P, result as res

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
DOCS = {"README.md": ROOT / "README.md", "DESIGN-GUIDE.md": ROOT / "docs" / "DESIGN-GUIDE.md", "CONSUMERS.md": ROOT / "docs" / "CONSUMERS.md"}
SPEC = ROOT / "docs" / "SPEC.md"
IDENT = CliIdentity("iobench", "0.1.0", "cache-proof disk benchmark", "iobench")
ID_RE = re.compile(r"IOB-[A-Z]+-\d+")


def spec_ids() -> set[str]:
    return set(re.findall(r"\*\*(IOB-[A-Z]+-\d+)", SPEC.read_text()))


def test_ids_defined_helper_sees_requirement_ids():
    assert {"IOB-CLI-01", "IOB-DOC-02", "IOB-STA-05"} <= spec_ids()


def collected_ids() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted(TESTS.glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef):
                m = re.fullmatch(r"test_([a-z]+)_(\d+)(?:_.*)?", node.name)
                if m:
                    out.setdefault(f"IOB-{m.group(1).upper()}-{m.group(2).zfill(2)}", []).append(f"{path.name}::{node.name}")
    return out


def test_doc_01_ids_anchors():
    ids, tested = spec_ids(), collected_ids()
    probes = {line.strip() for line in (TESTS / "probe_oracles.txt").read_text().splitlines() if line.strip()}
    assert probes == {"IOB-ACC-01", "IOB-GATE-01", "IOB-REL-01"} and probes <= ids
    untested = sorted(ids - set(tested) - probes)
    # IOB-ACC-01 / IOB-REL-01 are probes only; everything else needs a named test
    assert untested == [], untested
    unknown = sorted(set(tested) - ids)
    assert unknown == [], unknown
    # every cross-document anchor resolves
    for name, path in {**DOCS, "SPEC.md": SPEC}.items():
        text = path.read_text()
        for target, anchor in re.findall(r"\]\(([^)#\s]*)#([^)\s]+)\)", text):
            file = path if not target else (path.parent / target).resolve()
            assert file.exists(), (name, target)
            headings = {re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-") for h in re.findall(r"^#+\s+(.*)$", file.read_text(), re.M)}
            assert anchor in headings, (name, target, anchor)
        for target in re.findall(r"\]\(([^)#\s:]+)\)", text):
            assert (path.parent / target).resolve().exists(), (name, target)
    # controlled wrong: the id scan can go red
    assert "IOB-ZZZ-99" not in ids


def blocks(path: pathlib.Path, lang: str) -> list[str]:
    return re.findall(rf"^```{lang}\n(.*?)^```", path.read_text(), re.M | re.S)


def invocations(block: str) -> list[list[str]]:
    out = []
    for line in block.splitlines():
        for part in re.split(r";\s*|\s*&&\s*", line):
            part = part.strip()
            if part.startswith("#") or "iobench" not in part:
                continue
            tokens = shlex.split(part)
            if tokens and tokens[0] == "iobench":
                out.append(tokens[1:])
    return out


def test_doc_02_examples_parse_and_vocabulary_complete():
    registry = cli.build_cli(IDENT)
    parsed = 0
    for name, path in DOCS.items():
        for block in blocks(path, "bash"):
            for argv in invocations(block):
                if argv[:1] == ["--version"]:
                    continue
                try:
                    if argv[0] in registry.delegates:
                        registry.delegates[argv[0]].parse_args(argv[1:])
                    else:
                        registry.parse_args(argv + ["--dry-run"] if argv[0] in ("calibrate", "matrix", "run", "restore") else argv)
                except Exception as exc:  # noqa: BLE001
                    raise AssertionError(f"{name}: {' '.join(argv)}: {exc}") from exc
                parsed += 1
        for block in blocks(path, "json"):
            obj = json.loads(block)
            if "state" in obj:
                assert obj["state"] in res.EXIT and set(obj) == set(res.CheckResult(obj["state"]).as_dict()) - {"history"} | ({"history"} & set(obj))
                assert set(obj["warnings"]) <= set(res.WARNINGS)
            elif "writeback" in obj:
                for section, key, v in [(s, k, v) for s in ("writeback", "caps", "iocost_model", "iocost_qos") for k, v in P.iter_values(obj[s])]:
                    assert v["status"] in ("proven", "unproven", "absent") and v["basis"] in P.BASES, (section, key)
                    assert set(v.get("reasons", ())) <= set(P.UNPROVEN_REASONS)
                    if v["status"] == "absent":
                        assert v["reason"] in P.ABSENT_REASONS
            elif "history" in obj:
                assert obj["history"] in ("recorded", "single-run")
    assert parsed >= 12
    text = "\n".join(p.read_text() for p in DOCS.values())
    vocab = [*P.UNPROVEN_REASONS, *P.ABSENT_REASONS, *P.CACHE_STATES, *P.METERING, *P.FITS, *P.BASES, *res.EXIT, *res.WARNINGS, "read_floor_violated"]
    missing = [w for w in vocab if w not in text]
    assert missing == [], missing
    for code in range(10):
        assert re.search(rf"`{code}`", text), code
    for n in range(1, 18):
        assert f"IOB-PLA-{n:02d}" in text, n
    # controlled wrong: a bash block that no longer parses is caught
    with pytest.raises(Exception):
        registry.parse_args(["calibrate", "--no-such-flag"])
