"""DA-3/A-395 -- qualification: a real Go R1 lane, real `go test`, real
oracle, inside `tester-unified-go:local`.

Every other Go test in this suite judges a COMMITTED coverprofile with no
toolchain, which is exactly what A-334 forbids as evidence about Go. Neither
gate image can close that: `tester-unified` has no Go (DESIGN-GUIDE §10), and
`tester-unified-go` is not where the Python suite runs. So this module is the
missing proof, and it is shaped by the controller's DA-3 and DA-5 rulings
(`vbpub@237b9585`):

* DA-3 -- the pattern is `test_javascript_real_vitest.py`: skipped
  everywhere except an environment that explicitly opts in, driving the real
  thing rather than a heredoc.
* DA-5/A-402 -- assay runs INSIDE the Go image as a consumer would.
  `tester-unified-go:local` inherits `/usr/bin/python3` (3.13.5, Debian
  trixie, from `golang:1.25`) against a `requires-python = ">=3.11"` floor,
  and has no pip and no ensurepip, so the shipped ZIPAPP is the only install
  path. This module builds that zipapp with the release's own builder, binds
  the repository the way `tools/tester-unified-gate.sh` already does
  (`docker inspect "$HOSTNAME"`), and runs it in the container under
  `--network=none` and `--cgroup-parent`.

**This test shells out to `docker`; assay never does (A-030).** The gate
script does exactly this, from the same cockpit, for the same reason: the
judge must run where the toolchain is.

**Why it drives the LIBRARY entry point rather than `assay run` --
B057/DA-8, and this is a defect, not a preference.** A Go coverprofile keys
records by IMPORT PATH (`example.invalid/harness/internal/calc/calc.go`)
while `git diff` names the same file `internal/calc/calc.go`.
`GoAdapter.module_path` strips the difference and NOTHING can set it through
the CLI -- `cli._built_in_registry` builds `GoAdapter()` with the `""`
default, `config._KNOWN_JUDGE_FIELDS` has no key for it, `assay run` has no
flag. So `assay run` on any real Go module refuses
`ERROR`/`UNREADABLE_ARTIFACT` today, measured, and the only way a Go consumer
can judge anything is `runner.run_lane` with an adapter they built --
`tests/test_standalone.py`'s own consumer shape. That is what runs here.
**The day DA-8 is ruled, this module moves to `python3 <pyz> run …` and the
assertions below should survive the move unchanged**; they are about the
verdict document, not about which entry point produced it.

The zipapp is built from HEAD's committed OID by `build_release.py`'s own
design, so this module measures the COMMITTED tree, never the working copy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _PROJECT_ROOT.parent
_IMAGE = "tester-unified-go:local"

_ENV_REASON = (
    "real-Go qualification: needs ASSAY_GO_QUALIFICATION=1, a working "
    "`docker`, and the `tester-unified-go:local` image. Neither gate image "
    "can run it (tester-unified has no Go; the Python suite does not run in "
    "tester-unified-go), so per DA-3 it is an explicit opt-in rather than a "
    "registered-gate test. A-042/A-043: the cockpit has no Go and never will."
)


def _qualification_enabled() -> bool:
    if os.environ.get("ASSAY_GO_QUALIFICATION") != "1":
        return False
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "image", "inspect", _IMAGE],
        capture_output=True, text=True, check=False, timeout=120,
    )
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(not _qualification_enabled(), reason=_ENV_REASON)


# --- the container harness ----------------------------------------------------


def _host_repo_root() -> str:
    """The HOST path this container's `/workspaces/vbpub` is bound from.

    Identical derivation to `tools/tester-unified-gate.sh`'s own (and to
    srdm's `tools/gate.sh`): asked of the daemon rather than hardcoded,
    because an operator's home directory is not a fact this repository may
    assume. `ASSAY_GATE_HOST_REPO_ROOT` overrides it exactly as it does
    there.
    """
    override = os.environ.get("ASSAY_GATE_HOST_REPO_ROOT")
    if override:
        return override
    hostname = os.environ.get("HOSTNAME") or Path("/etc/hostname").read_text().strip()
    out = subprocess.run(
        [
            "docker", "inspect", hostname, "--format",
            '{{range .Mounts}}{{if eq .Destination "/workspaces/vbpub"}}'
            "{{println .Source}}{{end}}{{end}}",
        ],
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout.strip()
    assert out and "\n" not in out, f"could not derive one host repo root: {out!r}"
    return out


def _host_path_for(path: Path) -> str:
    """*path* (a cockpit path under `/tmp`) as the daemon sees it.

    The same translation, for the other mount this container has. `/tmp` is
    bind-mounted too, which is why a scratch tree can be handed to a sibling
    container at all -- BRIEF-1's committed probe script recorded the
    symptom ("`/tmp` is not visible to the Docker daemon at the same path")
    and the cause is a path TRANSLATION, not an absence.
    """
    hostname = os.environ.get("HOSTNAME") or Path("/etc/hostname").read_text().strip()
    tmp_source = subprocess.run(
        [
            "docker", "inspect", hostname, "--format",
            '{{range .Mounts}}{{if eq .Destination "/tmp"}}'
            "{{println .Source}}{{end}}{{end}}",
        ],
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout.strip()
    assert tmp_source, "this container has no /tmp bind mount to translate"
    relative = path.resolve().relative_to("/tmp")
    return f"{tmp_source}/{relative}"


def _docker_run(*, mounts: list[tuple[str, str, bool]], workdir: str, argv: list[str]):
    command = ["docker", "run", "--rm", "--network=none"]
    slice_name = os.environ.get("CGROUP_PARENT_DEV_BACKGROUND")
    if slice_name:
        # A-334's placement half: a gate container is placed on the host
        # tier by its caller, never left to a fail-open transient slice.
        command.append(f"--cgroup-parent={slice_name}")
    for source, destination, readonly in mounts:
        spec = f"type=bind,src={source},dst={destination}"
        command.extend(["--mount", spec + (",readonly" if readonly else "")])
    command.extend(["-w", workdir, _IMAGE, *argv])
    return subprocess.run(command, capture_output=True, text=True, timeout=1800)


@pytest.fixture(scope="module")
def zipapp(tmp_path_factory) -> Path:
    """The shipped artifact, built from HEAD's committed OID by the
    release's own builder -- offline, from the hash-bound five-wheel
    closure. Not `src/`: a zipapp built from a source tree reports
    `0+unknown` as its own version, and `judge_provenance` would then name
    no build at all (`build_release.py`'s own first load-bearing rule)."""
    outdir = tmp_path_factory.mktemp("release") / "dist"
    proc = subprocess.run(
        [
            "python3", str(_PROJECT_ROOT / "gate" / "distribution" / "build_release.py"),
            "--repo", str(_REPO_ROOT), "--outdir", str(outdir),
        ],
        capture_output=True, text=True, timeout=1800,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ASSAY_RELEASE_COMPLETE=" in proc.stdout, proc.stdout
    built = sorted(outdir.glob("assay-*.pyz"))
    assert len(built) == 1, built
    return built[0]


# --- the fixture repository ---------------------------------------------------

_SETUP = r"""
set -eu
REPO="$1"
BODY="$2"
TEST="$3"
rm -rf "$REPO"
mkdir -p "$REPO/internal/calc"
cd "$REPO"
git init -q -b main .
git config user.email harness@example.invalid
git config user.name harness
printf 'module example.invalid/harness\n\ngo 1.25\n' > go.mod
printf '.assay/\n' > .gitignore
cp /work/base.go internal/calc/calc.go
cp /work/base_test.go internal/calc/calc_test.go
git add -A
git commit -q -m base
git rev-parse HEAD > /work/base-sha
cp "$BODY" internal/calc/calc.go
cp "$TEST" internal/calc/calc_test.go
git add -A
git commit -q -m head
git rev-parse HEAD > /work/head-sha
"""

#: One statement (line 6) inside a block whose EXTENT is lines 5-7 -- the
#: signature and the closing brace are inside it and are not statements. That
#: gap is the whole subject of this wave.
_BASE_GO = """\
package calc

// Add is one statement, on line 6.

func Add(a, b int) int {
\treturn a + b
}
"""

_BASE_TEST = """\
package calc

import "testing"

func TestAdd(t *testing.T) {
\tif Add(1, 2) != 3 {
\t\tt.Fatal("Add")
\t}
}
"""

#: PASS scenario -- a second, fully covered function.
_HEAD_PASS_GO = _BASE_GO + """
// Multiply is added by the head commit and is fully covered.
func Multiply(a, b int) int {
\treturn a * b
}
"""

_HEAD_PASS_TEST = _BASE_TEST + """
func TestMultiply(t *testing.T) {
\tif Multiply(2, 3) != 6 {
\t\tt.Fatal("Multiply")
\t}
}
"""

#: FAIL scenario -- a guard whose defensive branch is never reached.
_HEAD_FAIL_GO = _BASE_GO + """
// Guard's negative branch is never exercised by the test below.
func Guard(v int) int {
\tif v < 0 {
\t\treturn -1
\t}
\treturn v
}
"""

_HEAD_FAIL_TEST = _BASE_TEST + """
func TestGuard(t *testing.T) {
\tif Guard(5) != 5 {
\t\tt.Fatal("Guard")
\t}
}
"""

_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["go", "test", "./...", "-count=1", "-coverpkg=./...", "-covermode=atomic", "-coverprofile=.assay/cover.out"]
env = {{ GOPROXY = "off", GOFLAGS = "-mod=mod", GOTOOLCHAIN = "local" }}
env_passthrough = ["PATH", "HOME", "GOCACHE", "GOMODCACHE"]
budget = "10m"
allow_argv_append = false

[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "go"
source_roots = ["internal"]
fail_under = 100.0
allow_excluded = false
base = "{base}"

[lanes.unit.judge.coverage]
format = "go-cover"
artifact = ".assay/cover.out"
producer = "go-test"
"""

#: The library driver, run INSIDE the container by the zipapp's own
#: interpreter. It is the `cmd_run` sequence with one substitution: the
#: adapter is built with a `module_path`, which the CLI cannot do (B057).
_DRIVER = r"""
import json, sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])

from assay import git, provenance, runner
from assay.adapters.go import GoAdapter
from assay.config import load_lane_file

root = Path("/work/fixture")
lane_file = load_lane_file(root / "assay.toml")
lane = lane_file.lanes["unit"]
# `cmd_run`'s own first step (cli.py), reproduced rather than skipped: a
# verdict that cannot name the artifact that produced it is exactly the
# un-auditable refusal this project's provenance work exists to remove.
judge_provenance, unidentified = provenance.identify_judge()
assert judge_provenance is not None, unidentified
verdict = runner.run_lane(
    lane,
    commit=git.head_rev(root),
    repo=root,
    project_root=root,
    adapter=GoAdapter(module_path="example.invalid/harness"),
    assay_version="qualification",
    judge_provenance=judge_provenance,
)
Path("/work/verdict.json").write_text(
    json.dumps(verdict.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
)
print("DRIVER_OK", verdict.outcome.value, verdict.exit_code)
"""


def _run_scenario(tmp_path: Path, zipapp: Path, *, body: str, test: str) -> dict:
    """Materialise the fixture, run the lane in the image, return the verdict."""
    work = tmp_path / "work"
    (work / "dist").mkdir(parents=True)
    shutil.copy(zipapp, work / "dist" / zipapp.name)
    (work / "base.go").write_text(_BASE_GO, encoding="utf-8")
    (work / "base_test.go").write_text(_BASE_TEST, encoding="utf-8")
    (work / "head.go").write_text(body, encoding="utf-8")
    (work / "head_test.go").write_text(test, encoding="utf-8")
    (work / "setup.sh").write_text(_SETUP, encoding="utf-8")
    (work / "driver.py").write_text(_DRIVER, encoding="utf-8")
    # The container runs as uid 1003 (`gate`, the Dockerfile's own RUN_UID)
    # and the cockpit is 1000, so the tree has to be writable by it. The
    # fixture repository is then created INSIDE the container, which is what
    # makes git's own files owned by the uid that will read them -- no
    # `safe.directory` override anywhere.
    for path in (work, *work.rglob("*")):
        path.chmod(0o777 if path.is_dir() else 0o666)
    (work / "dist" / zipapp.name).chmod(0o777)

    host_work = _host_path_for(work)
    mounts = [(host_work, "/work", False)]

    setup = _docker_run(
        mounts=mounts, workdir="/work",
        argv=["sh", "/work/setup.sh", "/work/fixture", "/work/head.go", "/work/head_test.go"],
    )
    assert setup.returncode == 0, setup.stdout + setup.stderr
    base = (work / "base-sha").read_text(encoding="utf-8").strip()

    (work / "fixture" / "assay.toml").write_text(_LANE.format(base=base), encoding="utf-8")
    commit = _docker_run(
        mounts=mounts, workdir="/work/fixture",
        argv=["sh", "-c", "git add -A && git commit -q -m lane && git status --porcelain"],
    )
    assert commit.returncode == 0, commit.stdout + commit.stderr
    assert commit.stdout.strip() == "", f"the fixture tree is dirty: {commit.stdout!r}"

    driven = _docker_run(
        mounts=mounts, workdir="/work/fixture",
        argv=["python3", "/work/driver.py", f"/work/dist/{zipapp.name}"],
    )
    assert "DRIVER_OK" in driven.stdout, driven.stdout + driven.stderr
    return json.loads((work / "verdict.json").read_text(encoding="utf-8"))


# --- the image itself ---------------------------------------------------------


def test_the_go_gate_image_still_carries_the_interpreter_the_judge_needs():
    """A-402, asserted as an OUTCOME rather than as a mechanism (A-396's
    shape). `tester-unified-go/Dockerfile` installs no Python and never
    mentions one: the interpreter is INHERITED from `golang:1.25`'s Debian
    trixie base. A base-image change that drops or downgrades it would
    otherwise disarm every Go qualification silently, because the skip above
    would still fire for a reason that reads like "not enabled"."""
    probe = _docker_run(
        mounts=[], workdir="/",
        argv=["sh", "-c", "python3 --version; go version; id -u"],
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    version, go_version, uid = probe.stdout.split("\n")[:3]
    assert version.startswith("Python 3."), version
    major, minor = version.removeprefix("Python ").split(".")[:2]
    assert (int(major), int(minor)) >= (3, 11), (
        f"{version} is below assay's own requires-python floor, so the "
        "shipped zipapp can no longer run in this image"
    )
    assert go_version.startswith("go version go1."), go_version
    assert uid.strip() == "1003", uid


# --- the lane, end to end -----------------------------------------------------


def test_a_real_go_lane_passes_and_records_the_toolchain_that_judged_it(
    tmp_path: Path, zipapp: Path
):
    """The positive. A fully covered two-commit Go diff must PASS, and the
    verdict must record the helper that produced it (A-395/B047 item 5): a
    real `helpers[]` entry, `role="statement-positions"`, with an identity
    naming the toolchain that actually ran. `identity` comes from the
    helper's own `runtime.Version()` inside the program the toolchain
    compiled -- never a string assay handed itself, which is what A-334
    forbids as evidence about an external system."""
    verdict = _run_scenario(
        tmp_path, zipapp, body=_HEAD_PASS_GO, test=_HEAD_PASS_TEST
    )

    assert verdict["outcome"] == "PASS", json.dumps(verdict, indent=2)
    r1 = next(claim for claim in verdict["claims"] if claim["rigor"] == "R1")
    assert r1["status"] == "PASS"
    assert r1["coverage"]["pct"] == 100.0

    # THE STATEMENT-GRANULAR CLAIM (F008-A3). `Multiply` occupies four added
    # lines -- a comment, `func Multiply(a, b int) int {`, `return a * b` and
    # `}` -- and exactly ONE of them begins a counted statement. The rule
    # this wave removed would have called the signature and the brace
    # executable too, because both are inside the block's own extent.
    assert r1["coverage"]["executable"] == 1, r1["coverage"]
    assert r1["coverage"]["covered"] == 1, r1["coverage"]
    assert r1["coverage"]["missing_lines"] == {}

    assert "helpers" in verdict, "a Go lane invokes the oracle, so it records it"
    assert len(verdict["helpers"]) == 1, verdict["helpers"]
    helper = verdict["helpers"][0]
    assert helper["role"] == "statement-positions"
    assert helper["tool"] == "go"
    assert helper["resolved_path"].endswith("/go"), helper
    assert helper["identity"].startswith("go version go1."), helper

    # The verdict is a real consumer artifact, produced by the shipped
    # archive and naming it (A-402's whole point).
    assert verdict["judge_provenance"]["artifact"] == "zipapp"


def test_a_real_go_lane_fails_and_names_the_uncovered_statement_line(
    tmp_path: Path, zipapp: Path
):
    """The paired negative, and the one that would catch an over-permissive
    correction: `Guard`'s `return -1` is genuinely never executed, and it is
    the ONLY uncovered statement in the diff. A verdict that named the `if`
    line, the closing brace or the signature would be reporting a line the
    developer cannot make executable."""
    verdict = _run_scenario(
        tmp_path, zipapp, body=_HEAD_FAIL_GO, test=_HEAD_FAIL_TEST
    )

    assert verdict["outcome"] == "FAIL", json.dumps(verdict, indent=2)
    r1 = next(claim for claim in verdict["claims"] if claim["rigor"] == "R1")
    assert r1["status"] == "FAIL"
    assert r1["reason_code"] == "UNCOVERED_LINES"

    missing = r1["coverage"]["missing_lines"]
    assert list(missing) == ["internal/calc/calc.go"], missing
    lines = missing["internal/calc/calc.go"]
    source = _HEAD_FAIL_GO.splitlines()
    assert [source[line - 1].strip() for line in lines] == ["return -1"], (
        f"the uncovered set names {lines}, which is "
        f"{[source[line - 1] for line in lines]}"
    )

    # The helper record travels with the FAIL exactly as with the PASS: it
    # describes what produced the claim, not whether the claim was good news.
    assert verdict["helpers"][0]["role"] == "statement-positions"
