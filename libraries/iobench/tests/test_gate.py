"""IOB-GATE-01: the coverage exclusions and the mutation targets stay in step with the shipped modules."""
import configparser
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iobench"
EXEMPT = {"host.py", "__main__.py"}


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


def test_gate_01_mutation_targets_cover_every_module():
    doc = tomllib.loads((ROOT / "assay.toml").read_text())
    targets = set(doc["lanes"]["iobench"]["judge"]["targets"])
    shipped = {f"src/iobench/{p.name}" for p in SRC.glob("*.py")} - {f"src/iobench/{n}" for n in EXEMPT}
    assert targets == shipped, (sorted(shipped - targets), sorted(targets - shipped))
    assert doc["lanes"]["iobench"]["judge"]["fail_under"] == 100.0


def test_gate_01_run_gate_registers_the_lanes():
    doc = tomllib.loads((ROOT / "run-gate.toml").read_text())
    assert {"r0-r1", "r2", "gate"} <= set(doc["lanes"])
    assert "--cov-fail-under=100" in " ".join(doc["lanes"]["r0-r1"]["argv"]) and "vm" not in str(doc["lanes"]).lower().replace("environment", "")
