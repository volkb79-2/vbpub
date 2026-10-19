from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
from types import SimpleNamespace
from pathlib import Path
import sys
import tomllib
from urllib.parse import unquote, urlsplit


TRUENAS_ROOT = Path(__file__).parents[2]
PATCH_ROOT = TRUENAS_ROOT / "boot-pool-patch"
SCRIPT = PATCH_ROOT / "run-live-gate.py"
CONFIG = TRUENAS_ROOT / "run-gate.toml"
README = PATCH_ROOT / "README.md"
LIVE_UI = PATCH_ROOT / "acceptance" / "live_ui.py"
UI_RESUME = PATCH_ROOT / "acceptance" / "ui_resume.py"
LIVE_INSPECT = PATCH_ROOT / "acceptance" / "live_inspect.py"
LIVE_VDEV_TOPOLOGY = PATCH_ROOT / "acceptance" / "live_vdev_topology.py"
CONSUMERS = PATCH_ROOT / "docs" / "CONSUMERS.md"
DESIGN_GUIDE = PATCH_ROOT / "docs" / "DESIGN-GUIDE.md"
USER_DOCS = (README, DESIGN_GUIDE, CONSUMERS)


def load_runner():
    sys.path.insert(0, str(TRUENAS_ROOT))
    spec = importlib.util.spec_from_file_location("truenas_networked_live_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_ui_resume_contract():
    spec = importlib.util.spec_from_file_location("truenas_ui_resume_contract", UI_RESUME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def markdown_anchor_ids(path: Path) -> set[str]:
    used: set[str] = set()
    for match in re.finditer(r"(?m)^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", path.read_text()):
        heading = match.group(1)
        heading = re.sub(r"!\[[^]]*\]\([^)]+\)", "", heading)
        heading = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", heading)
        heading = heading.replace("`", "").lower()
        slug = re.sub(r"[^\w -]", "", heading)
        slug = re.sub(r"-+", "-", re.sub(r"\s+", "-", slug.strip()))
        base = slug
        duplicate = 0
        while slug in used:
            duplicate += 1
            slug = f"{base}-{duplicate}"
        used.add(slug)
    return used


def assert_markdown_anchors_resolve(path: Path) -> None:
    text = path.read_text()
    for link in re.findall(r"\]\(([^)]+)\)", text):
        target = link.strip().split(None, 1)[0].strip("<>")
        parsed = urlsplit(target)
        if parsed.scheme or not parsed.path.endswith(".md") and not parsed.fragment:
            continue
        target_path = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path.resolve()
        if target_path.suffix.lower() != ".md":
            continue
        assert target_path.is_file(), f"{path}: linked Markdown file is missing: {target!r}"
        if parsed.fragment:
            headings = markdown_anchor_ids(target_path)
            assert unquote(parsed.fragment) in headings, (
                f"{path}: anchor #{parsed.fragment} does not resolve in {target_path}"
            )


def test_live_lanes_use_a_capped_networked_exec_runner() -> None:
    config = tomllib.loads(CONFIG.read_text())
    environment = config["environments"]["networked-tester"]
    assert environment["mode"] == "exec"
    assert environment["container_name"] == "truenas-live-acceptance-runner"
    for name in (
        "live-storage", "live-boot", "live-update", "live-v27-patch", "live-v28-patch",
        "live-path-b-provision", "live-path-b-partition", "live-ui", "live-ui-workloads",
        "live-vdev-topology", "live-inspect",
    ):
        lane = config["lanes"][name]
        assert lane["environment"] == "networked-tester"
        assert set(lane["required_env"]) <= set(environment["forward_env"])
    assert "PWMCP_WS_URL" in config["environments"]["networked-tester"]["forward_env"]
    assert "TRUENAS_UI_SECRET_FILE" in config["environments"]["networked-tester"]["forward_env"]
    assert "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT" in config["environments"]["networked-tester"]["forward_env"]
    assert "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET" in config["environments"]["networked-tester"]["forward_env"]
    assert "TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER" in config["environments"]["networked-tester"]["forward_env"]
    assert "live_vdev_topology.py" in " ".join(config["lanes"]["live-vdev-topology"]["argv"])
    assert config["lanes"]["live-vdev-topology"]["budget"] == "60m"


def test_ui_workload_cycle_waits_for_true_nas_table_rows_after_login() -> None:
    source = (PATCH_ROOT / "acceptance" / "live_ui_workloads.py").read_text()
    assert 'text.first.wait_for(state="visible", timeout=120_000)' in source
    assert 'r"^(?:play|play-circle)$"' in source
    assert 'r"^(?:stop|stop-circle)$"' in source
    assert "chevron-down" in source
    assert "wait_for_rpc_request(page, requests, stop_method, previous)" in source
    assert 'perform_ui_action(page, route, section, name, "Force Stop")' in source
    assert "has no guest OS" in source
    assert 'ui_action = "Power Off" if action == "Force Stop" else action' in source
    assert 'elif dialogs.get_by_role("button").count() > 0:' in source
    assert "if candidate.is_visible():" in source
    assert "if len(visible) == 1 and visible[0].is_enabled():" in source
    assert "visible snapshot={snapshot[:5000]!r}" in source


def test_preserved_ui_manifest_is_exported_from_the_readonly_tester(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    manifest = {"schema_version": 1, "profile": "path-a", "pool": "boot-pool"}
    destination = tmp_path / "artifacts" / "ui-workloads.json"
    calls: list[list[str]] = []

    def fake_docker_run(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["exec", runner.TESTER_NAME]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[1] == "cp":
            Path(argv[-1]).write_text(json.dumps(manifest), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected docker invocation: {argv!r}")

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    assert runner.export_ui_workload_manifest("/tmp/manifest.json", destination, 0)
    assert json.loads(destination.read_text(encoding="utf-8")) == manifest
    assert [call[1] for call in calls] == ["exec", "cp"]


def test_failed_ui_lane_without_manifest_does_not_create_host_artifact(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    destination = tmp_path / "artifacts" / "ui-workloads.json"
    monkeypatch.setattr(
        runner,
        "docker_run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="missing"),
    )
    assert not runner.export_ui_workload_manifest("/tmp/missing.json", destination, 1)
    assert not destination.exists()


def test_successful_ui_lane_requires_exported_manifest(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    destination = tmp_path / "artifacts" / "ui-workloads.json"
    monkeypatch.setattr(
        runner,
        "docker_run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="missing"),
    )
    try:
        runner.export_ui_workload_manifest("/tmp/missing.json", destination, 0)
    except runner.RunnerError as exc:
        assert "did not produce" in str(exc)
    else:
        raise AssertionError("a passing preserved UI lane must export its manifest")


def test_networked_runner_uses_gates_slice_and_small_verified_limits() -> None:
    source = SCRIPT.read_text()
    assert 'os.environ.get("CGROUP_PARENT_DEV_GATES", "")' in source
    assert '"--cgroup-parent", slice_name' in source
    assert '"--cpus", str(TESTER_CPU_CORES)' in source
    assert '"--memory", str(TESTER_MEMORY_BYTES)' in source
    assert 'TESTER_CPU_CORES = 0.5' in source
    assert 'TESTER_MEMORY_BYTES = 512 * 1024 * 1024' in source
    assert '"--network", network' in source
    assert '"--mount", f"type=bind,src={source},dst={destination},readonly"' in source
    assert '"live-update": 5400' in source
    assert '"live-v27-patch": 2400' in source
    assert '"live-v28-patch": 2400' in source
    assert '"live-path-b-partition": 900' in source
    assert '"live-ui": 2700' in source
    assert '"live-vdev-topology": 3600' in source
    assert '"live-inspect": 900' in source
    assert "LIVE_LANE_TIMEOUT_SECONDS[args.lane]" in source


def test_ui_gate_requires_private_credentials_and_pwmcp_contract(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    key = tmp_path / "root_ed25519"
    known_hosts = tmp_path / "known_hosts"
    secret = tmp_path / "ui-secrets.toml"
    key.write_text("private test key")
    key.chmod(0o600)
    known_hosts.write_text("truenas ssh host key")
    known_hosts.chmod(0o600)
    secret.write_text("[truenas_admin]\nusername='admin'\npassword='test'\n")
    secret.chmod(0o600)

    for name, value in {
        "TRUENAS_TEST_ALLOW_MUTATIONS": "1",
        "TRUENAS_EXPECTED_VERSION": "27.0.0-RC.1",
        "TRUENAS_STORAGE_PROFILE": "path-a",
        "TRUENAS_TEST_PORT": "2222",
        "TRUENAS_TEST_KEY": str(key),
        "TRUENAS_TEST_KNOWN_HOSTS": str(known_hosts),
        "TRUENAS_UI_SECRET_FILE": str(secret),
        "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT": "1",
        "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET": "boot-pool/user-data/ui-acceptance-202610180904493e3e9b",
        "PWMCP_WS_URL": "ws://pwmcp:3000/",
        "PWMCP_PLAYWRIGHT_VERSION": "1.64.0",
    }.items():
        monkeypatch.setenv(name, value)

    selected = runner.required_live_environment(
        "live-ui", "truenas-test", tmp_path, ui_port=8444,
    )
    assert selected["TRUENAS_UI_URL"] == "https://truenas-test:8444"
    assert selected["TRUENAS_UI_SECRET_FILE"] == str(secret)
    assert selected["PWMCP_WS_URL"] == "ws://pwmcp:3000/"
    assert selected["TRUENAS_TEST_RESUME_EXISTING_UI_ROOT"] == "1"
    assert selected["TRUENAS_TEST_RESUME_EXISTING_UI_DATASET"] == (
        "boot-pool/user-data/ui-acceptance-202610180904493e3e9b"
    )
    assert selected["TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER"] == ""

    monkeypatch.setenv("TRUENAS_TEST_RESUME_EXISTING_UI_DATASET", "boot-pool/user-data/other")
    try:
        runner.required_live_environment("live-ui", "truenas-test", tmp_path, ui_port=8444)
    except runner.RunnerError as exc:
        assert "ui-acceptance-<14 digits><6 lowercase hex>" in str(exc)
    else:
        raise AssertionError("live UI resume must reject a non-test-generated dataset path")
    monkeypatch.setenv(
        "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET",
        "boot-pool/user-data/ui-acceptance-202610180904493e3e9b",
    )

    monkeypatch.setenv("TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER", "uiacceptance16c8601b")
    selected = runner.required_live_environment(
        "live-ui", "truenas-test", tmp_path, ui_port=8444,
    )
    assert selected["TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER"] == "uiacceptance16c8601b"

    monkeypatch.setenv("TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER", "some-other-container")
    try:
        runner.required_live_environment("live-ui", "truenas-test", tmp_path, ui_port=8444)
    except runner.RunnerError as exc:
        assert "uiacceptance<8 lowercase hex>" in str(exc)
    else:
        raise AssertionError("stale UI cleanup must require an exact test-owned container name")
    monkeypatch.delenv("TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER")

    monkeypatch.setenv("TRUENAS_STORAGE_PROFILE", "path-b")
    try:
        runner.required_live_environment("live-ui", "truenas-test", tmp_path, ui_port=8444)
    except runner.RunnerError as exc:
        assert "only valid for Path A" in str(exc)
    else:
        raise AssertionError("the existing-root resume guard must refuse Path B")
    monkeypatch.setenv("TRUENAS_STORAGE_PROFILE", "path-a")

    monkeypatch.setenv("PWMCP_WS_URL", "ws://127.0.0.1:3000/")
    try:
        runner.required_live_environment("live-ui", "truenas-test", tmp_path, ui_port=8444)
    except runner.RunnerError as exc:
        assert "attached network service" in str(exc)
    else:
        raise AssertionError("the live UI lane must reject a PWMCP URL outside its attached network")

    monkeypatch.setenv("PWMCP_WS_URL", "ws://pwmcp:3000/")
    secret.chmod(0o644)
    try:
        runner.required_live_environment("live-ui", "truenas-test", tmp_path, ui_port=8444)
    except runner.RunnerError as exc:
        assert "credential file" in str(exc)
    else:
        raise AssertionError("the live UI lane must reject group/world-readable credentials")


def test_live_ui_port_comes_from_the_managed_vm_configuration() -> None:
    runner = load_runner()
    assert runner.configured_vm_https_port({
        "Config": {"Env": ["VM_NAME=truenas-test", "VM_HTTPS_PORT=8444"]},
    }) == 8444
    for env in ([], ["VM_HTTPS_PORT=8444", "VM_HTTPS_PORT=8445"], ["VM_HTTPS_PORT=0"]):
        try:
            runner.configured_vm_https_port({"Config": {"Env": env}})
        except runner.RunnerError as exc:
            assert "VM_HTTPS_PORT" in str(exc)
        else:
            raise AssertionError(f"invalid managed VM HTTPS port accepted: {env!r}")


def test_vdev_topology_lane_allows_only_guarded_path_a_root_resume(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    key = tmp_path / "root_ed25519"
    known_hosts = tmp_path / "known_hosts"
    secret = tmp_path / "ui-secrets.toml"
    key.write_text("private test key")
    key.chmod(0o600)
    known_hosts.write_text("truenas ssh host key")
    known_hosts.chmod(0o600)
    secret.write_text("[truenas_admin]\nusername='admin'\npassword='test'\n")
    secret.chmod(0o600)
    for name, value in {
        "TRUENAS_TEST_ALLOW_MUTATIONS": "1",
        "TRUENAS_EXPECTED_VERSION": "27.0.0-RC.1",
        "TRUENAS_STORAGE_PROFILE": "path-a",
        "TRUENAS_TEST_PORT": "2231",
        "TRUENAS_TEST_KEY": str(key),
        "TRUENAS_TEST_KNOWN_HOSTS": str(known_hosts),
        "TRUENAS_UI_SECRET_FILE": str(secret),
        "PWMCP_WS_URL": "ws://pwmcp:3000/",
        "PWMCP_PLAYWRIGHT_VERSION": "1.64.0",
    }.items():
        monkeypatch.setenv(name, value)
    for name in (
        "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT",
        "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET",
        "TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER",
    ):
        monkeypatch.delenv(name, raising=False)

    selected = runner.required_live_environment(
        "live-vdev-topology", "truenas-topology-test", tmp_path, ui_port=8451,
    )
    assert selected["TRUENAS_UI_URL"] == "https://truenas-topology-test:8451"
    assert selected["TRUENAS_UI_SECRET_FILE"] == str(secret)
    assert selected["PWMCP_WS_URL"] == "ws://pwmcp:3000/"

    monkeypatch.setenv("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT", "1")
    selected = runner.required_live_environment(
        "live-vdev-topology", "truenas-topology-test", tmp_path, ui_port=8451,
    )
    assert selected["TRUENAS_TEST_RESUME_EXISTING_UI_ROOT"] == "1"

    monkeypatch.setenv(
        "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET",
        "boot-pool/user-data/ui-acceptance-202610180904493e3e9b",
    )
    try:
        runner.required_live_environment(
            "live-vdev-topology", "truenas-topology-test", tmp_path, ui_port=8451,
        )
    except runner.RunnerError as exc:
        assert "only the directly verified Path A UI root" in str(exc)
    else:
        raise AssertionError("vdev topology lane must refuse a prior UI test dataset")
    monkeypatch.delenv("TRUENAS_TEST_RESUME_EXISTING_UI_DATASET")
    monkeypatch.delenv("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT")

    monkeypatch.setenv("TRUENAS_STORAGE_PROFILE", "path-b")
    try:
        runner.required_live_environment(
            "live-vdev-topology", "truenas-topology-test", tmp_path, ui_port=8451,
        )
    except runner.RunnerError as exc:
        assert "fresh Path A guest" in str(exc)
    else:
        raise AssertionError("vdev topology lane must refuse Path B")


def test_vdev_topology_acceptance_uses_ui_zvols_and_api_vdev_roles() -> None:
    source = LIVE_VDEV_TOPOLOGY.read_text()
    compile(source, str(LIVE_VDEV_TOPOLOGY), "exec")
    assert "create_path_a_root_in_ui" in source
    assert "inventory_before_ui_mutation" in source
    assert "select_dataset_parent(page, base_url, \"path-a\")" in source
    assert "create_zvol_in_ui" in source
    assert 'target.call("pool.create", payload' in source
    assert 'target.call("pool.update", pool["id"], request' in source
    assert '"cache":' in source and '"log":' in source and '"special":' in source
    assert '"RAIDZ1"' in source and '"MIRROR"' in source
    assert '"force_topology" not in topology' in source
    assert 'target.call("pool.export"' in source and '"destroy": True' in source
    assert "TNEXTRA001" in source and "TNEXTRA004" in source
    assert "parse_vdev_write_counters" in source
    assert "'zpool','iostat','-v','-y','-P','-p','-n'" in source
    assert "inspect_aux_vdev_selectors(page, base_url, zvol_names, disk_responses)" in source
    assert '"/ui/storage"' in source


def test_vdev_iostat_parser_totals_exact_log_and_special_device_writes() -> None:
    metrics_path = PATCH_ROOT / "acceptance" / "vdev_metrics.py"
    spec = importlib.util.spec_from_file_location("truenas_vdev_metrics_test", metrics_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output = """capacity operations bandwidth
pool alloc free read write read write
tank - - 0 0 0 0
  /dev/zvol/boot-pool/log - - 0 3 0 49152
  /dev/zvol/boot-pool/special - - 0 5 0 81920
  /dev/zvol/boot-pool/log - - 0 2 0 32768
"""
    assert module.parse_vdev_write_counters(
        output,
        ["/dev/zvol/boot-pool/log", "/dev/zvol/boot-pool/special"],
    ) == {
        "/dev/zvol/boot-pool/log": (5, 81920),
        "/dev/zvol/boot-pool/special": (5, 81920),
    }
    try:
        module.parse_vdev_write_counters(output, ["/dev/zvol/missing"])
    except ValueError as exc:
        assert "omitted vdev path" in str(exc)
    else:
        raise AssertionError("iostat parser accepted an absent vdev row")


def test_live_ui_resume_allows_only_the_verified_root_and_pinned_image_cache() -> None:
    source = LIVE_UI.read_text()
    compile(source, str(LIVE_UI), "exec")
    assert 'TRUENAS_TEST_RESUME_EXISTING_UI_ROOT must be unset or 1' in source
    assert 'resume_existing_root=resume_existing_root == "1"' in source
    assert "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET" in source
    assert "resume_existing_ui_dataset=resume_existing_ui_dataset" in source
    assert 'zfs_property(target, zvol_name, "volblocksize") == "16384"' in source
    assert "unexpected_ui_root_snapshots(root_snapshots, cached_images)" in source
    assert "from ui_resume import (" in source
    assert 'not any("/containers/" in name for name in descendants)' in source
    assert 'target.call("container.delete", record["id"]' in source
    assert 'cached-ui-images={cached_images if existing_root else []!r}' in source
    assert 'zfs_property(target, root, "mountpoint")' in source
    assert 'zfs_property(target, root, "canmount")' in source
    assert 'zfs_property(target, root, "quota")' in source
    assert '"find", mountpoint, "-mindepth", "1", "-maxdepth", "1"' in source
    assert 'selected = f"{selected}/{name}"' in source
    assert 'page.keyboard.press("ArrowRight")' in source
    container_create = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "create_container_in_ui"
    )
    container_create_source = ast.get_source_segment(source, container_create)
    assert 'get_by_role("dialog", name="Slide-in form", exact=True)' in container_create_source
    assert 'get_by_role("dialog", name="Creating Container", exact=True)' in container_create_source
    assert 'dialog.wait_for(state="hidden", timeout=900_000)' in container_create_source


def test_path_a_ui_resume_accepts_only_the_named_dataset_and_two_zvols() -> None:
    contract = load_ui_resume_contract()
    dataset = "boot-pool/user-data/ui-acceptance-202610180904493e3e9b"
    expected = {
        dataset,
        f"{dataset}/vm-disk",
        f"{dataset}/cache-device",
    }
    assert contract.resumed_ui_dataset_members(dataset) == expected
    pinned_image = (
        "boot-pool/user-data/.truenas_containers/images/"
        "alpine:3.24:amd64:default:20261016_13:00"
    )
    assert contract.unexpected_ui_root_snapshots(
        [f"{pinned_image}@image"], [pinned_image],
    ) == []
    assert contract.unexpected_ui_root_snapshots(
        [f"{pinned_image}@image", f"{dataset}@unreviewed"], [pinned_image],
    ) == [f"{dataset}@unreviewed"]
    direct = [
        "boot-pool/user-data/.truenas_containers",
        "boot-pool/user-data/.truenas_containers/containers",
        "boot-pool/user-data/.truenas_containers/images",
        "boot-pool/user-data/.truenas_containers/images/alpine:3.24:amd64:default:20261016_13:00",
        *sorted(expected),
    ]
    descendants, images = contract.ui_root_internal_datasets(
        direct, "boot-pool/user-data", "alpine:3.24:amd64:default",
        resumed_ui_dataset=dataset,
    )
    assert set(descendants) == set(direct)
    assert images == [
        "boot-pool/user-data/.truenas_containers/images/alpine:3.24:amd64:default:20261016_13:00"
    ]

    for invalid in (
        "boot-pool/user-data/other",
        "boot-pool/user-data/ui-acceptance-202610180904493E3E9B",
        "boot-pool/user-data/ui-acceptance-202610180904493e3e9b/child",
    ):
        try:
            contract.resumed_ui_dataset_members(invalid)
        except ValueError as exc:
            assert "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET" in str(exc)
        else:
            raise AssertionError(f"invalid resumed UI dataset accepted: {invalid!r}")

    for invalid_tree in (
        direct + ["boot-pool/user-data/unlisted-user-data"],
        [name for name in direct if name != f"{dataset}/cache-device"],
        direct + [f"{dataset}/unexpected-child"],
    ):
        try:
            contract.ui_root_internal_datasets(
                invalid_tree, "boot-pool/user-data", "alpine:3.24:amd64:default",
                resumed_ui_dataset=dataset,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"UI resume accepted an unexpected tree: {invalid_tree!r}")


def test_read_only_inventory_lane_does_not_require_mutation_guard(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    key = tmp_path / "root_ed25519"
    known_hosts = tmp_path / "known_hosts"
    key.write_text("private test key")
    key.chmod(0o600)
    known_hosts.write_text("truenas ssh host key")
    known_hosts.chmod(0o600)
    for name, value in {
        "TRUENAS_TEST_ALLOW_MUTATIONS": "",
        "TRUENAS_EXPECTED_VERSION": "28.0.0-MASTER+20261014-230204",
        "TRUENAS_STORAGE_PROFILE": "path-b",
        "TRUENAS_TEST_PORT": "2225",
        "TRUENAS_TEST_KEY": str(key),
        "TRUENAS_TEST_KNOWN_HOSTS": str(known_hosts),
    }.items():
        monkeypatch.setenv(name, value)

    selected = runner.required_live_environment("live-inspect", "truenas-test", tmp_path)
    assert selected["TRUENAS_TEST_ALLOW_MUTATIONS"] == ""


def test_read_only_inventory_script_uses_only_query_operations() -> None:
    source = LIVE_INSPECT.read_text()
    compile(source, str(LIVE_INSPECT), "exec")
    assert 'target.call("system.info"' in source
    assert 'target.query("container.query"' in source
    assert 'target.query("vm.query"' in source
    assert 'target.call("container.' not in source
    assert 'target.call("vm.' not in source
    assert 'target.call("pool.dataset.delete"' not in source
    assert 'target.shell("zpool list -H -o name,health"' in source
    assert 'findmnt -n -o SOURCE,OPTIONS --target /usr' in source
    assert 'findmnt -n -o SOURCE,FSTYPE --target /root' in source
    assert 'v27-boot-pool-patch-install.json' in source
    assert 'direct_storage_inventory(filesystems, pool_health)' in source
    assert 'direct_user_data_mountpoint_state' in source
    assert 'middleware_dataset_source_context(target)' in source
    assert "root.rglob('*.py')" in source
    assert "path.read_text(errors='replace')" in source
    assert '.write_text(' not in source


def test_read_only_inventory_reports_exact_direct_root_and_pool_presence() -> None:
    sys.path.insert(0, str(LIVE_INSPECT.parent))
    spec = importlib.util.spec_from_file_location("truenas_live_inspect_inventory", LIVE_INSPECT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    path_a = module.direct_storage_inventory(
        ["boot-pool\tfilesystem\t0\t0\t1", "boot-pool/ROOT/current\tfilesystem\t0\t0\t1"],
        ["boot-pool\tONLINE"],
    )
    assert path_a["direct_user_data_root_present"] is False
    assert path_a["direct_user_data_descendants"] == []
    assert path_a["direct_boot_disk_data_pool_present"] is False
    assert path_a["direct_boot_pool_dataset_names"] == ["boot-pool", "boot-pool/ROOT/current"]

    path_b = module.direct_storage_inventory(
        [
            "boot-pool\tfilesystem\t0\t0\t1",
            "boot-pool/user-data\tfilesystem\t0\t0\t1",
            "boot-pool/user-data/probe\tfilesystem\t0\t0\t1",
            "boot-disk-data\tfilesystem\t0\t0\t1",
        ],
        ["boot-pool\tONLINE", "boot-disk-data\tONLINE"],
    )
    assert path_b["direct_user_data_root_present"] is True
    assert path_b["direct_user_data_descendants"] == ["boot-pool/user-data/probe"]
    assert path_b["direct_boot_disk_data_pool_present"] is True
    assert path_b["direct_boot_disk_data_dataset_names"] == ["boot-disk-data"]


def test_pwmcp_alias_must_exist_on_the_selected_guest_network(monkeypatch) -> None:
    runner = load_runner()
    monkeypatch.setattr(
        runner,
        "docker_run",
        lambda argv, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{
                "Driver": "bridge",
                "Containers": {"pwmcp-id": {"Name": "pwmcp-service"}},
            }]),
            stderr="",
        ),
    )
    monkeypatch.setattr(
        runner,
        "docker_inspect",
        lambda name: {
            "NetworkSettings": {
                "Networks": {
                    "guest-bridge": {"Aliases": ["pwmcp", "pwmcp-service"]},
                },
            },
        },
    )
    assert runner.verify_network_alias("guest-bridge", "pwmcp") == "pwmcp-service"

    monkeypatch.setattr(
        runner,
        "docker_inspect",
        lambda name: {"NetworkSettings": {"Networks": {"guest-bridge": {"Aliases": [name]}}}},
    )
    try:
        runner.verify_network_alias("guest-bridge", "pwmcp")
    except runner.RunnerError as exc:
        assert "no container with DNS alias" in str(exc)
    else:
        raise AssertionError("the UI lane must refuse a network with no PWMCP alias")


def test_live_ui_script_inventories_before_mutating_and_checks_zfs_backing() -> None:
    source = LIVE_UI.read_text()
    tree = ast.parse(source)
    inventory = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "inventory_before_ui_mutation"
    )
    inventory_source = ast.get_source_segment(source, inventory)
    assert "direct_names = []" in inventory_source
    assert 'target.query("disk.query", timeout=120)' in inventory_source
    assert '"lsblk", "--json", "--tree", "--paths", "--bytes"' in inventory_source
    pool_presence_guard = next(
        node for node in ast.walk(inventory)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "pool"
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.In)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Name)
        and node.test.comparators[0].id == "pool_health"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        for node in ast.walk(pool_presence_guard)
    )
    path_b_pool_ui = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_path_b_pool_in_ui"
    )
    path_b_pool_ui_source = ast.get_source_segment(source, path_b_pool_ui)
    assert 'get_by_text("Storage", exact=True)' in path_b_pool_ui_source
    assert 'page.goto(base_url + "/ui/storage"' not in path_b_pool_ui_source
    assert 'create.wait_for(state="visible", timeout=60_000)' in path_b_pool_ui_source
    assert 'page.wait_for_timeout(500)\n            snapshot = dialog.aria_snapshot()\n            continue' in path_b_pool_ui_source
    assert 'observe_rpc_method(page, "disk.query", log_response=True)' in source
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    calls = [
        (node.lineno, node.func.id)
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    inventory_line = min(line for line, name in calls if name == "inventory_before_ui_mutation")
    dataset_write_line = min(line for line, name in calls if name == "create_dataset_in_ui")
    assert inventory_line < dataset_write_line
    path_a_root = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_path_a_root_in_ui"
    )
    path_a_root_source = ast.get_source_segment(source, path_a_root)
    assert 'name="Quota for this dataset and all children"' in path_a_root_source
    assert 'name="Quota for this dataset", exact=True' not in path_a_root_source
    zvol_create = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_zvol_in_ui"
    )
    zvol_create_source = ast.get_source_segment(source, zvol_create)
    assert 'get_by_role("textbox", name="Size", exact=True)' in zvol_create_source
    assert 'size.fill("128 MiB")' in zvol_create_source
    assert 'size.press("Tab")' in zvol_create_source
    assert 'sparse.focus()' in zvol_create_source
    assert 'sparse.press("Space")' in zvol_create_source
    assert 'get_by_role("dialog", name="Slide-in form", exact=True)' in zvol_create_source
    assert 'progress.wait_for(state="hidden", timeout=60_000)' in zvol_create_source
    assert 'if save_button.count() == 1 and not save_button.is_enabled()' in zvol_create_source
    assert 'name=re.compile(r"validation error", re.IGNORECASE)' in zvol_create_source
    assert 'zvol_request = dataset_create_properties(' in zvol_create_source
    assert 'properties.get("type") == "VOLUME"' in zvol_create_source
    assert 'properties.get("volsize")' in zvol_create_source
    assert 'volsize == 128 * 1024 * 1024' in zvol_create_source
    assert 'dataset_create_properties(dataset_create_requests[request_count_before_submit:], dataset)' in zvol_create_source
    compile(source, str(LIVE_UI), "exec")
    for fragment in (
        "findmnt",
        "zfs_property(target, dataset, \"used\")",
        "create_zvol_in_ui",
        "create_container_in_ui",
        "select_existing_zvol_in_vm_wizard",
        "vm.device.query",
        "zpool add",
        "pool.dataset.delete",
        'observe_rpc_method(page, "pool.dataset.create")',
        'websocket.on("framesent", on_frame_sent)',
        'websocket.on("framereceived", on_frame_received)',
        'for response in walk_dicts(frame)',
        'observe_rpc_method(page, "pool.dataset.recommended_zvol_blocksize", log_response=True)',
        'observe_rpc_method(page, "pool.dataset.compression_choices", log_response=True)',
        'basic_options.wait_for(state="visible", timeout=30_000)',
        '"Quota for this dataset and all children"',
        'quota.press("Tab")',
        'quota_dom.get("dataTest") == "input-quota"',
        '"root_create_guard"',
        '"root-excluded-from-descendant-readiness-v1"',
        'dataset_create_properties(create_requests, root)',
        'type(ui_quota) is int and ui_quota >= 1_000_000_000',
        '"path-a-root-ui-wire"',
        "container_referenced > 0",
        'f"{container_root}/images/{image_name}:{image_version}"',
        'f"used={container_used} bytes, referenced={container_referenced} bytes; image dataset "',
        "password=uuid.uuid4().hex[:8]",
        "def vm_region_next_button(dialog, expected_name: str)",
        "def active_vm_step_next_button(dialog)",
    ):
        assert fragment in source
    vm_form = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "vm_form_create"
    )
    vm_form_source = ast.get_source_segment(source, vm_form)
    assert 'vm_region_next_button(dialog, "Operating System")' in vm_form_source
    assert 'vm_region_next_button(dialog, "CPU And Memory")' in vm_form_source
    assert "active_vm_step_next_button(dialog)" in vm_form_source
    assert 'dialog.get_by_role("button", name="Next", exact=True)' not in vm_form_source
    vm_disk_select = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "select_existing_zvol_in_vm_wizard"
    )
    vm_disk_select_source = ast.get_source_segment(source, vm_disk_select)
    assert '"radio", name="Use existing disk image", exact=True' in vm_disk_select_source
    assert 'dialog.get_by_text("Use existing disk image", exact=True)' in vm_disk_select_source
    assert "visible_labels[0].click()" in vm_disk_select_source
    assert 'expect(existing_disk_choice).to_be_checked(timeout=15_000)' in vm_disk_select_source
    assert 'get_by_role("combobox", name="Select Existing Zvol", exact=True)' in vm_disk_select_source
    assert 'get_by_role("option", name=dataset, exact=True)' in vm_disk_select_source
    assert 'select_single_available_option(page, dialog, "Attach NIC")' in vm_form_source
    assert "def select_single_available_option(page, dialog, label: str)" in source
    assert 'target.call("container.device.create"' not in source
    assert 'target.call("vm.device.create"' not in source
    assert "inspect_session_timeout_ui(page, username)" in source
    assert 'name=re.compile(rf"^{re.escape(username)}(?:\\s|$)")' in source
    assert 'report_visible("session-timeout-dashboard")' in source
    assert 'report("session-timeout-ui", "Session Timeout is visible in Preferences")\n                    return' in source
    assert 'expect(existing_disk_choice).to_have_count(1, timeout=30_000)' in source
    assert "def submit_result_details(page, dialog)" in source
    assert "deadline = time.monotonic() + 20" in source
    assert "field_errors={form_errors!r}" in source
    assert "Save left the UI form visible" in source


def test_tester_cleanup_reads_back_stopped_state_after_docker_stop_timeout(monkeypatch) -> None:
    runner = load_runner()
    states = [True, False]
    calls = []
    timeouts = []

    def fake_docker_run(argv, *, check=True, timeout=30):
        calls.append(argv)
        timeouts.append(timeout)
        if argv[1] == "inspect":
            item = {
                "Config": {"Labels": {runner.RUNNER_LABEL: "owned-run"}},
                "State": {"Running": states.pop(0)},
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(item), stderr="")
        if argv[1] == "stop":
            raise runner.RunnerError("docker stop timed out after sending TERM")
        if argv[1] == "rm":
            return SimpleNamespace(returncode=0, stdout="truenas-live-acceptance-runner", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    runner.cleanup_owned_tester("owned-run")
    assert any(call[1] == "rm" for call in calls)
    assert timeouts[0] == runner.TESTER_DOCKER_INSPECT_TIMEOUT_SECONDS
    assert timeouts[2] == runner.TESTER_DOCKER_INSPECT_TIMEOUT_SECONDS


def test_tester_cleanup_treats_lowercase_docker_missing_object_as_absent(monkeypatch) -> None:
    runner = load_runner()
    calls = []

    def fake_docker_run(argv, *, check=True, timeout=30):
        calls.append(argv)
        assert argv[1] == "inspect"
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="error: no such object: truenas-live-acceptance-runner",
        )

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    runner.cleanup_owned_tester("prior-run")
    assert len(calls) == 1
    assert calls[0][1] == "inspect"


def test_tester_cleanup_rechecks_after_late_container_creation(monkeypatch) -> None:
    runner = load_runner()
    calls = []

    def fake_docker_run(argv, *, check=True, timeout=30):
        calls.append(argv)
        if argv[1] == "inspect" and len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="error: no such object: truenas-live-acceptance-runner",
            )
        if argv[1] == "inspect":
            item = {
                "Config": {"Labels": {runner.RUNNER_LABEL: "timed-out-run"}},
                "State": {"Running": False},
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(item), stderr="")
        if argv[1] == "rm":
            return SimpleNamespace(returncode=0, stdout="truenas-live-acceptance-runner", stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    monkeypatch.setattr(runner.time, "monotonic", lambda: 0)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    runner.cleanup_owned_tester("timed-out-run", absent_grace_seconds=1)
    assert [call[1] for call in calls] == ["inspect", "inspect", "rm"]


def test_tester_cleanup_never_removes_a_still_running_container(monkeypatch) -> None:
    runner = load_runner()
    calls = []

    def fake_docker_run(argv, *, check=True, timeout=30):
        calls.append(argv)
        if argv[1] == "inspect":
            item = {
                "Config": {"Labels": {runner.RUNNER_LABEL: "owned-run"}},
                "State": {"Running": True},
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(item), stderr="")
        if argv[1] == "stop":
            raise runner.RunnerError("docker stop timed out")
        raise AssertionError(argv)

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    try:
        runner.cleanup_owned_tester("owned-run")
    except runner.RunnerError as exc:
        assert "remains running" in str(exc)
    else:
        raise AssertionError("a running network tester must never be removed")
    assert not any(call[1] == "rm" for call in calls)


def test_stale_tester_recovery_removes_only_the_inspected_exited_identity(monkeypatch) -> None:
    runner = load_runner()
    run_id = "b" * 32
    container_id = "a" * 64
    item = {
        "Id": container_id,
        "Config": {
            "Image": runner.TESTER_IMAGE,
            "Labels": {runner.RUNNER_LABEL: run_id},
        },
        "State": {"Running": False, "Status": "exited"},
    }
    calls = []

    def fake_docker_run(argv, *, check=True, timeout=30):
        calls.append(argv)
        if argv[1] == "inspect":
            assert argv[-2:] == ["--format", "{{json .}}"]
            return SimpleNamespace(returncode=0, stdout=json.dumps(item), stderr="")
        if argv[1] == "rm":
            assert argv[2] == container_id
            return SimpleNamespace(returncode=0, stdout=container_id, stderr="")
        raise AssertionError(argv)

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    assert runner.recover_stale_tester() == container_id
    assert [call[1] for call in calls] == ["inspect", "inspect", "rm"]
    assert calls[0][2] == runner.TESTER_NAME
    assert calls[1][2] == container_id


def test_stale_tester_recovery_refuses_running_container(monkeypatch) -> None:
    runner = load_runner()
    item = {
        "Id": "a" * 64,
        "Config": {
            "Image": runner.TESTER_IMAGE,
            "Labels": {runner.RUNNER_LABEL: "b" * 32},
        },
        "State": {"Running": True, "Status": "running"},
    }
    calls = []

    def fake_docker_run(argv, *, check=True, timeout=30):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(item), stderr="")

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    try:
        runner.recover_stale_tester()
    except runner.RunnerError as exc:
        assert "confirmed in exited state" in str(exc)
    else:
        raise AssertionError("stale recovery must refuse a running tester")
    assert len(calls) == 1


def test_stale_tester_recovery_refuses_foreign_image_and_invalid_owner(monkeypatch) -> None:
    runner = load_runner()
    item = {
        "Id": "a" * 64,
        "Config": {
            "Image": "foreign-image:latest",
            "Labels": {runner.RUNNER_LABEL: "invalid-owner"},
        },
        "State": {"Running": False, "Status": "exited"},
    }
    calls = []

    def fake_docker_run(argv, *, check=True, timeout=30):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(item), stderr="")

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    try:
        runner.recover_stale_tester()
    except runner.RunnerError as exc:
        assert "only accepts image" in str(exc)
    else:
        raise AssertionError("stale recovery must refuse a foreign image")
    assert len(calls) == 1

    item["Config"]["Image"] = runner.TESTER_IMAGE
    try:
        runner.recover_stale_tester()
    except runner.RunnerError as exc:
        assert "valid live-gate ownership label" in str(exc)
    else:
        raise AssertionError("stale recovery must refuse a malformed ownership label")
    assert len(calls) == 2


def test_stale_tester_recovery_refuses_indeterminate_inspection(monkeypatch) -> None:
    runner = load_runner()
    calls = []

    def fake_docker_run(argv, *, check=True, timeout=30):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="daemon unavailable")

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    try:
        runner.recover_stale_tester()
    except runner.RunnerError as exc:
        assert "could not determine network tester state" in str(exc)
    else:
        raise AssertionError("inspection failure must not be treated as an absent tester")
    assert len(calls) == 1


def test_stale_tester_recovery_requires_existing_container(monkeypatch) -> None:
    runner = load_runner()

    def fake_docker_run(argv, *, check=True, timeout=30):
        assert argv[1] == "inspect"
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Error: No such object: truenas-live-acceptance-runner",
        )

    monkeypatch.setattr(runner, "docker_run", fake_docker_run)
    try:
        runner.recover_stale_tester()
    except runner.RunnerError as exc:
        assert "no network tester exists" in str(exc)
    else:
        raise AssertionError("recovery should report an absent tester explicitly")


def test_docker_host_paths_are_normalized_without_local_filesystem_resolution() -> None:
    runner = load_runner()
    expected = runner.normalized_docker_host_path(
        "/home/vb/volkb79-2/vbpub/artifacts/../artifacts/update.iso",
        "selected ISO host path",
    )
    assert str(expected) == "/home/vb/volkb79-2/vbpub/artifacts/update.iso"
    assert not Path(str(expected)).exists()
    try:
        runner.normalized_docker_host_path("relative/update.iso", "selected ISO host path")
    except runner.RunnerError as exc:
        assert "absolute Docker-host path" in str(exc)
    else:
        raise AssertionError("a relative Docker host path must be refused")


def test_live_environment_requires_owned_private_ssh_credentials(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    key = tmp_path / "root_ed25519"
    known_hosts = tmp_path / "known_hosts"
    key.write_text("private test key")
    key.chmod(0o600)
    known_hosts.write_text("truenas ssh host key")
    known_hosts.chmod(0o600)
    monkeypatch.setenv("TRUENAS_TEST_ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("TRUENAS_TEST_ALLOW_REBOOT", "1")
    monkeypatch.setenv("TRUENAS_EXPECTED_VERSION", "27.0.0-RC.1")
    monkeypatch.setenv("TRUENAS_STORAGE_PROFILE", "path-a")
    monkeypatch.setenv("TRUENAS_TEST_PORT", "2222")
    monkeypatch.setenv("TRUENAS_TEST_KEY", str(key))
    monkeypatch.setenv("TRUENAS_TEST_KNOWN_HOSTS", str(known_hosts))

    selected = runner.required_live_environment("live-boot", "truenas-test", tmp_path)
    assert selected["TRUENAS_TEST_KEY"] == str(key)
    assert selected["_TRUENAS_TESTER_UID_GID"] == f"{key.stat().st_uid}:{key.stat().st_gid}"

    key.chmod(0o644)
    try:
        runner.required_live_environment("live-boot", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "group/world accessible" in str(exc)
    else:
        raise AssertionError("SSH private keys with group/world access must be refused")


def test_live_boot_requires_reboot_guard_in_both_wrapper_and_lane() -> None:
    config = tomllib.loads(CONFIG.read_text())
    lane = config["lanes"]["live-boot"]
    source = SCRIPT.read_text()
    assert "TRUENAS_TEST_ALLOW_REBOOT" in lane["required_env"]
    assert 'os.environ.get("TRUENAS_TEST_ALLOW_REBOOT") != "1"' in source
    assert '"live-boot"' in source


def test_live_update_requires_three_guards_and_verified_media_paths(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    key = tmp_path / "root_ed25519"
    known_hosts = tmp_path / "known_hosts"
    update_iso = tmp_path / "TrueNAS.iso"
    metadata = tmp_path / "update-artifact.json"
    key.write_text("private test key")
    key.chmod(0o600)
    known_hosts.write_text("truenas ssh host key")
    known_hosts.chmod(0o600)
    update_iso.write_bytes(b"test ISO")
    metadata.write_text("{}")
    monkeypatch.setenv("TRUENAS_TEST_ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("TRUENAS_TEST_ALLOW_REBOOT", "1")
    monkeypatch.setenv("TRUENAS_TEST_ALLOW_UPDATE", "1")
    monkeypatch.setenv("TRUENAS_EXPECTED_VERSION", "27.0.0-RC.1")
    monkeypatch.setenv("TRUENAS_UPDATE_EXPECTED_VERSION", "28.0.0-MASTER+20261014-230204")
    monkeypatch.setenv("TRUENAS_STORAGE_PROFILE", "path-a")
    monkeypatch.setenv("TRUENAS_TEST_PORT", "2222")
    monkeypatch.setenv("TRUENAS_TEST_KEY", str(key))
    monkeypatch.setenv("TRUENAS_TEST_KNOWN_HOSTS", str(known_hosts))
    monkeypatch.setenv("TRUENAS_UPDATE_ISO", str(update_iso))
    monkeypatch.setenv("TRUENAS_UPDATE_METADATA", str(metadata))

    selected = runner.required_live_environment("live-update", "truenas-test", tmp_path)
    assert selected["TRUENAS_TEST_ALLOW_UPDATE"] == "1"
    assert selected["TRUENAS_UPDATE_ISO"] == str(update_iso)
    assert selected["TRUENAS_UPDATE_METADATA"] == str(metadata)

    workload_manifest = tmp_path / "ui-workloads.json"
    workload_manifest.write_text("{}")
    monkeypatch.setenv("TRUENAS_UI_WORKLOAD_MANIFEST", str(workload_manifest))
    selected = runner.required_live_environment("live-update", "truenas-test", tmp_path)
    assert selected["TRUENAS_UI_WORKLOAD_MANIFEST"] == str(workload_manifest)
    monkeypatch.delenv("TRUENAS_UI_WORKLOAD_MANIFEST")

    monkeypatch.setenv("TRUENAS_TEST_ALLOW_UPDATE", "0")
    try:
        runner.required_live_environment("live-update", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "TRUENAS_TEST_ALLOW_UPDATE=1" in str(exc)
    else:
        raise AssertionError("live-update must reject a missing destructive-update guard")


def test_live_v28_patch_is_registered_with_mutation_guard_and_exact_source_lane() -> None:
    config = tomllib.loads(CONFIG.read_text())
    lane = config["lanes"]["live-v28-patch"]
    source = SCRIPT.read_text()
    assert lane["environment"] == "networked-tester"
    assert "TRUENAS_TEST_ALLOW_MUTATIONS" in lane["required_env"]
    assert "TRUENAS_EXPECTED_VERSION" in lane["required_env"]
    assert "live_v28_patch.py" in lane["argv"][-1]
    assert "Path A or Path B" in lane["description"]
    assert "TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256" in config["environments"]["networked-tester"]["forward_env"]
    assert '"live-v28-patch"' in source


def test_live_v28_patch_accepts_path_b_and_guards_replacement_with_prior_digest(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    key = tmp_path / "root_ed25519"
    known_hosts = tmp_path / "known_hosts"
    key.write_text("private test key")
    key.chmod(0o600)
    known_hosts.write_text("truenas ssh host key")
    known_hosts.chmod(0o600)
    monkeypatch.setenv("TRUENAS_TEST_ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("TRUENAS_EXPECTED_VERSION", "28.0.0-MASTER+20261014-230204")
    monkeypatch.setenv("TRUENAS_STORAGE_PROFILE", "path-b")
    monkeypatch.setenv("TRUENAS_TEST_PORT", "2225")
    monkeypatch.setenv("TRUENAS_TEST_KEY", str(key))
    monkeypatch.setenv("TRUENAS_TEST_KNOWN_HOSTS", str(known_hosts))

    selected = runner.required_live_environment("live-v28-patch", "truenas-test", tmp_path)
    assert selected["TRUENAS_STORAGE_PROFILE"] == "path-b"
    assert selected["TRUENAS_V28_PATCH_ACTION"] == "install"

    monkeypatch.setenv("TRUENAS_V28_PATCH_ACTION", "replace")
    try:
        runner.required_live_environment("live-v28-patch", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "TRUENAS_TEST_ALLOW_PATCH_REPLACE=1" in str(exc)
    else:
        raise AssertionError("patch replacement must require its separate mutation guard")

    monkeypatch.setenv("TRUENAS_TEST_ALLOW_PATCH_REPLACE", "1")
    monkeypatch.setenv("TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256", "bad-digest")
    try:
        runner.required_live_environment("live-v28-patch", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "installed manifest" in str(exc)
    else:
        raise AssertionError("patch replacement must require the recorded prior bundle digest")

    prior = "a" * 64
    monkeypatch.setenv("TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256", prior)
    selected = runner.required_live_environment("live-v28-patch", "truenas-test", tmp_path)
    assert selected["TRUENAS_TEST_ALLOW_PATCH_REPLACE"] == "1"
    assert selected["TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256"] == prior


def test_live_v27_root_create_repair_requires_path_a_guard_and_matching_prior_bundle(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    key = tmp_path / "root_ed25519"
    known_hosts = tmp_path / "known_hosts"
    prior_bundle = tmp_path / "prior-v27.tar.gz"
    key.write_text("private test key")
    key.chmod(0o600)
    known_hosts.write_text("truenas ssh host key")
    known_hosts.chmod(0o600)
    prior_bundle.write_bytes(b"registered prior v27 bundle")
    prior_digest = hashlib.sha256(prior_bundle.read_bytes()).hexdigest()

    for name, value in {
        "TRUENAS_TEST_ALLOW_MUTATIONS": "1",
        "TRUENAS_EXPECTED_VERSION": "27.0.0-RC.1",
        "TRUENAS_STORAGE_PROFILE": "path-a",
        "TRUENAS_TEST_PORT": "2223",
        "TRUENAS_TEST_KEY": str(key),
        "TRUENAS_TEST_KNOWN_HOSTS": str(known_hosts),
        "TRUENAS_V27_PATCH_ACTION": "repair-root-create",
        "TRUENAS_TEST_ALLOW_PATCH_REPAIR": "1",
        "TRUENAS_V27_PRIOR_PATCH_BUNDLE": str(prior_bundle),
        "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256": prior_digest,
    }.items():
        monkeypatch.setenv(name, value)

    monkeypatch.setenv("TRUENAS_TEST_ALLOW_PATCH_REPAIR", "")
    try:
        runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "TRUENAS_TEST_ALLOW_PATCH_REPAIR=1" in str(exc)
    else:
        raise AssertionError("v27 root-create repair must require its separate mutation guard")

    monkeypatch.setenv("TRUENAS_TEST_ALLOW_PATCH_REPAIR", "1")
    selected = runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    assert selected["TRUENAS_V27_PATCH_ACTION"] == "repair-root-create"
    assert selected["TRUENAS_TEST_ALLOW_PATCH_REPAIR"] == "1"
    assert selected["TRUENAS_V27_PRIOR_PATCH_BUNDLE"] == str(prior_bundle)
    assert selected["TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256"] == prior_digest

    monkeypatch.setenv("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256", "bad-digest")
    try:
        runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256" in str(exc)
    else:
        raise AssertionError("v27 root-create repair must reject an incorrect prior bundle digest")

    monkeypatch.setenv("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256", prior_digest)
    monkeypatch.setenv("TRUENAS_STORAGE_PROFILE", "path-b")
    try:
        runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "requires TRUENAS_STORAGE_PROFILE=path-a" in str(exc)
    else:
        raise AssertionError("root-create repair must refuse Path B")


def test_live_v27_parent_mountpoint_repair_requires_prior_source_digest(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    key = tmp_path / "root_ed25519"
    known_hosts = tmp_path / "known_hosts"
    prior_bundle = tmp_path / "prior-v27.tar.gz"
    key.write_text("private test key")
    key.chmod(0o600)
    known_hosts.write_text("truenas ssh host key")
    known_hosts.chmod(0o600)
    prior_bundle.write_bytes(b"registered prior v27 bundle")
    prior_digest = hashlib.sha256(prior_bundle.read_bytes()).hexdigest()

    for name, value in {
        "TRUENAS_TEST_ALLOW_MUTATIONS": "1",
        "TRUENAS_EXPECTED_VERSION": "27.0.0-RC.1",
        "TRUENAS_STORAGE_PROFILE": "path-a",
        "TRUENAS_TEST_PORT": "2223",
        "TRUENAS_TEST_KEY": str(key),
        "TRUENAS_TEST_KNOWN_HOSTS": str(known_hosts),
        "TRUENAS_V27_PATCH_ACTION": "repair-root-parent-mountpoint",
        "TRUENAS_TEST_ALLOW_PATCH_REPAIR": "1",
        "TRUENAS_V27_PRIOR_PATCH_BUNDLE": str(prior_bundle),
        "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256": prior_digest,
        "TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256": "",
    }.items():
        monkeypatch.setenv(name, value)

    try:
        runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256" in str(exc)
    else:
        raise AssertionError("parent-mountpoint repair must require the inventoried source digest")

    prior_dataset_digest = "a" * 64
    monkeypatch.setenv("TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256", prior_dataset_digest)
    selected = runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    assert selected["TRUENAS_V27_PATCH_ACTION"] == "repair-root-parent-mountpoint"
    assert selected["TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256"] == prior_digest
    assert selected["TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256"] == prior_dataset_digest

    monkeypatch.setenv("TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256", "invalid")
    try:
        runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256" in str(exc)
    else:
        raise AssertionError("parent-mountpoint repair must reject a malformed source digest")


def test_live_v27_zvol_blocksize_repair_requires_the_inspected_target_digest(tmp_path, monkeypatch) -> None:
    runner = load_runner()
    key = tmp_path / "root_ed25519"
    known_hosts = tmp_path / "known_hosts"
    prior_bundle = tmp_path / "prior-v27.tar.gz"
    key.write_text("private test key")
    key.chmod(0o600)
    known_hosts.write_text("truenas ssh host key")
    known_hosts.chmod(0o600)
    prior_bundle.write_bytes(b"registered prior v27 bundle")
    prior_digest = hashlib.sha256(prior_bundle.read_bytes()).hexdigest()
    for name, value in {
        "TRUENAS_TEST_ALLOW_MUTATIONS": "1",
        "TRUENAS_EXPECTED_VERSION": "27.0.0-RC.1",
        "TRUENAS_STORAGE_PROFILE": "path-a",
        "TRUENAS_TEST_PORT": "2223",
        "TRUENAS_TEST_KEY": str(key),
        "TRUENAS_TEST_KNOWN_HOSTS": str(known_hosts),
        "TRUENAS_V27_PATCH_ACTION": "repair-zvol-blocksize",
        "TRUENAS_TEST_ALLOW_PATCH_REPAIR": "1",
        "TRUENAS_V27_PRIOR_PATCH_BUNDLE": str(prior_bundle),
        "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256": prior_digest,
        "TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256": "",
        "TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256": "",
    }.items():
        monkeypatch.setenv(name, value)
    try:
        runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256" in str(exc)
    else:
        raise AssertionError("zvol blocksize repair must require the inventoried target digest")

    source_digest = "b" * 64
    monkeypatch.setenv("TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256", source_digest)
    selected = runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    assert selected["TRUENAS_V27_PATCH_ACTION"] == "repair-zvol-blocksize"
    assert selected["TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256"] == prior_digest
    assert selected["TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256"] == source_digest

    monkeypatch.setenv("TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256", "invalid")
    try:
        runner.required_live_environment("live-v27-patch", "truenas-test", tmp_path)
    except runner.RunnerError as exc:
        assert "TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256" in str(exc)
    else:
        raise AssertionError("zvol blocksize repair must reject a malformed target digest")


def test_user_docs_link_the_live_gate_and_name_all_lane_values() -> None:
    readme = README.read_text()
    consumers = CONSUMERS.read_text()
    design = DESIGN_GUIDE.read_text()
    all_docs = "\n".join(document.read_text() for document in USER_DOCS)
    lane_names = tomllib.loads(CONFIG.read_text())["lanes"].keys()
    assert "## Run live storage acceptance" in consumers
    assert "docs/CONSUMERS.md" in readme
    assert "live-storage" in consumers
    assert "live-boot" in consumers
    assert "live-update" in consumers
    assert "live-v28-patch" in consumers
    assert "live-ui" in consumers
    assert "run-live-gate.py" in consumers
    for lane_name in lane_names:
        assert lane_name in all_docs, f"consumer-facing docs omit registered lane {lane_name!r}"
    for action in (
        "install", "repair-root-create", "repair-root-parent-mountpoint",
        "repair-zvol-blocksize",
    ):
        assert action in consumers, f"consumer-facing docs omit v27 patch action {action!r}"
    for profile in ("path-a", "path-b"):
        assert profile in all_docs, f"consumer-facing docs omit storage profile {profile!r}"
    for document in (readme, consumers, design):
        assert "repair-root-create" in document
        assert "repair-root-parent-mountpoint" in document
        assert "repair-zvol-blocksize" in document
    ui_acceptance = consumers.split("## Run PWMCP UI storage acceptance", 1)[1].split(
        "## Repair the legacy v27 root-create guard", 1
    )[0]
    assert "Do not run `live-path-b-provision`" in ui_acceptance
    assert "`live-path-b-partition`" in ui_acceptance
    assert "TrueNAS UI pool wizard" in ui_acceptance
    assert "TRUENAS_V27_PRIOR_PATCH_BUNDLE" in consumers
    assert "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256" in consumers
    for document in (readme, design, consumers):
        assert "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT" in document
        assert "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET" in document
    assert "registered live-boot gate" in design
    assert "registered live-update gate" in design


def test_user_docs_cross_document_anchors_resolve_and_config_examples_stay_gateable() -> None:
    config_languages = {"toml", "yaml", "yml", "json"}
    for document in USER_DOCS:
        assert_markdown_anchors_resolve(document)
        code_blocks = re.findall(r"(?ms)^```([^\n]*)\n(.*?)^```[ \t]*$", document.read_text())
        config_blocks = [block for language, block in code_blocks if language.strip().lower() in config_languages]
        assert not config_blocks, (
            f"{document} now has a config example; add SHIPPED-loader and current-schema assertions"
        )
