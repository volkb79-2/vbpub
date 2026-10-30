"""MDT-HS-02 item 4: the consumer devcontainer template against the facts it states.

The template is parsed as JSONC (with the initializer's own parser) and every fact is
asserted against its single source: the Dockerfile for PATH / NPM / venv / the `mdt`
group, host-setup's units for the bind sources and slice names, the initializer for the
home layout, the example config for the cgroup defaults, the builder script for the
remote name.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "templates"))
import initialize_container_environment as bootstrap  # noqa: E402

TEMPLATE_TEXT = (ROOT / "templates" / "devcontainer.json").read_text(encoding="utf-8")
TEMPLATE = bootstrap._parse_jsonc_object(TEMPLATE_TEXT)
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
EXAMPLE = (ROOT / "host-setup" / "host-setup.env.example").read_text(encoding="utf-8")
HS = ROOT / "host-setup"


def _example(key: str) -> str:
    return re.search(rf'(?m)^{key}="?([^"\n]*)"?$', EXAMPLE).group(1)


def _mounts() -> dict[str, dict[str, str]]:
    result = {}
    for spec in TEMPLATE["mounts"]:
        fields = dict(part.split("=", 1) if "=" in part else (part, "") for part in spec.split(","))
        result[fields["target"]] = fields
    return result


def _dockerfile_group_roots() -> list[str]:
    match = re.search(r"(?m)^# Group that owns the image-managed tool trees \(([^)]+)\)", DOCKERFILE)
    return [part.strip() for part in match.group(1).split(",")]


def _image_path_prefix() -> list[str]:
    return re.search(r'(?m)^ENV MDT_PATH_PREFIX="([^"]+)"', DOCKERFILE).group(1).split(":")


def _image_home() -> str:
    suffix = re.search(r'(?m)^ENV MDT_PATH_SUFFIX="([^"]+)"', DOCKERFILE).group(1)
    return str(Path(suffix).parent.parent)  # <home>/.local/bin -> <home>


def test_the_fresh_home_layout_matches_the_initializer() -> None:
    """Sources: the initializer's constants, its prefix rule and the README's stated modes; the
    container-side home comes from the image (MDT_PATH_SUFFIX). Literals: none for names or modes."""
    mounts = _mounts()
    assert mounts[_image_home()]["source"] == "${localEnv:HOME}/" + bootstrap.HOME_SOURCE_SPELLING + "/"
    assert mounts["/tmp"]["source"] == "${localEnv:HOME}/" + bootstrap.TMP_SOURCE_SPELLING + "/"
    # One home per checkout: the spelling carries the workspace folder basename, and the
    # initializer's fallback names are that spelling expanded for the current checkout.
    assert bootstrap.HOME_SOURCE_SPELLING == bootstrap.SOURCE_PREFIX + "${localWorkspaceFolderBasename}-home"
    assert bootstrap.TMP_SOURCE_SPELLING == bootstrap.SOURCE_PREFIX + "${localWorkspaceFolderBasename}-tmp"
    assert bootstrap.HOME_SOURCE_NAME == bootstrap.SOURCE_PREFIX + Path.cwd().name + "-home"
    assert bootstrap.TMP_SOURCE_NAME == bootstrap.SOURCE_PREFIX + Path.cwd().name + "-tmp"
    readme = (ROOT / "templates" / "README.md").read_text(encoding="utf-8")
    assert f"mode `{bootstrap.HOME_MODE:04o}`" in readme
    assert f"mode `{bootstrap.TMP_MODE & 0o7777:04o}`" in readme
    assert mounts["/run/mdt/host-ssh"]["source"] == "${localEnv:HOME}/.ssh/"
    assert bootstrap.MODE_OVERRIDES[".ssh"] == 0o700
    assert "readonly" in mounts["/run/mdt/host-ssh"]
    # every host $HOME source is spelled mdt-cockpit-<purpose> or is the native ~/.ssh
    for fields in mounts.values():
        source = fields["source"]
        if source.startswith("${localEnv:HOME}/"):
            assert source.removeprefix("${localEnv:HOME}/").startswith((bootstrap.SOURCE_PREFIX, ".ssh")), source


def test_opt_mdt_and_srv_belong_to_the_mdt_group_in_the_image() -> None:
    roots = _dockerfile_group_roots()
    assert roots, "the Dockerfile names the group-owned roots"
    for root in roots:
        assert root in TEMPLATE_TEXT, root
    assert 'groupadd -g "${MDT_GID}" mdt' in DOCKERFILE


def test_path_order_npm_prefix_and_venv_are_defined_once_by_the_image() -> None:
    env = {**TEMPLATE["containerEnv"], **TEMPLATE.get("remoteEnv", {})}
    assert "PATH" not in env and "NPM_CONFIG_PREFIX" not in env
    parts = _image_path_prefix()
    assert re.search(r'(?m)^ENV PATH="\$\{MDT_PATH_PREFIX\}:\$\{PATH\}:\$\{MDT_PATH_SUFFIX\}"$', DOCKERFILE)
    # the template explains the order the image defines, in the image's order
    positions = [TEMPLATE_TEXT.index(part) for part in parts]
    assert positions == sorted(positions)
    # the npm prefix and the primary venv are among the image's PATH entries
    npm = re.search(r'(?m)^ENV NPM_CONFIG_PREFIX="([^"]+)"$', DOCKERFILE).group(1)
    venv = re.search(r"python3 -m venv (/\S+)", DOCKERFILE).group(1).rstrip(";")
    assert npm + "/bin" in parts and venv + "/bin" in parts


def test_cgroup_env_keys_match_the_initializer_the_units_and_the_daemon_default() -> None:
    env = TEMPLATE["containerEnv"]
    assert {k for k in env if k.startswith("CGROUP_PARENT_DEV_")} == set(bootstrap.CGROUP_ENV_KEYS)
    # every declared slice is a unit host-setup renders
    for key in bootstrap.CGROUP_ENV_KEYS:
        assert (HS / "units" / (env[key] + ".in")).exists(), (key, env[key])
    # only the INTERACTIVE tier is joined at create time; it must equal the runArg
    run_args = TEMPLATE["runArgs"]
    assert "--cgroup-parent=" + env["CGROUP_PARENT_DEV_INTERACTIVE"] in run_args
    assert len([a for a in run_args if str(a).startswith("--cgroup-parent")]) == 1
    # BACKGROUND is the Docker daemon's default parent that host-setup writes to daemon.json
    assert env["CGROUP_PARENT_DEV_BACKGROUND"] == _example("DOCKER_DAEMON_CGROUP_PARENT")


def test_the_template_comment_states_which_tiers_must_match_runargs() -> None:
    """The old comment said the first TWO keys had to match runArgs; only INTERACTIVE is joined."""
    flat = " ".join(TEMPLATE_TEXT.split())
    assert "The first two MUST stay in sync" not in flat
    assert "Only CGROUP_PARENT_DEV_INTERACTIVE must match the --cgroup-parent runArg" in flat


def test_every_host_bind_source_and_socket_comes_from_a_host_setup_unit() -> None:
    """Sources: docker-api-socket.conf (ListenStream), mdt-cgprofile.conf (d line),
    mdt-buildkitd.service.in (RuntimeDirectory + --addr) and the builder script (MANAGED_BUILDER)."""
    mounts = _mounts()
    listen = re.search(r"(?m)^ListenStream=(/\S+)$", (HS / "units" / "docker-api-socket.conf").read_text()).group(1)
    cgprofile = re.search(r"(?m)^d (/\S+) ", (HS / "units" / "mdt-cgprofile.conf").read_text()).group(1)
    unit = (HS / "units" / "mdt-buildkitd.service.in").read_text()
    runtime_dir = "/run/" + re.search(r"(?m)^RuntimeDirectory=(\S+)$", unit).group(1)
    socket_name = Path(re.search(r"--addr unix://(/\S+)", unit).group(1)).name
    for target in (str(Path(listen).parent), cgprofile, runtime_dir):
        assert mounts[target]["source"] == target and mounts[target]["type"] == "bind", target
    env = TEMPLATE["containerEnv"]
    assert env["DOCKER_HOST"] == "unix://" + listen
    assert env["BUILDKIT_HOST"] == "unix://" + runtime_dir + "/" + socket_name
    builder = (ROOT / "scripts" / "mdt_buildkit_builder.py").read_text()
    assert env["BUILDX_BUILDER"] == re.search(r'(?m)^MANAGED_BUILDER = "([^"]+)"', builder).group(1)


def test_host_setup_needs_nothing_new_from_the_devcontainer() -> None:
    """MDT-HS-01 added host-side reconcile, pins and a lister; none of it is passed or mounted
    here. The template must not mention host-only operator tools as container requirements."""
    for forbidden in ("DEV_IO_", "mdt-cgroup-io-limits", "IO_BASELINE"):
        assert forbidden not in "\n".join(TEMPLATE["mounts"] + list(TEMPLATE["containerEnv"]))


def test_missing_bind_source_policy_vocabulary_has_one_meaning_everywhere() -> None:
    assert bootstrap.MISSING_SOURCE_POLICY_KEY == "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY"
    assert _example(bootstrap.MISSING_SOURCE_POLICY_KEY) == bootstrap.DEFAULT_MISSING_SOURCE_POLICY
    wizard = (HS / "mdt-host-setup-wizard.py").read_text()
    for value in bootstrap.MISSING_SOURCE_POLICIES:
        assert value in wizard
    assert "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY" in TEMPLATE_TEXT
    assert str(bootstrap.HOST_CONFIG_PATH) == "/etc/mdt/host-setup.env"
    assert TEMPLATE["initializeCommand"] == "python3 .devcontainer/initialize_container_environment.py"
    assert "initialize_container_environment.py" in (ROOT / "templates" / "README.md").read_text()


def test_the_docker_gid_runarg_is_explained_where_a_reader_can_find_it() -> None:
    assert "--group-add" in TEMPLATE["runArgs"] and "${localEnv:DOCKER_GID}" in TEMPLATE["runArgs"]
    flat = " ".join(TEMPLATE_TEXT.split())
    assert "(see repo docs)" not in flat
    assert "export DOCKER_GID=$(getent group docker | cut -d: -f3)" in flat


def test_the_template_readme_points_at_the_docker_hub_login_and_the_host_inspection_tool() -> None:
    readme = " ".join((ROOT / "templates" / "README.md").read_text().split())
    assert "docker login docker.io" in readme
    assert "docs/CONSUMERS.md#docker-hub-login" in readme.replace("../", "")
    assert "~/.docker/config.json" in readme
    assert "mdt-cgroup-io-limits.sh" in readme
    consumers = " ".join((ROOT / "docs" / "CONSUMERS.md").read_text().split())
    assert "host-escape -- /usr/local/sbin/mdt-cgroup-io-limits.sh" in consumers
