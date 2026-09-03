from __future__ import annotations

import re
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import time
import urllib.parse
import urllib.request

from . import inuse_partition_editor
from .actions import HostActions
from .config import Config, ConfigError
from .state import StateStore
from .templates import (
    APT_CUSTOM,
    APT_PERIODIC_CONFIG,
    APT_PRIORITIES,
    APT_SOURCES,
    APT_UPDATE_NOTIFY_SCRIPT,
    APT_UPDATE_NOTIFY_SERVICE,
    APT_UPDATE_NOTIFY_TIMER,
    BOOT_NOTIFY_SERVICE,
    CGROUP2_FLAGS_SCRIPT,
    CGROUP2_FLAGS_SERVICE,
    DOCKER_CLEANUP_SERVICE,
    DOCKER_CLEANUP_TIMER,
    FSTRIM_OVERRIDE,
    KSM_SERVICE,
    NEEDRESTART_CONFIG,
    NOTIFY_SCRIPT,
    OOMD_CONFIG,
    REBOOT_CHECK_SCRIPT,
    REBOOT_CHECK_SERVICE,
    REBOOT_CHECK_TIMER,
    ROOT_SHRINK_BUILD_HOOK,
    ROOT_SHRINK_LOCAL_PREMOUNT_HOOK,
    STAGE2_SERVICE,
    THP_SERVICE,
    UNATTENDED_UPGRADES_CONFIG,
    ZSWAP_SERVICE,
)


SUPPORTED_RELEASES = {"trixie", "forky"}
SWAP_TYPE_GUID = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"


class Installer:
    def __init__(self, config: Config, actions: HostActions):
        self.config = config
        self.actions = actions
        self.state = StateStore(config.state_dir)
        self.state.dry_run = actions.dry_run
        self.release = self._detect_release()
        self.root_disk, self.root_partition_path, self.root_number = self._discover_root()

    def _run(self, argv: list[str], description: str = "", dangerous: bool = False) -> str:
        output = self.actions.run(argv, description=description, dangerous=dangerous)
        return (output or "").strip()

    def _detect_release(self) -> str:
        os_release = Path("/etc/os-release")
        values: dict[str, str] = {}
        if not self.actions.dry_run:
            for line in os_release.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    values[key] = value.strip().strip('"')
        release = values.get("VERSION_CODENAME", "trixie" if self.actions.dry_run else "")
        if release not in SUPPORTED_RELEASES:
            raise ConfigError(f"unsupported or undetected Debian release: {release!r}")
        return release

    def _discover_root(self) -> tuple[str, str, int]:
        if self.actions.dry_run:
            return "vda", "/dev/vda3", 3
        root = self._run(["/usr/bin/findmnt", "-n", "-o", "SOURCE", "/"], "find root device")
        if not root.startswith("/dev/"):
            raise RuntimeError(f"root is not a plain block-device mount: {root!r}")
        match = re.fullmatch(r"/dev/(?P<disk>.+?)(?:p)?(?P<number>[0-9]+)", root)
        if not match:
            raise RuntimeError(f"cannot derive root disk and partition number from {root!r}")
        return match.group("disk"), root, int(match.group("number"))

    @property
    def _partition_base(self) -> str:
        return f"/dev/{self.root_disk}p" if any(char.isdigit() for char in self.root_disk) else f"/dev/{self.root_disk}"

    def install(self) -> None:
        self.state.save_new(StateStore.new(self.config))
        self._stage1()

    def resume(self) -> None:
        saved = self.state.load()
        persisted = saved.get("config", {})
        allowed = set(self.config.__dataclass_fields__)
        self.config = replace(self.config, **{key: value for key, value in persisted.items() if key in allowed})
        if self.config.credential_mode == "systemd":
            credential_dir = Path("/run/credentials/vbpub-bootstrap-stage2.service")
        else:
            credential_dir = Path(self.config.state_dir) / "credentials"
        token_file = credential_dir / "telegram_bot_token"
        chat_file = credential_dir / "telegram_chat_id"
        if not self.actions.dry_run and token_file.is_file() and chat_file.is_file():
            token = token_file.read_text(encoding="utf-8").strip()
            chat_id = chat_file.read_text(encoding="utf-8").strip()
            if token and chat_id:
                self.config = replace(self.config, telegram_bot_token=token, telegram_chat_id=chat_id)
        thread_file = Path(self.config.state_dir) / "telegram_thread_id"
        if not self.actions.dry_run and thread_file.is_file():
            thread_id = thread_file.read_text(encoding="utf-8").strip()
            if thread_id.isdigit():
                self.state.save(telegram_thread_id=thread_id)
        self.state.save(phase="stage2", status="running")
        try:
            self._stage2()
            self.state.save(status="success", phase="done")
            if not self.actions.dry_run:
                Path(self.config.state_dir, "stage2_done").touch(mode=0o600)
        except BaseException as exc:
            self.state.save(status="failed", phase="stage2", last_error=str(exc))
            raise

    def status(self) -> dict[str, object]:
        state = self.state.load()
        log_dir = Path(self.config.log_dir)
        logs = sorted(str(path) for path in (log_dir.rglob("*") if log_dir.exists() else []) if path.is_file())
        return {
            **state,
            "logs": logs[-20:],
            "dry_run": self.actions.dry_run,
            "planned_action_count": len(self.actions.planned),
        }

    def show_plan(self) -> dict[str, object]:
        partitions, new_root_size = self._plan_swap_partitions()
        plan_path = Path(self.config.state_dir) / "partition-plan.sfdisk"
        if plan_path.is_file():
            plan_text = plan_path.read_text(encoding="utf-8")
        else:
            if self.actions.dry_run:
                current_dump = "\n".join([
                    "label: gpt",
                    f"device: /dev/{self.root_disk}",
                    "",
                    f"{self._partition_base}{self.root_number} : start=2500608, size={new_root_size}, type=0fc63daf-8483-4772-8e79-3d69d8477de4",
                ])
            else:
                current_dump = self._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{self.root_disk}"], dangerous=False)
            plan_text = self._write_sfdisk_plan(partitions, new_root_size)
        return {
            "run_id": self.state.load().get("run_id") if (Path(self.config.state_dir) / "state.json").is_file() else "",
            "release": self.release,
            "root_device": self.root_partition_path,
            "new_root_size_sectors": new_root_size,
            "swap_partitions": [
                {"device": f"{self._partition_base}{number}", "start": start, "sectors": size}
                for number, (start, size) in enumerate(partitions, start=self.root_number + 1)
            ],
            "sfdisk_plan": plan_text,
        }

    def verify(self) -> None:
        transaction_path = Path(self.config.state_dir) / "disk-transaction.json"
        if not transaction_path.is_file():
            raise RuntimeError(f"disk transaction manifest does not exist: {transaction_path}")
        manifest = json.loads(transaction_path.read_text(encoding="utf-8"))
        backup = Path(manifest["backup"])
        checksum = Path(manifest["checksum"])
        if not backup.is_file() or not checksum.is_file():
            raise RuntimeError("backup or checksum is missing")
        expected = checksum.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(backup.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"backup checksum mismatch: expected {expected}, got {actual}")
        self._health_gate_swap_devices()

    def disable_stage2(self) -> None:
        self._run(["/usr/bin/systemctl", "disable", "vbpub-bootstrap-stage2.service"], "disable stage2 unit")
        marker = Path(self.config.state_dir) / "stage2_done"
        if not self.actions.dry_run:
            marker.touch(mode=0o600)
        self.state.mark_step("stage2", "disabled", "unit disabled and completion marker set")

    def _packages(self, packages: list[str], stage: str) -> None:
        self._run(["/usr/bin/apt-get", "update", "-qq"], f"{stage}: refresh apt metadata")
        argv = ["/usr/bin/apt-get", "install", "-y", "--no-install-recommends", *packages]
        self._run(argv, f"{stage}: install packages", dangerous=True)
        self.state.mark_step(f"{stage}_packages", "success", " ".join(packages))

    def _configure_apt(self) -> None:
        sources_path = Path("/etc/apt/sources.list.d/debian.sources")
        backup = Path(f"/etc/apt/sources.list.v2-backup-{int(time.time())}")
        if sources_path.is_file() and not self.actions.dry_run:
            shutil.copy2(sources_path, backup)
        self.actions.write_file("/etc/apt/sources.list.d/debian.sources", APT_SOURCES.format(release=self.release))
        self.actions.write_file("/etc/apt/apt.conf.d/custom.conf", APT_CUSTOM)
        self.actions.write_file("/etc/apt/preferences.d/debian-priorities", APT_PRIORITIES.format(release=self.release))
        self._packages(["ca-certificates", "curl", "git", "python3"], "apt")
        if not self.actions.dry_run:
            policy = self._run(["/usr/bin/apt-cache", "policy"], "verify apt suites resolve")
            expected = [self.release, f"{self.release}-updates", f"{self.release}-security", f"{self.release}-backports", "testing", "unstable"]
            missing = [suite for suite in expected if suite not in policy]
            if missing:
                raise RuntimeError(f"APT configuration did not resolve suite(s): {', '.join(missing)}")
        self.state.mark_step("apt_config", "success", self.release)

    def _configure_users(self) -> None:
        packages = ["htop", "iftop", "less", "man-db", "mc", "nano"]
        self._packages(packages, "users")
        nanorc = "\n".join([
            "set tabsize 4", "set softwrap", "set tabstospaces", "set mouse",
            "set linenumbers", "set smooth", "set autoindent", "set boldtext",
            'include /usr/share/nano/*.nanorc', "",
        ])
        aliases = "\n".join([
            "alias ll='ls -alF'", "alias la='ls -A'", "alias l='ls -CF'",
            "alias ls='ls --color=auto'", "alias grep='grep --color=auto'", "",
        ])
        self.actions.write_file("/root/.nanorc", nanorc, 0o600)
        self.actions.write_file("/root/.bash_aliases", aliases, 0o600)
        self.state.mark_step("user_config", "success", ", ".join(packages))

    def _configure_journald(self) -> None:
        content = """[Journal]
Storage=persistent
Compress=yes
SystemMaxUse=200M
SystemKeepFree=500M
SystemMaxFileSize=100M
MaxRetentionSec=12month
MaxFileSec=1month
"""
        self.actions.write_file("/etc/systemd/journald.conf.d/99-vbpub-v2.conf", content)
        self._run(["/usr/bin/systemctl", "restart", "systemd-journald"], "restart journald", dangerous=True)
        self.state.mark_step("journald_config", "success", "persistent 200M journal")

    def _install_docker(self) -> None:
        arch = platform.machine()
        if arch == "x86_64":
            apt_arch = "amd64"
        elif arch == "aarch64":
            apt_arch = "arm64"
        else:
            raise RuntimeError(f"unsupported architecture for Docker installation: {arch}")
        self._packages(["ca-certificates", "curl", "gnupg"], "docker-prereqs")
        self.actions.mkdir("/etc/apt/keyrings")
        parsed = urllib.parse.urlparse("https://download.docker.com/linux/debian/gpg")
        if parsed.scheme != "https":
            raise RuntimeError("Docker GPG URI must use HTTPS")
        if self.actions.dry_run:
            self.actions.write_file("/etc/apt/keyrings/docker.asc", "dry-run-gpg-key\n", 0o644)
        else:
            with urllib.request.urlopen("https://download.docker.com/linux/debian/gpg", timeout=30) as response:
                key = response.read()
            self.actions.write_file("/etc/apt/keyrings/docker.asc", key.decode("utf-8"), 0o644)
        docker_sources = "\n".join([
            "Types: deb",
            "URIs: https://download.docker.com/linux/debian",
            f"Suites: {self.release}",
            "Components: stable",
            "Signed-By: /etc/apt/keyrings/docker.asc",
            f"Architectures: {apt_arch}",
            "",
        ])
        self.actions.write_file("/etc/apt/sources.list.d/docker.sources", docker_sources)
        self._packages(["containerd.io", "docker-buildx-plugin", "docker-ce", "docker-ce-cli", "docker-compose-plugin"], "docker")
        # Merge, not overwrite (D-F1: daemon.json ownership split with mdt
        # host-setup, which separately owns only "cgroup-parent" and merges
        # the same way — each tool's own keys land correctly regardless of
        # run order, and neither clobbers a hand-added key or the other
        # tool's keys). live-restore/log-driver/log-opts are NOT this
        # method's keys — see _configure_docker_daemon(), called right after
        # this in _stage1().
        existing = self._read_json_for_merge("/etc/docker/daemon.json")
        existing["features"] = {"buildkit": True}
        existing["metrics-addr"] = "127.0.0.1:9323"
        existing["storage-driver"] = "overlay2"
        existing["userland-proxy"] = False
        self.actions.write_file("/etc/docker/daemon.json", json.dumps(existing, indent=2) + "\n", 0o644)
        self._run(["/usr/bin/systemctl", "enable", "--now", "docker"], "enable Docker", dangerous=True)
        self.state.mark_step("docker_install", "success", apt_arch)

    def _read_json_for_merge(self, path: str) -> dict:
        # Dry-run never touches the real filesystem for a read either — same
        # convention as _health_gate_swap_devices()'s zswap-compressor read
        # (installer.py, "zstd" if self.actions.dry_run else Path(...).read_text()).
        if self.actions.dry_run:
            return {}
        p = Path(path)
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"refusing to merge into an unparseable {path}: {exc}") from exc

    def _configure_docker_daemon(self) -> None:
        # Owns live-restore/log-driver/log-opts only — see the ownership
        # split rationale in _install_docker()'s comment above. mdt
        # host-setup/install.sh owns "cgroup-parent" the same, disjoint way.
        existing = self._read_json_for_merge("/etc/docker/daemon.json")
        existing["live-restore"] = self.config.docker_live_restore
        existing["log-driver"] = self.config.docker_log_driver
        existing.setdefault("log-opts", {})
        existing["log-opts"]["max-size"] = self.config.docker_log_max_size
        existing["log-opts"]["max-file"] = self.config.docker_log_max_file
        self.actions.write_file("/etc/docker/daemon.json", json.dumps(existing, indent=2) + "\n", 0o644)
        self.state.mark_step("docker_daemon_config", "success", self.config.docker_log_driver)

    def _install_notify_helper(self) -> None:
        # Unconditional, like cgroup2-flags: harmless if Telegram isn't
        # configured (the script itself no-ops when credentials are absent),
        # and vbpub-reboot-check/vbpub-apt-check depend on it existing.
        self.actions.write_file("/usr/local/sbin/vbpub-notify", NOTIFY_SCRIPT, 0o755)
        self.state.mark_step("notify_helper", "success", "/usr/local/sbin/vbpub-notify")

    def _configure_docker_cleanup(self) -> None:
        self.actions.write_file(
            "/etc/systemd/system/vbpub-docker-cleanup.service",
            DOCKER_CLEANUP_SERVICE.format(age_hours=self.config.docker_cleanup_max_age_hours),
        )
        self.actions.write_file("/etc/systemd/system/vbpub-docker-cleanup.timer", DOCKER_CLEANUP_TIMER)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        self._run(
            ["/usr/bin/systemctl", "enable", "--now", "vbpub-docker-cleanup.timer"],
            "enable docker cleanup timer", dangerous=True,
        )
        self.state.mark_step(
            "docker_cleanup", "success",
            f"weekly, age>={self.config.docker_cleanup_max_age_hours}h, images+containers+build-cache only",
        )

    def _unattended_upgrade_origins(self) -> list[str]:
        release = self.release
        security = [
            f'    "origin=Debian,codename={release},label=Debian-Security";',
            f'    "origin=Debian,codename={release}-security,label=Debian-Security";',
        ]
        if self.config.apt_auto_upgrade_mode == "security-only":
            return security
        return security + [
            f'    "origin=Debian,codename={release}";',
            f'    "origin=Debian,codename={release}-updates";',
            f'    "origin=Debian,codename={release}-backports";',
            '    "origin=Debian,suite=testing";',
            '    "origin=Debian,suite=unstable";',
        ]

    def _configure_apt_auto_upgrade(self) -> None:
        if self.config.apt_auto_upgrade_mode == "notify-only":
            self.actions.write_file("/usr/local/sbin/vbpub-apt-check", APT_UPDATE_NOTIFY_SCRIPT, 0o755)
            self.actions.write_file("/etc/systemd/system/vbpub-apt-check.service", APT_UPDATE_NOTIFY_SERVICE)
            self.actions.write_file("/etc/systemd/system/vbpub-apt-check.timer", APT_UPDATE_NOTIFY_TIMER)
            self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
            self._run(
                ["/usr/bin/systemctl", "enable", "--now", "vbpub-apt-check.timer"],
                "enable apt notify-only check", dangerous=True,
            )
            self.state.mark_step("apt_auto_upgrade", "success", "notify-only")
            return
        self._packages(["unattended-upgrades", "needrestart"], "apt-auto-upgrade")
        origins = self._unattended_upgrade_origins()
        self.actions.write_file(
            "/etc/apt/apt.conf.d/51-vbpub-unattended-upgrades",
            UNATTENDED_UPGRADES_CONFIG.format(mode=self.config.apt_auto_upgrade_mode, origins="\n".join(origins)),
        )
        self.actions.write_file("/etc/apt/apt.conf.d/20auto-upgrades", APT_PERIODIC_CONFIG)
        self.actions.write_file("/etc/needrestart/conf.d/vbpub.conf", NEEDRESTART_CONFIG)
        self._run(
            ["/usr/bin/systemctl", "enable", "--now", "apt-daily.timer", "apt-daily-upgrade.timer"],
            "enable apt auto-upgrade timers", dangerous=True,
        )
        self.state.mark_step("apt_auto_upgrade", "success", self.config.apt_auto_upgrade_mode)

    def _configure_cgroup2_flags(self) -> None:
        # Default ON, no config flag — memory_recursiveprot missing silently
        # defeats every slice's MemoryLow/MemoryMin with no other symptom
        # (systemctl show keeps reporting the value you set). Applied
        # immediately (enable --now), unlike zswap/thp's enable-only: those
        # write to sysfs knobs ordered before swap.target/sysinit.target
        # hasn't been reached yet in this stage1/stage2 split; a cgroup2
        # remount has no such ordering requirement and is safe to apply the
        # moment it's written (idempotent — a no-op if already correct).
        self.actions.write_file(
            "/usr/local/sbin/vbpub-cgroup2-flags.sh", CGROUP2_FLAGS_SCRIPT, 0o755
        )
        self.actions.write_file("/etc/systemd/system/cgroup2-flags.service", CGROUP2_FLAGS_SERVICE)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        self._run(
            ["/usr/bin/systemctl", "enable", "--now", "cgroup2-flags.service"],
            "enable cgroup2 mount-flags unit", dangerous=True,
        )
        self.state.mark_step("cgroup2_flags", "success", "memory_recursiveprot+nsdelegate")

    def _configure_ksm(self) -> None:
        self.actions.write_file("/etc/systemd/system/ksm-config.service", KSM_SERVICE)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        self._run(["/usr/bin/systemctl", "enable", "ksm-config.service"], "enable KSM unit")
        self.state.mark_step("ksm_config", "success", "ksmd enabled host-wide, opt-in per process")

    def _configure_oomd(self) -> None:
        self.actions.mkdir("/etc/systemd/oomd.conf.d")
        self.actions.write_file("/etc/systemd/oomd.conf.d/vbpub.conf", OOMD_CONFIG)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        self._run(
            ["/usr/bin/systemctl", "enable", "--now", "systemd-oomd"],
            "enable systemd-oomd with vbpub thresholds", dangerous=True,
        )
        self.state.mark_step("oomd_config", "success", "SwapUsedLimit=90%, pressure=60%/20s")

    def _configure_fstrim(self) -> None:
        self.actions.mkdir("/etc/systemd/system/fstrim.timer.d")
        self.actions.write_file("/etc/systemd/system/fstrim.timer.d/vbpub-daily.conf", FSTRIM_OVERRIDE)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        self._run(
            ["/usr/bin/systemctl", "enable", "--now", "fstrim.timer"],
            "enable daily fstrim", dangerous=True,
        )
        self.state.mark_step("fstrim_config", "success", "daily, whole-disk")

    def _configure_auto_reboot(self) -> None:
        self.actions.write_file("/usr/local/sbin/vbpub-reboot-check", REBOOT_CHECK_SCRIPT, 0o755)
        self.actions.write_file("/etc/systemd/system/vbpub-reboot-check.service", REBOOT_CHECK_SERVICE)
        self.actions.write_file(
            "/etc/systemd/system/vbpub-reboot-check.timer",
            REBOOT_CHECK_TIMER.format(time=self.config.reboot_window_time),
        )
        self.actions.write_file("/etc/systemd/system/vbpub-boot-notify.service", BOOT_NOTIFY_SERVICE)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        self._run(
            ["/usr/bin/systemctl", "enable", "--now", "vbpub-reboot-check.timer"],
            "enable scheduled reboot-required check", dangerous=True,
        )
        self._run(
            ["/usr/bin/systemctl", "enable", "vbpub-boot-notify.service"],
            "enable post-reboot notice", dangerous=True,
        )
        self.state.mark_step("auto_reboot", "success", self.config.reboot_window_time)

    def _configure_zswap(self) -> None:
        self.actions.write_file(
            "/etc/systemd/system/zswap-config.service",
            ZSWAP_SERVICE.format(
                compressor=self.config.zswap_compressor,
                zpool=self.config.zswap_zpool,
                pool_percent=self.config.zswap_pool_percent,
            ),
        )
        self.actions.write_file("/etc/modules-load.d/vbpub-zstd.conf", "zstd\n")
        self.actions.write_file(
            "/etc/sysctl.d/99-vbpub-swap.conf",
            "\n".join([
                f"vm.swappiness = {self.config.vm_swappiness}",
                "vm.page-cluster = 0",
                "vm.vfs_cache_pressure = 50",
                "vm.watermark_scale_factor = 125",
                "vm.dirty_ratio = 15",
                "vm.dirty_background_ratio = 5",
                "",
            ]),
        )
        self.actions.write_file("/etc/systemd/system/thp-config.service", THP_SERVICE)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        self._run(["/usr/bin/systemctl", "enable", "zswap-config.service", "thp-config.service"], "enable early tuning units")
        self.state.mark_step("zswap_config", "success", self.config.zswap_compressor)

    def _disk_facts(self) -> tuple[int, int, int]:
        if self.actions.dry_run:
            def gib_to_sectors(size_gib: int) -> int:
                return size_gib * 1024 * 1024 * 1024 // 512
            return gib_to_sectors(512), 2500608, gib_to_sectors(8)
        size_output = self._run(["/usr/sbin/blockdev", "--getsize64", f"/dev/{self.root_disk}"])
        dump = self._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{self.root_disk}"])
        disk_sectors = int(size_output) // 512
        # Table's attribute parser is regex-based (_ATTR_RE) and tolerates
        # padded (`start=        2048`) and quoted (`name="EFI System
        # Partition"`) attributes the way real sfdisk --dump output looks —
        # unlike a naive line.split(), which mis-tokenizes a quoted value
        # containing spaces. raw=/disk_sectors= reuse the dump we already
        # fetched through HostActions instead of letting Table shell out.
        table = inuse_partition_editor.Table(f"/dev/{self.root_disk}", raw=dump, disk_sectors=disk_sectors)
        root_entry = next((part for part in table.parts if part["num"] == self.root_number), None)
        if root_entry is None:
            raise RuntimeError(f"could not parse current root partition from sfdisk dump")
        return disk_sectors, root_entry["start"], root_entry["size"]

    def _preflight_disk_transaction(self) -> dict[str, object]:
        if self.actions.dry_run:
            return {"mode": "dry-run", "holders": [], "mounts": []}
        holders = Path("/sys/block") / self.root_disk / "holders"
        holder_names = sorted(path.name for path in holders.iterdir()) if holders.is_dir() else []
        if holder_names:
            raise RuntimeError(
                f"refusing disk transaction: {self.root_disk} has active holder mappings: {', '.join(holder_names)}"
            )
        findmnt = self._run(["/usr/bin/findmnt", "-rn", "-o", "SOURCE,TARGET,FSTYPE"], "enumerate mounted sources")
        forbidden = []
        prefix = f"{self._partition_base}"
        for line in findmnt.splitlines():
            source = line.split()[0] if line.split() else ""
            if source == f"/dev/{self.root_disk}" or (source.startswith(prefix) and source[len(prefix):].isdigit()):
                forbidden.append(line)
        allowed = {self.root_partition_path}
        unexpected_mounts = [line for line in forbidden if line.split()[0] not in allowed]
        if unexpected_mounts:
            raise RuntimeError("refusing disk transaction: partitions are mounted:\n" + "\n".join(unexpected_mounts))
        return {"holders": holder_names, "mounts": forbidden}

    def _parse_partition_entries(self, dump: str) -> dict[int, dict[str, str]]:
        entries: dict[int, dict[str, str]] = {}
        prefix = self._partition_base
        for line in dump.splitlines():
            columns = line.split()
            if not columns or not columns[0].startswith(prefix):
                continue
            suffix = columns[0][len(prefix):]
            if not suffix.isdigit():
                continue
            values: dict[str, str] = {}
            for item in columns[1:]:
                key, _, value = item.strip(",").partition("=")
                if value:
                    values[key] = value
            entries[int(suffix)] = values
        return entries

    def _validate_plan_geometry(
        self,
        current: dict[int, dict[str, str]],
        plan: dict[int, dict[str, str]],
        new_root_size: int,
    ) -> None:
        root_current = current.get(self.root_number)
        root_plan = plan.get(self.root_number)
        if not root_current or not root_plan:
            raise RuntimeError("partition plan does not preserve the root partition")
        before_end = int(root_current["start"]) + int(root_current["size"])
        after_end = int(root_plan["start"]) + int(root_plan["size"])
        if after_end > before_end:
            raise RuntimeError("partition plan unexpectedly grows the root partition")
        for number, entry in current.items():
            if number > self.root_number:
                raise RuntimeError(
                    f"fresh-install contract violated: existing partition {number} follows root and would be dropped"
                )
        ordered = sorted(plan.items())
        previous_start = -1
        previous_end = -1
        for number, entry in ordered:
            start = int(entry["start"])
            size = int(entry["size"])
            end = start + size
            if previous_end >= 0 and start < previous_end:
                raise RuntimeError(f"partition plan overlap detected at partition {number}")
            previous_start, previous_end = start, end
        swap_numbers = [number for number in plan if number > self.root_number]
        expected_numbers = list(range(self.root_number + 1, self.root_number + self.config.swap_file_count + 1))
        if swap_numbers != expected_numbers:
            raise RuntimeError(f"partition plan has unexpected swap numbering: {swap_numbers!r}")

    def _plan_swap_partitions(self) -> tuple[list[tuple[int, int]], int]:
        disk_sectors, root_start, root_size = self._disk_facts()
        total_sectors = self.config.swap_disk_total_gb * 1024 * 1024 * 1024 // 512
        per_device = total_sectors // self.config.swap_file_count
        alignment = 2048
        per_device -= per_device % alignment
        if per_device <= 0:
            raise RuntimeError(f"swap target too small for {self.config.swap_file_count} devices")
        actual_total = per_device * self.config.swap_file_count
        end_buffer = 2048
        new_root_size = max(root_size, self.config.preserve_root_size_gb * 1024 * 1024 * 1024 // 512)
        first_swap_start = ((root_start + new_root_size + alignment - 1) // alignment) * alignment
        required_end = first_swap_start + actual_total + end_buffer
        if required_end > disk_sectors:
            needed_gib = (required_end - disk_sectors + 1024 ** 3 - 1) // 1024 ** 3
            raise RuntimeError(f"disk lacks space for known swap shape; reduce swap by about {needed_gib} GiB")
        start = first_swap_start
        partitions = [(start + index * per_device, per_device) for index in range(self.config.swap_file_count)]
        return partitions, new_root_size

    def _root_filesystem_facts(self) -> tuple[int, int]:
        """(block_size_bytes, minimum_blocks) for the live, mounted root filesystem.

        Both dumpe2fs -h and resize2fs -P are read-only estimate operations —
        safe to run against root while it's mounted read-write, unlike the
        actual shrink, which needs the offline window the initramfs hook
        provides (see CASE-B-ROOT-SHRINK-DESIGN.md).
        """
        if self.actions.dry_run:
            return 4096, 1_000_000
        dumpe2fs_output = self._run(
            ["/usr/sbin/dumpe2fs", "-h", self.root_partition_path],
            "read root filesystem block size",
            dangerous=False,
        )
        block_size = 0
        for line in dumpe2fs_output.splitlines():
            if line.strip().startswith("Block size:"):
                block_size = int(line.split(":", 1)[1].strip())
        if block_size <= 0:
            raise RuntimeError("could not determine root filesystem block size from dumpe2fs -h")
        resize2fs_output = self._run(
            ["/usr/sbin/resize2fs", "-P", self.root_partition_path],
            "compute minimum ext filesystem size",
            dangerous=False,
        )
        minimum_blocks = 0
        for line in resize2fs_output.splitlines():
            if "Estimated minimum size of the filesystem:" in line:
                minimum_blocks = int(line.rsplit(":", 1)[1].strip())
        if minimum_blocks <= 0:
            raise RuntimeError("could not determine minimum root filesystem size from resize2fs -P")
        return block_size, minimum_blocks

    def _plan_root_shrink(self) -> None:
        """Case B: install an initramfs hook to shrink root offline, pre-mount.

        Called late in _stage1(), before _install_stage2()/_reboot() -- an
        initramfs-tools local-premount hook fires on EVERY boot, so
        installing it before stage1's own existing reboot is enough; no
        extra reboot cycle is needed. Auto-detected, not configurable:
        _plan_swap_partitions() succeeding at all means free space already
        covers the known swap shape (Case A, the inuse_partition_editor.Table
        path in _apply_known_swap_shape() handles it exactly as today, and
        this method is a no-op). Only its "disk lacks space" failure mode
        means root itself must shrink first (Case B).
        """
        try:
            self._plan_swap_partitions()
        except RuntimeError as exc:
            if "disk lacks space for known swap shape" not in str(exc):
                raise
        else:
            self.state.mark_step("root_shrink", "not_needed", "existing free space already covers the planned swap shape")
            return

        self._packages(["e2fsprogs"], "stage1")
        disk_sectors, root_start, root_size = self._disk_facts()
        sector = 512
        block_size, minimum_blocks = self._root_filesystem_facts()
        minimum_sectors = (minimum_blocks * block_size + sector - 1) // sector
        # Headroom above the filesystem's own reported minimum: resize2fs -P
        # is an estimate, and shrinking to the exact byte-for-byte minimum
        # leaves zero room for anything the live system wrote between that
        # estimate and the offline shrink actually running.
        safety_margin_sectors = (256 * 1024 * 1024) // sector
        requested_sectors = self.config.preserve_root_size_gb * 1024 * 1024 * 1024 // sector
        alignment = 2048
        # Refuse outright rather than silently target a larger size than
        # configured -- CASE-B-ROOT-SHRINK-DESIGN.md's explicit "always
        # verify at runtime, refuse if it doesn't fit" rule. Clamping up via
        # max() here would mean preserve_root_size_gb quietly stops being
        # the number that governs the shrink the moment it's too small,
        # with nothing telling the operator their configured value was
        # overridden.
        if requested_sectors < minimum_sectors + safety_margin_sectors:
            raise RuntimeError(
                f"preserve_root_size_gb ({self.config.preserve_root_size_gb} GiB) is smaller than the "
                f"root filesystem's own minimum plus margin ({(minimum_sectors + safety_margin_sectors) * sector / 1024**3:.2f} GiB); "
                f"raise preserve_root_size_gb rather than shrinking further than configured"
            )
        target_root_sectors = ((requested_sectors + alignment - 1) // alignment) * alignment
        if target_root_sectors >= root_size:
            raise RuntimeError(
                f"root shrink target ({target_root_sectors} sectors) is not smaller than the current "
                f"root ({root_size} sectors); refusing to plan a no-op or growing shrink"
            )

        # per_device is the identical computation _plan_swap_partitions() just
        # did with these same config values -- reaching this line already
        # proves it raised "lacks space", not "too small for N devices", so
        # per_device > 0 here is an established invariant, not something to
        # re-check.
        total_swap_sectors = self.config.swap_disk_total_gb * 1024 * 1024 * 1024 // sector
        per_device = total_swap_sectors // self.config.swap_file_count
        per_device -= per_device % alignment
        actual_total = per_device * self.config.swap_file_count
        end_buffer = 2048
        first_swap_start = ((root_start + target_root_sectors + alignment - 1) // alignment) * alignment
        required_end = first_swap_start + actual_total + end_buffer
        if required_end > disk_sectors:
            needed_gib = (required_end - disk_sectors + 1024 ** 3 - 1) // 1024 ** 3
            raise RuntimeError(
                f"disk still lacks space for the known swap shape even after shrinking root to its "
                f"filesystem minimum; reduce swap by about {needed_gib} GiB"
            )
        swap_partitions = [
            (first_swap_start + index * per_device, per_device) for index in range(self.config.swap_file_count)
        ]

        plan_text = self._write_sfdisk_plan(swap_partitions, target_root_sectors)
        target_blocks = (target_root_sectors * sector) // block_size
        env_content = "".join([
            f"DEVICE={self.root_partition_path}\n",
            f"DISK=/dev/{self.root_disk}\n",
            f"TARGET_BLOCKS={target_blocks}\n",
            f"TARGET_SECTORS={target_root_sectors}\n",
            "SFDISK_PLAN=/etc/vbpub/root-shrink-plan.sfdisk\n",
        ])
        self.actions.write_file("/etc/vbpub/root-shrink-plan.sfdisk", plan_text, 0o600)
        self.actions.write_file("/etc/vbpub/root-shrink-plan.env", env_content, 0o600)
        self.actions.write_file(
            "/etc/initramfs-tools/hooks/vbpub-root-shrink", ROOT_SHRINK_BUILD_HOOK, 0o755
        )
        self.actions.write_file(
            "/etc/initramfs-tools/scripts/local-premount/vbpub-root-shrink",
            ROOT_SHRINK_LOCAL_PREMOUNT_HOOK,
            0o755,
        )
        self._run(["/usr/sbin/update-initramfs", "-u"], "rebuild initramfs with the root-shrink hook", dangerous=True)
        self.state.mark_step(
            "root_shrink", "planned",
            f"target root {target_root_sectors} sectors (was {root_size}); hook installed",
        )

    def _verify_and_apply_root_shrink(self) -> bool:
        """First action in _stage2(): resolve whatever the Case B hook did (or didn't).

        No root_shrink step recorded -> Case A, nothing to do, proceed exactly
        as today (returns False). root_size already <= the planned target ->
        the hook succeeded on a prior boot: clean up the hook (so future boots
        don't keep paying its idempotency-check cost) and return True.
        Otherwise the hook silently no-op'd or failed: mark it, notify, and
        stop stage2 here rather than guessing or retrying destructively -- a
        clearly-flagged, always-bootable, partially-provisioned host beats a
        silent wrong one.

        The return value matters: the hook's OWN sfdisk write (built by
        _plan_root_shrink() via the same _write_sfdisk_plan()) already placed
        the swap partitions on disk in the SAME write that shrunk root --
        unlike Case A, there is no separate partition-table step left to do.
        Calling _apply_known_swap_shape() again after a real (non-dry-run)
        success would try to write those same partitions a second time, and
        _validate_plan_geometry() would refuse it as "existing partition
        follows root and would be dropped" (adversarial review finding, a
        stage2 crash immediately after root_shrink reports success). The
        caller must skip straight to _activate_swap_partitions() when this
        returns True. Dry-run returns False deliberately: no hook ever wrote
        anything for real, so the ordinary Case-A dry-run planning path
        (already tested, already correct) should still run to show its plan.
        """
        state = self.state.load()
        root_shrink_step = state.get("steps", {}).get("root_shrink")
        if not root_shrink_step or root_shrink_step.get("status") != "planned":
            return False
        disk_sectors, root_start, root_size = self._disk_facts()
        if self.actions.dry_run:
            self.state.mark_step("root_shrink", "success", "dry-run: assumed the hook succeeded")
            return False
        plan_env = Path("/etc/vbpub/root-shrink-plan.env")
        target_sectors = None
        if plan_env.is_file():
            for line in plan_env.read_text(encoding="utf-8").splitlines():
                if line.startswith("TARGET_SECTORS="):
                    target_sectors = int(line.partition("=")[2].strip())
        if target_sectors is None:
            raise RuntimeError(
                "root shrink step is 'planned' but /etc/vbpub/root-shrink-plan.env is missing or malformed"
            )
        if root_size <= target_sectors:
            for path in (
                "/etc/initramfs-tools/scripts/local-premount/vbpub-root-shrink",
                "/etc/initramfs-tools/hooks/vbpub-root-shrink",
                "/etc/vbpub/root-shrink-plan.env",
                "/etc/vbpub/root-shrink-plan.sfdisk",
            ):
                Path(path).unlink(missing_ok=True)
            self._run(
                ["/usr/sbin/update-initramfs", "-u"], "rebuild initramfs without the root-shrink hook", dangerous=True
            )
            self.state.mark_step("root_shrink", "success", f"root shrunk to {root_size} sectors (target {target_sectors})")
            return True
        self.state.mark_step("root_shrink", "failed", f"root is still {root_size} sectors, target was {target_sectors}")
        self._notify(
            f"vbpub: root shrink did not complete (still {root_size} sectors, wanted <= {target_sectors}); "
            f"swap was not configured, host is otherwise healthy"
        )
        raise RuntimeError(
            f"root shrink did not complete: root is still {root_size} sectors (target {target_sectors}); "
            f"stopping before swap placement rather than guessing"
        )

    def _write_sfdisk_plan(self, partitions: list[tuple[int, int]], new_root_size: int) -> str:
        prefix = self._partition_base
        if self.actions.dry_run:
            dump = "\n".join([
                "label: gpt",
                f"device: /dev/{self.root_disk}",
                "",
                f"{prefix}{self.root_number} : start=2500608, size={new_root_size}, type=0fc63daf-8483-4772-8e79-3d69d8477de4",
            ])
        else:
            dump = self._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{self.root_disk}"], dangerous=False)
        # Excludes ANY partition line (not just root's own) -- the `kept`
        # loop below already captures every partition before root verbatim;
        # matching only root's own prefix here left every earlier partition
        # (e.g. an ESP before a root_number > 1) in BOTH header_lines and
        # kept, duplicating its line in the generated plan (adversarial
        # review finding, reproduced against the project's own fixtures).
        header_lines = [
            line for line in dump.splitlines()
            if ":" in line and not line.startswith(prefix)
        ]
        kept: list[str] = []
        for line in dump.splitlines():
            columns = line.split()
            if not columns or not columns[0].startswith(prefix):
                continue
            try:
                number = int(columns[0][len(prefix):])
            except ValueError:
                continue
            if number < self.root_number:
                kept.append(line)
        lines = [*header_lines, ""]
        for line in kept:
            lines.append(line)
        root_line = next(
            line for line in dump.splitlines()
            if line.startswith(f"{prefix}{self.root_number} ") or line.startswith(f"{prefix}{self.root_number}, ")
        )
        device, separator, attributes = root_line.partition(":")
        root_parts = [part.strip(",") for part in attributes.split()]
        rebuilt = []
        inserted = False
        for part in root_parts:
            key = part.partition("=")[0]
            if key == "size":
                rebuilt.append(f"size={new_root_size}")
                inserted = True
            else:
                rebuilt.append(part)
        if not inserted:
            rebuilt.append(f"size={new_root_size}")
        lines.append(f"{prefix}{self.root_number} : " + ", ".join(rebuilt))
        for index, (start, size) in enumerate(partitions, start=self.root_number + 1):
            lines.append(f"{prefix}{index} : start={start}, size={size}, type={SWAP_TYPE_GUID}")
        return "\n".join(lines) + "\n"

    def _apply_known_swap_shape(self) -> None:
        partitions, new_root_size = self._plan_swap_partitions()
        preflight = self._preflight_disk_transaction()
        if self.actions.dry_run:
            current_dump = "\n".join([
                "label: gpt",
                f"device: /dev/{self.root_disk}",
                "",
                f"{self._partition_base}{self.root_number} : start=2500608, size={new_root_size}, type=0fc63daf-8483-4772-8e79-3d69d8477de4",
            ])
        else:
            current_dump = self._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{self.root_disk}"], dangerous=False)
        current_entries = self._parse_partition_entries(current_dump)
        plan_text = self._write_sfdisk_plan(partitions, new_root_size)
        plan_entries = self._parse_partition_entries(plan_text)
        self._validate_plan_geometry(current_entries, plan_entries, new_root_size)
        backup_dir = Path(self.config.state_dir) / "backups"
        timestamp = int(time.time())
        backup_name = f"ptable-{timestamp}.sfdisk"
        checksum_name = f"{backup_name}.sha256"
        plan_path = str(Path(self.config.state_dir) / "partition-plan.sfdisk")
        if self.actions.dry_run:
            self.actions.write_file(plan_path, plan_text)
        else:
            self.actions.write_file(str(backup_dir / backup_name), current_dump, 0o600)
            backup_digest = hashlib.sha256(current_dump.encode("utf-8")).hexdigest()
            self.actions.write_file(str(backup_dir / checksum_name), f"{backup_digest}  {backup_name}\n", 0o644)
            self.actions.write_file(plan_path, plan_text)
            self._run(["/usr/sbin/sfdisk", "--force", "--no-reread", f"/dev/{self.root_disk}"], dangerous=True)
            # partx -a + udevadm settle (inuse_partition_editor.Table.write()'s
            # own apply mechanism, ported to go through HostActions rather than
            # its bare subprocess.run) is more reliable than partprobe alone at
            # getting the new swap partitions' /dev/xxxN nodes to actually exist
            # before _activate_swap_partitions() tries to mkswap them (P0#3).
            self._run(["/usr/sbin/partx", "-a", f"/dev/{self.root_disk}"], "register new partitions with the kernel", dangerous=True)
            self._run(["/usr/bin/udevadm", "settle"], "wait for udev to create new device nodes")
            readback = self._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{self.root_disk}"], dangerous=False)
            readback_entries = self._parse_partition_entries(readback)
            if readback_entries != plan_entries:
                self._run(
                    ["/usr/sbin/sfdisk", "--force", f"/dev/{self.root_disk}", str(backup_dir / backup_name)],
                    description="rollback failed partition write",
                    dangerous=True,
                )
                self._run(["/usr/sbin/partprobe", f"/dev/{self.root_disk}"], "refresh kernel view after rollback")
                raise RuntimeError(f"partition table verification failed; restored backup {backup_dir / backup_name}")
            expected_paths = [
                f"{self._partition_base}{number}"
                for number in range(self.root_number + 1, self.root_number + self.config.swap_file_count + 1)
            ]
            for path in expected_paths:
                for _ in range(50):
                    if self.actions.exists(path):
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"partition device did not appear after partx/udevadm settle: {path}")
        manifest = {
            "preflight": preflight,
            "plan": plan_text,
            "current": current_dump,
            "backup": str(backup_dir / backup_name),
            "checksum": str(backup_dir / checksum_name),
            "new_root_size_sectors": new_root_size,
        }
        self.actions.write_file(
            str(Path(self.config.state_dir) / "disk-transaction.json"),
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            0o600,
        )
        self.state.mark_step("partitions", "planned" if self.actions.dry_run else "success", plan_path)

    def _health_gate_swap_devices(self) -> None:
        expected_numbers = range(self.root_number + 1, self.root_number + self.config.swap_file_count + 1)
        prefix = self._partition_base
        swaps_output = self._run(["/usr/sbin/swapon", "--show=NAME,TYPE,SIZE,PRIO", "--noheadings"], dangerous=False) if not self.actions.dry_run else ""
        active_names = {line.split()[0] for line in swaps_output.splitlines() if line.strip()}
        fstab_path = Path("/etc/fstab")
        fstab_lines = fstab_path.read_text(encoding="utf-8").splitlines() if fstab_path.is_file() else []
        fstab_partuuids = {
            line.split()[0].removeprefix("PARTUUID=")
            for line in fstab_lines
            if len(line.split()) >= 3 and line.split()[2].lower() == "swap" and line.split()[0].startswith("PARTUUID=")
        }
        for number in expected_numbers:
            path = f"{prefix}{number}"
            partuuid = "dry-run" if self.actions.dry_run else self._run(["/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", path])
            if not partuuid:
                raise RuntimeError(f"health gate failed: missing PARTUUID on {path}")
            if not self.actions.dry_run and path not in active_names:
                raise RuntimeError(f"health gate failed: {path} is formatted but not active")
            if not self.actions.dry_run and partuuid not in fstab_partuuids:
                raise RuntimeError(f"health gate failed: {path} PARTUUID is absent from fstab")
        compressor = "zstd" if self.actions.dry_run else Path("/sys/module/zswap/parameters/compressor").read_text(encoding="utf-8").strip()
        if compressor != self.config.zswap_compressor:
            raise RuntimeError(f"health gate failed: zswap compressor is {compressor!r}, expected {self.config.zswap_compressor!r}")
        output_file = Path(self.config.stage2_output)
        if not self.actions.dry_run and not (output_file.is_file() and output_file.stat().st_size >= 0):
            raise RuntimeError(f"health gate failed: stage2 log does not exist: {output_file}")
        self.state.mark_step("health_gate", "planned" if self.actions.dry_run else "success", f"{self.config.swap_file_count} swaps verified")

    def _activate_swap_partitions(self) -> None:
        prefix = self._partition_base
        first_number = self.root_number + 1
        last_number = self.root_number + self.config.swap_file_count
        fstab_entries = []
        if not self.actions.dry_run:
            self._run(["/usr/sbin/swapoff", "-a"], "disable existing swap before formatting fresh devices", True)
        discard = ",discard=once" if self.config.swap_discard else ""
        for offset, number in enumerate(range(first_number, last_number + 1), start=1):
            path = f"{prefix}{number}"
            label = f"vbpub-swap{offset}"
            partuuid = "dry-run" if self.actions.dry_run else self._run(["/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", path])
            if not self.actions.dry_run and not partuuid:
                raise RuntimeError(f"expected swap partition has no PARTUUID after partitioning: {path}")
            self._run(["/usr/sbin/mkswap", "-L", label, path], f"format {path}", dangerous=True)
            self._run(["/usr/bin/swapon", "-p", str(self.config.swap_priority), path], f"enable {path}", dangerous=True)
            refreshed = partuuid if self.actions.dry_run else self._run(["/usr/bin/blkid", "-s", "PARTUUID", "-o", "value", path])
            if not refreshed:
                raise RuntimeError(f"PARTUUID disappeared after mkswap: {path}")
            fstab_entries.append(f"PARTUUID={refreshed} none swap sw,pri={self.config.swap_priority}{discard} 0 0")
        self._persist_fstab(fstab_entries)
        self.state.mark_step("swap_partitions", "planned" if self.actions.dry_run else "success", f"{self.config.swap_file_count} native GPT swaps, labeled vbpub-swapN")

    def _persist_fstab(self, entries: list[str]) -> None:
        fstab_path = Path("/etc/fstab")
        existing_lines = fstab_path.read_text(encoding="utf-8").splitlines() if fstab_path.is_file() else []
        retained = [
            line for line in existing_lines
            if not (line.strip() and len(line.split()) >= 3 and line.split()[2].lower() == "swap")
        ]
        content = "\n".join(retained + entries + [""])
        self.actions.write_file("/etc/fstab", content, 0o644)

    def _install_stage2(self) -> None:
        python = shutil.which("python3") or "/usr/bin/python3"
        # parents[1], not [2]: installer.py lives at .../debian-install-v2/
        # debian_install_v2/installer.py, so parents[1] is the directory that
        # directly contains the debian_install_v2 package — `-m` prepends
        # WorkingDirectory to sys.path, so `-m debian_install_v2.bootstrap`
        # only resolves from there. parents[2] was one level too high
        # (ModuleNotFoundError on every real host — see DEBIAN-INSTALLv2-REVIEW.md P1#4).
        working_directory = str(Path(__file__).resolve().parents[1])
        env_file = "/etc/vbpub/bootstrap.env"
        credentials_line = "-"
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            # /usr/local/sbin/vbpub-notify (NOTIFY_SCRIPT) is called from
            # several standalone systemd units (reboot-check, boot-notify,
            # apt-update-notify) that declare no LoadCredential= of their
            # own, so $CREDENTIALS_DIRECTORY is never set for them -- it
            # always falls back to the ONE fixed path /etc/vbpub/credentials
            # (shell templates in this codebase use fixed paths only, never
            # state_dir interpolation). Write that copy unconditionally,
            # regardless of credential_mode, or root-storage installs (the
            # config default) silently never send a single notification --
            # every notify call finds empty files and exits 0 (adversarial
            # review finding). Same content, same 0600 mode as the
            # mode-specific copy below; no additional exposure.
            self.actions.write_file("/etc/vbpub/credentials/telegram_bot_token", self.config.telegram_bot_token + "\n", 0o600)
            self.actions.write_file("/etc/vbpub/credentials/telegram_chat_id", self.config.telegram_chat_id + "\n", 0o600)
            if self.config.credential_mode == "root-storage":
                credential_dir = Path(self.config.state_dir) / "credentials"
                self.actions.write_file(str(credential_dir / "telegram_bot_token"), self.config.telegram_bot_token + "\n", 0o600)
                self.actions.write_file(str(credential_dir / "telegram_chat_id"), self.config.telegram_chat_id + "\n", 0o600)
                credential_note = "root-only Telegram credentials installed"
            else:
                credentials_line = (
                    f"telegram_bot_token:/etc/vbpub/credentials/telegram_bot_token "
                    f"telegram_chat_id:/etc/vbpub/credentials/telegram_chat_id"
                )
                credential_note = "systemd LoadCredential Telegram credentials configured"
        else:
            credential_note = "Telegram disabled"
        env = "\n".join([
            f"VBPUB_STATE_DIR={self.config.state_dir}",
            f"VBPUB_STAGE2_OUTPUT={self.config.stage2_output}",
            f"PYTHONUNBUFFERED=1",
            "",
        ])
        self.actions.write_file(env_file, env, 0o600)
        service = STAGE2_SERVICE.format(
            state_dir=self.config.state_dir,
            env_file=env_file,
            python=python,
            output=self.config.stage2_output,
            working_directory=working_directory,
            credentials_line=credentials_line,
        )
        self.actions.write_file("/etc/systemd/system/vbpub-bootstrap-stage2.service", service)
        marker = Path(self.config.state_dir) / "stage1_done"
        if not self.actions.dry_run:
            marker.touch(mode=0o600)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload stage2 unit")
        self._run(["/usr/bin/systemctl", "enable", "vbpub-bootstrap-stage2.service"], "enable stage2 unit")
        self.state.mark_step("stage2_unit", "success", f"{self.config.stage2_output}; {credential_note}")

    def _notify(self, message: str) -> None:
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        if not token or not chat_id or self.actions.dry_run:
            return
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }).encode("utf-8")
        request = urllib.request.Request(f"https://api.telegram.org/bot{urllib.parse.quote(token)}/sendMessage", data=data)
        try:
            urllib.request.urlopen(request, timeout=15).close()
        except OSError as exc:
            print(f"[WARN] Telegram notification failed: {exc}", flush=True)

    def _reboot(self) -> None:
        if self.config.never_reboot or not self.config.auto_reboot_after_stage1:
            self.state.mark_step("reboot", "deferred", "disabled by configuration")
            return
        self.state.mark_step("reboot", "scheduled", "stage2 resumes on next boot")
        self._run(["/usr/bin/systemctl", "reboot"], "reboot into stage2", dangerous=True)

    def _stage1(self) -> None:
        if self.config.run_apt_config:
            self._configure_apt()
        self._packages(["python3"], "stage1")
        self._install_notify_helper()
        if self.config.run_user_config:
            self._configure_users()
        if self.config.run_journald_config:
            self._configure_journald()
        if self.config.run_docker_install:
            self._install_docker()
            self._configure_docker_daemon()
            if self.config.run_docker_cleanup:
                self._configure_docker_cleanup()
        if self.config.run_apt_auto_upgrade:
            self._configure_apt_auto_upgrade()
        self._plan_root_shrink()
        self._install_stage2()
        self._reboot()

    def _stage2(self) -> None:
        self._packages(["e2fsprogs", "util-linux"], "stage2")
        # True iff Case B's own hook already wrote the swap partitions as
        # part of shrinking root -- _apply_known_swap_shape() must then be
        # skipped, not re-run (see _verify_and_apply_root_shrink()'s docstring).
        swap_partitions_already_written = self._verify_and_apply_root_shrink()
        self._configure_zswap()
        self._configure_cgroup2_flags()
        if self.config.run_ksm:
            self._configure_ksm()
        if self.config.run_oomd_config:
            self._configure_oomd()
        if self.config.run_fstrim:
            self._configure_fstrim()
        if self.config.run_auto_reboot:
            self._configure_auto_reboot()
        if not swap_partitions_already_written:
            self._apply_known_swap_shape()
        self._activate_swap_partitions()
        self._health_gate_swap_devices()
        self.state.save(phase="done", status="success")
