"""CIU-5 / S5a — `ciu dev` dev-loop profile + runner.

Covers parse/validate of `[<root>.dev]`, the pure `docker run` argv builder
(prebuild ordering, mounts, ports, env, network), and the run_dev orchestration
(dependency health gate, render failure → exit 2) with docker fully injected —
no real containers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import dev  # noqa: E402
from ciu.dev import DevProfile, build_run_command, parse_dev_profile, run_dev  # noqa: E402


# ---------------------------------------------------------------------------
# parse_dev_profile (S5a shape validation)
# ---------------------------------------------------------------------------


def _stack(dev_table: dict, root: str = "webapp_ui") -> dict:
    return {root: {"dev": dev_table}}


class TestParseDevProfile:
    def test_full_profile(self):
        profile = parse_dev_profile(
            _stack({
                "image": "node:22-alpine",
                "command": "npm run dev",
                "prebuild": ["npm run fetch:openapi", "npm run gen:api"],
                "port": 5173,
                "mount": ["./:/app", "/app/node_modules"],
                "depends_on": ["webapp-server"],
                "workdir": "/app",
                "env": {"NODE_ENV": "development"},
            }),
            "webapp_ui",
        )
        assert profile.command == "npm run dev"
        assert profile.image == "node:22-alpine"
        assert profile.prebuild == ("npm run fetch:openapi", "npm run gen:api")
        assert profile.ports == ((5173, 5173),)
        assert profile.mounts == ("./:/app", "/app/node_modules")
        assert profile.depends_on == ("webapp-server",)
        assert profile.env == {"NODE_ENV": "development"}

    def test_missing_dev_table_aborts(self):
        with pytest.raises(ValueError, match=r"\[S5a\].*no \[dev\] table"):
            parse_dev_profile({"webapp_ui": {}}, "webapp_ui")

    def test_missing_command_aborts(self):
        with pytest.raises(ValueError, match=r"\[S5a\].*command is required"):
            parse_dev_profile(_stack({"image": "node"}), "webapp_ui")

    def test_missing_image_and_build_aborts(self):
        with pytest.raises(ValueError, match=r"\[S5a\].*image.*build"):
            parse_dev_profile(_stack({"command": "npm run dev"}), "webapp_ui")

    def test_build_table_accepted_without_image(self):
        profile = parse_dev_profile(
            _stack({"command": "x", "build": {"context": ".", "target": "dev"}}),
            "webapp_ui",
        )
        assert profile.image is None
        assert profile.build == {"context": ".", "target": "dev"}

    def test_prebuild_must_be_list_of_strings(self):
        with pytest.raises(ValueError, match=r"\[S5a\].*prebuild"):
            parse_dev_profile(
                _stack({"image": "n", "command": "x", "prebuild": "npm run gen"}),
                "webapp_ui",
            )

    def test_port_host_container_string(self):
        profile = parse_dev_profile(
            _stack({"image": "n", "command": "x", "port": ["8080:80", 5173]}),
            "webapp_ui",
        )
        assert profile.ports == ((8080, 80), (5173, 5173))

    def test_port_bool_rejected(self):
        with pytest.raises(ValueError, match=r"\[S5a\].*port"):
            parse_dev_profile(
                _stack({"image": "n", "command": "x", "port": True}), "webapp_ui"
            )

    def test_port_bare_numeric_string_in_list(self):
        profile = parse_dev_profile(
            _stack({"image": "n", "command": "x", "port": ["5173"]}), "webapp_ui"
        )
        assert profile.ports == ((5173, 5173),)

    def test_port_malformed_host_container_rejected(self):
        with pytest.raises(ValueError, match=r"\[S5a\].*port"):
            parse_dev_profile(
                _stack({"image": "n", "command": "x", "port": "8080:"}), "webapp_ui"
            )


# ---------------------------------------------------------------------------
# build_run_command (pure argv)
# ---------------------------------------------------------------------------


class TestBuildRunCommand:
    def test_assembles_full_argv(self):
        profile = DevProfile(
            command="npm run dev",
            image="node:22-alpine",
            prebuild=("npm run fetch:openapi", "npm run gen:api"),
            ports=((5173, 5173),),
            mounts=("./:/app", "/app/node_modules"),
            env={"NODE_ENV": "development"},
            workdir="/app",
        )
        argv = build_run_command(profile, image="node:22-alpine",
                                 network="proj-dev-network", interactive=False)
        assert argv[:3] == ["docker", "run", "--rm"]
        assert "--network" in argv and "proj-dev-network" in argv
        assert "-p" in argv and "5173:5173" in argv
        assert argv[-3] == "sh" and argv[-2] == "-c"
        # prebuild steps run in order, then exec the dev command in one container.
        assert argv[-1] == (
            "npm run fetch:openapi && npm run gen:api && exec npm run dev"
        )
        # mounts + env present
        assert "./:/app" in argv and "NODE_ENV=development" in argv

    def test_no_prebuild_skips_prebuild_steps(self):
        profile = DevProfile(command="npm run dev", image="node",
                             prebuild=("npm run gen:api",))
        argv = build_run_command(profile, image="node", no_prebuild=True)
        assert argv[-1] == "exec npm run dev"

    def test_profile_network_wins_over_default(self):
        profile = DevProfile(command="x", image="node", network="pinned-net")
        argv = build_run_command(profile, image="node", network="default-net")
        assert "pinned-net" in argv and "default-net" not in argv


# ---------------------------------------------------------------------------
# run_dev orchestration (docker injected)
# ---------------------------------------------------------------------------


def _write_dev_stack(stack_dir: Path, *, depends_on: str | None = None) -> None:
    stack_dir.mkdir(parents=True, exist_ok=True)
    deps = f'depends_on = ["{depends_on}"]\n' if depends_on else ""
    (stack_dir / "ciu.defaults.toml.j2").write_text(
        "[webapp_ui]\n"
        "[webapp_ui.dev]\n"
        'image = "node:22-alpine"\n'
        'command = "npm run dev"\n'
        'prebuild = ["npm run gen:api"]\n'
        "port = 5173\n"
        'mount = ["./:/app"]\n'
        f"{deps}",
        encoding="utf-8",
    )


class TestRunDev:
    def test_happy_path_runs_dev_container(self, tmp_path):
        repo = tmp_path / "repo"
        stack = repo / "apps" / "webapp-ui"
        _write_dev_stack(stack)
        recorded = {}

        def run_fn(argv, **kw):
            recorded["argv"] = argv
            return 0

        rc = run_dev(
            "apps/webapp-ui",
            repo_root=repo,
            global_loader=lambda root: {},
            run_fn=run_fn,
        )
        assert rc == 0
        assert recorded["argv"][:3] == ["docker", "run", "--rm"]
        assert recorded["argv"][-1].endswith("exec npm run dev")

    def test_dependency_unhealthy_aborts(self, tmp_path):
        repo = tmp_path / "repo"
        stack = repo / "apps" / "webapp-ui"
        _write_dev_stack(stack, depends_on="webapp-server")
        ran = {"called": False}

        def run_fn(argv, **kw):
            ran["called"] = True
            return 0

        rc = run_dev(
            "apps/webapp-ui",
            repo_root=repo,
            global_loader=lambda root: {},
            wait_fn=lambda service: False,   # dependency never healthy
            run_fn=run_fn,
        )
        assert rc == 1
        assert ran["called"] is False  # never launched the dev container

    def test_dependency_healthy_then_runs(self, tmp_path):
        repo = tmp_path / "repo"
        stack = repo / "apps" / "webapp-ui"
        _write_dev_stack(stack, depends_on="webapp-server")
        rc = run_dev(
            "apps/webapp-ui",
            repo_root=repo,
            global_loader=lambda root: {},
            wait_fn=lambda service: True,
            run_fn=lambda argv, **kw: 0,
        )
        assert rc == 0

    def test_missing_stack_dir_returns_2(self, tmp_path):
        rc = run_dev(
            "apps/nope",
            repo_root=tmp_path,
            global_loader=lambda root: {},
            run_fn=lambda argv, **kw: 0,
        )
        assert rc == 2

    def test_bad_profile_returns_2(self, tmp_path):
        repo = tmp_path / "repo"
        stack = repo / "apps" / "broken"
        stack.mkdir(parents=True)
        # [dev] table missing `command` → ValueError [S5a] → exit 2
        (stack / "ciu.defaults.toml.j2").write_text(
            '[webapp_ui]\n[webapp_ui.dev]\nimage = "node"\n', encoding="utf-8"
        )
        rc = run_dev(
            "apps/broken",
            repo_root=repo,
            global_loader=lambda root: {},
            run_fn=lambda argv, **kw: 0,
        )
        assert rc == 2
