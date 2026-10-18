"""The image-runtime lane's own contract (D10), tested without Docker.

The scenarios in tests/runtime/ need real images; THIS file pins the rules the lane must
obey whatever the scenarios do: how it is registered, that it refuses to run without
explicit image refs, and that every container it starts is placed, named and removed
as specified.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "tests" / "runtime"
sys.path.insert(0, str(RUNTIME))
import harness  # noqa: E402
import test_image_contract  # noqa: E402
import test_system_defaults  # noqa: E402


@pytest.fixture()
def lane_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    monkeypatch.setenv("MDT_RUNTIME_IMAGE_NATIVE", "example/native:test")
    monkeypatch.setenv("MDT_RUNTIME_IMAGE_VSC", "example/vsc:test")


def test_the_lane_is_registered_as_a_bare_host_command_with_explicit_image_refs() -> None:
    lane = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]["image-runtime"]
    assert lane["kind"] == "command"
    assert lane["environment"] == "bare-host"
    assert lane["required_env"] == [
        "CGROUP_PARENT_DEV_GATES", "MDT_RUNTIME_IMAGE_NATIVE", "MDT_RUNTIME_IMAGE_VSC",
    ]
    assert lane["argv"][:4] == ["python3", "-m", "unittest", "discover"]
    assert (ROOT / lane["argv"][lane["argv"].index("-s") + 1]).is_dir()


def test_the_lane_has_no_third_party_dependency() -> None:
    allowed = set(sys.stdlib_module_names) | {"harness"} | {p.stem for p in RUNTIME.glob("test_*.py")}
    offenders = []
    for path in RUNTIME.glob("*.py"):
        for module in re.findall(r"^(?:from|import)\s+([A-Za-z_][\w]*)", path.read_text(encoding="utf-8"), re.MULTILINE):
            if module not in allowed and module != "__future__":
                offenders.append((path.name, module))
    assert offenders == []


@pytest.mark.parametrize("missing", ["MDT_RUNTIME_IMAGE_NATIVE", "MDT_RUNTIME_IMAGE_VSC", "CGROUP_PARENT_DEV_GATES"])
def test_the_lane_refuses_when_a_ref_is_unset_and_never_defaults_to_latest(
    monkeypatch: pytest.MonkeyPatch, lane_env: None, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(harness.ConfigurationError) as excinfo:
        harness.require_environment()
    assert missing in str(excinfo.value)
    assert ":latest" not in str(excinfo.value).replace("never falls back to a :latest tag", "")


def test_discovery_without_the_refs_fails_instead_of_passing(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(RUNTIME), "-p", "test_image_contract.py"],
        cwd=ROOT, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "MDT_RUNTIME_IMAGE_NATIVE" in result.stderr or "ConfigurationError" in result.stderr


class _FakeDocker:
    def __init__(self, fail_run: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail_run = fail_run

    def __call__(self, args, *, input_bytes=None, timeout=None):
        self.calls.append(list(args))
        if self.fail_run and args[0] == "run":
            raise subprocess.TimeoutExpired(args, timeout or 1)
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")


def test_every_container_is_gate_placed_init_offline_uniquely_named_and_removed_by_exact_name(
    monkeypatch: pytest.MonkeyPatch, lane_env: None
) -> None:
    fake = _FakeDocker()
    monkeypatch.setattr(harness, "_docker", fake)
    harness.run("example/native:test", ["bash", "-c", "true"], user="vscode", tty=True)
    harness.run("example/native:test", ["bash", "-c", "true"])
    runs = [call for call in fake.calls if call[0] == "run"]
    removals = [call for call in fake.calls if call[:2] == ["rm", "-f"]]
    assert len(runs) == 2 and len(removals) == 2
    names = []
    for run in runs:
        assert run[run.index("--cgroup-parent") + 1] == "dev-gates.slice"
        assert "--init" in run
        assert run[run.index("--network") + 1] == "none"
        name = run[run.index("--name") + 1]
        assert re.fullmatch(r"mdt-cockpit-rt-\d+-\d+", name), name
        names.append(name)
    assert len(set(names)) == 2, "names must be unique"
    assert [removal[2] for removal in removals] == names, "removed by EXACT name, nothing else"
    assert all(len(removal) == 3 for removal in removals), "no filter or extra selector may reach `docker rm`"


def test_a_hung_container_is_still_removed_by_exact_name(
    monkeypatch: pytest.MonkeyPatch, lane_env: None
) -> None:
    fake = _FakeDocker(fail_run=True)
    monkeypatch.setattr(harness, "_docker", fake)
    result = harness.run("example/native:test", ["bash", "-c", "sleep 1"], timeout=1)
    assert result.returncode == 124 and "TIMEOUT" in result.stderr
    run = next(call for call in fake.calls if call[0] == "run")
    removal = next(call for call in fake.calls if call[:2] == ["rm", "-f"])
    assert removal[2] == run[run.index("--name") + 1]


def test_volumes_and_throwaway_images_are_removed_by_exact_name(
    monkeypatch: pytest.MonkeyPatch, lane_env: None
) -> None:
    fake = _FakeDocker()
    monkeypatch.setattr(harness, "_docker", fake)
    with harness.volume() as name:
        assert re.fullmatch(r"mdt-cockpit-rt-\d+-\d+-vol", name)
    with harness.built_image("FROM scratch\n", label="x") as tag:
        assert tag.startswith("mdt-cockpit-rt-") and tag.endswith(":throwaway")
    assert ["volume", "rm", "-f", name] in fake.calls
    assert ["image", "rm", "-f", tag] in fake.calls
    build = next(call for call in fake.calls if call[0] == "buildx")
    assert build[build.index("--cgroup-parent") + 1] == "dev-gates.slice"
    assert build[build.index("--builder") + 1] == "default", "local-only bases are invisible to the remote builder"


def test_the_strace_parser_keeps_only_successful_opens_in_order() -> None:
    trace = "\n".join([
        '123   openat(AT_FDCWD, "/home/vscode/.config/htop/htoprc", O_RDWR|O_NOCTTY) = -1 ENOENT (No such file)',
        '123   openat(AT_FDCWD, "/etc/htoprc", O_RDONLY|O_NOCTTY) = 5',
        '123   openat(AT_FDCWD, "/usr/share/terminfo/x/xterm", O_RDONLY) = 6',
    ])
    assert test_system_defaults._opened(trace) == ["/etc/htoprc", "/usr/share/terminfo/x/xterm"]


def test_layer_sizes_parse_in_docker_units() -> None:
    assert test_image_contract._size_bytes("0B") == 0
    assert test_image_contract._size_bytes("16.2MB") == pytest.approx(16.2e6)
    assert test_image_contract._size_bytes("1.1GB") == pytest.approx(1.1e9)
    with pytest.raises(AssertionError):
        test_image_contract._size_bytes("lots")


@pytest.mark.parametrize(
    "created_by",
    [
        "RUN /bin/sh -c chmod -R g+rwX /opt/mdt/npm # buildkit",
        "RUN /bin/sh -c chgrp -R mdt /srv # buildkit",
        "RUN /bin/sh -c find /opt/mdt/npm -exec chgrp mdt {} + # buildkit",
        "RUN /bin/sh -c find /opt/mdt -type f -execdir chmod g+w {} + # buildkit",
        "RUN /bin/sh -c python3 -c \"import os; [os.chmod(p, 0o664) for p in os.listdir('/opt/mdt/npm')]\" # buildkit",
    ],
)
def test_a_large_permission_only_layer_is_detected(created_by: str) -> None:
    assert test_image_contract.permission_only_layers([f"657MB\t{created_by}"]) != []


@pytest.mark.parametrize(
    "line",
    [
        "657MB\tRUN /bin/sh -c umask 0002; npm install -g codex; mdt-perms fix /opt/mdt/npm # buildkit",
        "216MB\tRUN /bin/sh -c cp -a /tmp/omz /opt/mdt/oh-my-zsh && chown -R root:mdt /opt/mdt/oh-my-zsh # buildkit",
        "11.5MB\tRUN /bin/sh -c chmod -R g+rwX /opt/mdt/npm # buildkit",
        "211MB\tRUN /bin/sh -c cp -ar /tmp/features && chmod -R 0755 /tmp/dev-container-features # buildkit",
        "0B\tRUN /bin/sh -c echo hi # buildkit",
    ],
)
def test_content_layers_small_layers_and_unrelated_layers_are_not_flagged(line: str) -> None:
    assert test_image_contract.permission_only_layers([line]) == []


# ── the pty driver: waits for what the program SHOWS, never for a duration ─────


@pytest.fixture()
def fake_container(monkeypatch: pytest.MonkeyPatch):
    """Run `bash -c <script>` instead of `docker run ...`, still on a real pseudo-terminal."""
    removed: list[str] = []
    monkeypatch.setattr(harness, "DOCKER", "bash")
    monkeypatch.setattr(harness, "_docker", lambda args, **kw: removed.append(args[-1]) or subprocess.CompletedProcess(args, 0, b"", b""))

    def use(script: str) -> None:
        monkeypatch.setattr(harness, "_container_command", lambda name, image, argv, **kw: ["-c", script])

    use.removed = removed  # type: ignore[attr-defined]
    return use


def test_drive_sends_keys_when_the_screen_shows_the_expected_text(fake_container) -> None:
    fake_container("printf '\\033[1mREADY\\033[0m\\n'; read line; echo \"GOT:$line\"")

    session = harness.drive("img", ["x"], [("READY", b"hello\n")])

    assert session.returncode == 0 and session.completed_steps == 1
    assert "GOT:hello" in session.screen
    assert "\x1b" not in session.screen, "escape sequences are removed from the screen text"


def test_drive_answers_steps_in_order_and_each_only_after_the_previous_one(fake_container) -> None:
    fake_container("echo FIRST; read a; echo \"A:$a\"; echo SECOND; read b; echo \"B:$b\"")

    session = harness.drive("img", ["x"], [("FIRST", b"1\n"), ("SECOND", b"2\n")])

    assert session.completed_steps == 2
    assert session.screen.index("A:1") < session.screen.index("SECOND") < session.screen.index("B:2")


def test_drive_tolerates_a_dialog_that_never_appears(fake_container) -> None:
    fake_container("echo MAIN; read a; echo done")

    session = harness.drive("img", ["x"], [("MAIN", b"go\n"), ("Really quit", b"\r")])

    assert session.returncode == 0 and session.completed_steps == 1, "the optional trailing step is simply not completed"


def test_drive_reports_a_program_that_exits_without_showing_the_text(fake_container) -> None:
    fake_container("echo something else; exit 3")

    session = harness.drive("img", ["x"], [("READY", b"x")])

    assert session.returncode == 3 and session.completed_steps == 0


def test_drive_removes_its_container_by_exact_name(fake_container, lane_env) -> None:
    fake_container("true")

    harness.drive("img", ["x"], [])

    assert len(fake_container.removed) == 1 and re.fullmatch(r"mdt-cockpit-rt-\d+-\d+", fake_container.removed[0])


def test_no_scenario_lets_a_short_in_container_timeout_end_a_tool_or_decide_a_verdict() -> None:
    """Under host contention a tool that has not drawn its first screen after 4 s must not turn
    the lane red: every `timeout N` a scenario still uses is a failsafe of at least 120 s."""
    offenders = []
    for path in sorted(RUNTIME.glob("test_*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"\btimeout\s+(?:-s\s+\w+\s+)?\"?(\d+)\b", line):
                if int(match.group(1)) < 120:
                    offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []
    assert harness.DRIVE_FAILSAFE >= 120 and harness.GENEROUS_TIMEOUT >= 120


def test_drive_matches_text_that_the_program_interleaves_with_escape_sequences(fake_container) -> None:
    """htop and mc paint their key bar cell by cell: 'F10' and 'Quit' are separated by colours."""
    fake_container("printf 'F10\\033[30;46mQuit\\033[0m\\n'; read k; echo \"KEY:$k\"")

    session = harness.drive("img", ["x"], [(r"F10Quit", b"q\n")])

    assert session.completed_steps == 1 and "KEY:q" in session.screen


def test_install_verbs_in_the_arg_blob_do_not_exempt_a_permission_only_layer() -> None:
    """N4: `|82 OCI_DESCRIPTION=... install ... /bin/sh -c chmod -R ...` - the verb match must be
    on the command, not on the leading build-arg metadata."""
    line = (
        "657MB\t|82 OCI_DESCRIPTION=curated install of tar and cp tools PYTHON_VERSION=3.14 "
        "/bin/sh -c chmod -R g+rwX /opt/mdt/npm # buildkit"
    )
    assert test_image_contract.permission_only_layers([line]) != []
    # and a real content layer with the same ARG blob stays exempt
    content = line.replace("chmod -R g+rwX /opt/mdt/npm", "npm install -g codex; mdt-perms fix /opt/mdt/npm")
    assert test_image_contract.permission_only_layers([content]) == []


@pytest.mark.parametrize(
    ("created_by", "command"),
    [
        ("|2 A=install B=cp /bin/sh -c chmod -R g+w /srv # buildkit", "chmod -R g+w /srv # buildkit"),
        ("/bin/sh -c #(nop)  COPY file:abc in /opt/mdt/x ", "COPY file:abc in /opt/mdt/x "),
        ("COPY --chmod=0755 scripts/mdt-perms /usr/local/bin/mdt-perms # buildkit", "COPY --chmod=0755 scripts/mdt-perms /usr/local/bin/mdt-perms # buildkit"),
    ],
)
def test_command_text_drops_the_arg_blob_and_the_shell_prefix(created_by: str, command: str) -> None:
    assert test_image_contract.command_text(created_by) == command
