#!/usr/bin/env python3
"""The installed systemd units and strict probe configuration stay one contract."""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
DEPLOY = HOSTOBS / "deploy"
SYSTEMD = DEPLOY / "systemd"
CONFIG = DEPLOY / "hostobsd.toml"
SERVICE = SYSTEMD / "hostobs-probe.service"

HARDENING = {
    "NoNewPrivileges": "yes",
    "ProtectControlGroups": "yes",
    "PrivateNetwork": "yes",
    "RestrictAddressFamilies": "AF_UNIX",
    "SystemCallFilter": "@system-service",
    "AmbientCapabilities": "CAP_DAC_READ_SEARCH",
    "CapabilityBoundingSet": "CAP_DAC_READ_SEARCH",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "PrivateTmp": "yes",
    "PrivateDevices": "yes",
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "LockPersonality": "yes",
    "MemoryDenyWriteExecute": "yes",
    "RestrictNamespaces": "yes",
    "SystemCallArchitectures": "native",
}


def unit(path: Path) -> dict[str, dict[str, list[str]]]:
    """An INI-style key list; repeated keys stay repeated."""
    sections: dict[str, dict[str, list[str]]] = {}
    current = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, {})
            continue
        if current is None or "=" not in line:
            raise AssertionError(f"{path}:{number}: not an INI-style key")
        key, value = line.split("=", 1)
        sections[current].setdefault(key.strip(), []).append(value.strip())
    return sections


class DeployUnits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = unit(SERVICE)
        cls.sockets = {
            level: unit(SYSTEMD / f"hostobs-{level}.socket")
            for level in ("observe", "operator")
        }
        with CONFIG.open("rb") as handle:
            cls.config = tomllib.load(handle)

    def one(self, sections, section: str, key: str, path: Path) -> str:
        values = sections.get(section, {}).get(key, [])
        self.assertEqual(len(values), 1, f"{path}: [{section}] {key} must appear once")
        return values[0]

    def test_service_activation_restart_and_hardening(self):
        service = self.service
        self.assertEqual(
            self.one(service, "Service", "ExecStart", SERVICE).split()[1:],
            ["--config", "/etc/hostobs/hostobsd.toml"],
            f"{SERVICE}: [Service] ExecStart",
        )
        self.assertEqual(
            self.one(service, "Service", "Restart", SERVICE),
            "on-failure",
            f"{SERVICE}: [Service] Restart",
        )
        self.assertEqual(
            self.one(service, "Service", "RestartPreventExitStatus", SERVICE),
            "2",
            f"{SERVICE}: [Service] RestartPreventExitStatus",
        )
        for key, value in HARDENING.items():
            self.assertEqual(
                self.one(service, "Service", key, SERVICE),
                value,
                f"{SERVICE}: [Service] {key}",
            )
        for key in ("ProtectProc", "ProcSubset"):
            self.assertNotIn(
                key, service.get("Service", {}), f"{SERVICE}: [Service] must not set {key}"
            )

    def test_every_configured_level_has_one_exact_socket(self):
        levels = self.config["probe"]["levels"]
        declared = service_socket_units(self.service)
        self.assertEqual(
            sorted(declared),
            sorted(f"hostobs-{level}.socket" for level in levels),
            f"{SERVICE}: [Service] Sockets=",
        )
        for level in levels:
            path = SYSTEMD / f"hostobs-{level}.socket"
            sock = self.sockets[level]
            self.assertEqual(
                self.one(sock, "Socket", "FileDescriptorName", path),
                level,
                f"{path}: [Socket] FileDescriptorName",
            )
            self.assertEqual(
                self.one(sock, "Socket", "Accept", path), "no", f"{path}: [Socket] Accept"
            )
            self.assertEqual(
                self.one(sock, "Socket", "ListenStream", path),
                f"/run/hostobs/{level}/hostobs.sock",
                f"{path}: ListenStream",
            )
            self.assertEqual(
                self.one(sock, "Socket", "SocketGroup", path),
                f"hostobs-{level}",
                f"{path}: SocketGroup",
            )
            self.assertEqual(
                self.one(sock, "Socket", "SocketUser", path),
                "root",
                f"{path}: [Socket] SocketUser",
            )
            self.assertEqual(
                self.one(sock, "Socket", "SocketMode", path),
                "0660",
                f"{path}: [Socket] SocketMode",
            )
            self.assertEqual(
                self.one(sock, "Socket", "DirectoryMode", path),
                "0750",
                f"{path}: [Socket] DirectoryMode",
            )
            self.assertEqual(
                self.one(sock, "Socket", "Service", path),
                "hostobs-probe.service",
                f"{path}: Service",
            )
            self.assertEqual(
                self.one(sock, "Install", "WantedBy", path),
                "sockets.target",
                f"{path}: [Install] WantedBy",
            )

    def test_config_has_exactly_the_probe_process_keys(self):
        self.assertEqual(
            set(self.config), {"probe", "limits"}, f"{CONFIG}: root table keys"
        )
        self.assertEqual(
            set(self.config["probe"]),
            {"levels", "tick_ms", "cgroup_root"},
            f"{CONFIG}: [probe] keys",
        )
        self.assertEqual(set(self.config["limits"]), {"max_rows"}, f"{CONFIG}: [limits] keys")
        self.assertEqual(
            self.config["probe"]["levels"],
            ["observe", "operator"],
            f"{CONFIG}: [probe] levels",
        )
        self.assertEqual(
            self.config["probe"]["tick_ms"], 1000, f"{CONFIG}: [probe] tick_ms"
        )
        self.assertEqual(
            self.config["probe"]["cgroup_root"],
            "/sys/fs/cgroup",
            f"{CONFIG}: [probe] cgroup_root",
        )
        self.assertEqual(
            self.config["limits"]["max_rows"], 1024, f"{CONFIG}: [limits] max_rows"
        )


def service_socket_units(service: dict[str, dict[str, list[str]]]) -> list[str]:
    values = service.get("Service", {}).get("Sockets", [])
    return [word for value in values for word in value.split()]


if __name__ == "__main__":
    unittest.main()
