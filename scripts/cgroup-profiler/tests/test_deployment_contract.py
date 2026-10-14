"""Behavioral and structural checks for deployment and gate contracts."""

import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap
import tomllib


COMPOSE_TEMPLATE = Path(__file__).resolve().parents[1] / "ciu.compose.yml.j2"
PROJECT_ROOT = COMPOSE_TEMPLATE.parent


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.removesuffix("\n")


def test_host_gate_mounts_linked_worktree_and_git_common_directory(tmp_path):
    repo = tmp_path / "vbpub\n"
    repo.mkdir()
    _git(tmp_path, "init", "--initial-branch=main", str(repo))
    _git(repo, "config", "user.name", "Gate Test")
    _git(repo, "config", "user.email", "gate-test@example.invalid")
    (repo / "tracked.txt").write_text("gate fixture\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "fixture")

    worktree = repo / ".worktrees" / "gate,\"candidate\\t-\tname\n"
    worktree.parent.mkdir()
    _git(repo, "worktree", "add", "-b", "gate-candidate", str(worktree))
    actual_common_dir = Path(
        _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    assert actual_common_dir == repo / ".git"
    reported_common_dir = tmp_path / "git-metadata\n"

    docker_log = tmp_path / "docker.jsonl"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_git = shutil.which("git")
    assert real_git is not None
    git_shim = bin_dir / "git"
    git_shim.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [[ ${1:-} == "-C" && ${2:-} == "$GATE_WORKTREE" \\
                && ${3:-} == "rev-parse" && ${4:-} == "--path-format=absolute" \\
                && ${5:-} == "--git-common-dir" ]]; then
                printf '%s\\n' "$GATE_GIT_COMMON_DIR"
                exit 0
            fi
            exec "$REAL_GIT" "$@"
            """
        ),
        encoding="utf-8",
    )
    git_shim.chmod(0o755)

    docker = bin_dir / "docker"
    docker.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys

            args = sys.argv[1:]
            with open(os.environ["DOCKER_LOG"], "a", encoding="utf-8") as log:
                log.write(json.dumps(args) + "\\n")

            if args[:2] == ["image", "inspect"]:
                raise SystemExit(0)
            if args[0] == "inspect":
                fmt = args[args.index("--format") + 1]
                if "Mounts" in fmt:
                    print(json.dumps([
                        {"Destination": os.environ["TEST_CONTAINER_ROOT"], "Source": "/host/workspace/repo"},
                        {"Destination": os.environ["TEST_NESTED_ROOT"], "Source": "/host/worktrees"},
                        {"Destination": os.environ["TEST_DECOY_ROOT"], "Source": "/host/decoy"},
                        {"Destination": os.environ["TEST_GIT_COMMON_ROOT"], "Source": "/host/git-common"},
                    ]))
                elif "CgroupParent" in fmt and "NanoCpus" not in fmt:
                    print("dev-interactive.slice")
                elif "NanoCpus" in fmt:
                    name = args[-1]
                    parent = "dev-interactive.slice" if "probe" in name else "dev-gates.slice"
                    print("3000000000 " + parent)
                raise SystemExit(0)
            if args[0] == "run":
                print("container-id")
                raise SystemExit(0)
            if args[0] == "wait":
                print("0")
                raise SystemExit(0)
            if args[0] == "logs":
                if "probe" in args[-1]:
                    print("unit=dev-gates.slice\\ncgroup=/sys/fs/cgroup/dev-gates.slice")
                raise SystemExit(0)
            if args[0] in {"update", "rm"}:
                raise SystemExit(0)
            raise SystemExit(2)
            """
        ),
        encoding="utf-8",
    )
    docker.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "DOCKER_LOG": str(docker_log),
            "TEST_CONTAINER_ROOT": str(repo),
            "TEST_NESTED_ROOT": str(worktree.parent),
            "TEST_DECOY_ROOT": str(worktree.parent / "gate,\"candidate\\t"),
            "TEST_GIT_COMMON_ROOT": str(reported_common_dir),
            "GATE_WORKTREE": str(worktree),
            "GATE_GIT_COMMON_DIR": str(reported_common_dir),
            "REAL_GIT": real_git,
            "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
            "CGROUP_PARENT_DEV_INTERACTIVE": "dev-interactive.slice",
        }
    )
    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "tools" / "gate.sh"), str(worktree)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    calls = [json.loads(line) for line in docker_log.read_text(encoding="utf-8").splitlines()]
    gate_run = next(
        call
        for call in calls
        if call[0] == "run" and any(arg.startswith("--memory=") for arg in call)
    )
    assert "--init" in gate_run
    assert "--pids-limit=8192" in gate_run
    mounts = [gate_run[index + 1] for index, arg in enumerate(gate_run[:-1]) if arg == "--mount"]
    decoded_mounts = [next(csv.reader(io.StringIO(mount))) for mount in mounts]
    assert decoded_mounts == [
        [
            "type=bind",
            f"source=/host/worktrees/{worktree.name}",
            f"target={worktree}",
            "readonly",
        ],
        ["type=bind", "source=/host/git-common", f"target={reported_common_dir}", "readonly"],
    ]
    assert gate_run[gate_run.index("-w") + 1] == f"{worktree}/scripts/cgroup-profiler"
    assert "tester-unified/build-image.sh" in (PROJECT_ROOT / "tools" / "gate.sh").read_text(
        encoding="utf-8"
    )

    main_result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "tools" / "gate.sh"), str(repo)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert main_result.returncode == 0, main_result.stdout + main_result.stderr
    calls = [json.loads(line) for line in docker_log.read_text(encoding="utf-8").splitlines()]
    main_gate_run = next(
        call
        for call in calls
        if call[0] == "run"
        and any(arg.startswith("--memory=") for arg in call)
        and call[call.index("-w") + 1] == f"{repo}/scripts/cgroup-profiler"
    )
    assert "--init" in main_gate_run
    assert "--pids-limit=8192" in main_gate_run
    main_mounts = [
        main_gate_run[index + 1]
        for index, arg in enumerate(main_gate_run[:-1])
        if arg == "--mount"
    ]
    assert [next(csv.reader(io.StringIO(mount))) for mount in main_mounts] == [
        ["type=bind", "source=/host/workspace/repo", f"target={repo}", "readonly"]
    ]


def test_observer_r2_serializes_mutants_that_share_one_tester_cgroup():
    gate = tomllib.loads((PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    assay = tomllib.loads((PROJECT_ROOT / "assay.toml").read_text(encoding="utf-8"))
    assert gate["lanes"]["r2"]["resource_observer"] == "cgprofile"
    assert assay["lanes"]["r2"]["judge"]["mutation"]["jobs"] == 1


def test_only_daemon_uses_host_pid_namespace_and_mount_modes_are_explicit():
    compose = COMPOSE_TEMPLATE.read_text(encoding="utf-8")
    defaults = " ".join(
        (COMPOSE_TEMPLATE.parent / "ciu.defaults.toml.j2")
        .read_text(encoding="utf-8")
        .split()
    )
    gate_script = (COMPOSE_TEMPLATE.parent / "tools" / "gate.sh").read_text(
        encoding="utf-8"
    )

    assert 'cgroup: "private"' in compose
    assert 'network_mode: "none"' in compose
    assert compose.count('pid: "host"') == 1
    assert "CGPROFILE_PID_NAMESPACE_MODE: host" in compose
    assert "managed service alone joins the host PID namespace" in defaults
    assert 'cgroup: "host"' not in compose
    assert 'network_mode: "host"' not in compose
    assert "source: /proc\n        target: /hostproc\n        read_only: true" in compose
    assert (
        "source: /sys/fs/cgroup\n        target: /sys/fs/cgroup\n"
        "        read_only: false"
    ) in compose
    assert (
        "source: /run/dbus/system_bus_socket\n"
        "        target: /run/dbus/system_bus_socket\n"
        "        read_only: true\n"
        "        bind:\n"
        "          create_host_path: false"
    ) in compose
    assert (
        "source: {{ cgprofile.daemon.socket_dir }}\n"
        "        target: {{ cgprofile.daemon.socket_dir }}\n"
        "        bind:\n"
        "          create_host_path: false"
    ) in compose
    assert (
        "- {{ cgprofile.daemon.socket_dir }}:"
        "{{ cgprofile.daemon.socket_dir }}"
    ) not in compose
    assert "AttachProcessesToUnit" in compose
    assert "lib.placement.CgroupWriteGuard" in compose
    assert "--cgroupns=host" not in gate_script
    assert "--pid=host" not in gate_script
    assert "--net=host" not in gate_script


def test_user_docs_link_the_namespace_and_placement_rationale():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    design = (PROJECT_ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (PROJECT_ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    consumers_normalized = " ".join(consumers.split())
    defaults = (PROJECT_ROOT / "ciu.defaults.toml.j2").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    target = "DESIGN-GUIDE.md#daemon-safety-and-placement"
    assert target in readme
    assert target in consumers
    assert "The daemon alone uses" in readme
    assert "host PID namespace so DAMON sysfs can resolve host" in readme
    assert "the daemon never joins a host namespace" not in readme
    assert "PID and cgroup namespaces remain private." not in readme
    assert "consistency check, not host authentication" in readme
    assert "## Daemon safety and placement" in design
    assert "host PID namespace" in design
    assert "not an independent attestation" in design
    assert "create_host_path: false" in design
    assert (
        "refuses a missing source rather than creating an empty directory"
        in consumers_normalized
    )
    assert "process-table/PID-operation authority" in " ".join(design.split())
    assert "host-PID mode" in consumers
    assert "not an independent proof" in consumers_normalized
    assert "it is not PID 1 in the host namespace" in defaults
    assert "never joins a host" not in dockerfile
    assert "This image does not" in dockerfile
    assert (
        "host cgroup bind is read-only because it does not perform daemon placement"
        in " ".join(design.split())
    )
    assert (
        "daemon's host cgroup-v2 bind is therefore writable"
        in " ".join(design.split())
    )


def test_image_recipes_use_one_working_directory_and_build_before_ciu_up():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    pre_release = readme.split("Before the first CMRU release", 1)[1].split(
        "The daemon's version response", 1
    )[0]
    assert "From the repository root" in pre_release
    assert "cd scripts/cgroup-profiler" in pre_release
    assert pre_release.index("python3 build-push.py --build") < pre_release.index(
        "ciu up"
    )

    consumers = " ".join(
        (PROJECT_ROOT / "docs" / "CONSUMERS.md")
        .read_text(encoding="utf-8")
        .split()
    )
    local_development = consumers.split(
        "For local development after the release pin has been committed", 1
    )[1].split("## ", 1)[0]
    assert "From `scripts/cgroup-profiler/`" in local_development
    assert local_development.index("python3 build-push.py --build") < (
        local_development.index("ciu up --dir .")
    )

    start_daemon = consumers.split("## Start the host daemon", 1)[1].split(
        "## ", 1
    )[0]
    assert "local development image" in start_daemon
    assert "no `[cgprofile.image]` release override" in start_daemon
    assert "`ciu up` selects the pinned GHCR image instead" in start_daemon
