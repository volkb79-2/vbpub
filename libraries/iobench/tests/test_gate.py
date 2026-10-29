"""IOB-GATE-01: the coverage exclusions and the mutation targets stay in step with the shipped modules."""
import ast
import configparser
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iobench"
EXEMPT = {"host.py", "__main__.py"}
NO_EXECUTABLE_TARGETS = {"__init__.py"}


def test_gate_01_coverage_exclusions_match():
    text = (ROOT / ".coveragerc").read_text()
    cp = configparser.ConfigParser(allow_no_value=True)
    cp.read_string(text)
    omitted = {line.strip().rsplit("/", 1)[-1] for line in cp["run"]["omit"].splitlines() if line.strip() and not line.strip().startswith("#")}
    assert omitted == EXEMPT
    # every omit entry carries a reason comment right above it
    lines = text.splitlines()
    for name in EXEMPT:
        i = next(n for n, line in enumerate(lines) if line.strip().endswith("/" + name))
        assert lines[i - 1].strip().startswith("#") and len(lines[i - 1].strip()) > 20
    assert cp["run"]["branch"] == "True"


def test_gate_01_package_initializer_has_no_executable_source():
    module = ast.parse((SRC / "__init__.py").read_text(encoding="utf-8"))
    assert len(module.body) == 1
    assert isinstance(module.body[0], ast.Expr)
    assert isinstance(module.body[0].value, ast.Constant)
    assert isinstance(module.body[0].value.value, str)


def test_gate_01_mutation_targets_cover_every_executable_module():
    doc = tomllib.loads((ROOT / "assay.toml").read_text())
    targets = set(doc["lanes"]["iobench"]["judge"]["targets"])
    excluded = EXEMPT | NO_EXECUTABLE_TARGETS
    shipped = {f"src/iobench/{p.name}" for p in SRC.glob("*.py")} - {
        f"src/iobench/{name}" for name in excluded
    }
    assert targets == shipped, (sorted(shipped - targets), sorted(targets - shipped))
    assert doc["lanes"]["iobench"]["judge"]["fail_under"] == 100.0


def test_gate_01_assay_coverage_lane_does_not_stop_after_a_failure():
    doc = tomllib.loads((ROOT / "assay.toml").read_text())
    argv = doc["lanes"]["iobench"]["argv"]
    assert not any(
        token == "--maxfail" or token.startswith("--maxfail=") for token in argv
    )


def test_gate_01_run_gate_registers_the_lanes():
    doc = tomllib.loads((ROOT / "run-gate.toml").read_text())
    assert {"r0-r1", "r2", "gate"} <= set(doc["lanes"])
    assert "--cov-fail-under=100" in " ".join(doc["lanes"]["r0-r1"]["argv"]) and "vm" not in str(doc["lanes"]).lower().replace("environment", "")
