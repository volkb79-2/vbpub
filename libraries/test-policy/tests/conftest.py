from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def _stat(pid: int, ppid: int, state: str, command: str, start_time: int) -> str:
    # Fields after comm: state, ppid, fields 5..21, starttime (field 22).
    return f"{pid} ({command}) {state} {ppid} " + "0 " * 17 + f"{start_time}\n"


def _mountinfo(mount_options: str = "rw", super_options: str = "rw") -> str:
    return (
        "36 29 0:32 / /proc rw,nosuid,nodev,noexec,relatime - proc proc "
        f"{mount_options},{super_options}\n"
    )


class FakeEstateRoots:
    """A real-format ``/proc`` and cgroup-v2 tree for boundary tests."""

    def __init__(self, base: Path) -> None:
        self.proc = base / "proc"
        self.cgroup = base / "cgroup"
        self.proc.mkdir()
        self.cgroup.mkdir()
        self.pid = 0
        self.mountinfo(_mountinfo())

    def process(
        self,
        pid: int,
        ppid: int,
        *,
        state: str = "S",
        command: str = "worker",
        start_time: int | None = None,
    ) -> None:
        path = self.proc / str(pid)
        path.mkdir(exist_ok=True)
        (path / "stat").write_text(
            _stat(pid, ppid, state, command, pid * 100 if start_time is None else start_time),
            encoding="utf-8",
        )

    def me(self, pid: int) -> None:
        self.pid = pid
        (self.proc / "self").mkdir(exist_ok=True)
        (self.proc / "self" / "stat").write_text(
            (self.proc / str(pid) / "stat").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (self.proc / "self" / "cgroup").write_text("0::/\n", encoding="utf-8")

    def mountinfo(self, text: str) -> None:
        (self.proc / "self").mkdir(exist_ok=True)
        (self.proc / "self" / "mountinfo").write_text(text, encoding="utf-8")

    def limit(
        self,
        name: str,
        value: str,
        *,
        directory: Path | None = None,
    ) -> None:
        target = self.cgroup if directory is None else directory
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(f"{value}\n", encoding="utf-8")

    def private(
        self,
        *,
        memory: str = "536870912",
        pids: str = "512",
        cgroup_path: str = "/",
        extra_paths: tuple[str, ...] = (),
        mount_options: str = "",
        super_options: str = "",
    ) -> None:
        self.process(1, 0, command="docker-init")
        self.process(2, 1, command="top")
        self.process(3, 2, command="pytest")
        self.process(4, 3, command="worker")
        self.me(3)

        lines = ["0::" + cgroup_path]
        lines.extend(extra_paths)
        (self.proc / "self" / "cgroup").write_text("\n".join(lines) + "\n", encoding="utf-8")
        leaf = self.cgroup.joinpath(*Path(cgroup_path).parts[1:])
        leaf.mkdir(parents=True, exist_ok=True)
        (self.cgroup / "cgroup.controllers").write_text("memory pids\n", encoding="utf-8")
        (leaf / "cgroup.controllers").write_text("memory pids\n", encoding="utf-8")
        self.limit("memory.max", memory, directory=leaf)
        self.limit("pids.max", pids, directory=leaf)
        self.mountinfo(_mountinfo(mount_options, super_options))


@pytest.fixture
def fake_estate(tmp_path) -> FakeEstateRoots:
    return FakeEstateRoots(tmp_path)
