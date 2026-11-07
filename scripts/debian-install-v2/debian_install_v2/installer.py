from __future__ import annotations

import contextlib
import re
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable

from . import inuse_partition_editor
from .actions import ActionError, ActionTimeout, ActionUnreapable, HostActions
from .config import Config, ConfigError, load_config, persisted_config_data, resolve_notify_backend
from .disk_layout import GIB, automatic_swap_size_gb
from . import layout_policy
from .host_facts import _code, collect_host_facts, format_facts_html
from .notify import NotifyConfigError, format_mattermost_message, post_webhook, redact_text
from .state import StateError, StateStore
from .templates import (
    APT_CUSTOM,
    APT_PERIODIC_CONFIG,
    APT_PRIORITIES,
    APT_SOURCES,
    APT_UPDATE_NOTIFY_SCRIPT,
    APT_UPDATE_NOTIFY_SERVICE,
    APT_UPDATE_NOTIFY_TIMER,
    BOOT_NOTIFY_SERVICE,
    BOOTSTRAP_FAILED_SERVICE,
    CGROUP2_FLAGS_SCRIPT,
    CGROUP2_FLAGS_SERVICE,
    DOCKER_CLEANUP_SERVICE,
    DOCKER_CLEANUP_TIMER,
    FSTRIM_OVERRIDE,
    IOCOST_MODEL_PATH,
    IOCOST_SCRIPT,
    IOCOST_SERVICE,
    KSM_TMPFILES,
    LEGACY_TUNING_UNITS,
    MIN_FREE_FLOOR_SERVICE,
    NEEDRESTART_CONFIG,
    NOTIFY_SCRIPT,
    OOMD_CONFIG,
    REBOOT_CHECK_SCRIPT,
    REBOOT_CHECK_SERVICE,
    REBOOT_CHECK_TIMER,
    ROOT_SHRINK_BUILD_HOOK,
    ROOT_SHRINK_LOCAL_PREMOUNT_HOOK,
    STAGE2_SERVICE,
    SWAP_HEALTH_SCRIPT,
    SWAP_SYSCTL,
    THP_TMPFILES,
    UNATTENDED_UPGRADES_CONFIG,
    ZSWAP_MODULES_LOAD,
    ZSWAP_SERVICE,
    render_min_free_floor_script,
)
from .userconfig import BASHRC_ALIASES_SNIPPET, USER_RC_FILES


SUPPORTED_RELEASES = {"trixie", "forky"}
FAILED_UNIT_NAME = "vbpub-bootstrap-failed@.service"
SWAP_TYPE_GUID = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"
_LOG = logging.getLogger("debian_install_v2.installer")

TELEGRAM_MESSAGE_LIMIT = 4096
CLOUD_INIT_USER_DATA_CACHE = "/var/lib/cloud/instance/user-data.txt"
CLOUD_INIT_USER_DATA_CACHE_FILES = (
    CLOUD_INIT_USER_DATA_CACHE,
    "/var/lib/cloud/instance/user-data.txt.i",
    "/var/lib/cloud/instance/cloud-config.txt",
    "/var/lib/cloud/instance/obj.pkl",
)
# Fixed outside cloud-init's cache and independent of the profile-selected
# state_dir. A retained NoCloud ISO can run runcmd again after `cloud-init clean`.
INSTALL_COMPLETE_MARKER = "/var/lib/vbpub/install-v2-complete"

# LT-F-r1002-01: stage2's apt-get exited 100 because unattended-upgrade (started
# by apt-daily-upgrade.service 4 s earlier) held the dpkg lock. Every apt-get the
# installer runs waits up to this long for the dpkg frontend lock. Fixed
# constant, not a Config field: it is an operational safety margin, not a
# per-host choice (keeps the config schema/wizard unchanged).
APT_LOCK_TIMEOUT_S = 600
APT_LOCK_OPTION = ["-o", f"DPkg::Lock::Timeout={APT_LOCK_TIMEOUT_S}"]
# DPkg::Lock::Timeout only covers the dpkg frontend lock; `apt-get update`'s
# lists lock (/var/lib/apt/lists/lock) fails immediately regardless of it, so
# a lock-contention failure gets a bounded retry (attempts, seconds apart).
APT_LOCK_RETRY_DELAY_S = 30
# Total retry wait matches APT_LOCK_TIMEOUT_S (20 sleeps x 30 s = 600 s).
# Running unattended-upgrade is deliberately waited for, never stopped.
APT_LOCK_RETRY_ATTEMPTS = APT_LOCK_TIMEOUT_S // APT_LOCK_RETRY_DELAY_S + 1
_APT_LOCK_ERROR_MARKERS = (
    "Could not get lock",
    "Unable to acquire the dpkg frontend lock",
    "Unable to lock directory",
    "Unable to lock the administration directory",
)
# unattended-upgrade (2.12, trixie) takes the apt system lock ONCE via
# apt_pkg.pkgsystem_lock() and has no retry loop of its own (verified live on
# v1001, LT-UPG fix round 1). Its own wordings for "someone else holds a lock":
_UU_LOCK_ERROR_MARKERS = (
    "Lock could not be acquired (another package manager running?)",
    "Cache lock can not be acquired",
    "Lock file is already taken",  # /var/run/unattended-upgrades.lock
)
# After the apt timers are disabled a service run already in flight keeps
# going (disable --now stops the TIMER only): wait for it, bounded.
APT_SERVICE_WAIT_S = 600
APT_SERVICE_POLL_S = 10
_APT_SERVICES = ("apt-daily.service", "apt-daily-upgrade.service")
# Controller keys the installer places carry this comment marker; only keys
# with it are ever pruned by a successful stage2.
CONTROLLER_KEY_MARKER = "vbpub-controller-ephemeral-"

# Installer iobench runs against the planned, unformatted raw partition.
# iobench's veryquick profile has a 210 s hard budget; this outer bound leaves
# room for process startup and result persistence without an unbounded wait.
IOBENCH_OUTER_TIMEOUT_S = 270
IOBENCH_RESULT_KEYS = ("rbps", "rseqiops", "rrandiops", "wbps", "wseqiops", "wrandiops")
# A terminal step status means cleanup was already verified; resume skips it.
# "failed" is deliberately NOT terminal: it means cleanup could not be
# verified, so a resumed stage2 must re-run the leftover check.
IOBENCH_FINAL_STATUSES = {"success", "skipped", "warned"}

# Per-host CPU benchmark (HB-2). Geekbench archive pins per CPU architecture
# (platform.machine()): (version, sha256 of the Linux tarball), read from
# tools/cpu_benchmark.pins.json, the one source shared with `mdt host-setup
# cpu-benchmark`. There is no "newest from the CDN" default in the installer: an
# unpinned architecture is `warned`, never run unverified (aarch64 has no
# verified pin yet).
def _load_cpu_benchmark_pins() -> dict[str, tuple[str, str]]:
    path = Path(__file__).resolve().parents[1] / "tools" / "cpu_benchmark.pins.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        arch: (entry["version"], entry["sha256"])
        for arch, entry in data.items()
        if not arch.startswith("_") and isinstance(entry, dict) and entry.get("version") and entry.get("sha256")
    }


CPU_BENCHMARK_PINS: dict[str, tuple[str, str]] = _load_cpu_benchmark_pins()
# Hard limit handed to the tool (its process-group kill) and, with slack for the
# download/extract/result-page fetch, to the outer command. Free Geekbench under
# QEMU TCG can exceed 15 minutes (docs/CONSUMERS.md).
CPU_BENCHMARK_TOOL_TIMEOUT_S = 1800
CPU_BENCHMARK_OUTER_TIMEOUT_S = 2700
CPU_BENCHMARK_FINAL_STATUSES = {"success", "skipped", "warned"}


class InstallerError(RuntimeError):
    """Expected host-precondition or installer-operation failure."""


class HostIdentityRefusal(InstallerError):
    """This is not the kind of host the config/installer is for.

    The failure guard posts it and installs NOTHING on the host (no controller
    key, no state, no timer changes): a key left on a machine we should not be
    touching would be a root credential with nothing to remove it (controller
    ruling D1). Raised ONLY by the host-shape checks in inspect(): unsupported
    or undetected Debian release, and a root that is not a plain block-device
    mount. Plan-level refusals (disk too small, preserve_root_size_gb below the
    filesystem minimum, mounted partitions, ...) are NOT identity refusals: the
    host is the right one, so the key stays for diagnosis.
    """


class CompletedInstallRefusal(InstallerError):
    """A fresh-install entrypoint was invoked after this host completed stage two."""


class UnsupportedHostRelease(HostIdentityRefusal, ConfigError):
    """Release not in SUPPORTED_RELEASES (stays a ConfigError for existing callers)."""


def _is_docker_interface(name: object) -> bool:
    """Docker-owned interfaces by Docker's own naming: docker0, br-<12 hex>, veth<hex>."""
    return isinstance(name, str) and re.fullmatch(r"docker0|br-[0-9a-f]{12}|veth[0-9a-f]+", name) is not None


def _split_for_telegram(message: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split `message` into <= `limit`-char chunks for Telegram's sendMessage.

    Prefers the last blank-line boundary within the limit (keeps sections/
    headings together), falls back to the last single newline, and only
    hard-splits mid-line if neither boundary exists in the current window.
    """
    if len(message) <= limit:
        return [message]
    chunks: list[str] = []
    remaining = message
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at < limit // 2:
            split_at = window.rfind("\n")
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


class Installer:
    def __init__(
        self,
        config: Config,
        actions: HostActions,
        *,
        inspect_host: bool = True,
    ):
        self.config = config
        self.actions = actions
        self.state = StateStore(config.state_dir)
        self.state.dry_run = actions.dry_run
        self._notify_stage = "stage1"
        # Shown in the "Install complete" notification (set when io.cost is skipped).
        self._iocost_note = ""
        # One line for the "Install complete" milestone (scores + result URL; never the claim link).
        self._cpu_benchmark_note = ""
        self.release = ""
        self.root_disk = ""
        self.root_partition_path = ""
        self.root_number = 0
        # The exception the failure guard last reported (identity, not a flag:
        # one Installer may see several runs), so nested guards post ONCE.
        self._reported_failure: BaseException | None = None
        # run_id of the state THIS run created (None until install() saved it).
        self._run_id: str | None = None
        # True while handling a failure whose state.json belongs to ANOTHER run:
        # the state is then left strictly untouched (no step marks, no mark).
        self._state_frozen = False
        # True while the failure guard runs its bookkeeping: telegram-verbose
        # step posts are suppressed so the ONE failure post is the first message.
        self._in_failure_handling = False
        if inspect_host:
            self.inspect()

    def inspect(self) -> None:
        """Read release and root device from the host (can raise: call it inside failure_guard)."""
        self.release = self._detect_release()
        self.root_disk, self.root_partition_path, self.root_number = self._discover_root()

    def _run(
        self,
        argv: list[str],
        description: str = "",
        dangerous: bool = False,
        input: str | None = None,
        timeout: float | None = None,
    ) -> str:
        # input is forwarded only when actually supplied (not just non-None
        # by default) so the many pre-existing test doubles for
        # actions.run(argv, description=..., dangerous=...) -- fixed,
        # narrower signatures with no **kwargs -- keep working unchanged;
        # only _apply_known_swap_shape()'s two sfdisk calls ever pass one.
        kwargs: dict[str, object] = {"description": description, "dangerous": dangerous}
        if input is not None:
            kwargs["input"] = input
        # timeout likewise: only the io benchmark's sync/umount/mkfs/tool pass one.
        if timeout is not None:
            kwargs["timeout"] = timeout
        output = self.actions.run(argv, **kwargs)
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
            raise UnsupportedHostRelease(f"unsupported or undetected Debian release: {release!r}")
        return release

    def _discover_root(self) -> tuple[str, str, int]:
        if self.actions.dry_run:
            return "vda", "/dev/vda3", 3
        root = self._run(["/usr/bin/findmnt", "-n", "-o", "SOURCE", "/"], "find root device")
        if not root.startswith("/dev/"):
            raise HostIdentityRefusal(f"root is not a plain block-device mount: {root!r}")
        match = re.fullmatch(r"/dev/(?P<disk>.+?)(?:p)?(?P<number>[0-9]+)", root)
        if not match:
            raise HostIdentityRefusal(f"cannot derive root disk and partition number from {root!r}")
        return match.group("disk"), root, int(match.group("number"))

    @property
    def _partition_base(self) -> str:
        return f"/dev/{self.root_disk}p" if any(char.isdigit() for char in self.root_disk) else f"/dev/{self.root_disk}"

    def _duration_since_start(self) -> str:
        try:
            started_raw = self.state.load().get("started_at")
            if not started_raw:
                return "unknown"
            started = datetime.fromisoformat(started_raw)
            elapsed = datetime.now(timezone.utc) - started
            total_seconds = int(elapsed.total_seconds())
            minutes, seconds = divmod(total_seconds, 60)
            hours, minutes = divmod(minutes, 60)
            return (f"{hours}h" if hours else "") + f"{minutes}m{seconds}s"
        except Exception:
            return "unknown"

    def _initial_report_message(self) -> str:
        facts_html = format_facts_html(collect_host_facts(self))
        plan = self.show_plan()
        swap_total = (
            f"{plan['swap_total_gb']} GiB"
            if plan["swap_total_gb"] is not None
            else "disabled"
        )
        retained_swap = ""
        tail_swap = ""
        if plan["swap_policy"] == "on":
            retained_swap = f"{plan['swap_retained_before_root_gb']:.2f} GiB kept before root"
            tail_swap = f"{plan['swap_after_root_gb']:.2f} GiB after root"
        plan_lines = [
            "<b>Install plan</b>",
            f"Release: {_code(self.release)} | Root: {_code(plan['root_device'])} "
            f"-> {plan['new_root_size_sectors']} sectors",
            f"Disk policy: root={_code(str(plan['root_policy']))}, "
            f"swap={_code(str(plan['swap_policy']))}, "
            f"swap total={_code(swap_total)}",
        ]
        if plan["partitioning"]:
            plan_lines.append(
                f"Partitioning: {_code(json.dumps(plan['partitioning'], sort_keys=True))}; "
                f"{len(plan['data_partitions'])} planned data/raw partition(s) in front of the end-of-disk swap "
                "(see README \"Disk layout\")"
            )
        plan_lines.append("Resolved disk table (GiB):")
        plan_lines.extend(
            f"  {row['entry']}: {row['gib']:.2f} GiB at sector {row['start']}"
            for row in plan["plan_table"]
        )
        if plan["swap_policy"] == "on":
            plan_lines.append(
                f"Swap allocation: {_code(retained_swap)}, {_code(tail_swap)} "
                f"({plan['swap_partition_count']} partitions)"
            )
        for swap in plan["swap_partitions"]:
            plan_lines.append(f"Swap partition: {_code(swap['device'])} ({swap['sectors']} sectors)")
        plan_lines.append(f"sfdisk plan:\n<pre>{plan['sfdisk_plan']}</pre>")
        return "<b>Starting debian-install-v2</b>\n\n" + facts_html + "\n\n" + "\n".join(plan_lines)

    @property
    def _notifications_enabled(self) -> bool:
        return self._notify_backend() != "none" and not self.actions.dry_run

    def _notify_backend(self) -> str:
        """telegram | mattermost | none, from config; unusable credentials -> none."""
        try:
            backend = resolve_notify_backend(self.config)
        except (ConfigError, NotifyConfigError):
            return "none"
        if backend == "telegram" and not (self.config.telegram_bot_token and self.config.telegram_chat_id):
            return "none"
        if backend == "mattermost" and not self.config.mattermost_webhook_url:
            return "none"
        return backend

    @contextlib.contextmanager
    def failure_guard(self, phase: str = "stage1"):
        """Run a block under the install failure handling (LT-EARLY).

        ANY exception raised inside -- plan building, validation, show_plan,
        host inspection, stage one itself -- records a failed state.json with
        the cause, makes sure the controller key is on the host (a failed
        install keeps it for diagnosis), posts ONE failure notice, then
        re-raises. Nested guards report a given exception only once.
        """
        try:
            yield
        except BaseException as exc:
            if exc is not self._reported_failure:
                self._reported_failure = exc
                self._handle_failure(exc, phase)
            raise

    def _handle_failure(self, exc: BaseException, phase: str) -> None:
        """Failure bookkeeping. Every step is best-effort; it never raises.

        * HostIdentityRefusal / CompletedInstallRefusal: post ONLY. No state,
          controller key or timer change; the host must remain untouched.
        * Otherwise: failed state.json (see _record_failed_state for the rule
          about a state that belongs to another run), the controller key
          (idempotent), the apt timers ONLY if this run held them, then ONE post.
        * failure_notified_at is written only when the post was delivered AND
          the state is this run's.
        """
        self._in_failure_handling = True
        self._state_frozen = False
        try:
            refusal = isinstance(exc, (HostIdentityRefusal, CompletedInstallRefusal))
            owns_state = False
            note = ""
            if refusal:
                self._state_frozen = True  # never touch (or quote) any state.json
                if not self.actions.dry_run:
                    if isinstance(exc, HostIdentityRefusal):
                        note = " (host-identity refusal: nothing was installed or changed on this host)"
                    else:
                        note = " (completed-install refusal: existing state and install markers were left untouched)"
            elif not self.actions.dry_run:  # a dry run never writes state or touches the host
                owns_state, note = self._record_failed_state(exc, phase)
                self._ensure_controller_key_after_failure()
                self._restore_apt_timers_after_failure()
            try:
                delivered = self._notify(
                    f"<b>Install FAILED</b> during {phase}: {_code(str(exc) + note)}",
                    event="install FAILED", status="fail", excerpt=str(exc) + note,
                )
            except Exception:
                delivered = False
            if delivered and owns_state:
                # Same dedup mark the stage2 path writes: the OnFailure notifier
                # skips its own post when this one was delivered. Never set for
                # an undelivered post (the failure would go completely silent).
                try:
                    self.state.save(failure_notified_at=datetime.now(timezone.utc).isoformat())
                except Exception as state_exc:
                    _LOG.warning("could not record failure_notified_at: %s", state_exc)
        finally:
            self._in_failure_handling = False

    def _record_failed_state(self, exc: BaseException, phase: str) -> tuple[bool, str]:
        """Mark THIS run's state failed. Returns (state is this run's, note for the post).

        S6 decision: a state.json that already exists but was NOT created by
        this run (different run_id, or unreadable) is left strictly untouched
        -- it may describe an install that is still running or completed, and
        flipping it to failed would be the surprising side effect of a re-run
        that was refused early. The refusal is reported in the post, with the
        existing run's id. When no state exists, a fresh one is created for
        this run so the failure is on record.
        """
        try:
            if self.state.path.is_file():
                existing_id = self.state.load().get("run_id")
                if self._run_id is None or existing_id != self._run_id:
                    self._state_frozen = True
                    return False, (
                        f" (an existing state.json from a different run, {existing_id!r}, "
                        "was left untouched)"
                    )
            else:
                # Failed before install() created the state (e.g. show_plan).
                fresh = StateStore.new(self.config)
                self.state.save_new(fresh)
                self._run_id = fresh["run_id"]
            self.state.save(status="failed", phase=phase, last_error=str(exc))
            return True, ""
        except Exception as state_exc:
            _LOG.warning("could not record the failed state: %s", state_exc)
            if self._run_id is None and self.state.path.is_file():
                self._state_frozen = True
                return False, " (an existing state.json could not be read and was left untouched)"
            return False, ""

    def _ensure_controller_key_after_failure(self) -> None:
        """A failed install leaves the controller key for diagnosis -- even when
        the failure came before the key step ran (otherwise the host is
        unreachable). Idempotent; best-effort."""
        if not self.config.controller_ssh_pubkey.strip():
            return
        try:
            # A foreign (frozen) state says nothing about THIS run's key step.
            step = None if self._state_frozen else self.state.load().get("steps", {}).get("controller_ssh_key")
            if isinstance(step, dict) and step.get("status") == "success":
                return
        except Exception:
            pass
        try:
            self._configure_controller_ssh_key()
        except Exception as exc:
            _LOG.warning("could not install the controller key after failure: %s", exc)

    def install(self) -> None:
        with self.failure_guard("stage1"):
            self._refuse_completed_install()
            # LT-F-r1002-03: before state init / plan code that can fail early
            # (stage1 repeats it; it is idempotent).
            self._secure_bootstrap_files()
            self._install_guarded()

    def _refuse_completed_install(self) -> None:
        """Prevent a replayed cloud-init seed from starting a second disk install."""
        fixed_marker = Path(INSTALL_COMPLETE_MARKER)
        if fixed_marker.exists() or fixed_marker.is_symlink():
            raise CompletedInstallRefusal(
                "this host already has the debian-install-v2 completion marker; "
                "refusing to start a fresh install"
            )
        state_path = Path(self.config.state_dir) / "state.json"
        state_marker = Path(self.config.state_dir) / "stage2_done"
        if state_marker.exists() or state_marker.is_symlink():
            raise CompletedInstallRefusal(
                f"{state_marker} marks stage two complete; refusing to start a fresh install"
            )
        if not state_path.exists() and not state_path.is_symlink():
            return
        try:
            state = self.state.load()
        except StateError as exc:
            raise CompletedInstallRefusal(
                f"cannot safely inspect existing installer state at {state_path}; "
                "refusing to overwrite it"
            ) from exc
        if state.get("phase") == "done" or state.get("status") == "success":
            raise CompletedInstallRefusal(
                f"{state_path} records a completed install; refusing to start a fresh install"
            )

    def _install_guarded(self) -> None:
        new_state = StateStore.new(self.config)
        self.state.save_new(new_state)
        self._run_id = new_state["run_id"]
        if self._notifications_enabled:
            # This is a courtesy notification, not part of the install
            # itself -- a bug in facts-collection/plan-preview code here
            # must never abort the whole install before _stage1() even
            # gets a chance to run, and (since this happens before the
            # try/except below) never silently skip every failure
            # notification too (adversarial review finding, 2026-09-08: an
            # unguarded show_plan() call here once did exactly that for
            # Case B hosts -- see _resolve_swap_plan()'s docstring for the
            # actual bug that triggered this backstop).
            try:
                if self._notify_backend() == "mattermost":
                    self._notify("", event=self._initial_event_text(), status="run")
                else:
                    self._notify(self._initial_report_message())
            except Exception as exc:
                _LOG.warning("could not build/send initial report notification: %s", exc)
        self._stage1()

    def resume(self) -> None:
        # Stage2 appends to custom_script.output2 (systemd StandardOutput=append:
        # creates it 0644), so restrict /root/custom_script* before anything can
        # write to or fail around it.
        self._secure_bootstrap_files()
        # Before anything below can fail: a failure message must say stage2.
        self._notify_stage = "stage2"
        saved = self.state.load()
        if "failure_notified_at" in saved:
            # A mark from an earlier run must not silence the OnFailure notifier
            # if THIS run dies before reaching the failure path below.
            # (None, not a deleted key: StateStore.save only merges.)
            saved = self.state.save(failure_notified_at=None)
        persisted = saved.get("config", {})
        if not isinstance(persisted, dict):
            raise StateError("state manifest does not contain a configuration object")
        config_data = persisted_config_data(persisted)
        try:
            persisted_config = load_config(raw_json=json.dumps(config_data))
        except ConfigError as exc:
            raise StateError(f"saved installation configuration is invalid: {exc}") from exc
        if persisted_config.state_dir != self.config.state_dir:
            raise StateError(
                "state_dir in the saved configuration does not match the loaded state directory"
            )
        self.config = replace(
            persisted_config,
            telegram_bot_token=self.config.telegram_bot_token,
            telegram_chat_id=self.config.telegram_chat_id,
            mattermost_webhook_url=self.config.mattermost_webhook_url,
        )
        if self.config.credential_mode == "systemd":
            # systemd supplies this path for LoadCredential= entries. Resolve
            # that runtime fact rather than guessing its private directory.
            credential_dir_value = os.environ.get("CREDENTIALS_DIRECTORY", "")
            credential_dir = (
                Path(credential_dir_value)
                if credential_dir_value.startswith("/")
                else None
            )
        else:
            credential_dir = Path(self.config.state_dir) / "credentials"
        token_file = credential_dir / "telegram_bot_token" if credential_dir else None
        chat_file = credential_dir / "telegram_chat_id" if credential_dir else None
        if (
            not self.actions.dry_run
            and token_file is not None
            and chat_file is not None
            and token_file.is_file()
            and chat_file.is_file()
        ):
            token = token_file.read_text(encoding="utf-8").strip()
            chat_id = chat_file.read_text(encoding="utf-8").strip()
            if token and chat_id:
                self.config = replace(self.config, telegram_bot_token=token, telegram_chat_id=chat_id)
        webhook_file = credential_dir / "mattermost_webhook_url" if credential_dir else None
        if not self.actions.dry_run and webhook_file is not None and webhook_file.is_file():
            webhook = webhook_file.read_text(encoding="utf-8").strip()
            if webhook:
                self.config = replace(self.config, mattermost_webhook_url=webhook)
        thread_file = Path(self.config.state_dir) / "telegram_thread_id"
        if not self.actions.dry_run and thread_file.is_file():
            thread_id = thread_file.read_text(encoding="utf-8").strip()
            if thread_id.isdigit():
                self.state.save(telegram_thread_id=thread_id)
        self.state.save(phase="stage2", status="running")
        self._notify("<b>Resumed stage2</b> after reboot.", event="resumed after reboot", status="run")
        try:
            self._stage2()
            self.state.save(status="success", phase="done")
            if not self.actions.dry_run:
                Path(self.config.state_dir, "stage2_done").touch(mode=0o600)
            # Only NOW, after the marker external pollers watch for already
            # exists on disk: this used to run inside _stage2() itself,
            # BEFORE this marker was ever touched. Live-confirmed
            # 2026-09-09 (v1001 round 12, the first fully successful
            # install this whole effort): scp-api-install-host.py's own
            # completion poller authenticates with this exact controller
            # key, so revoking it before the marker exists created a race
            # where a successful install could permanently strand its own
            # external poller -- unable to ever reconnect and see the
            # marker it's specifically waiting for, silently retrying for
            # up to its full timeout (an hour, by default) and then
            # reporting a spurious TimeoutError for an install that had
            # actually already succeeded.
            if self._controller_key_retained():
                self._mark_step("controller_ssh_key_retained", "success", "configured to retain after successful stage2")
                self._prune_stale_controller_keys()
            else:
                self._remove_controller_ssh_key()
            if self._notifications_enabled:
                duration = self._duration_since_start()
                facts_html = format_facts_html(collect_host_facts(self))
                retained = self._controller_key_retained()
                key_note = "\n\nController key retained on host." if retained else ""
                key_event = ", controller key retained on host" if retained else ""
                io_note = f"\n\n{_code(self._iocost_note)}" if self._iocost_note else ""
                io_event = f", {self._iocost_note}" if self._iocost_note else ""
                cpu_note = f"\n\n{_code(self._cpu_benchmark_note)}" if self._cpu_benchmark_note else ""
                cpu_event = f", {self._cpu_benchmark_note}" if self._cpu_benchmark_note else ""
                self._notify(
                    f"<b>Install complete</b> (duration: {duration})"
                    f"{key_note}{io_note}{cpu_note}\n\n{facts_html}",
                    event=f"install complete (duration {duration}){key_event}{io_event}{cpu_event}", status="ok",
                )
        except BaseException as exc:
            self.state.save(status="failed", phase="stage2", last_error=str(exc))
            self._restore_apt_timers_after_failure()
            try:
                delivered = self._notify(
                    f"<b>Install FAILED</b> during stage2: {_code(str(exc))}",
                    event="install FAILED", status="fail", excerpt=str(exc),
                )
            except Exception:
                delivered = False
            # failure_notified_at lets the OnFailure notifier (failure_notify.py)
            # skip its own post, so it is recorded ONLY once this post actually
            # succeeded: a failed or raising post leaves it unset and the
            # notifier posts (the stage must fail loudly).
            if delivered:
                self.state.save(failure_notified_at=datetime.now(timezone.utc).isoformat())
            raise

    def status(self) -> dict[str, object]:
        state = self.state.load()
        log_dir = Path(self.config.log_dir)
        logs = sorted(str(path) for path in (log_dir.rglob("*") if log_dir.exists() else []) if path.is_file())
        # LT-F-v1001-08: log_dir is a legacy, normally-absent directory; the real
        # install logs are the provider's custom_script.output* and stage2_output,
        # so list those when they exist (after the legacy ones: logs[-20:] keeps the tail).
        real = [Path(self.config.stage2_output), *sorted(self._BOOTSTRAP_DIR.glob("custom_script.output*"))]
        for path in real:
            if path.is_file() and str(path) not in logs:
                logs.append(str(path))
        return {
            **state,
            "logs": logs[-20:],
            "dry_run": self.actions.dry_run,
            "planned_action_count": len(self.actions.planned),
        }

    def show_plan(self) -> dict[str, object]:
        partitions, new_root_size, shrink_needed = self._resolve_swap_plan()
        plan_path = Path(self.config.state_dir) / "partition-plan.sfdisk"
        if plan_path.is_file():
            plan_text = plan_path.read_text(encoding="utf-8")
        else:
            plan_text = self._write_sfdisk_plan(partitions, new_root_size)
        spec = self._disk_spec()
        disk_sectors, root_start, root_size = self._disk_facts()
        live = self._live_partition_entries(disk_sectors, root_start, root_size)
        before_root = self._partitions_before_root(live, root_start)
        retained = self._swap_partitions_before_root(live, root_start) if self.config.swap_policy == "on" else {}
        # Swap-off removes pre-root swap too, so its partition number is free
        # for the new plan. Other pre-root partitions retain their numbers.
        planned_before_root = {
            number: entry for number, entry in before_root.items()
            if self.config.swap_policy == "on" or not self._is_swap_partition(entry)
        }
        planned = self._data_area_layout(partitions, planned_before_root)
        table_rows: list[dict[str, object]] = []

        def add_row(entry: str, number: int | None, start: int, size: int, partlabel: str = "") -> None:
            table_rows.append({
                "entry": entry,
                "device": f"{self._partition_base}{number}" if number is not None else "unallocated",
                "number": number,
                "partlabel": partlabel,
                "start": start,
                "start_gib": round(start * 512 / GIB, 2),
                "sectors": size,
                "gib": round(size * 512 / GIB, 2),
            })

        for number, entry in before_root.items():
            if self._is_swap_partition(entry):
                continue
            add_row("preserved", number, int(entry["start"]), int(entry["size"]), entry.get("name", ""))
        add_row("root", self.root_number, root_start, new_root_size,
                live.get(self.root_number, {}).get("name", "root"))
        for (number, start, size), data in zip(planned, spec.entries()):
            add_row("iobench raw" if data.partlabel == layout_policy.IOBENCH_PARTLABEL else "data",
                    number, start, size, data.partlabel)
        if self.config.swap_policy == "on":
            planned_swaps = [
                (number, int(entry["start"]), int(entry["size"]))
                for number, entry in sorted(retained.items())
            ]
            tail_numbers = self._tail_swap_numbers(len(partitions), before_root)
            planned_swaps.extend(
                (number, start, size)
                for number, (start, size) in zip(tail_numbers, partitions)
            )
        else:
            planned_swaps = []
        for number, start, size in planned_swaps:
            entry = live.get(number, {})
            add_row("swap", number, start, size, entry.get("name", f"vbpub-swap{number}"))

        # Represent free space between planned extents explicitly. This is
        # normally the intentional reserve or the alignment gap after a kept root.
        managed = sorted(
            (row for row in table_rows if row["entry"] in {"root", "data", "iobench raw", "swap"}),
            key=lambda row: int(row["start"]),
        )
        for left, right in zip(managed, managed[1:]):
            left_end = int(left["start"]) + int(left["sectors"])
            gap = int(right["start"]) - left_end
            if gap > 0:
                add_row("reserve", None, left_end, gap)
        table_rows.sort(key=lambda row: int(row["start"]))
        swap_total = (
            self._effective_swap_total_gb()
            if self.config.swap_policy == "on"
            else None
        )
        retained_total_gb = (
            sum(size for number, _start, size in planned_swaps if number in retained) * 512 / GIB
            if self.config.swap_policy == "on"
            else None
        )
        tail_total_gb = (
            sum(size for number, _start, size in planned_swaps if number not in retained) * 512 / GIB
            if self.config.swap_policy == "on"
            else None
        )
        return {
            "run_id": self.state.load().get("run_id") if (Path(self.config.state_dir) / "state.json").is_file() else "",
            "release": self.release,
            "root_device": self.root_partition_path,
            "new_root_size_sectors": new_root_size,
            "root_shrink_check": (
                "validated"
                if shrink_needed
                else "not required"
            ),
            "root_policy": self._root_policy(),
            "partitioning": self.config.partitioning or {},
            "data_partitions": [
                {"device": f"{self._partition_base}{number}", "start": start, "sectors": size,
                 "partlabel": data.partlabel}
                for (number, start, size), data in zip(planned, spec.entries())
                if data.partlabel != layout_policy.IOBENCH_PARTLABEL
            ],
            "iobench_partition": next((
                {"device": f"{self._partition_base}{number}", "start": start, "sectors": size,
                 "partlabel": data.partlabel}
                for (number, start, size), data in zip(planned, spec.entries())
                if data.partlabel == layout_policy.IOBENCH_PARTLABEL
            ), None),
            "plan_table": table_rows,
            "swap_policy": self.config.swap_policy,
            "swap_total_gb": swap_total,
            "swap_retained_before_root_gb": retained_total_gb,
            "swap_after_root_gb": tail_total_gb,
            "swap_partition_count": len(planned_swaps),
            "swap_partitions": [
                {"device": f"{self._partition_base}{number}", "start": start, "sectors": size}
                for number, start, size in planned_swaps
            ],
            "sfdisk_plan": plan_text,
        }

    def verify(self) -> None:
        transaction_path = Path(self.config.state_dir) / "disk-transaction.json"
        if not transaction_path.is_file():
            raise InstallerError(f"disk transaction manifest does not exist: {transaction_path}")
        try:
            manifest = json.loads(transaction_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InstallerError(f"cannot read disk transaction manifest: {exc}") from exc
        if not isinstance(manifest, dict):
            raise InstallerError("disk transaction manifest must be a JSON object")
        paths = {}
        for name in ("backup", "checksum"):
            value = manifest.get(name)
            if not isinstance(value, str) or not value or "\x00" in value:
                raise InstallerError(f"disk transaction manifest has no valid {name} path")
            path = Path(value)
            if not path.is_absolute():
                raise InstallerError(f"disk transaction manifest {name} path must be absolute")
            paths[name] = path
        backup = paths["backup"]
        checksum = paths["checksum"]
        if not backup.is_file() or not checksum.is_file():
            raise InstallerError("backup or checksum is missing")
        checksum_fields = checksum.read_text(encoding="utf-8").split()
        if not checksum_fields or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum_fields[0]):
            raise InstallerError("disk transaction checksum file is malformed")
        expected = checksum_fields[0].lower()
        actual = hashlib.sha256(backup.read_bytes()).hexdigest()
        if actual != expected:
            raise InstallerError(f"backup checksum mismatch: expected {expected}, got {actual}")
        if self.actions.dry_run:
            raise InstallerError("verify requires live host checks; --dry-run is not meaningful here")
        self._health_gate_swap_devices(mark_step=False)

    def disable_stage2(self) -> None:
        self._run(["/usr/bin/systemctl", "disable", "vbpub-bootstrap-stage2.service"], "disable stage2 unit")
        marker = Path(self.config.state_dir) / "stage2_done"
        if not self.actions.dry_run:
            marker.touch(mode=0o600)
        self._mark_step("stage2", "disabled", "unit disabled and completion marker set")

    def _apt_get(self, args: list[str], description: str, dangerous: bool = False) -> str:
        """The single place the installer invokes apt-get: always with the dpkg
        lock timeout, and with one bounded retry when the failure is lock
        contention (see APT_LOCK_* above). Other failures raise immediately."""
        return self._run_lock_retry(["/usr/bin/apt-get", *APT_LOCK_OPTION, *args], description, dangerous)

    def _run_lock_retry(
        self,
        argv: list[str],
        description: str,
        dangerous: bool = False,
        *,
        markers: tuple[str, ...] = _APT_LOCK_ERROR_MARKERS,
        markers_in_output: bool = False,
    ) -> str:
        """Run ``argv``; on apt/dpkg lock contention retry (bounded, see APT_LOCK_*).

        ``markers_in_output``: also treat a run that EXITED 0 but printed one of
        the markers as contention (unattended-upgrade prints its lock message
        and exits; do not trust that the exit code says so).
        """
        for attempt in range(1, APT_LOCK_RETRY_ATTEMPTS + 1):
            try:
                out = self._run(argv, description, dangerous=dangerous)
                if not (markers_in_output and any(marker in out for marker in markers)):
                    return out
                # Exit 0 but the lock message was printed: handled as contention below.
                raise ActionError(f"action failed (0): {description}\n{out}")
            except ActionError as exc:
                locked = any(marker in str(exc) for marker in markers)
                if not locked or attempt == APT_LOCK_RETRY_ATTEMPTS:
                    raise
                _LOG.warning(
                    "apt lock held by another process during '%s' (attempt %s/%s); retrying in %ss.",
                    description, attempt, APT_LOCK_RETRY_ATTEMPTS, APT_LOCK_RETRY_DELAY_S,
                )
                time.sleep(APT_LOCK_RETRY_DELAY_S)
        raise AssertionError("unreachable")  # pragma: no cover

    def _packages(self, packages: list[str], stage: str) -> None:
        self._apt_get(["update", "-qq"], f"{stage}: refresh apt metadata")
        self._apt_get(
            ["install", "-y", "--no-install-recommends", *packages],
            f"{stage}: install packages", dangerous=True,
        )
        self._mark_step(f"{stage}_packages", "success", " ".join(packages))

    def _configure_apt(self) -> None:
        sources_path = Path("/etc/apt/sources.list.d/debian.sources")
        backup = Path(f"/etc/apt/sources.list.v2-backup-{int(time.time())}")
        if sources_path.is_file() and not self.actions.dry_run:
            shutil.copy2(sources_path, backup)
        self.actions.write_file("/etc/apt/sources.list.d/debian.sources", APT_SOURCES.format(release=self.release))
        self.actions.write_file("/etc/apt/apt.conf.d/custom.conf", APT_CUSTOM)
        self.actions.write_file("/etc/apt/preferences.d/debian-priorities", APT_PRIORITIES.format(release=self.release))

        # A freshly-booted VPS's network/DNS isn't always fully settled the
        # instant customScript starts running - confirmed live 2026-09-08 on
        # two independent hosts, both first-attempt: apt-get update fetched
        # the release/updates/security suites fine but silently dropped
        # backports/testing/unstable, then succeeded immediately on a manual
        # retry seconds later with no code changes at all. Retry a few times
        # before treating it as a real configuration failure.
        expected = [self.release, f"{self.release}-updates", f"{self.release}-security", f"{self.release}-backports", "testing", "unstable"]
        missing = list(expected)
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            self._apt_get(["update", "-qq"], "apt: refresh apt metadata")
            if self.actions.dry_run:
                missing = []
                break
            policy = self._run(["/usr/bin/apt-cache", "policy"], "verify apt suites resolve")
            missing = [suite for suite in expected if suite not in policy]
            if not missing:
                break
            if attempt < max_attempts:
                _LOG.warning(
                    "apt-get update did not resolve suite(s) yet: %s "
                    "(attempt %s/%s); retrying in 5s.",
                    ", ".join(missing), attempt, max_attempts,
                )
                time.sleep(5)
        if missing:
            raise InstallerError(f"APT configuration did not resolve suite(s): {', '.join(missing)}")

        self._packages(["ca-certificates", "curl", "git", "python3"], "apt")
        self._mark_step("apt_config", "success", self.release)

    def _configure_users(self) -> None:
        packages = ["htop", "iftop", "less", "man-db", "mc", "nano"]
        self._packages(packages, "users")
        # root AND /etc/skel (new users inherit it). A file is written only when
        # it is missing: an existing one (hand-edited, or rewritten by htop/mc
        # itself) is operator-owned and never overwritten, so a re-run or resume
        # is a no-op for files we already placed and safe for ones the operator changed.
        kept = []
        for home, mode in (("/root", 0o600), ("/etc/skel", 0o644)):
            for relative, content in USER_RC_FILES:
                path = f"{home}/{relative}"
                if self.actions.exists(path):
                    kept.append(path)
                    continue
                self.actions.write_file(path, content, mode)
            self._ensure_bashrc_sources_aliases(f"{home}/.bashrc")
        detail = ", ".join(packages) + "; rc files for root and /etc/skel"
        if kept:
            detail += f"; kept existing: {', '.join(kept)}"
        self._mark_step("user_config", "success", detail)

    def _ensure_bashrc_sources_aliases(self, bashrc: str) -> None:
        """Make .bashrc source ~/.bash_aliases, once (Debian's skel .bashrc already does)."""
        path = Path(bashrc)
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if ".bash_aliases" in existing:
            return
        mode = (path.stat().st_mode & 0o777) if path.is_file() else 0o644
        self.actions.write_file(bashrc, existing + BASHRC_ALIASES_SNIPPET, mode)

    def _configure_journald(self) -> None:
        # 1G/60d, not the earlier 200M/12month: with docker_log_driver
        # defaulting to journald, this is now also every container's log
        # store, not just the host's own -- operator-set retention,
        # 2026-09-09.
        content = """[Journal]
Storage=persistent
Compress=yes
SystemMaxUse=1G
SystemKeepFree=500M
SystemMaxFileSize=100M
MaxRetentionSec=60d
MaxFileSec=1month
"""
        self.actions.write_file("/etc/systemd/journald.conf.d/99-vbpub-v2.conf", content)
        self._run(["/usr/bin/systemctl", "restart", "systemd-journald"], "restart journald", dangerous=True)
        self._mark_step("journald_config", "success", "persistent 1G/60d journal")

    def _install_docker(self) -> None:
        arch = platform.machine()
        if arch == "x86_64":
            apt_arch = "amd64"
        elif arch == "aarch64":
            apt_arch = "arm64"
        else:
            raise InstallerError(f"unsupported architecture for Docker installation: {arch}")
        self._packages(["ca-certificates", "curl", "gnupg"], "docker-prereqs")
        self.actions.mkdir("/etc/apt/keyrings")
        parsed = urllib.parse.urlparse("https://download.docker.com/linux/debian/gpg")
        if parsed.scheme != "https":
            raise InstallerError("Docker GPG URI must use HTTPS")
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
        self._mark_step("docker_install", "success", apt_arch)

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
            raise InstallerError(f"refusing to merge into an unparseable {path}: {exc}") from exc

    def _host_networks(self) -> list[tuple[str, ipaddress._BaseNetwork]]:
        """The host's own subnets: (description, network) from `ip -j addr` and `ip -j route`."""
        found: list[tuple[str, ipaddress._BaseNetwork]] = []
        addr_out = self._run(["/usr/sbin/ip", "-j", "addr", "show"], "list host addresses (docker pool check)")
        route_out = self._run(["/usr/sbin/ip", "-j", "route", "show"], "list host routes (docker pool check)")
        try:
            for iface in json.loads(addr_out or "[]"):
                if _is_docker_interface(iface.get("ifname")):
                    continue  # Docker's own bridges (a re-run) are not conflicts
                for info in iface.get("addr_info", []):
                    local, prefix = info.get("local"), info.get("prefixlen")
                    if local is None or prefix is None:
                        continue
                    network = ipaddress.ip_interface(f"{local}/{prefix}").network
                    found.append((f"address {local}/{prefix} on {iface.get('ifname', '?')}", network))
        except (ValueError, TypeError, AttributeError) as exc:
            raise InstallerError(f"cannot parse `ip -j addr` output for the docker pool check: {exc}") from exc
        # IPv4 routes only: the allowlist admits `ip -j` without `-6`; IPv6
        # pools are still checked against every inet6 address from `addr`.
        try:
            for route in json.loads(route_out or "[]"):
                dst = route.get("dst")
                if dst in (None, "default") or _is_docker_interface(route.get("dev")):
                    continue
                network = ipaddress.ip_network(dst, strict=False)
                if network.prefixlen == 0:
                    continue  # literal 0.0.0.0/0 or ::/0 is a default route
                found.append((f"route {dst}", network))
        except (ValueError, TypeError, AttributeError) as exc:
            raise InstallerError(f"cannot parse `ip -j route` output for the docker pool check: {exc}") from exc
        return found

    def _check_address_pools_against_host(self) -> None:
        """Fail if a configured docker pool overlaps the host's own addresses or routes."""
        pools = self.config.docker_default_address_pools
        if not pools or self.actions.dry_run:
            return
        host = self._host_networks()
        for pool in pools:
            base = ipaddress.ip_network(pool["base"])
            for description, network in host:
                if base.version == network.version and base.overlaps(network):
                    raise InstallerError(
                        f"docker_default_address_pools base {pool['base']} overlaps the host's "
                        f"{description}; choose a different pool base"
                    )

    def _configure_docker_daemon(self) -> None:
        self._check_address_pools_against_host()
        # Owns live-restore/log-driver/log-opts/default-address-pools only — see the ownership
        # split rationale in _install_docker()'s comment above. mdt
        # host-setup/install.sh owns "cgroup-parent" the same, disjoint way.
        existing = self._read_json_for_merge("/etc/docker/daemon.json")
        existing["live-restore"] = self.config.docker_live_restore
        existing["log-driver"] = self.config.docker_log_driver
        # max-size/max-file are json-file/local-specific rotation options --
        # meaningless (Docker logs a per-container warning and ignores them)
        # under journald, where _configure_journald()'s SystemMaxUse/
        # MaxRetentionSec are the real retention knobs instead.
        if self.config.docker_log_driver in ("json-file", "local"):
            existing.setdefault("log-opts", {})
            existing["log-opts"]["max-size"] = self.config.docker_log_max_size
            existing["log-opts"]["max-file"] = self.config.docker_log_max_file
        else:
            # A host re-run after switching drivers (e.g. json-file ->
            # journald, the 2026-09-09 default flip) must not leave a
            # stale, meaningless log-opts block behind -- this function
            # owns log-driver/log-opts together (see comment above), so an
            # idempotent re-run must fully reflect the current driver.
            existing.pop("log-opts", None)
        # Owned like log-opts: an idempotent re-run reflects the current
        # config, so an empty list removes a stale pool block.
        if self.config.docker_default_address_pools:
            existing["default-address-pools"] = [
                {"base": pool["base"], "size": pool["size"]}
                for pool in self.config.docker_default_address_pools
            ]
        else:
            existing.pop("default-address-pools", None)
        self.actions.write_file("/etc/docker/daemon.json", json.dumps(existing, indent=2) + "\n", 0o644)
        self._mark_step("docker_daemon_config", "success", self.config.docker_log_driver)

    def _install_notify_helper(self) -> None:
        # Unconditional, like cgroup2-flags: harmless if Telegram isn't
        # configured (the script itself no-ops when credentials are absent),
        # and vbpub-reboot-check/vbpub-apt-check depend on it existing.
        self.actions.write_file("/usr/local/sbin/vbpub-notify", NOTIFY_SCRIPT, 0o755)
        self._mark_step("notify_helper", "success", "/usr/local/sbin/vbpub-notify")

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
        self._mark_step(
            "docker_cleanup", "success",
            f"weekly, age>={self.config.docker_cleanup_max_age_hours}h, images+containers+build-cache only",
        )

    def _unattended_upgrade_origins(self) -> list[str]:
        """Origins-Pattern lines, each verified against the published Release
        fields (see tests/test_lt_upg.py RELEASE_STANZAS):

        * main / -updates / -security / testing / unstable Release files all
          say ``Origin: Debian``; security has ``Label: Debian-Security``.
        * trixie-backports says ``Origin: Debian Backports`` (NOT ``Debian``)
          -- LT-F-v1001-10: ``origin=Debian,codename=trixie-backports`` never
          matched, so unattended-upgrades never considered backports.
        """
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
            f'    "origin=Debian Backports,codename={release}-backports";',
        ]
        # testing/unstable are deliberately NOT allowed (controller ruling): the
        # sources and pins keep them for manual `-t testing` installs, but
        # unattended-upgrades picks the highest version of any ALLOWED origin
        # regardless of pins, producing mixed testing sets (LT-07).

    def _configure_apt_auto_upgrade(self) -> None:
        if self.config.apt_auto_upgrade_mode == "notify-only":
            self.actions.write_file("/usr/local/sbin/vbpub-apt-check", APT_UPDATE_NOTIFY_SCRIPT, 0o755)
            self.actions.write_file("/etc/systemd/system/vbpub-apt-check.service", APT_UPDATE_NOTIFY_SERVICE)
            self.actions.write_file("/etc/systemd/system/vbpub-apt-check.timer", APT_UPDATE_NOTIFY_TIMER)
            self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
            # vbpub-apt-check.timer is enabled+started by _release_apt_timers()
            # at the end of stage2, not here.
            self._hold_apt_timers()
            self._mark_step("apt_auto_upgrade", "success", "notify-only")
            return
        self._packages(["unattended-upgrades", "needrestart"], "apt-auto-upgrade")
        origins = self._unattended_upgrade_origins()
        self.actions.write_file(
            "/etc/apt/apt.conf.d/51-vbpub-unattended-upgrades",
            UNATTENDED_UPGRADES_CONFIG.format(
                mode=self.config.apt_auto_upgrade_mode,
                origins="\n".join(origins),
                lock_timeout=APT_LOCK_TIMEOUT_S,
            ),
        )
        self.actions.write_file("/etc/apt/apt.conf.d/20auto-upgrades", APT_PERIODIC_CONFIG)
        self.actions.write_file("/etc/needrestart/conf.d/vbpub.conf", NEEDRESTART_CONFIG)
        # Timers are NOT started here (LT-F-r1002-01): see _hold_apt_timers().
        self._hold_apt_timers()
        self._mark_step("apt_auto_upgrade", "success", self.config.apt_auto_upgrade_mode)

    # --- LT-UPG: one unattended-upgrade run during stage1 ------------------

    _BOOT_DIR = Path("/boot")
    _UU_UPGRADED_RE = re.compile(r"Packages that will be upgraded:[ \t]*(.*)")

    @staticmethod
    def _kernel_key(release: str) -> tuple[int, ...]:
        """Sort key for kernel release strings ('7.2.6+deb13-amd64' > '6.12.111+deb13-amd64')."""
        return tuple(int(n) for n in re.findall(r"\d+", release))

    def _running_kernel(self) -> str:
        return platform.release()

    def _boot_kernel(self) -> str:
        """The kernel GRUB boots next: the highest installed /boot/vmlinuz-*.
        Falls back to the running kernel when none is visible."""
        names = [p.name[len("vmlinuz-"):] for p in self._BOOT_DIR.glob("vmlinuz-*")]
        names = [n for n in names if re.search(r"\d", n)]
        return max(names, key=self._kernel_key) if names else self._running_kernel()

    _TAIL_LINES = 4
    _TAIL_LINE_MAX = 160
    _TAIL_MAX = 600

    def _failure_tail(self, exc: BaseException) -> str:
        """The cause of a failed command, for a step detail / post.

        The real ActionError text is ``action failed (N): <description>\\n<output>``
        (actions.py), so line 1 only names the step; the cause is the last few
        non-empty output lines. Each is truncated, secrets are redacted, and
        ``<>&`` are dropped so the text is safe in the Telegram HTML post.
        """
        lines = str(exc).splitlines()
        header = lines[0].strip() if lines else ""
        body = [line.strip() for line in lines[1:] if line.strip()][-self._TAIL_LINES:]
        body = [
            line if len(line) <= self._TAIL_LINE_MAX else line[: self._TAIL_LINE_MAX - 1] + "…"
            for line in body
        ]
        text = " | ".join([header, *body] if body else [header])
        text = re.sub(r"[<>&]", " ", text)
        text = redact_text(text, (self.config.mattermost_webhook_url, self.config.telegram_bot_token))
        return text if len(text) <= self._TAIL_MAX else text[: self._TAIL_MAX - 1] + "…"

    def _upgrade_at_install(self) -> None:
        """Run unattended-upgrade ONCE now (stage1), instead of waiting for the
        first daily timer. Called after the apt sources, pins and the
        unattended-upgrades config are written and BEFORE _plan_root_shrink()
        and the stage1 reboot: that reboot then boots any new kernel (no
        extra reboot), and -- because the root-shrink hook is installed after
        the upgrade and ends in `update-initramfs -u -k all` -- every kernel
        the upgrade installed gets the hook in its initramfs.

        The unattended-upgrades config written by _configure_apt_auto_upgrade
        already encodes the mode (security-only -> security origins only;
        full -> every origin incl. backports); this step just runs it. A
        failed run is recorded and reported but does not abort the install.
        """
        step = "apt_upgrade_at_install"
        mode = self.config.apt_auto_upgrade_mode
        if not self.config.run_apt_auto_upgrade:
            self._mark_step(step, "skipped", "run_apt_auto_upgrade is off")
            return
        if not self.config.apt_upgrade_at_install:
            self._mark_step(step, "skipped", "apt_upgrade_at_install is off")
            return
        if mode == "notify-only":
            # The count is informational: a broken-deps simulation must not
            # fail the install (it is "warned", never fatal).
            try:
                out = self._apt_get(["-s", "full-upgrade"], "count pending upgrades (simulation only)")
            except ActionError as exc:
                tail = self._failure_tail(exc)
                self._mark_step(step, "warned", f"notify-only: pending-upgrade simulation failed: {tail}")
                self._upgrade_summary = f"⚠️ apt upgrade simulation failed (notify-only), install continued: {tail}"
                return
            pending = sum(1 for line in out.splitlines() if line.startswith("Inst "))
            self._mark_step(step, "skipped", f"notify-only: nothing installed; {pending} package(s) pending")
            self._upgrade_summary = f"apt upgrade skipped (notify-only): {pending} package(s) pending"
            return
        before = self._running_kernel()
        try:
            out = self._run_lock_retry(
                ["/usr/bin/unattended-upgrade", "-v"],
                f"install-time unattended-upgrade ({mode})", dangerous=True,
                markers=_APT_LOCK_ERROR_MARKERS + _UU_LOCK_ERROR_MARKERS,
                markers_in_output=True,
            )
        except ActionError as exc:
            # ActionError text is "action failed (N): <description>\n<stdout>":
            # line 1 names only the step, the CAUSE is the end of the output.
            tail = self._failure_tail(exc)
            self._mark_step(step, "warned", f"{mode}: unattended-upgrade failed: {tail}")
            self._upgrade_summary = f"⚠️ apt upgrade failed ({mode}), install continued: {tail}"
            return
        matches = self._UU_UPGRADED_RE.findall(out)
        count = len(matches[-1].split()) if matches else 0
        after = self._boot_kernel()
        detail = f"{mode}: {count} package(s) upgraded; kernel {before} -> {after}"
        if after != before:
            if self.config.never_reboot or not self.config.auto_reboot_after_stage1:
                note = "reboot required for the new kernel (reboot disabled by configuration)"
            else:
                note = "the new kernel boots on the stage1 reboot"
            detail += f"; {note}"
        self._mark_step(step, "success", detail)
        self._upgrade_summary = f"apt upgrade {detail}"

    #: One-line outcome of _upgrade_at_install(), appended to the single
    #: stage1-complete post (Mattermost/Telegram) rather than posted on its own
    #: -- keeps one post per milestone.
    _upgrade_summary = ""

    # Hook name as it appears in `lsinitramfs` (scripts/local-premount/<name>).
    _SHRINK_HOOK_NAME = "vbpub-root-shrink"

    def _initramfs_hook_state(self) -> dict[str, bool]:
        """{initrd image name: contains the root-shrink hook} for EVERY installed kernel."""
        result: dict[str, bool] = {}
        for image in sorted(self._BOOT_DIR.glob("initrd.img-*")):
            if image.suffix in {".dpkg-bak", ".new", ".bak"}:
                continue
            listing = self._run(["/usr/bin/lsinitramfs", str(image)], f"list contents of {image.name}")
            # The EXACT premount entry, not a substring: any listing line that
            # merely contains the name (a backup, a different path) must not count.
            entries = {line.strip().removeprefix("./") for line in listing.splitlines()}
            result[image.name] = f"scripts/local-premount/{self._SHRINK_HOOK_NAME}" in entries
        return result

    _APT_TIMERS = ("apt-daily.timer", "apt-daily-upgrade.timer")

    def _hold_apt_timers(self) -> None:
        """Keep the apt timers off for the whole install (stage1 start .. stage2 end).

        LT-F-r1002-01: stage1 used to `enable --now` them, and Debian enables
        them by default anyway; the persistent apt-daily-upgrade timer then
        fired minutes after the stage2 reboot and unattended-upgrade grabbed
        the dpkg lock mid-install. `disable --now` stops them and keeps them
        from starting on the reboot; _release_apt_timers() re-enables them
        with `--now` only after stage2's last package operation. The
        DPkg::Lock::Timeout on every installer apt-get (_apt_get) remains the
        primary defence for anything else holding the lock.
        """
        # Recorded BEFORE the command: even a partially applied disable counts as
        # "this run held the timers", so a failure restores them. The state step
        # (silent, not _mark_step) lets stage2, a new process, know it too.
        self._apt_timers_held = True
        try:
            self.state.mark_step("apt_timers_held", "success", " ".join(self._APT_TIMERS))
        except Exception as exc:
            _LOG.warning("could not record apt_timers_held in the state: %s", exc)
        self._run(
            ["/usr/bin/systemctl", "disable", "--now", *self._APT_TIMERS],
            "hold apt timers until the install is finished", dangerous=True,
        )
        self._wait_apt_services_idle()

    def _wait_apt_services_idle(self) -> None:
        """Wait (bounded) until apt-daily(-upgrade).service are no longer active.

        `disable --now` stops the TIMER only: a service run that already started
        (a leftover apt-daily-upgrade from the image's first boot) keeps going
        and holds the apt lock, and unattended-upgrade does not wait for locks
        itself. Never stops the service (a running upgrade is waited for).
        """
        busy_states = {"active", "activating", "deactivating", "reloading"}
        waited = 0
        while True:
            busy = []
            for unit in _APT_SERVICES:
                state = self._run(
                    ["/usr/bin/systemctl", "show", "--property=ActiveState", "--value", unit],
                    f"check whether {unit} is still running",
                )
                if state.strip() in busy_states:
                    busy.append(f"{unit} ({state.strip()})")
            if not busy:
                return
            if waited >= APT_SERVICE_WAIT_S:
                _LOG.warning(
                    "apt services still active after %ss: %s; continuing (lock retries follow)",
                    waited, ", ".join(busy),
                )
                return
            _LOG.warning(
                "waiting for %s to finish (%ss of %ss waited)", ", ".join(busy), waited, APT_SERVICE_WAIT_S,
            )
            time.sleep(APT_SERVICE_POLL_S)
            waited += APT_SERVICE_POLL_S

    #: True once THIS process disabled the apt timers (see _hold_apt_timers).
    _apt_timers_held = False

    def _this_run_holds_apt_timers(self) -> bool:
        """This run disabled the timers and has not yet re-enabled them.

        In-memory flag (stage1, same process) or the state step written by
        _hold_apt_timers (stage2 is a new process). An install that never got
        as far as holding them (e.g. show_plan refusing the machine) must not
        touch the timers an operator may have disabled on purpose.
        """
        if self._state_frozen:  # the state on disk belongs to another run
            return self._apt_timers_held
        try:
            steps = self.state.load().get("steps", {})
        except Exception:
            return self._apt_timers_held
        held = steps.get("apt_timers_held")
        if isinstance(held, dict) and held.get("status") == "success":
            released = steps.get("apt_timers")
            return not (isinstance(released, dict) and released.get("status") == "success")
        return self._apt_timers_held

    def _release_apt_timers(self) -> None:
        """Last stage2 step (after every apt-get): enable + start the apt timers."""
        timers = list(self._APT_TIMERS)
        if self.config.run_apt_auto_upgrade and self.config.apt_auto_upgrade_mode == "notify-only":
            timers.append("vbpub-apt-check.timer")
        # With auto-upgrade off this restores the Debian default (enabled).
        self._run(
            ["/usr/bin/systemctl", "enable", "--now", *timers],
            "enable apt timers (install finished)", dangerous=True,
        )
        self._mark_step("apt_timers", "success", " ".join(timers))

    def _restore_apt_timers_after_failure(self) -> None:
        """Best-effort: a failed install must not leave the apt timers disabled.
        Idempotent (stage2 runs in a new process, so no in-memory flag). Never raises."""
        if self.actions.dry_run:
            return
        # Only what THIS run disabled: never touch timers it did not hold.
        if not self._this_run_holds_apt_timers():
            return
        try:
            self._run(
                ["/usr/bin/systemctl", "enable", "--now", *self._APT_TIMERS],
                "re-enable apt timers after install failure", dangerous=True,
            )
            _LOG.warning("install failed: apt timers re-enabled")
            self._apt_timers_held = False
            try:
                self.state.mark_step("apt_timers_held", "restored", "re-enabled after install failure")
            except Exception:
                pass
        except Exception as exc:
            _LOG.warning("install failed and apt timers could not be re-enabled: %s", exc)

    def _configure_cgroup2_flags(self) -> None:
        # Default ON, but disableable for minimal profiles —
        # memory_recursiveprot missing silently defeats every slice's
        # MemoryLow/MemoryMin with no other symptom (systemctl show keeps
        # reporting the value you set). Applied
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
        self._mark_step("cgroup2_flags", "success", "memory_recursiveprot+nsdelegate")

    def _try_run(self, argv: list[str], description: str) -> str | None:
        """Run a best-effort command; return the failure text instead of raising."""
        try:
            self._run(argv, description)
        except (ActionError, InstallerError) as exc:
            return str(exc).splitlines()[0] if str(exc) else "failed"
        return None

    def _apply_tmpfiles(self, path: str) -> str | None:
        """Apply one tmpfiles.d file now (it also applies at every boot).

        Best effort: a `w!` line whose knob is absent on this kernel fails that
        line only, and must not abort the install. Returns the failure text.
        `--boot` is required: systemd-tmpfiles skips `w!` lines without it, so
        a bare `--create` would exit 0 and change nothing.
        """
        return self._try_run(["/usr/bin/systemd-tmpfiles", "--create", "--boot", path], f"apply {path} now")

    def _retire_legacy_tuning_units(self) -> None:
        """Disable and delete the old thp-config/ksm-config units when present."""
        retired = []
        for unit in LEGACY_TUNING_UNITS:
            path = f"/etc/systemd/system/{unit}"
            if self.actions.exists(path):
                self._run(["/usr/bin/systemctl", "disable", unit], f"disable legacy {unit}")
                self.actions.remove_file(path)
                retired.append(unit)
        if retired:
            self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")

    def _configure_ksm(self) -> None:
        # tmpfiles.d `w!`, as gstammtisch does -- no unit to keep enabled.
        path = "/etc/tmpfiles.d/vbpub-ksm.conf"
        self.actions.write_file(path, KSM_TMPFILES)
        error = self._apply_tmpfiles(path)
        detail = "ksmd enabled host-wide, opt-in per process"
        self._mark_step("ksm_config", "warned" if error else "success", f"{detail}; tmpfiles apply: {error}" if error else detail)

    def _configure_oomd(self) -> None:
        # systemd-oomd ships as its own package on Debian, not part of the
        # base systemd install - confirmed live 2026-09-08 on two freshly
        # provisioned trixie hosts, both failing identically with "Unit
        # systemd-oomd.service does not exist" before this install step.
        self._packages(["systemd-oomd"], "oomd")
        self.actions.mkdir("/etc/systemd/oomd.conf.d")
        self.actions.write_file("/etc/systemd/oomd.conf.d/vbpub.conf", OOMD_CONFIG)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        self._run(
            ["/usr/bin/systemctl", "enable", "--now", "systemd-oomd"],
            "enable systemd-oomd with vbpub thresholds", dangerous=True,
        )
        self._mark_step("oomd_config", "success", "SwapUsedLimit=90%, pressure=60%/20s")

    def _configure_fstrim(self) -> None:
        self.actions.mkdir("/etc/systemd/system/fstrim.timer.d")
        self.actions.write_file("/etc/systemd/system/fstrim.timer.d/vbpub-daily.conf", FSTRIM_OVERRIDE)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        self._run(
            ["/usr/bin/systemctl", "enable", "--now", "fstrim.timer"],
            "enable daily fstrim", dangerous=True,
        )
        self._mark_step("fstrim_config", "success", "daily, whole-disk")

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
        self._mark_step("auto_reboot", "success", self.config.reboot_window_time)

    def _configure_zswap(self) -> None:
        self.actions.write_file(
            "/etc/systemd/system/zswap-config.service",
            ZSWAP_SERVICE.format(
                compressor=self.config.zswap_compressor,
                pool_percent=self.config.zswap_pool_percent,
                accept_threshold_percent=self.config.zswap_accept_threshold_percent,
                shrinker="Y" if self.config.zswap_shrinker_enabled else "N",
            ),
        )
        self.actions.write_file("/etc/modules-load.d/vbpub-zstd.conf", ZSWAP_MODULES_LOAD)
        self.actions.write_file(
            "/etc/sysctl.d/99-vbpub-swap.conf",
            SWAP_SYSCTL.format(swappiness=self.config.vm_swappiness),
        )
        self.actions.write_file("/etc/tmpfiles.d/vbpub-thp.conf", THP_TMPFILES)
        self.actions.write_file(
            "/usr/local/sbin/vbpub-min-free-floor", render_min_free_floor_script(), 0o755
        )
        self.actions.write_file("/etc/systemd/system/vbpub-min-free-floor.service", MIN_FREE_FLOOR_SERVICE)
        self.actions.write_file("/usr/local/sbin/vbpub-swap-health", SWAP_HEALTH_SCRIPT, 0o755)
        # THP/KSM used to be oneshot units; they are tmpfiles.d entries now.
        self._retire_legacy_tuning_units()
        if not self.config.run_ksm and self.actions.exists("/etc/tmpfiles.d/vbpub-ksm.conf"):
            self.actions.remove_file("/etc/tmpfiles.d/vbpub-ksm.conf")
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        # enable --now, not a bare enable: both units are WantedBy=sysinit.target
        # (templates.py), a target already passed earlier in THIS boot --
        # `_health_gate_swap_devices()` reads the live zswap compressor from
        # sysfs immediately afterward, in the same _stage2() run, with no
        # reboot in between. A bare `enable` only symlinks the unit for the
        # NEXT boot; it never actually runs this boot, so the health gate
        # was reading the kernel's still-default 'lzo' compressor instead of
        # the configured one. Live-confirmed 2026-09-09 (v1001 round 10, the
        # first round to ever get this far -- pre-existing since 9ab176399,
        # 2026-08-27, never reached before because earlier rounds died
        # during partitioning): "health gate failed: zswap compressor is
        # 'lzo', expected 'zstd'".
        # "enable-now" (a single token) is NOT a real systemctl verb --
        # confirmed via `man systemctl` against systemd 257.13-1~deb13u1,
        # the exact version this installer targets: --now is a FLAG
        # combined with the enable verb, not its own operation. Caught by
        # review before this shipped for real -- every OTHER enable+start
        # call site in this file already uses the correct two-token form
        # (see e.g. line 406, "docker"); this one was a copy-paste slip
        # that would have printed "Unknown operation enable-now." and
        # aborted _configure_zswap() with a brand new failure instead of
        # fixing the original one.
        self._run(["/usr/bin/systemctl", "enable", "--now", "zswap-config.service", "vbpub-min-free-floor.service"], "enable and start early tuning units")
        # Apply the new sysctl.d file and the THP tmpfiles entries now too;
        # both also apply at every boot. Best effort, like all knob writes
        # that must work on both the 6.12 and the 7.x kernel line.
        errors = [
            error for error in (
                self._try_run(["/usr/bin/systemctl", "restart", "systemd-sysctl.service"], "apply sysctl.d now"),
                self._apply_tmpfiles("/etc/tmpfiles.d/vbpub-thp.conf"),
            ) if error
        ]
        detail = self.config.zswap_compressor + (f"; best-effort apply failed: {'; '.join(errors)}" if errors else "")
        self._mark_step("zswap_config", "warned" if errors else "success", detail)

    def _load_io_benchmark_result(self) -> dict[str, int] | None:
        """The persisted benchmark coefficients, or None when absent/invalid."""
        path = Path(self.config.state_dir) / "io-benchmark.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        results = record.get("results") if isinstance(record, dict) else None
        if not isinstance(results, dict):
            return None
        values: dict[str, int] = {}
        for key in IOBENCH_RESULT_KEYS:
            value = results.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                return None
            values[key] = value
        return values

    def _remove_iocost_units(self) -> None:
        """Drop a previously installed io.cost unit (no valid result any more)."""
        service = "/etc/systemd/system/vbpub-iocost.service"
        if self.actions.exists(service):
            self._run(["/usr/bin/systemctl", "disable", "vbpub-iocost.service"], "disable io.cost unit")
            for path in (service, IOCOST_MODEL_PATH, "/usr/local/sbin/vbpub-iocost-setup"):
                self.actions.remove_file(path)
            self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")

    def _configure_iocost(self) -> None:
        """io.cost for the root disk from the benchmark; skipped (not failed) without a result."""
        if not self.config.iocost_enabled:
            # A re-run with iocost off must not leave an earlier unit configuring io.cost at boot.
            self._remove_iocost_units()
            self._mark_step("iocost", "skipped", "iocost_enabled is false")
            return
        results = self._load_io_benchmark_result()
        if results is None:
            self._remove_iocost_units()
            note = "io.cost not configured: no valid io benchmark result (enable run_io_benchmark to get one)"
            self._mark_step("iocost", "skipped", note)
            self._iocost_note = note
            return
        coefficients = " ".join(f"{key}={results[key]}" for key in IOBENCH_RESULT_KEYS)
        self.actions.mkdir("/etc/vbpub")
        self.actions.write_file(IOCOST_MODEL_PATH, coefficients + "\n")
        self.actions.write_file("/usr/local/sbin/vbpub-iocost-setup", IOCOST_SCRIPT, 0o755)
        self.actions.write_file("/etc/systemd/system/vbpub-iocost.service", IOCOST_SERVICE)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload systemd units")
        # io.cost is advisory: the boot script exits 1 when it cannot resolve the
        # root device or the kernel refuses the write. That must not abort the install.
        error = self._try_run(["/usr/bin/systemctl", "enable", "--now", "vbpub-iocost.service"], "enable and start io.cost unit")
        if error:
            self._iocost_warn(f"io.cost unit failed to start: {error}")
            return
        self._mark_step("iocost", "planned" if self.actions.dry_run else "success", coefficients)

    def _iocost_warn(self, text: str) -> None:
        """Advisory io.cost problem: log it, mark the step warned, surface it in the notification."""
        _LOG.warning("%s", text)
        self._iocost_note = f"{self._iocost_note}; {text}" if self._iocost_note else text
        self._mark_step("iocost", "warned", text)

    @staticmethod
    def _cgroup_io_lines(text: str) -> list[dict[str, str]]:
        """Parse io.cost.model / io.cost.qos: one ``MAJ:MIN k=v ...`` line per device."""
        lines = []
        for raw in text.splitlines():
            parts = raw.split()
            if not parts:
                continue
            entry = {"dev": parts[0]}
            entry.update(dict(part.split("=", 1) for part in parts[1:] if "=" in part))
            lines.append(entry)
        return lines

    _CGROUP_ROOT = Path("/sys/fs/cgroup")
    _SYS_CLASS_BLOCK = Path("/sys/class/block")

    def _health_gate_iocost(self) -> None:
        """Model and qos read back as configured (only when io.cost was set up).

        io.cost is advisory: a mismatch warns and marks the step, it never raises.
        """
        if self.actions.dry_run or not self.config.iocost_enabled:
            return
        results = self._load_io_benchmark_result()
        model_path = self._CGROUP_ROOT / "io.cost.model"
        if results is None or not model_path.exists():
            return  # no benchmark result (unit not installed) or a kernel without io.cost
        devno = None
        dev_file = self._SYS_CLASS_BLOCK / self.root_disk / "dev"
        if dev_file.is_file():
            devno = dev_file.read_text(encoding="utf-8").strip()
        models = [m for m in self._cgroup_io_lines(model_path.read_text(encoding="utf-8")) if devno in (None, m["dev"])]
        wanted = {key: str(value) for key, value in results.items()}
        if not any(m.get("model") == "linear" and all(m.get(k) == v for k, v in wanted.items()) for m in models):
            self._iocost_warn(f"io.cost.model does not read back the benchmark coefficients for {devno or 'the root disk'}")
            return
        qos_path = self._CGROUP_ROOT / "io.cost.qos"
        qos_text = qos_path.read_text(encoding="utf-8") if qos_path.exists() else ""
        qos = [q for q in self._cgroup_io_lines(qos_text) if devno in (None, q["dev"])]
        if not any(q.get("enable") == "1" for q in qos):
            self._iocost_warn(f"io.cost.qos is not enabled for {devno or 'the root disk'}")

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
        try:
            table = inuse_partition_editor.Table(f"/dev/{self.root_disk}", raw=dump, disk_sectors=disk_sectors)
        except SystemExit as exc:
            # Table.__init__ calls sys.exit() on an unsupported disklabel --
            # a clean CLI idiom for that module's own __main__ entrypoint,
            # but SystemExit is a BaseException, not Exception: bootstrap.py's
            # `except Exception` around the installer's own run does not
            # catch it, so it would otherwise skip the normal
            # "{PROG}: action failed: ..." message and state/notification
            # error-reporting path every other RuntimeError here goes
            # through (adversarial review finding).
            raise InstallerError(f"could not read partition table: {exc}") from exc
        if table.sector != 512:
            # Every sector computation in this module (here and throughout
            # _plan_swap_partitions()/_plan_root_shrink()/_write_sfdisk_plan())
            # hardcodes 512-byte sectors -- true for every VPS/cloud host
            # this tool targets (virtio-blk/virtio-scsi present 512-byte
            # logical sectors regardless of physical backing storage), but
            # not universally true (a native 4Kn disk with no 512e
            # emulation reports 4096). Table already parses the dump's own
            # `sector-size:` line, so refuse outright here rather than
            # silently pairing a wrong-unit disk_sectors with sfdisk's own
            # (correctly-scaled) start=/size= values on the destructive
            # Case A/B partitioning path (adversarial review finding).
            raise InstallerError(
                f"disk /dev/{self.root_disk} reports {table.sector}-byte sectors, not 512 -- "
                f"this tool's sector arithmetic assumes 512 throughout and has not been verified against this disk"
            )
        root_entry = next((part for part in table.parts if part["num"] == self.root_number), None)
        if root_entry is None:
            raise InstallerError(f"could not parse current root partition from sfdisk dump")
        if root_entry["size"] <= 0:
            raise InstallerError(f"root partition {self.root_number} has a non-positive size in sfdisk dump")
        return disk_sectors, root_entry["start"], root_entry["size"]

    def _preflight_disk_transaction(self) -> dict[str, object]:
        if self.actions.dry_run:
            return {"mode": "dry-run", "holders": [], "mounts": []}
        holders = Path("/sys/block") / self.root_disk / "holders"
        holder_names = sorted(path.name for path in holders.iterdir()) if holders.is_dir() else []
        if holder_names:
            raise InstallerError(
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
        unexpected_mounts = []
        disk_sectors, root_start, root_size = self._disk_facts()
        entries = self._live_partition_entries(disk_sectors, root_start, root_size)
        for line in forbidden:
            source = line.split()[0]
            if source in allowed:
                continue
            suffix = source[len(prefix):] if source.startswith(prefix) else ""
            if suffix.isdigit():
                number = int(suffix)
                entry = entries.get(number, {})
                if "start" in entry and "size" in entry:
                    end = int(entry["start"]) + int(entry["size"])
                    if end <= root_start:
                        continue
            unexpected_mounts.append(line)
        if unexpected_mounts:
            raise InstallerError("refusing disk transaction: partitions are mounted:\n" + "\n".join(unexpected_mounts))
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
            # Regex-based (inuse_partition_editor.ATTR_RE), not a per-token
            # line.split() -- the padded real sfdisk --dump format
            # (`start=        2048`) is silently mis-tokenized by a plain
            # split into separate "start=" / "2048," pieces, dropping both
            # keys entirely (adversarial review finding: this crashed
            # _validate_plan_geometry() with an uncaught KeyError on the
            # rollback-decision path instead of a clean diagnostic).
            attrs_text = line.partition(":")[2]
            values = {key: value.strip('"') for key, value in inuse_partition_editor.ATTR_RE.findall(attrs_text)}
            entries[int(suffix)] = values
        return entries

    @staticmethod
    def _geometry_for_comparison(attrs: dict[str, str]) -> dict[str, str | None]:
        """Project a parsed partition-entry dict down to the fields a
        write plan actually controls (start/size/type), case-folding
        type. Live-confirmed 2026-09-09 (v1001 round 9, this same
        readback-verification diagnostic's own first live outing): a
        raw dict `!=` between the in-memory plan and a real `sfdisk
        --dump` readback flags EVERY real write as a mismatch, not just
        broken ones -- real sfdisk always assigns a random `uuid=` the
        plan never specifies, and normalizes `type=` to uppercase
        regardless of the case it was written in. A perfectly correct
        forward write was rolled back over exactly these two cosmetic
        differences, undoing otherwise-successful partitioning. Compare
        only what the plan itself constrains.
        """
        return {
            "start": attrs.get("start"),
            "size": attrs.get("size"),
            "type": (attrs.get("type") or "").upper(),
        }

    @staticmethod
    def _partition_number_ranges(numbers: Iterable[int]) -> list[str]:
        """Return disjoint ``partx --nr`` ranges without spanning other numbers."""
        ordered = sorted(set(int(number) for number in numbers))
        if not ordered:
            return []
        groups: list[tuple[int, int]] = []
        start = previous = ordered[0]
        for number in ordered[1:]:
            if number != previous + 1:
                groups.append((start, previous))
                start = number
            previous = number
        groups.append((start, previous))
        return [f"{start}:{end}" for start, end in groups]

    def _validate_plan_geometry(
        self,
        current: dict[int, dict[str, str]],
        plan: dict[int, dict[str, str]],
        new_root_size: int,
    ) -> None:
        root_current = current.get(self.root_number)
        root_plan = plan.get(self.root_number)
        if not root_current or not root_plan:
            raise InstallerError("partition plan does not preserve the root partition")
        before_end = int(root_current["start"]) + int(root_current["size"])
        after_end = int(root_plan["start"]) + int(root_plan["size"])
        if after_end > before_end:
            raise InstallerError("partition plan unexpectedly grows the root partition")
        before_root = self._partitions_before_root(current, int(root_current["start"]))
        preserved = {
            number: entry for number, entry in before_root.items()
            if not self._is_swap_partition(entry) or self.config.swap_policy == "on"
        }
        for number, entry in preserved.items():
            planned = plan.get(number)
            if planned is None or any(
                planned.get(field) != entry.get(field)
                for field in ("start", "size", "type")
                if field in entry
            ):
                raise InstallerError(
                    f"partition plan does not preserve pre-root partition {number}"
                )
        for number, entry in current.items():
            if number != self.root_number and ("start" not in entry or "size" not in entry):
                raise InstallerError(
                    f"cannot validate partition-table transaction: partition {number} has incomplete geometry"
                )
            start = int(entry["start"]) if "start" in entry else 0
            end = start + int(entry["size"]) if "size" in entry else 0
            if number != self.root_number and start < before_end and end > int(root_current["start"]):
                raise InstallerError(f"partition {number} overlaps the current root partition")
            if (
                number != self.root_number and start >= before_end
                and not self._is_swap_partition(entry) and not self._is_data_partition(entry)
            ):
                raise InstallerError(
                    f"unsupported disk layout: non-swap partition {number} follows root"
                )
        # Partition numbers are identifiers, not a promise of physical order.
        # SFDISK/GPT tables may number two disjoint pre-root partitions in the
        # opposite order to their on-disk starts; overlap validation must sort
        # by geometry or it rejects a valid table (or reasons about the wrong
        # neighbor).
        ordered = sorted(plan.items(), key=lambda item: int(item[1]["start"]))
        previous_end = -1
        for number, entry in ordered:
            start = int(entry["start"])
            size = int(entry["size"])
            end = start + size
            if previous_end >= 0 and start < previous_end:
                raise InstallerError(f"partition plan overlap detected at partition {number}")
            previous_end = end
        if self.config.swap_policy == "on":
            retained = self._swap_partitions_before_root(current, int(root_current["start"]))
            root_end_after = int(root_plan["start"]) + int(root_plan["size"])
            tail_numbers = sorted(
                number for number, entry in plan.items()
                if number != self.root_number
                and self._is_swap_partition(entry)
                and int(entry["start"]) >= root_end_after
            )
            expected_numbers = self._tail_swap_numbers(len(tail_numbers), before_root)
            if tail_numbers != expected_numbers:
                raise InstallerError(f"partition plan has unexpected swap numbering: {tail_numbers!r}")

    def _plan_swap_partitions(
        self,
        *,
        validate_shrink_safety: bool = True,
        root_safety_facts: tuple[int, int, int] | None = None,
    ) -> tuple[list[tuple[int, int]], int]:
        disk_sectors, root_start, root_size = self._disk_facts()
        if self.config.swap_policy == "untouched":
            raise InstallerError(
                "swap_policy=untouched was removed; choose on or off so the disk plan owns the full layout"
            )
        entries = self._live_partition_entries(disk_sectors, root_start, root_size)
        alignment = layout_policy.ALIGN
        swap_entries = {
            number: entry for number, entry in entries.items()
            if self._is_swap_partition(entry)
        }
        root_end = root_start + root_size
        if self.config.swap_policy == "on":
            incomplete_swap = [
                number for number, entry in swap_entries.items()
                if "start" not in entry or "size" not in entry
            ]
            if incomplete_swap:
                raise InstallerError(
                    "cannot safely retain or replace swap partition(s) with incomplete geometry: "
                    + ", ".join(map(str, sorted(incomplete_swap)))
                )
        spec = self._disk_spec()
        policy = self._root_policy()
        unknown_geometry = [
            number for number, entry in entries.items()
            if number != self.root_number
            and ("start" not in entry or "size" not in entry)
        ]
        if unknown_geometry:
            raise InstallerError(
                "cannot safely resize root because partition geometry is missing for "
                + ", ".join(map(str, sorted(unknown_geometry)))
            )
        overlapping_root = [
            number for number, entry in entries.items()
            if number != self.root_number
            and "start" in entry and "size" in entry
            and int(entry["start"]) < root_end
            and int(entry["start"]) + int(entry["size"]) > root_start
        ]
        if overlapping_root:
            raise InstallerError(
                "partition(s) overlap the current root extent "
                f"({', '.join(map(str, sorted(overlapping_root)))}); refusing disk changes"
            )
        unknown_after_root = [
            number for number, entry in entries.items()
            if number != self.root_number
            and not self._is_swap_partition(entry)
            and not self._is_data_partition(entry)
            and "start" in entry and int(entry["start"]) >= root_end
        ]
        if unknown_after_root:
            raise InstallerError(
                "unsupported disk layout: non-swap partition(s) follow root "
                f"({', '.join(map(str, sorted(unknown_after_root)))}); refusing to move or overwrite them"
            )

        retained_before_root = (
            self._swap_partitions_before_root(entries, root_start)
            if self.config.swap_policy == "on" else {}
        )
        preserved_before_root = (
            self._partitions_before_root(entries, root_start)
            if self.config.swap_policy == "on" else {}
        )
        # One source of truth for every plan: _pool_end uses the shared GPT
        # safe-end helper, with or without planned tail swap.
        boundary = self._pool_end([])

        partitions: list[tuple[int, int]] = []
        actual_swap_sectors = 0
        tail_partition_count = 0
        if self.config.swap_policy == "on":
            total_gb = self._effective_swap_total_gb(disk_sectors * 512)
            total_sectors = total_gb * GIB // 512
            retained_sectors = sum(int(entry["size"]) for entry in retained_before_root.values())
            tail_target_sectors = max(0, total_sectors - retained_sectors)
            remaining_partition_slots = max(0, self.config.swap_partition_count - len(retained_before_root))
            if tail_target_sectors < alignment:
                # There is less than one aligned MiB of requested swap left;
                # the retained pre-root partition already supplies the
                # configured target to partition-table precision.
                tail_target_sectors = 0
            elif not remaining_partition_slots:
                if retained_before_root:
                    raise InstallerError(
                        "pre-root swap partitions use all configured swap_partition_count slots, "
                        "but do not meet swap_disk_total_gb; increase the configured count or total"
                    )
                raise InstallerError("swap_partition_count must be at least one when swap_policy=on")
            else:
                tail_partition_count = min(
                    remaining_partition_slots,
                    tail_target_sectors // alignment,
                )
            self._tail_swap_numbers(tail_partition_count, preserved_before_root)
            per_partition = (
                tail_target_sectors // tail_partition_count
                if tail_partition_count else 0
            )
            per_partition -= per_partition % alignment
            if tail_partition_count and per_partition <= 0:
                raise InstallerError(
                    f"remaining swap target is too small for {tail_partition_count} aligned partitions"
                )
            actual_swap_sectors = per_partition * tail_partition_count
            current_swap_start = (
                (root_start + root_size + alignment - 1) // alignment
            ) * alignment
            available_after_current_root = max(0, boundary - current_swap_start)
            if (
                actual_swap_sectors > available_after_current_root
                and policy == "untouched"
            ):
                available_gib = available_after_current_root * 512 / GIB
                requested_gib = tail_target_sectors * 512 / GIB
                raise InstallerError(
                    f"swap_policy=on requests {requested_gib:.2f} GiB, but only "
                    f"{available_gib:.2f} GiB is free after the current root; "
                    "partitioning.root=\"keep\" forbids the shrink needed to make room. "
                    "Reduce swap_disk_total_gb or choose a growable root size."
                )

        planned_swap_start = boundary - actual_swap_sectors if actual_swap_sectors else None
        planned_swaps = [(planned_swap_start, actual_swap_sectors)] if planned_swap_start is not None else []
        max_root_end = self._pool_end(planned_swaps)
        max_root_size = max_root_end - root_start
        if max_root_size <= 0:
            raise InstallerError(
                "requested swap leaves no usable space for a positive-size root partition; "
                "reduce swap_disk_total_gb or increase the disk size"
            )

        try:
            pool_plan = layout_policy.plan_pool(
                spec,
                root_start=root_start,
                pool_end=max_root_end,
                root_current_size=root_size,
                min_root_sectors=self.config.preserve_root_size_gb * GIB // 512,
            )
        except layout_policy.LayoutPolicyError as exc:
            raise InstallerError(str(exc)) from exc
        target_root_size = pool_plan.root_size

        if self.config.swap_policy == "on":
            root_end = root_start + target_root_size
            # Swap always sits at the END of the disk (layout_policy): any
            # space between root and swap stays unallocated for the cow area
            # and reserve, so root and cow remain growable.
            adjacent_start = ((root_end + alignment - 1) // alignment) * alignment
            first_swap_start = max(adjacent_start, max_root_end)
            required_end = first_swap_start + actual_swap_sectors
            if required_end > boundary:
                available_sectors = max(0, boundary - first_swap_start)
                available_gib = available_sectors * 512 / GIB
                wanted_gib = actual_swap_sectors * 512 / GIB
                if spec.root.kind == "keep":
                    reason = 'partitioning.root="keep" forbids changing the root'
                else:
                    reason = "the requested swap layout does not fit after resizing root"
                raise InstallerError(
                    f"swap_policy=on needs {wanted_gib:.2f} GiB, but only {available_gib:.2f} GiB "
                    f"is available after the current root; {reason}; reduce swap_disk_total_gb "
                    "or choose a growable root size"
                )
            partitions = [
                (first_swap_start + index * per_partition, per_partition)
                for index in range(tail_partition_count)
            ]
        data_extent = list(pool_plan.extents)
        if self.config.swap_policy == "on" and partitions:
            try:
                layout_policy.validate_swap_last(
                    list(partitions),
                    [(root_start, target_root_size)]
                    + data_extent
                    + [
                        (int(entry["start"]), int(entry["size"]))
                        for entry in preserved_before_root.values()
                        if "start" in entry and "size" in entry
                    ],
                    disk_sectors,
                )
            except layout_policy.LayoutPolicyError as exc:
                raise InstallerError(str(exc)) from exc
        if target_root_size < root_size and policy != "resize":
            raise InstallerError('partitioning.root="keep" cannot shrink root')
        if target_root_size < root_size:
            if validate_shrink_safety:
                self._validate_root_shrink_target(
                    root_start,
                    target_root_size,
                    safety_facts=root_safety_facts,
                )
        return partitions, target_root_size

    def _disk_spec(self) -> layout_policy.DiskSpec:
        """The only supported root/data/swap geometry plan."""
        try:
            return layout_policy.disk_spec(self.config.partitioning)
        except layout_policy.LayoutPolicyError as exc:
            raise InstallerError(str(exc)) from exc

    def _root_policy(self) -> str:
        """Root resize behaviour is derived exclusively from partitioning.root."""
        return self._disk_spec().root_policy

    def _root_guarded(self) -> bool:
        """Root must not be grown later by cloud-init/initramfs: its size is a decision of the plan."""
        return self._disk_spec().root.kind != "star"

    def _memory_total_bytes(self) -> int:
        if self.actions.dry_run:
            return 8 * GIB
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB$", text, re.MULTILINE)
        if not match:
            raise InstallerError("cannot determine guest RAM from /proc/meminfo for automatic swap sizing")
        return int(match.group(1)) * 1024

    def _effective_swap_total_gb(self, disk_bytes: int | None = None) -> int:
        if self.config.swap_disk_total_gb is not None:
            return self.config.swap_disk_total_gb
        if disk_bytes is None:
            disk_sectors, _root_start, _root_size = self._disk_facts()
            disk_bytes = disk_sectors * 512
        try:
            return automatic_swap_size_gb(
                disk_bytes=disk_bytes,
                memory_bytes=self._memory_total_bytes(),
            )
        except ValueError as exc:
            raise InstallerError(f"cannot derive automatic swap size: {exc}") from exc

    def _live_partition_entries(
        self, disk_sectors: int, root_start: int, root_size: int
    ) -> dict[int, dict[str, str]]:
        if self.actions.dry_run:
            return {
                self.root_number: {
                    "start": str(root_start), "size": str(root_size),
                    "type": "0fc63daf-8483-4772-8e79-3d69d8477de4",
                }
            }
        dump = self._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{self.root_disk}"])
        return self._parse_partition_entries(dump)

    def _data_area_cfg(self) -> layout_policy.DataArea:
        return self._disk_spec().data

    def _is_data_partition(self, entry: dict[str, str]) -> bool:
        """An installer-managed data-area partition (type GUID and GPT name match one planned data partition)."""
        wanted = {(e.type_guid.lower(), e.partlabel) for e in self._disk_spec().entries()}
        return ((entry.get("type") or "").lower(), entry.get("name", "")) in wanted

    def _pool_end(self, swap_partitions: list[tuple[int, int]]) -> int:
        """End of root/data/raw pool: first swap start or shared GPT-safe disk boundary."""
        if swap_partitions:
            disk_sectors, _root_start, _root_size = self._disk_facts()
            return layout_policy.pool_end(disk_sectors, swap_partitions)
        disk_sectors, _root_start, _root_size = self._disk_facts()
        return layout_policy.pool_end(disk_sectors)

    def _planned_before_root(self) -> dict[int, dict[str, str]]:
        """Partitions wholly before root (they keep their numbers); empty when no data area needs numbering."""
        if not self._disk_spec().partition_count:
            return {}
        disk_sectors, root_start, root_size = self._disk_facts()
        before_root = self._partitions_before_root(
            self._live_partition_entries(disk_sectors, root_start, root_size), root_start
        )
        if self.config.swap_policy == "off":
            before_root = {
                number: entry for number, entry in before_root.items()
                if not self._is_swap_partition(entry)
            }
        return before_root

    def _data_area_layout(
        self,
        swap_partitions: list[tuple[int, int]],
        reserved_numbers: Iterable[int],
    ) -> list[tuple[int, int, int]]:
        """(partition number, start, size) of every data partition, in disk order (empty without a data area)."""
        spec = self._disk_spec()
        if spec.partition_count == 0:
            return []
        _disk_sectors, root_start, root_size = self._disk_facts()
        try:
            plan = layout_policy.plan_pool(
                spec,
                root_start=root_start,
                pool_end=self._pool_end(swap_partitions),
                root_current_size=root_size,
                min_root_sectors=self.config.preserve_root_size_gb * GIB // 512,
            )
        except layout_policy.LayoutPolicyError as exc:
            raise InstallerError(str(exc)) from exc
        numbers = self._tail_numbers(spec.partition_count, reserved_numbers)
        return [(number, start, size) for number, (start, size) in zip(numbers, plan.extents)]

    @staticmethod
    def _is_swap_partition(entry: dict[str, str]) -> bool:
        partition_type = (entry.get("type") or "").lower()
        return partition_type in {SWAP_TYPE_GUID, "82"}

    def _partitions_before_root(
        self,
        entries: dict[int, dict[str, str]],
        root_start: int,
    ) -> dict[int, dict[str, str]]:
        """Return all partitions wholly before root, independent of their IDs."""
        before_root: dict[int, dict[str, str]] = {}
        for number, entry in entries.items():
            if number == self.root_number:
                continue
            if "start" not in entry or "size" not in entry:
                raise InstallerError(
                    f"cannot determine physical order: partition {number} has incomplete geometry"
                )
            start = int(entry["start"])
            size = int(entry["size"])
            if start < 0 or size <= 0:
                raise InstallerError(
                    f"cannot determine physical order: partition {number} has invalid geometry"
                )
            end = start + size
            if start < root_start < end:
                raise InstallerError(
                    f"partition {number} overlaps the root partition boundary; refusing disk changes"
                )
            if end <= root_start:
                before_root[number] = entry
        return before_root

    def _swap_partitions_before_root(
        self,
        entries: dict[int, dict[str, str]],
        root_start: int,
    ) -> dict[int, dict[str, str]]:
        """Return swap partitions wholly before root, keyed by partition number."""
        return {
            number: entry
            for number, entry in self._partitions_before_root(entries, root_start).items()
            if self._is_swap_partition(entry)
        }

    def _tail_numbers(self, count: int, reserved_numbers: Iterable[int]) -> list[int]:
        """The first `count` ascending partition numbers after root, skipping preserved partitions' numbers."""
        reserved = {*reserved_numbers, self.root_number}
        available = [
            number for number in range(self.root_number + 1, layout_policy.GPT_MAX_PARTITIONS + 1)
            if number not in reserved
        ]
        if count > len(available):
            raise InstallerError(
                f"cannot allocate {count} partitions after root: GPT partition-number limit reached"
            )
        return available[:count]

    def _tail_swap_numbers(self, count: int, reserved_numbers: Iterable[int]) -> list[int]:
        """Swap numbers: they follow the data partitions, because swap is physically last.

        Numbers after root ascend with disk position: data partitions (config order) first, swaps after.
        """
        spec = self._disk_spec()
        data_count = spec.partition_count
        return self._tail_numbers(data_count + count, reserved_numbers)[data_count:]

    def _validate_root_shrink_target(
        self,
        root_start: int,
        target_sectors: int,
        *,
        safety_facts: tuple[int, int, int] | None = None,
    ) -> None:
        sector = 512
        target_bytes = target_sectors * sector
        minimum_root_bytes = self.config.preserve_root_size_gb * GIB
        if target_bytes < minimum_root_bytes:
            raise InstallerError(
                f"planned root size {target_bytes / GIB:.2f} GiB is below preserve_root_size_gb "
                f"({self.config.preserve_root_size_gb} GiB); reduce the swap target or lower the configured floor"
            )
        block_size, minimum_blocks, used_bytes = (
            safety_facts if safety_facts is not None else self._root_shrink_safety_facts()
        )
        filesystem_minimum = minimum_blocks * block_size
        safety_bytes = 256 * 1024 * 1024
        if filesystem_minimum + safety_bytes > target_bytes:
            raise InstallerError(
                f"root filesystem minimum plus 256 MiB safety margin is "
                f"{(filesystem_minimum + safety_bytes) / GIB:.2f} GiB, above the planned "
                f"{target_bytes / GIB:.2f} GiB root size; reduce swap or increase disk size"
            )
        if self.actions.dry_run:
            return
        if used_bytes + safety_bytes > target_bytes:
            raise InstallerError(
                f"root currently uses {used_bytes / GIB:.2f} GiB of files/metadata, but the planned "
                f"root size is {target_bytes / GIB:.2f} GiB (including a 256 MiB safety margin); "
                "remove data or reduce the swap target"
            )

    def _root_shrink_safety_facts(self) -> tuple[int, int, int]:
        """Snapshot filesystem minimum and used bytes for one shrink decision."""
        block_size, minimum_blocks = self._root_filesystem_facts()
        used_bytes = 0 if self.actions.dry_run else self._root_used_bytes()
        return block_size, minimum_blocks, used_bytes

    @staticmethod
    def _root_used_bytes() -> int:
        try:
            stats = os.statvfs("/")
        except OSError as exc:
            raise InstallerError(f"cannot inspect current root filesystem usage: {exc}") from exc
        return (stats.f_blocks - stats.f_bfree) * stats.f_frsize

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
            raise InstallerError("could not determine root filesystem block size from dumpe2fs -h")
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
            raise InstallerError("could not determine minimum root filesystem size from resize2fs -P")
        return block_size, minimum_blocks

    def _resolve_swap_plan(
        self,
        *,
        validate_shrink_safety: bool = True,
        root_safety_facts: tuple[int, int, int] | None = None,
    ) -> tuple[list[tuple[int, int]], int, bool]:
        """Resolve final root/swap geometry without making any disk changes."""
        partitions, target_root_size = self._plan_swap_partitions(
            validate_shrink_safety=validate_shrink_safety,
            root_safety_facts=root_safety_facts,
        )
        _disk_sectors, _root_start, current_root_size = self._disk_facts()
        return partitions, target_root_size, target_root_size < current_root_size

    def _resolve_root_shrink_plan(
        self,
        *,
        validate_shrink_safety: bool = True,
        root_safety_facts: tuple[int, int, int] | None = None,
    ) -> tuple[list[tuple[int, int]], int, bool]:
        """Resolve the exact final layout that the offline hook will apply."""
        return self._resolve_swap_plan(
            validate_shrink_safety=validate_shrink_safety,
            root_safety_facts=root_safety_facts,
        )

    def _plan_root_shrink(
        self,
        resolved_plan: tuple[list[tuple[int, int]], int, bool] | None = None,
    ) -> None:
        """Case B: install an initramfs hook to shrink root offline, pre-mount.

        Called late in _stage1(), before _install_stage2()/_reboot() -- an
        initramfs-tools local-premount hook fires on EVERY boot, so
        installing it before stage1's own existing reboot is enough; no
        extra reboot cycle is needed. Auto-detected via _resolve_swap_plan():
        not needed at all (Case A, the inuse_partition_editor.Table path in
        _apply_known_swap_shape() handles it exactly as today) unless the
        disk lacks space for the known swap shape as-is.
        """
        swap_partitions, target_root_sectors, shrink_needed = (
            resolved_plan if resolved_plan is not None else self._resolve_root_shrink_plan()
        )
        if not shrink_needed:
            self._mark_step("root_shrink", "not_needed", "existing free space already covers the planned swap shape")
            return
        self._packages(["e2fsprogs"], "stage1")
        _, _, root_size = self._disk_facts()
        sector = 512
        block_size, _ = self._root_filesystem_facts()

        plan_text = self._write_sfdisk_plan(swap_partitions, target_root_sectors)
        target_blocks = (target_root_sectors * sector) // block_size
        env_content = "".join([
            f"DEVICE={self.root_partition_path}\n",
            f"DISK=/dev/{self.root_disk}\n",
            f"TARGET_BLOCKS={target_blocks}\n",
            f"TARGET_SECTORS={target_root_sectors}\n",
            f"SAFETY_BLOCKS={(256 * 1024 * 1024 + block_size - 1) // block_size}\n",
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
        # -k all, not a bare -u: -u only rebuilds the CURRENTLY RUNNING
        # kernel's initrd. Live-confirmed root cause of r1002's silent
        # shrink no-op (2026-09-09): stage1 install steps before this point
        # can pull in a newer linux-image package as an ordinary dependency
        # (confirmed on r1002: 6.12.94+deb13 was running when this ran, but
        # 6.12.107+deb13 was ALSO installed and reboot's GRUB default picks
        # the newest kernel) -- that newer kernel's own postinst had
        # already auto-generated ITS initrd (without our hook, installed
        # only after the fact) at package-install time, so a plain -u here
        # silently rebuilt the WRONG (soon-to-be-unused) kernel's image
        # while the one GRUB actually booted kept its pristine, hookless
        # initrd -- zero disk change, no logged failure, exactly what was
        # observed. -k all rebuilds every installed kernel's initrd
        # regardless of which one is currently running or which one GRUB
        # ultimately boots.
        self._run(
            ["/usr/sbin/update-initramfs", "-u", "-k", "all"],
            "rebuild initramfs with the root-shrink hook", dangerous=True,
        )
        # LT-UPG: a hookless initrd on ANY kernel GRUB might pick is the r1002
        # silent no-op again -- verify every image, fail the install loudly.
        if not self.actions.dry_run:
            missing = [name for name, has in self._initramfs_hook_state().items() if not has]
            if missing:
                raise InstallerError(
                    "root-shrink hook missing from initramfs of: " + ", ".join(missing)
                )
        self._mark_step(
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
            self._mark_step("root_shrink", "success", "dry-run: assumed the hook succeeded")
            return False
        plan_env = Path("/etc/vbpub/root-shrink-plan.env")
        target_sectors = None
        if plan_env.is_file():
            for line in plan_env.read_text(encoding="utf-8").splitlines():
                if line.startswith("TARGET_SECTORS="):
                    target_sectors = int(line.partition("=")[2].strip())
        if target_sectors is None:
            raise InstallerError(
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
            # -k all: same reasoning as _plan_root_shrink()'s own build call
            # -- more than one kernel can be installed by this point, and a
            # stale hook+plan-file copy left in a non-running kernel's
            # initrd is untidy even though it's harmless (the idempotent
            # no-op check in the hook script would just skip it next boot).
            self._run(
                ["/usr/sbin/update-initramfs", "-u", "-k", "all"],
                "rebuild initramfs without the root-shrink hook", dangerous=True,
            )
            detail = f"root shrunk to {root_size} sectors (target {target_sectors})"
            if not self.actions.dry_run:
                # Report, never fail: the shrink already succeeded, and this check
                # (lsinitramfs per image) must not turn a successful install into
                # a failed one.
                try:
                    stale = [name for name, has in self._initramfs_hook_state().items() if has]
                except Exception as exc:
                    detail += f"; WARNING could not check initramfs for a stale hook: {self._failure_tail(exc)}"
                    _LOG.warning("stale-hook check failed (install continues): %s", exc)
                else:
                    if stale:
                        # Harmless (the hook no-ops once root is at target) but untidy: report, don't fail.
                        detail += f"; WARNING hook still in initramfs of: {', '.join(stale)}"
                        _LOG.warning("root-shrink hook still in initramfs of: %s", ", ".join(stale))
            self._mark_step("root_shrink", "success", detail)
            return True
        self._mark_step("root_shrink", "failed", f"root is still {root_size} sectors, target was {target_sectors}")
        # No _notify() here: this raises, and resume()'s own except block already
        # sends a single failure notification covering this (and every other)
        # stage2 failure - a second message here would just be a duplicate.
        raise InstallerError(
            f"root shrink did not complete: root is still {root_size} sectors (target {target_sectors}); "
            f"stopping before swap placement rather than guessing"
        )

    def _write_sfdisk_plan(self, partitions: list[tuple[int, int]], new_root_size: int) -> str:
        prefix = self._partition_base
        if self.actions.dry_run:
            _disk_sectors, root_start, root_size = self._disk_facts()
            dump = self._current_partition_dump(root_start, root_size)
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
        current_entries = self._parse_partition_entries(dump)
        root_entry = current_entries.get(self.root_number, {})
        root_start = int(root_entry.get("start", "0"))
        preserved_before_root = self._partitions_before_root(current_entries, root_start)
        if self.config.swap_policy == "off":
            preserved_before_root = {
                number: entry for number, entry in preserved_before_root.items()
                if not self._is_swap_partition(entry)
            }
        retained_before_root = {
            number: entry for number, entry in preserved_before_root.items()
            if self._is_swap_partition(entry)
        }
        kept: list[str] = []
        for line in dump.splitlines():
            columns = line.split()
            if not columns or not columns[0].startswith(prefix):
                continue
            try:
                number = int(columns[0][len(prefix):])
            except ValueError:
                continue
            is_managed_swap = (
                self.config.swap_policy in {"on", "off"}
                and self._is_swap_partition(current_entries.get(number, {}))
            )
            is_retained_pre_root_swap = (
                self.config.swap_policy == "on" and number in retained_before_root
            )
            if (number in preserved_before_root and not is_managed_swap) or is_retained_pre_root_swap:
                kept.append(line)
        lines = [*header_lines, ""]
        for line in kept:
            lines.append(line)
        root_line = next(
            line for line in dump.splitlines()
            if line.startswith(f"{prefix}{self.root_number} ") or line.startswith(f"{prefix}{self.root_number}, ")
        )
        device, separator, attributes = root_line.partition(":")
        # Real `sfdisk --dump` output pads attribute values for column
        # alignment (e.g. "start=        2048, size=      497664, ..."), so
        # a plain whitespace .split() mis-tokenizes "start=" and "2048,"
        # into two separate, malformed pieces -- silently dropping the
        # "start" key entirely once re-parsed by _parse_partition_entries(),
        # which later crashed _validate_plan_geometry() with an uncaught
        # KeyError('start') (confirmed live 2026-09-08 against a real host's
        # dump; the exact same mis-tokenization bug _parse_partition_entries()
        # itself was already fixed for, via this same ATTR_RE, but that fix
        # was never applied here). Use ATTR_RE to extract key=value pairs
        # regardless of padding, preserving every non-"size" value verbatim
        # (including quoted ones, e.g. a name="...").
        rebuilt = []
        inserted = False
        for key, value in inuse_partition_editor.ATTR_RE.findall(attributes):
            if key == "size":
                rebuilt.append(f"size={new_root_size}")
                inserted = True
            else:
                rebuilt.append(f"{key}={value}")
        if not inserted:
            rebuilt.append(f"size={new_root_size}")
        lines.append(f"{prefix}{self.root_number} : " + ", ".join(rebuilt))
        data_entries = self._disk_spec().entries()
        for (number, start, size), data in zip(self._data_area_layout(partitions, preserved_before_root), data_entries):
            lines.append(
                f"{prefix}{number} : start={start}, size={size}, type={data.type_guid}, "
                f'name="{data.partlabel}"'
            )
        for number, (start, size) in zip(
            self._tail_swap_numbers(len(partitions), preserved_before_root), partitions
        ):
            lines.append(f"{prefix}{number} : start={start}, size={size}, type={SWAP_TYPE_GUID}")
        return "\n".join(lines) + "\n"

    def _apply_known_swap_shape(self) -> None:
        partitions, new_root_size = self._plan_swap_partitions()
        _disk_sectors, _root_start, current_root_size = self._disk_facts()
        if not self.actions.dry_run and new_root_size < current_root_size:
            raise InstallerError(
                "root shrink was required but the initramfs resize step did not apply it; "
                "refusing to write swap partitions"
            )
        # Root growth is applied after the free-space/swap boundary exists,
        # through an exact per-partition sfdisk update. Never publish a larger mounted-root partition in
        # this raw full-table write.
        write_root_size = min(new_root_size, current_root_size)
        preflight = self._preflight_disk_transaction()
        if self.actions.dry_run:
            _disk_sectors, root_start, root_size = self._disk_facts()
            current_dump = self._current_partition_dump(root_start, current_root_size)
        else:
            current_dump = self._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{self.root_disk}"], dangerous=False)
        current_entries = self._parse_partition_entries(current_dump)
        plan_text = self._write_sfdisk_plan(partitions, write_root_size)
        plan_entries = self._parse_partition_entries(plan_text)
        self._validate_plan_geometry(current_entries, plan_entries, write_root_size)
        backup_dir = Path(self.config.state_dir) / "backups"
        timestamp = int(time.time())
        backup_name = f"ptable-{timestamp}.sfdisk"
        checksum_name = f"{backup_name}.sha256"
        plan_path = str(Path(self.config.state_dir) / "partition-plan.sfdisk")
        if self.actions.dry_run:
            self.actions.write_file(plan_path, plan_text)
        else:
            current_geometry = {
                number: self._geometry_for_comparison(entry)
                for number, entry in current_entries.items()
            }
            plan_geometry = {
                number: self._geometry_for_comparison(entry)
                for number, entry in plan_entries.items()
            }
            if current_geometry != plan_geometry:
                self._refuse_to_disturb_data_partitions(current_entries, plan_entries)
                self.actions.write_file(str(backup_dir / backup_name), current_dump, 0o600)
                backup_digest = hashlib.sha256(current_dump.encode("utf-8")).hexdigest()
                self.actions.write_file(str(backup_dir / checksum_name), f"{backup_digest}  {backup_name}\n", 0o644)
                self.actions.write_file(plan_path, plan_text)
                old_numbers = {
                    number for number, entry in current_entries.items()
                    if self._is_swap_partition(entry) or self._is_data_partition(entry)
                }
                new_numbers = {
                    number for number, entry in plan_entries.items()
                    if self._is_swap_partition(entry) or self._is_data_partition(entry)
                }
                changed_numbers = {
                    number for number in old_numbers & new_numbers
                    if self._geometry_for_comparison(current_entries[number])
                    != self._geometry_for_comparison(plan_entries[number])
                }
                self._write_and_verify_partition_plan(
                    current_dump=current_dump,
                    plan_text=plan_text,
                    plan_entries=plan_entries,
                    new_numbers=new_numbers - old_numbers,
                    delete_numbers=old_numbers - new_numbers,
                    update_numbers=changed_numbers,
                    backup_path=str(backup_dir / backup_name),
                )
            else:
                self._mark_step("partitions", "success", "disk partition layout already matches the requested swap policy")
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
        self._mark_step("partitions", "planned" if self.actions.dry_run else "success", plan_path)

    def _refuse_to_disturb_data_partitions(
        self,
        current_entries: dict[int, dict[str, str]],
        plan_entries: dict[int, dict[str, str]],
    ) -> None:
        """A rerun must never delete, renumber or resize a data-area partition that already holds a signature.

        The data partition number follows the swap partitions, so a changed swap count moves it. Only a partition
        that is provably blank (blkid exit 2) may be re-planned; an unreadable probe counts as "in use".
        """
        for number, entry in current_entries.items():
            if not self._is_data_partition(entry):
                continue
            planned = plan_entries.get(number)
            if planned is not None and self._is_data_partition(planned) and (
                self._geometry_for_comparison(entry) == self._geometry_for_comparison(planned)
            ):
                continue
            signature = self._blkid_value(f"{self._partition_base}{number}", "TYPE")
            if signature:
                raise InstallerError(
                    f"refusing to delete or resize data-area partition {number}: it holds a {signature} signature "
                    "and the requested layout (for example a changed swap partition count) would move or change it. "
                    "Keep the layout of the installed host, or migrate the data and wipe the partition by hand first"
                )

    def _write_and_verify_partition_plan(
        self,
        *,
        current_dump: str,
        plan_text: str,
        plan_entries: dict[int, dict[str, str]],
        new_numbers: Iterable[int],
        backup_path: str,
        delete_numbers: Iterable[int] = (),
        update_numbers: Iterable[int] = (),
        record_in_dry_run: bool = False,
    ) -> None:
        """Forward sfdisk write + partx -a + udevadm settle + readback verify.

        Shared by _apply_known_swap_shape() for the final planned pool and
        swap entries, so there is exactly ONE raw-sfdisk write path. On a readback
        mismatch it restores ``current_dump`` through _restore_partition_table()
        and raises. Extracted verbatim from _apply_known_swap_shape(); the
        live-bug annotations below belong to that original block.

        Dry-run: nothing runs unless ``record_in_dry_run``; then actions are recorded, but
        no readback comparison and no device-node wait happen.
        """
        new_numbers = tuple(new_numbers)
        delete_numbers = tuple(delete_numbers)
        update_numbers = tuple(update_numbers)
        if not self.actions.dry_run or record_in_dry_run:
            # input=plan_text is REQUIRED -- sfdisk with no positional script
            # argument reads its new table from stdin (this is exactly how
            # inuse_partition_editor.py's own Table.write() calls it:
            # `run(["sfdisk", ...], input=self.to_dump())`); the port here had
            # dropped that `input=` entirely, and HostActions.run() had no
            # stdin plumbing at all to carry it even if supplied. Under
            # systemd (Type=oneshot, no StandardInput=), default stdin is
            # /dev/null, so sfdisk got an empty script and silently wrote
            # nothing -- readback then never matched the plan, and EVERY
            # real run fell into the (also broken, see below) rollback
            # branch. Confirmed live 2026-09-09 on v1001 round 6: this was
            # the true cause of "rollback failed partition write", not the
            # partx/blkid path bugs fixed earlier in this same session.
            self._run(
                ["/usr/sbin/sfdisk", "--force", "--no-reread", f"/dev/{self.root_disk}"],
                dangerous=True,
                input=plan_text,
            )
            # partx -a + udevadm settle (inuse_partition_editor.Table.write()'s
            # own apply mechanism, ported to go through HostActions rather than
            # its bare subprocess.run) is more reliable than partprobe alone at
            # getting the new swap partitions' /dev/xxxN nodes to actually exist
            # before _activate_swap_partitions() tries to mkswap them (P0#3).
            # Confirmed live 2026-09-08 on real Debian 13 trixie: partx lives
            # at /usr/bin/partx (util-linux), NOT /usr/sbin/partx -- the code
            # had assumed the latter and crashed with a bare
            # FileNotFoundError the moment a real host actually reached this
            # line (every existing test faked the subprocess call, so the
            # wrong path was never exercised for real). blkid similarly moved
            # the OTHER direction on this same host (/usr/sbin/blkid, not
            # /usr/bin/blkid) -- see _health_gate_swap_devices() and
            # _activate_swap_partitions() below. Paths were verified directly
            # against a live host with `which`, not assumed from any package
            # changelog.
            # --nr <range> is REQUIRED, not optional (confirmed live
            # 2026-09-08, the very next real host to reach this line once
            # the path above was fixed): a bare `partx -a <disk>` with no
            # range tries to re-add EVERY partition number on the disk,
            # including the ones the kernel already has (the ESP/boot/root
            # partitions this write never touched) -- and adding an
            # already-registered partition fails outright ("error adding
            # partitions 1-3"). inuse_partition_editor.Table.write() itself
            # already scopes this correctly (`partx --add --nr min:max`);
            # the port here had dropped that scoping.
            for number_range in self._partition_number_ranges(delete_numbers):
                self._run_with_retries(
                    ["/usr/bin/partx", "-d", "--nr", number_range, f"/dev/{self.root_disk}"],
                    "remove obsolete swap partitions from the kernel",
                )
            for number_range in self._partition_number_ranges(update_numbers):
                self._run_with_retries(
                    ["/usr/bin/partx", "-u", "--nr", number_range, f"/dev/{self.root_disk}"],
                    "update resized swap partitions in the kernel",
                )
            for number_range in self._partition_number_ranges(new_numbers):
                self._run_with_retries(
                    ["/usr/bin/partx", "-a", "--nr", number_range, f"/dev/{self.root_disk}"],
                    "register new partitions with the kernel",
                )
            self._run(["/usr/bin/udevadm", "settle"], "wait for udev to create new device nodes")
            readback = self._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{self.root_disk}"], dangerous=False)
            readback_entries = self._parse_partition_entries(readback)
            expected_geometry = {n: self._geometry_for_comparison(a) for n, a in plan_entries.items()}
            actual_geometry = {n: self._geometry_for_comparison(a) for n, a in readback_entries.items()}
            if not self.actions.dry_run and actual_geometry != expected_geometry:
                # Diagnostic-only, computed before the rollback below
                # overwrites the disk: every prior mismatch on this exact
                # line has needed an SSH session to a still-broken host to
                # find out WHAT differed (2026-09-08/09, three separate
                # rounds) -- surface the actual diff in the raised error
                # itself so it reaches Telegram/custom_script.output2
                # without another live round + manual SSH dig. Compares
                # the same normalized (start/size/type-cased) projection
                # used for the pass/fail decision above, not the raw
                # attrs dicts -- otherwise the diff itself would show the
                # exact uuid/type-case noise that _geometry_for_comparison
                # exists to ignore.
                all_numbers = sorted(set(expected_geometry) | set(actual_geometry))
                diff_parts = []
                for number in all_numbers:
                    expected = expected_geometry.get(number)
                    actual = actual_geometry.get(number)
                    if expected != actual:
                        diff_parts.append(f"p{number}: expected={expected} actual={actual}")
                mismatch_detail = "; ".join(diff_parts) or "(dicts differ but no per-number diff found)"
                self._restore_partition_table(
                    current_dump,
                    new_numbers,
                    "rollback failed partition write",
                    add_numbers=delete_numbers,
                    update_numbers=update_numbers,
                )
                raise InstallerError(
                    f"partition table verification failed; restored backup {backup_path}; "
                    f"diff: {mismatch_detail}"
                )
            if not self.actions.dry_run:
                for number in new_numbers:
                    path = f"{self._partition_base}{number}"
                    for _ in range(50):
                        if self.actions.exists(path):
                            break
                        time.sleep(0.1)
                    else:
                        raise InstallerError(f"partition device did not appear after partx/udevadm settle: {path}")

    def _run_with_retries(self, argv: list[str], description: str, attempts: int = 5, backoff: float = 1.0) -> str:
        """Run a kernel-sync command, retrying on a non-zero exit (udev may still hold the device)."""
        for attempt in range(1, attempts + 1):
            try:
                return self._run(argv, description, dangerous=True)
            except ActionError:
                if attempt == attempts:
                    raise
                time.sleep(backoff)
        raise AssertionError("unreachable")  # pragma: no cover

    def _restore_partition_table(
        self,
        dump: str,
        drop_numbers: Iterable[int],
        why: str,
        *,
        add_numbers: Iterable[int] = (),
        update_numbers: Iterable[int] = (),
    ) -> None:
        """Write ``dump`` back with sfdisk and retract ``drop_numbers`` from the kernel.

        The single rollback primitive: used by _write_and_verify_partition_plan()
        on a readback mismatch and by the io.cost benchmark's cleanup (where
        ``dump`` is the pre-benchmark table). Does NOT verify the result; the
        caller reads the table back.
        """
        # Same missing-stdin bug as the forward write: sfdisk
        # takes its restore script on stdin, not as a positional
        # path argument -- a bare positional arg after the device is
        # sfdisk's (unrelated) "operate on just this partition
        # number" syntax, which is why the live failure was
        # literally "failed to parse partition number: '<path>'".
        # `dump` is the exact backup content already held in
        # memory (identical to what was just written to
        # the backup file), so feed it straight back in
        # rather than re-reading the file. _run() unconditionally
        # .strip()s command output, so the dump lost its
        # trailing newline on the way in (unlike plan_text, which
        # _write_sfdisk_plan() builds with one already) -- restore
        # it so both `input=` payloads are terminated the same way
        # (adversarial-review finding, 2026-09-09: harmless in
        # practice, sfdisk's line reader handles a final
        # unterminated line fine, but an unintentional divergence
        # from the reference inuse_partition_editor.py, which
        # never strips at all).
        drop_numbers = tuple(drop_numbers)
        add_numbers = tuple(add_numbers)
        update_numbers = tuple(update_numbers)
        # udevadm settle FIRST: right after a mkfs/umount udev may still be
        # probing the device, which makes the partx -d below fail EBUSY.
        self._run(["/usr/bin/udevadm", "settle"], "wait for udev before restoring the partition table")
        # --no-reread (like the forward write): the partx -d/-u below sync the
        # kernel anyway, and a BLKRRPART with the root mounted is the unsafe part.
        self._run(
            ["/usr/sbin/sfdisk", "--force", "--no-reread", f"/dev/{self.root_disk}"],
            description=why,
            dangerous=True,
            input=dump + "\n",
        )
        # partx + udevadm settle instead of partprobe (not even
        # installed by this package set -- it ships in the separate
        # `parted` package, never one of stage2's own dependencies).
        # Two calls, not one (adversarial-review finding,
        # 2026-09-08, round 2 -- confirmed directly against
        # util-linux's own partx.c source, not just man-page
        # recall): this restores the OLD table, a
        # strictly SMALLER set of partitions than what the
        # just-reverted write's own partx -a already registered with
        # the kernel (the new partitions, the resized root).
        # `partx -u` alone only fixes GEOMETRY for partition numbers
        # still present in the restored table (which is exactly what
        # un-resizes root back to its original size) -- upd_parts()
        # silently skips (warns, does not delete) any number that's
        # now entirely ABSENT from the restored table, per
        # util-linux's own source. The vanished partition
        # numbers need an explicit, scoped `-d --nr` first; `-d`
        # treats an already-absent partition as success (ENXIO), so
        # this is safe/idempotent even if the forward path never got
        # as far as registering them.
        for number_range in self._partition_number_ranges(drop_numbers):
            self._run_with_retries(
                ["/usr/bin/partx", "-d", "--nr", number_range, f"/dev/{self.root_disk}"],
                "retract stale partitions after rollback",
            )
        for number_range in self._partition_number_ranges(update_numbers):
            self._run_with_retries(
                ["/usr/bin/partx", "-u", "--nr", number_range, f"/dev/{self.root_disk}"],
                "restore changed partition geometry after rollback",
            )
        for number_range in self._partition_number_ranges(add_numbers):
            self._run_with_retries(
                ["/usr/bin/partx", "-a", "--nr", number_range, f"/dev/{self.root_disk}"],
                "restore deleted partitions after rollback",
            )
        self._run(["/usr/bin/udevadm", "settle"], "wait for udev after rollback")

    # ------------------------------------------------------------------
    # Installer benchmark: iobench uses the planned raw partition in place.
    # It is never formatted, mounted, added temporarily, or removed.
    # ------------------------------------------------------------------

    def _current_partition_dump(self, root_start: int, root_size: int) -> str:
        if self.actions.dry_run:
            disk_sectors = max(1, root_start + root_size)
            entries = self._live_partition_entries(disk_sectors, root_start, root_size)
            lines = [
                "label: gpt",
                f"device: /dev/{self.root_disk}",
                "",
            ]
            entries.setdefault(
                self.root_number,
                {
                    "start": str(root_start), "size": str(root_size),
                    "type": layout_policy.LINUX_FS_TYPE_GUID,
                },
            )
            for number, entry in sorted(entries.items()):
                if "start" not in entry or "size" not in entry:
                    raise InstallerError(
                        f"dry-run partition {number} has incomplete geometry"
                    )
                partition_type = entry.get("type") or layout_policy.LINUX_FS_TYPE_GUID
                lines.append(
                    f"{self._partition_base}{number} : start={entry['start']}, "
                    f"size={entry['size']}, type={partition_type}"
                )
            return "\n".join(lines)
        return self._run(["/usr/sbin/sfdisk", "--dump", f"/dev/{self.root_disk}"], dangerous=False)

    def _cpu_benchmark_tool_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "tools" / "cpu_benchmark.py"

    def _verify_cpu_benchmark_tool(self, tool: Path) -> str:
        """Return the sha256 of the shared tool after checking it against its committed pin file."""
        if not tool.is_file():
            raise InstallerError(f"cpu benchmark tool not found: {tool}")
        digest = hashlib.sha256(tool.read_bytes()).hexdigest()
        pin_file = tool.with_name(tool.name + ".sha256")
        try:
            committed = pin_file.read_text(encoding="utf-8").split()[0]
        except (OSError, IndexError):
            raise InstallerError(f"tool integrity check failed: {pin_file} is missing or empty") from None
        if committed != digest:
            raise InstallerError(
                f"tool integrity check failed: {tool} sha256 {digest} does not match the committed {pin_file.name}"
            )
        return digest

    def _cpu_benchmark_pin(self) -> tuple[str, str]:
        """The (version, archive sha256) pin: config override, else the built-in pin for this architecture."""
        if self.config.cpu_benchmark_geekbench_version:
            return self.config.cpu_benchmark_geekbench_version, self.config.cpu_benchmark_sha256
        machine = platform.machine()
        pin = CPU_BENCHMARK_PINS.get(machine)
        if pin is None:
            raise InstallerError(
                f"no pinned Geekbench archive for architecture {machine!r}; set cpu_benchmark_geekbench_version "
                "and cpu_benchmark_sha256 to measure this host"
            )
        return pin

    @staticmethod
    def _cpu_benchmark_failure_text(exc: BaseException) -> str:
        """One advisory line from a failed tool run (the tool's own messages are already redacted)."""
        text = str(exc)
        match = re.match(r"action failed \((\d+)\)", text)
        lines = [line.strip() for line in text.splitlines()[1:] if line.strip()]
        detail = lines[-1] if lines else (text.splitlines()[0] if text else type(exc).__name__)
        code = match.group(1) if match else ""
        if code == "4":
            return (
                f"cpu benchmark partial: {detail}; the result URL is recorded, scores can be filled later "
                "with `mdt host-setup cpu-benchmark --resolve`"
            )
        if code == "2":
            return f"cpu benchmark refused: {detail}"
        if isinstance(exc, ActionTimeout):
            return "cpu benchmark timed out (process group killed)"
        return f"cpu benchmark failed: {detail}"

    def _run_cpu_benchmark(self) -> None:
        """Measure the host CPU once (memo 4.6): shared tools/cpu_benchmark.py, advisory.

        Writes /var/lib/mdt/host-facts/cpu-benchmark.json (0644) and the 0600 claim
        file through the tool; this method never sees the claim link (the tool
        redacts it from every line it prints). Any failure -- missing tool, no pin,
        refused, timeout, partial, no egress -- marks the step ``warned`` and the
        install continues. Idempotent across stage-two resumes (a terminal step is
        not repeated; its note is restored for the final milestone).
        """
        if not self.config.run_cpu_benchmark:
            return
        previous = self.state.load().get("steps", {}).get("cpu_benchmark")
        if previous and previous.get("status") in CPU_BENCHMARK_FINAL_STATUSES:
            self._cpu_benchmark_note = previous.get("detail", "") if previous.get("status") != "skipped" else ""
            return
        self._mark_step("cpu_benchmark", "started", "")
        try:
            tool = self._cpu_benchmark_tool_path()
            self._verify_cpu_benchmark_tool(tool)
            version, sha256 = self._cpu_benchmark_pin()
            output = self._run(
                [
                    str(tool), "--geekbench-version", version, "--sha256", sha256,
                    "--timeout", str(CPU_BENCHMARK_TOOL_TIMEOUT_S),
                    "--produced-by", f"debian-install-v2 {self._installer_version()}",
                ],
                "measure the host CPU once with Geekbench (shared tools/cpu_benchmark.py)", dangerous=True,
                timeout=CPU_BENCHMARK_OUTER_TIMEOUT_S,
            )
        except Exception as exc:  # advisory by design (memo 4.6): nothing here may stop the install
            note = self._cpu_benchmark_failure_text(exc) if isinstance(exc, ActionError) else f"cpu benchmark not run: {exc}"
            note = note.splitlines()[0][:400]
            self._cpu_benchmark_note = note
            self._mark_step("cpu_benchmark", "warned", note)
            return
        if self.actions.dry_run:
            self._mark_step("cpu_benchmark", "planned", f"would run Geekbench {version}")
            return
        lines = [line.strip() for line in output.splitlines() if line.strip().startswith("cpu-benchmark:")]
        summary = lines[-1][:400] if lines else "cpu-benchmark: finished (no summary line)"
        if "claim" in summary.lower():  # defence in depth: the tool already redacts; the milestone must never carry it
            summary = "cpu-benchmark: finished (summary withheld)"
        self._cpu_benchmark_note = summary
        self._mark_step("cpu_benchmark", "success", summary)

    @staticmethod
    def _installer_version() -> str:
        try:
            return (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            return "unknown"

    def _raw_iobench_partition(self) -> tuple[int, int, int, str]:
        """Resolve the raw benchmark partition from the plan and verify its PARTLABEL."""
        spec = self._disk_spec()
        if spec.iobench_raw_gb is None:
            raise InstallerError("run_io_benchmark requires the planned iobench raw partition")
        entries = spec.entries()
        raw_index = next((i for i, item in enumerate(entries) if item.partlabel == layout_policy.IOBENCH_PARTLABEL), None)
        if raw_index is None:
            raise InstallerError("the disk plan has no iobench raw partition")
        live: dict[int, dict[str, str]] | None = None
        if not self.actions.dry_run:
            disk_sectors, root_start, root_size = self._disk_facts()
            live = self._live_partition_entries(disk_sectors, root_start, root_size)
            labelled = [entry for entry in live.values() if entry.get("name") == layout_policy.IOBENCH_PARTLABEL]
            if len(labelled) != 1:
                raise InstallerError(
                    f"expected exactly one disk partition with PARTLABEL={layout_policy.IOBENCH_PARTLABEL}; "
                    f"found {len(labelled)}"
                )
        partitions, _target_root = self._plan_swap_partitions()
        before_root = self._planned_before_root()
        geometry = self._data_area_layout(partitions, before_root)
        numbers = self._tail_numbers(spec.partition_count, before_root)
        if raw_index >= len(geometry) or raw_index >= len(numbers):
            raise InstallerError("the iobench raw partition is outside the resolved plan")
        number = numbers[raw_index]
        _planned_number, start, size = geometry[raw_index]
        device = f"{self._partition_base}{number}"
        if live is not None:
            entry = live.get(number, {})
            if (
                entry.get("name") != layout_policy.IOBENCH_PARTLABEL
                or (entry.get("type") or "").lower() != layout_policy.LINUX_FS_TYPE_GUID.lower()
                or entry.get("start") != str(start)
                or entry.get("size") != str(size)
            ):
                raise InstallerError(
                    f"planned raw benchmark partition {device} does not match PARTLABEL="
                    f"{layout_policy.IOBENCH_PARTLABEL} and its expected geometry"
                )
        return number, start, size, device

    @staticmethod
    def _format_iocost_summary(results: dict[str, int]) -> str:
        return (
            f"rbps {results['rbps']}, wbps {results['wbps']}, "
            f"rseqiops {results['rseqiops']}, rrandiops {results['rrandiops']}, "
            f"wseqiops {results['wseqiops']}, wrandiops {results['wrandiops']} IOPS"
        )

    def _run_io_benchmark(self) -> None:
        """Run iobench veryquick on the planned, unformatted raw partition."""
        if not self.config.run_io_benchmark:
            return
        state = self.state.load()
        previous = state.get("steps", {}).get("io_benchmark")
        if previous and previous.get("status") in IOBENCH_FINAL_STATUSES:
            return
        _number, _start, size, device = self._raw_iobench_partition()
        size_gib = size * 512 / GIB
        self._mark_step("io_benchmark", "started", f"PARTLABEL={layout_policy.IOBENCH_PARTLABEL} {device}")
        try:
            if not self.actions.dry_run:
                mounted = self._run(
                    ["/usr/bin/findmnt", "-rn", "-o", "SOURCE,TARGET,FSTYPE"],
                    "verify the raw benchmark partition is not mounted",
                    dangerous=False,
                )
                if any(line.split() and os.path.realpath(line.split()[0]) == os.path.realpath(device)
                       for line in mounted.splitlines()):
                    raise InstallerError(f"refusing to wipe mounted raw benchmark partition {device}")
                if device in self._active_swap_sources():
                    raise InstallerError(f"refusing to wipe active swap partition {device}")
            self._packages(["fio"], "io_benchmark")
            self._run(
                ["/usr/sbin/wipefs", "-a", device],
                f"wipe signatures from planned raw benchmark partition {device}",
                dangerous=True,
            )
            partuuid = (
                "00000000-0000-0000-0000-000000000000"
                if self.actions.dry_run
                else self._run(["/usr/sbin/blkid", "-s", "PARTUUID", "-o", "value", device])
            )
            if not partuuid:
                raise InstallerError(f"planned raw benchmark partition has no PARTUUID: {device}")
            runner = Path(__file__).resolve().parents[1] / "tools" / "iobench-install-runner"
            output = self._run(
                [
                    str(runner),
                    "--target", device,
                    "--expect-size", str(size * 512),
                    "--expect-partuuid", partuuid,
                    "--state-dir", str(Path(self.config.state_dir) / "iobench"),
                ],
                f"run iobench veryquick on {device}",
                dangerous=True,
                timeout=IOBENCH_OUTER_TIMEOUT_S,
            )
            if self.actions.dry_run:
                summary = f"dry-run: would run iobench veryquick on {device} ({size_gib:.1f} GiB)"
                self._mark_step("io_benchmark", "planned", summary)
                return
            results = self._load_io_benchmark_result()
            if results is None:
                raise InstallerError("iobench completed without six usable io.cost coefficients")
            summary = f"{self._format_iocost_summary(results)} on {device} ({size_gib:.1f} GiB, iobench veryquick)"
            self._mark_step("io_benchmark", "success", summary)
            if not self.config.telegram_verbose_progress:
                self._notify(f"<b>io benchmark</b>: {summary}", event=f"io benchmark: {summary}", status="ok")
        except ActionUnreapable as exc:
            detail = f"iobench could not be reaped; raw partition {device} may still be in use: {exc}"
            self._mark_step("io_benchmark", "failed", detail)
            raise InstallerError(detail) from exc
        except Exception as exc:
            detail = f"iobench failed (advisory): {str(exc).splitlines()[0][:400]}"
            self._mark_step("io_benchmark", "warned", detail)
            if not self.config.telegram_verbose_progress:
                self._notify(f"<b>io benchmark</b>: {_code(detail)}", event=detail, status="run")

    _ZSWAP_PARAMS = Path("/sys/module/zswap/parameters")

    def _health_gate_zswap(self) -> None:
        """The live zswap knobs read back as configured. Booleans read as Y/N."""
        if self.actions.dry_run:
            return
        # compressor first: it is the most common mismatch (the kernel's lzo default).
        expected = (
            ("compressor", self.config.zswap_compressor),
            ("enabled", "Y"),
            ("max_pool_percent", str(self.config.zswap_pool_percent)),
            ("accept_threshold_percent", str(self.config.zswap_accept_threshold_percent)),
            ("shrinker_enabled", "Y" if self.config.zswap_shrinker_enabled else "N"),
        )
        for name, want in expected:
            actual = (self._ZSWAP_PARAMS / name).read_text(encoding="utf-8").strip()
            if actual != want:
                raise InstallerError(f"health gate failed: zswap {name} is {actual!r}, expected {want!r}")

    def _health_gate_swap_devices(self, *, mark_step: bool = True) -> None:
        fstab_path = Path("/etc/fstab")
        fstab_lines = fstab_path.read_text(encoding="utf-8").splitlines() if fstab_path.is_file() else []
        fstab_swap_lines = [
            line for line in fstab_lines
            if line.strip() and len(line.split()) >= 3 and line.split()[2].lower() == "swap"
        ]
        if self.config.swap_policy == "on":
            disk_sectors, root_start, root_size = self._disk_facts()
            live = self._live_partition_entries(disk_sectors, root_start, root_size)
            before_root = self._partitions_before_root(live, root_start)
            retained = self._swap_partitions_before_root(live, root_start)
            tail_partitions, _target_root_size = self._plan_swap_partitions()
            expected_numbers = sorted({
                *retained,
                *self._tail_swap_numbers(len(tail_partitions), before_root),
            })
            prefix = self._partition_base
            swaps_output = self._run(
                ["/usr/sbin/swapon", "--show=NAME,TYPE,SIZE,PRIO", "--noheadings"], dangerous=False
            ) if not self.actions.dry_run else ""
            active_names = {line.split()[0] for line in swaps_output.splitlines() if line.strip()}
            fstab_partuuids = {
                line.split()[0].removeprefix("PARTUUID=")
                for line in fstab_swap_lines
                if line.split()[0].startswith("PARTUUID=")
            }
            for number in expected_numbers:
                path = f"{prefix}{number}"
                partuuid = "dry-run" if self.actions.dry_run else self._run(
                    ["/usr/sbin/blkid", "-s", "PARTUUID", "-o", "value", path]
                )
                if not partuuid:
                    raise InstallerError(f"health gate failed: missing PARTUUID on {path}")
                if not self.actions.dry_run and path not in active_names:
                    raise InstallerError(f"health gate failed: {path} is formatted but not active")
                if not self.actions.dry_run and partuuid not in fstab_partuuids:
                    raise InstallerError(f"health gate failed: {path} PARTUUID is absent from fstab")
            swap_detail = f"{len(expected_numbers)} swaps verified ({len(retained)} retained before root)"
        elif self.config.swap_policy == "off":
            swaps_output = self._run(
                ["/usr/sbin/swapon", "--show=NAME,TYPE,SIZE,PRIO", "--noheadings"], dangerous=False
            ) if not self.actions.dry_run else ""
            if not self.actions.dry_run and swaps_output.strip():
                raise InstallerError("health gate failed: swap_policy=off but active swap remains")
            if not self.actions.dry_run and fstab_swap_lines:
                raise InstallerError("health gate failed: swap_policy=off but /etc/fstab still enables swap")
            swap_detail = "swap disabled and persistent fstab entries removed"
        if self.config.run_zswap_config:
            self._health_gate_zswap()
        self._health_gate_iocost()
        output_file = Path(self.config.stage2_output)
        if not self.actions.dry_run and not (output_file.is_file() and output_file.stat().st_size >= 0):
            raise InstallerError(f"health gate failed: stage2 log does not exist: {output_file}")
        if mark_step:
            self._mark_step("health_gate", "planned" if self.actions.dry_run else "success", swap_detail)

    def _disable_active_swap(self) -> None:
        if self.config.swap_policy == "on":
            self._remove_managed_swap_backings()
            return
        self._run(["/usr/sbin/swapoff", "-a"], "disable active swap before applying the configured swap policy", True)
        if not self.actions.dry_run and self._active_swap_sources():
            raise InstallerError("swapoff returned successfully but active swap remains")
        if self.config.swap_policy == "off":
            self._persist_fstab([])
            self._mark_step("swap_partitions", "success" if not self.actions.dry_run else "planned", "swap disabled; existing backing files/LVs are not deleted")

    def _activate_swap_partitions(self) -> None:
        if self.config.swap_policy != "on":
            return
        prefix = self._partition_base
        disk_sectors, root_start, root_size = self._disk_facts()
        live = self._live_partition_entries(disk_sectors, root_start, root_size)
        before_root = self._partitions_before_root(live, root_start)
        retained = self._swap_partitions_before_root(live, root_start)
        tail_partitions, _target_root_size = self._plan_swap_partitions()
        tail_numbers = self._tail_swap_numbers(len(tail_partitions), before_root)
        layout = [
            (number, int(entry["start"]), int(entry["size"]), True)
            for number, entry in retained.items()
        ]
        layout.extend(
            (number, start, size, False)
            for number, (start, size) in zip(tail_numbers, tail_partitions)
        )
        layout.sort(key=lambda item: item[1])
        fstab_entries = []
        discard = ",discard=once" if self.config.swap_discard else ""
        for offset, (number, _start, _size, was_retained) in enumerate(layout, start=1):
            path = f"{prefix}{number}"
            label = f"vbpub-swap-pre{offset}" if was_retained else f"vbpub-swap{offset}"
            partuuid = "dry-run" if self.actions.dry_run else self._run(["/usr/sbin/blkid", "-s", "PARTUUID", "-o", "value", path])
            if not self.actions.dry_run and not partuuid:
                raise InstallerError(f"expected swap partition has no PARTUUID after partitioning: {path}")
            self._run(["/usr/sbin/mkswap", "-L", label, path], f"format {path}", dangerous=True)
            self._run(["/usr/sbin/swapon", "-p", str(self.config.swap_priority), path], f"enable {path}", dangerous=True)
            refreshed = partuuid if self.actions.dry_run else self._run(["/usr/sbin/blkid", "-s", "PARTUUID", "-o", "value", path])
            if not refreshed:
                raise InstallerError(f"PARTUUID disappeared after mkswap: {path}")
            fstab_entries.append(f"PARTUUID={refreshed} none swap sw,pri={self.config.swap_priority}{discard} 0 0")
        self._persist_fstab(fstab_entries)
        self._mark_step(
            "swap_partitions",
            "planned" if self.actions.dry_run else "success",
            f"{len(layout)} native GPT swaps activated ({len(retained)} retained before root)",
        )

    def _blkid_value(self, device: str, tag: str) -> str:
        """blkid tag of a device, "" when blank (blkid exits 2 on a blank device)."""
        if self.actions.dry_run:
            return ""
        try:
            return self._run(["/usr/sbin/blkid", "-s", tag, "-o", "value", device])
        except ActionError as exc:
            # blkid exits 2 only when it found no matching tag (a blank device). Any other failure
            # (I/O error, missing device node, probe error) is NOT blank: fail closed.
            if "action failed (2)" in str(exc):
                return ""
            raise InstallerError(f"cannot determine whether {device} is blank (blkid failed): {exc}") from exc

    def _devices_with_label(self, label: str) -> list[str]:
        """Block devices whose filesystem label is `label` (blkid exit 2 = none)."""
        if self.actions.dry_run:
            return []
        try:
            out = self._run(["/usr/sbin/blkid", "-t", f"LABEL={label}", "-o", "device"])
        except ActionError as exc:
            if "action failed (2)" in str(exc):
                return []
            raise InstallerError(f"cannot list devices labelled {label} (blkid failed): {exc}") from exc
        return [line.strip() for line in out.splitlines() if line.strip()]

    def _ensure_data_filesystem(self, device: str, fs: str, label: str | None) -> None:
        """mkfs only on a BLANK device; the same fs again is kept; anything else is refused."""
        existing = self._blkid_value(device, "TYPE")
        if existing == fs:
            return
        if existing:
            raise InstallerError(
                f"refusing to format {device} as {fs}: it already holds a {existing} signature"
            )
        label_args = ["-L", label] if label else []
        self._run([f"/usr/sbin/mkfs.{fs}", *label_args, device], f"mkfs.{fs} {device}", dangerous=True)

    def _data_mount_entry(self, source: str, mount: str, fs: str) -> str:
        options = "noatime,user_subvol_rm_allowed,nofail" if fs == "btrfs" else "defaults,nofail"
        docker = "/var/lib/docker"
        if mount == docker or mount.startswith(docker + "/") or docker.startswith(mount + "/"):
            # nofail alone would let docker start and fill the root fs when this mount is missing
            options += ",x-systemd.required-by=docker.service"
        return f"{source} {mount} {fs} {options} 0 2" if fs == "ext4" else f"{source} {mount} {fs} {options} 0 0"

    def _configure_data_area(self) -> None:
        """Create filesystem / LVM on the data-area partition the table already holds, mount it, persist fstab."""
        area = self._data_area_cfg()
        if area.kind == "none":
            self._mark_step("data_area", "not_needed", "no data area in partitioning")
            return
        self._packages(layout_policy.data_area_packages(area), "data-area")
        disk_sectors, root_start, root_size = self._disk_facts()
        live = self._live_partition_entries(disk_sectors, root_start, root_size)
        before_root = self._planned_before_root()
        tail_partitions, _target_root_size = self._plan_swap_partitions()
        layout = self._data_area_layout(tail_partitions, before_root)
        if not layout:
            raise InstallerError("a data area is configured but the plan has no data partition")
        devices = []
        for (number, _start, _size), entry in zip(layout, area.entries()):
            if not self.actions.dry_run and not self._is_data_partition(live.get(number, {})):
                raise InstallerError(f"expected data-area partition {number} ({entry.partlabel}) is not in the partition table")
            devices.append(f"{self._partition_base}{number}")
        device = devices[0]
        mounts: list[tuple[str, str, str]] = []  # (fstab source, mount point, fs)
        if area.kind == "partitions":
            for part_device, volume in zip(devices, area.volumes):
                if volume.fs == "none":
                    continue  # a raw partition (e.g. for a hand-made zfs pool): nothing to create or mount
                self._ensure_data_filesystem(part_device, volume.fs, volume.label)
                others = [
                    d for d in self._devices_with_label(volume.label or "")
                    if os.path.realpath(d) != os.path.realpath(part_device)
                ]
                if others:
                    raise InstallerError(
                        f"label {volume.label} is also on {', '.join(others)}: an ambiguous label would leave "
                        f"{volume.mount} on the root filesystem; wipe or relabel the stale device and rerun"
                    )
                uuid = self._blkid_value(part_device, "UUID")
                mounts.append((f"UUID={uuid}" if uuid else f"LABEL={volume.label}", volume.mount or "", volume.fs))
        else:
            pv_type = self._blkid_value(device, "TYPE")
            if pv_type not in {"", "LVM2_member"}:
                raise InstallerError(f"refusing to make {device} an LVM physical volume: it holds a {pv_type} signature")
            if pv_type == "":
                self._run(["/usr/sbin/pvcreate", "-y", device], f"pvcreate {device}", dangerous=True)
            existing_vg_pvs = ""
            if not self.actions.dry_run:
                try:
                    existing_vg_pvs = self._run(["/usr/sbin/vgs", "--noheadings", "-o", "pv_name", area.vg])
                except ActionError:
                    existing_vg_pvs = ""
            if existing_vg_pvs:
                if device not in existing_vg_pvs.split():
                    raise InstallerError(f"volume group {area.vg} already exists on other physical volumes: {existing_vg_pvs}")
            else:
                self._run(["/usr/sbin/vgcreate", area.vg, device], f"vgcreate {area.vg}", dangerous=True)
            have: set[str] = set()
            if not self.actions.dry_run:
                try:
                    have = set(self._run(["/usr/sbin/lvs", "--noheadings", "-o", "lv_name", area.vg]).split())
                except ActionError:
                    have = set()
            self._run(["/usr/sbin/vgchange", "-ay", area.vg], f"activate {area.vg}")
            vg_extents = layout_policy.estimate_vg_extents(layout[0][2])
            if not self.actions.dry_run:
                try:
                    vg_extents = int(self._run(
                        ["/usr/sbin/vgs", "--noheadings", "-o", "vg_extent_count", area.vg]
                    ).split()[0])
                except (ActionError, ValueError, IndexError) as exc:
                    raise InstallerError(f"cannot read the extent count of volume group {area.vg}: {exc}") from exc
            try:
                lv_plans = layout_policy.plan_lvs(area, vg_extents)
            except layout_policy.LayoutPolicyError as exc:
                raise InstallerError(str(exc)) from exc
            for lv in lv_plans:  # fixed and percent LVs first, the "*" LV last (it takes what is left)
                if lv.name not in have:
                    self._run(
                        ["/usr/sbin/lvcreate", "-y", "-n", lv.name, *lv.args, area.vg],
                        f"lvcreate {area.vg}/{lv.name}", dangerous=True,
                    )
            self._run(["/usr/bin/udevadm", "settle"], "wait for LV device nodes")
            for lv in area.volumes:
                if lv.fs == "none":
                    continue  # left unformatted on purpose (e.g. for a hand-made zfs pool)
                lv_device = f"/dev/{area.vg}/{lv.name}"
                self._ensure_data_filesystem(lv_device, lv.fs, lv.label)
                mounts.append((lv_device, lv.mount or "", lv.fs))
        mounts.sort(key=lambda item: item[1])  # parents before children in fstab
        wanted = {mount for _source, mount, _fs in mounts}
        retained = [
            line for line in self._read_fstab_lines()
            if not (line.strip() and not line.lstrip().startswith("#") and len(line.split()) >= 2 and line.split()[1] in wanted)
        ]
        entries = [self._data_mount_entry(source, mount, fs) for source, mount, fs in mounts]
        self.actions.write_file("/etc/fstab", "\n".join(retained + entries + [""]), 0o644)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload fstab-generated units")
        for _source, mount, _fs in mounts:
            self._run(["/usr/bin/mkdir", "-p", mount], f"create {mount}")
            mounted = False
            if not self.actions.dry_run:
                try:
                    self._run(["/usr/bin/findmnt", "-rn", "-o", "SOURCE,TARGET,FSTYPE", mount])
                    mounted = True
                except ActionError:
                    mounted = False
            if not mounted:
                self._run(["/usr/bin/mount", mount], f"mount {mount}", dangerous=True)
        self._mark_step(
            "data_area", "planned" if self.actions.dry_run else "success",
            f"{area.kind} on {device}: " + ", ".join(f"{mount} ({fs})" for _s, mount, fs in mounts),
        )

    def _grow_root_to_plan(self) -> None:
        """Grow the root partition/filesystem after its final boundary is present."""
        _disk_sectors, root_start, current_size = self._disk_facts()
        _partitions, target_size, _shrink_needed = self._resolve_swap_plan()
        if self._root_policy() == "untouched":
            self._mark_step("root_resize", "not_needed", "root is untouched (partitioning.root=keep)")
            return
        if current_size > target_size:
            raise InstallerError(
                f"root partition is {current_size} sectors, larger than the planned {target_size}; "
                "the offline shrink step did not complete"
            )
        prior_status = ""
        state_path = Path(self.config.state_dir) / "state.json"
        if state_path.is_file():
            prior_status = self.state.load().get("steps", {}).get("root_resize", {}).get("status", "")
        if (
            current_size == target_size
            and prior_status in {"success", "not_needed"}
            and (self.actions.dry_run or self._kernel_partition_sectors() == current_size)
        ):
            return
        # The planner only grows supported ext filesystems. This read-only
        # probe also refuses unknown root filesystems before sfdisk edits GPT.
        self._root_filesystem_facts()
        self._mark_step("root_resize", "started", f"current={current_size} target={target_size}")
        if target_size > current_size:
            self._run(
                ["/usr/sbin/sfdisk", "--no-reread", "--no-tell-kernel", "-N", str(self.root_number),
                 f"/dev/{self.root_disk}"],
                "grow root partition to the planned size",
                dangerous=True,
                input=f",{target_size}\n",
            )
            self._run(
                ["/usr/bin/partx", "-u", "--nr", str(self.root_number), f"/dev/{self.root_disk}"],
                "tell the kernel the root partition grew",
                dangerous=True,
            )
        if self.actions.dry_run:
            self._run(
                ["/usr/sbin/resize2fs", self.root_partition_path],
                "grow root filesystem to the enlarged partition",
                dangerous=True,
            )
            self._mark_step("root_resize", "planned", f"grow root partition/filesystem to {target_size} sectors")
            return
        if target_size > current_size:
            disk_sectors, _root_start, grown_size = self._disk_facts()
            entries = self._live_partition_entries(disk_sectors, root_start, grown_size)
            original_root_end = root_start + current_size
            following_starts = []
            for number, entry in entries.items():
                if number == self.root_number:
                    continue
                if "start" not in entry or "size" not in entry:
                    raise InstallerError(
                        f"cannot verify root growth boundary: partition {number} has incomplete geometry"
                    )
                start = int(entry["start"])
                size = int(entry["size"])
                if start < original_root_end and start + size > root_start:
                    raise InstallerError(
                        f"partition {number} overlaps the original root extent; refusing root growth"
                    )
                if start >= original_root_end:
                    following_starts.append(start)
            permitted_end = min(following_starts) if following_starts else disk_sectors
            maximum_size = min(permitted_end - root_start, target_size)
            if grown_size != target_size:
                raise InstallerError(
                    f"sfdisk produced root size {grown_size} sectors; the plan requires exactly "
                    f"{target_size} and the next partition/disk boundary permits at most {maximum_size}; "
                    "refusing to resize the filesystem to an unexpected boundary"
                )
        else:
            grown_size = current_size
        # Crash safety: the table (sfdisk --dump) can be ahead of the kernel when an earlier run died between
        # `sfdisk -N` and `partx -u`. Compare the KERNEL size to the table and
        # re-inform the kernel before resize2fs, otherwise resize2fs sees the old size, reports "nothing to do"
        # and success would be recorded with a small filesystem.
        kernel_sectors = self._kernel_partition_sectors()
        if kernel_sectors != grown_size:
            self._run(
                ["/usr/bin/partx", "-u", "--nr", str(self.root_number), f"/dev/{self.root_disk}"],
                f"tell the kernel the root partition is {grown_size} sectors (kernel has {kernel_sectors})",
                dangerous=True,
            )
            kernel_sectors = self._kernel_partition_sectors()
            if kernel_sectors != grown_size:
                raise InstallerError(
                    f"kernel reports root partition {kernel_sectors} sectors but the table says {grown_size} "
                    "after partx -u; refusing to resize the filesystem or record success (reboot, then re-run)"
                )
        self._run(
            ["/usr/sbin/resize2fs", self.root_partition_path],
            "grow root filesystem to fill its partition",
            dangerous=True,
        )
        fs_bytes = self._root_filesystem_bytes()
        partition_bytes = kernel_sectors * 512
        # resize2fs fills the partition up to a partial block/group; a filesystem more than 1 MiB + 0.1 percent
        # short of the partition means the resize did not happen.
        if fs_bytes > partition_bytes or partition_bytes - fs_bytes > 1048576 + partition_bytes // 1000:
            raise InstallerError(
                f"root filesystem is {fs_bytes} bytes but the partition is {partition_bytes} bytes after resize2fs; "
                "refusing to record root_resize success"
            )
        self._mark_step("root_resize", "success", f"root partition/filesystem grown to {grown_size} sectors")

    def _kernel_partition_sectors(self) -> int:
        """Root partition size as the KERNEL sees it (512-byte sectors), not as the table says."""
        out = self._run(
            ["/usr/sbin/blockdev", "--getsize64", self.root_partition_path],
            "read the kernel's root partition size",
        )
        try:
            return int(out.split()[0]) // 512
        except (IndexError, ValueError) as exc:
            raise InstallerError(f"cannot parse blockdev --getsize64 output for {self.root_partition_path}: {out!r}") from exc

    def _root_filesystem_bytes(self) -> int:
        """Current ext filesystem size (block count x block size) of the root partition."""
        out = self._run(["/usr/sbin/dumpe2fs", "-h", self.root_partition_path], "read root filesystem size")
        block_size = block_count = 0
        for line in out.splitlines():
            key, _sep, value = line.partition(":")
            if key.strip() == "Block size":
                block_size = int(value.strip())
            elif key.strip() == "Block count":
                block_count = int(value.strip())
        if block_size <= 0 or block_count <= 0:
            raise InstallerError("could not read the root filesystem size from dumpe2fs -h")
        return block_size * block_count

    def _ensure_disk_transaction_manifest(self) -> None:
        """Persist a checksummed pre-change table even when the policy is a no-op."""
        manifest_path = Path(self.config.state_dir) / "disk-transaction.json"
        if self.actions.dry_run:
            if str(manifest_path) in self.actions.dry_run_writes:
                return
        elif manifest_path.is_file():
            return
        disk_sectors, root_start, root_size = self._disk_facts()
        current_dump = self._current_partition_dump(root_start, root_size)
        preflight = self._preflight_disk_transaction()
        timestamp = int(time.time())
        backup_dir = Path(self.config.state_dir) / "backups"
        backup_name = f"ptable-{timestamp}.sfdisk"
        checksum_name = f"{backup_name}.sha256"
        backup_path = backup_dir / backup_name
        checksum_path = backup_dir / checksum_name
        digest = hashlib.sha256(current_dump.encode("utf-8")).hexdigest()
        self.actions.write_file(str(backup_path), current_dump + "\n", 0o600)
        self.actions.write_file(str(checksum_path), f"{digest}  {backup_name}\n", 0o644)
        _partitions, target_root, _shrink = self._resolve_swap_plan()
        manifest = {
            "preflight": preflight,
            "operation": "layout-snapshot-before-root-resize",
            "plan": current_dump,
            "current": current_dump,
            "backup": str(backup_path),
            "checksum": str(checksum_path),
            "new_root_size_sectors": target_root,
        }
        self.actions.write_file(
            str(manifest_path), json.dumps(manifest, indent=2, sort_keys=True) + "\n", 0o600
        )

    def _read_fstab_lines(self) -> list[str]:
        fstab_path = Path("/etc/fstab")
        if self.actions.dry_run or not fstab_path.is_file():
            return []
        return fstab_path.read_text(encoding="utf-8").splitlines()

    @staticmethod
    def _decode_fstab_field(value: str) -> str:
        return re.sub(
            r"\\([0-7]{3})",
            lambda match: chr(int(match.group(1), 8)),
            value,
        )

    def _fstab_swap_sources(self) -> list[str]:
        sources: list[str] = []
        for line in self._read_fstab_lines():
            fields = line.split()
            if fields and not fields[0].startswith("#") and len(fields) >= 3 and fields[2].lower() == "swap":
                sources.append(self._decode_fstab_field(fields[0]))
        return list(dict.fromkeys(sources))

    def _active_swap_sources(self) -> dict[str, str]:
        output = self._run(
            ["/usr/sbin/swapon", "--show=NAME,TYPE,SIZE,PRIO", "--noheadings"],
            "inspect active swap sources",
            dangerous=False,
        )
        sources: dict[str, str] = {}
        for line in output.splitlines():
            fields = line.split()
            if len(fields) >= 2:
                sources[self._decode_fstab_field(fields[0])] = fields[1].lower()
        return sources

    def _resolve_swap_block_source(self, source: str) -> str | None:
        if source.startswith("/dev/"):
            return source
        if "=" not in source:
            return None
        kind, value = source.split("=", 1)
        if not value:
            return None
        if kind == "UUID":
            argv = ["/usr/sbin/blkid", "-U", value]
        elif kind == "LABEL":
            argv = ["/usr/sbin/blkid", "-L", value]
        elif kind in {"PARTUUID", "PARTLABEL"}:
            argv = ["/usr/sbin/blkid", "-t", f"{kind}={value}", "-o", "device"]
        else:
            return None
        try:
            output = self._run(argv, "resolve configured swap source", dangerous=False)
        except ActionError:
            return None
        matches = [line.strip() for line in output.splitlines() if line.strip()]
        if len(matches) > 1:
            raise InstallerError(f"swap source {source!r} resolves to multiple block devices")
        return matches[0] if matches else None

    def _swap_backing_removal_targets(
        self,
        active: dict[str, str],
        fstab_sources: list[str],
    ) -> tuple[list[str], list[str]]:
        """Identify only configured swapfiles and LVs; leave ordinary partitions intact."""
        files: set[str] = set()
        logical_volumes: set[str] = set()
        configured_sources = set(fstab_sources)
        for source in dict.fromkeys([*active, *fstab_sources]):
            active_type = active.get(source)
            configured = source in configured_sources
            if source.startswith("/") and not source.startswith("/dev/"):
                path = Path(source)
                if path.is_symlink():
                    if active_type == "file" or configured:
                        raise InstallerError(
                            f"configured swapfile is a symlink; refusing to delete its target: {source}"
                        )
                    continue
                if not path.is_file():
                    if active_type == "file":
                        raise InstallerError(f"active swapfile is not a regular file: {source}")
                    if configured and path.exists():
                        raise InstallerError(
                            f"fstab declares {source} as swap but it is not a regular file; refusing cleanup"
                        )
                    continue
                if active_type == "file":
                    files.add(source)
                    continue
                try:
                    signature = self._run(
                        ["/usr/sbin/blkid", "-p", "-s", "TYPE", "-o", "value", source],
                        "verify configured swapfile signature",
                        dangerous=False,
                    )
                except ActionError as exc:
                    if configured:
                        raise InstallerError(
                            f"cannot verify configured swapfile {source}; refusing to remove it"
                        ) from exc
                    continue
                if signature.strip().lower() == "swap":
                    files.add(source)
                elif configured:
                    raise InstallerError(
                        f"fstab declares {source} as swap but it has no swap signature; refusing to delete it"
                    )
                continue

            device = self._resolve_swap_block_source(source)
            if not device:
                continue
            try:
                details = self._run(
                    ["/usr/bin/lsblk", "-no", "TYPE,PATH", device],
                    "classify configured swap block source",
                    dangerous=False,
                )
            except ActionError as exc:
                if active_type:
                    raise InstallerError(f"cannot classify active swap source {source}: {exc}") from exc
                continue
            rows = [line.split(None, 1) for line in details.splitlines() if line.strip()]
            if len(rows) != 1 or len(rows[0]) != 2:
                if active_type:
                    raise InstallerError(f"cannot uniquely classify active swap block source {source}")
                continue
            device_type, device_path = rows[0]
            if device_type.lower() == "lvm":
                if configured and not active_type:
                    try:
                        signature = self._run(
                            ["/usr/sbin/blkid", "-p", "-s", "TYPE", "-o", "value", device_path],
                            "verify configured swap logical-volume signature",
                            dangerous=False,
                        )
                    except ActionError as exc:
                        raise InstallerError(
                            f"cannot verify configured swap logical volume {device_path}; "
                            "refusing to delete it"
                        ) from exc
                    if signature.strip().lower() != "swap":
                        raise InstallerError(
                            f"fstab declares {device_path} as swap but it has no swap signature; "
                            "refusing to delete it"
                        )
                logical_volumes.add(device_path)
        return sorted(files), sorted(logical_volumes)

    def _remove_managed_swap_backings(self) -> None:
        """Disable swap, remove configured files/LVs, and clear stale fstab entries."""
        if self.actions.dry_run:
            self._run(["/usr/sbin/swapoff", "-a"], "disable active swap before replacement", dangerous=True)
            self._persist_fstab([])
            self._mark_step("swap_backing_cleanup", "planned", "remove configured swapfiles and swap logical volumes")
            return

        active = self._active_swap_sources()
        fstab_sources = self._fstab_swap_sources()
        files, logical_volumes = self._swap_backing_removal_targets(active, fstab_sources)
        lvremove = shutil.which("lvremove") if logical_volumes else None
        if logical_volumes and not lvremove:
            raise InstallerError(
                "swap logical volume(s) were found but lvremove is unavailable; install lvm2 or remove them manually"
            )
        if active:
            self._run(
                ["/usr/sbin/swapoff", "-a"],
                "disable active swap before replacing its backing",
                dangerous=True,
            )
            if self._active_swap_sources():
                raise InstallerError("swapoff returned successfully but active swap remains; refusing to remove backing")
        self._persist_fstab([])
        for path in files:
            self.actions.remove_file(path)
        for path in logical_volumes:
            if lvremove is None:
                raise InstallerError(
                    "lvremove became unavailable after swap cleanup preflight; refusing to continue"
                )
            self._run(
                [lvremove, "-y", path],
                f"remove swap logical volume {path}",
                dangerous=True,
                timeout=120,
            )
        detail = f"removed {len(files)} swapfile(s) and {len(logical_volumes)} swap logical volume(s)"
        self._mark_step("swap_backing_cleanup", "success", detail)

    def _persist_fstab(self, entries: list[str]) -> None:
        existing_lines = self._read_fstab_lines()
        retained = [
            line for line in existing_lines
            if not (line.strip() and len(line.split()) >= 3 and line.split()[2].lower() == "swap")
        ]
        content = "\n".join(retained + entries + [""])
        self.actions.write_file("/etc/fstab", content, 0o644)

    def _install_stage2(self) -> None:
        python = shutil.which("python3") or "/usr/bin/python3"
        # installer.py lives at .../debian-install-v2/debian_install_v2/
        # installer.py, so parents[1] is the install directory: it holds the
        # entrypoint debian-install-v2.py and the cli_extended wheel. The unit
        # runs the ENTRYPOINT (never `-m debian_install_v2...`, which bypasses
        # the entrypoint's wheel sys.path setup: LT-F-v1001-01).
        working_directory = str(Path(__file__).resolve().parents[1])
        entrypoint = f"{working_directory}/debian-install-v2.py"
        notifier = f"{working_directory}/debian_install_v2/failure_notify.py"
        env_file = "/etc/vbpub/bootstrap.env"
        # One LoadCredential= line per credential, and NONE in root-storage
        # mode (a literal `LoadCredential=-` logs "Couldn't read inherited
        # credential '-'" on every start: LT-F-v1001-04).
        credential_lines: list[str] = []
        backend = self._notify_backend()
        # vbpub-notify picks its backend from whichever credential file exists,
        # so a re-run that switches backend (or to `none`) must REMOVE the other
        # backend's files, or the helper keeps posting through a stale credential.
        stale = {
            "mattermost": ("telegram_bot_token", "telegram_chat_id"),
            "telegram": ("mattermost_webhook_url",),
        }.get(backend, ("telegram_bot_token", "telegram_chat_id", "mattermost_webhook_url"))
        for directory in ("/etc/vbpub/credentials", f"{self.config.state_dir}/credentials"):
            for name in stale:
                self.actions.remove_file(f"{directory}/{name}")
        if backend == "mattermost":
            self.actions.write_file(
                "/etc/vbpub/credentials/mattermost_webhook_url",
                self.config.mattermost_webhook_url + "\n", 0o600,
            )
            if self.config.credential_mode == "root-storage":
                credential_dir = Path(self.config.state_dir) / "credentials"
                self.actions.write_file(
                    str(credential_dir / "mattermost_webhook_url"),
                    self.config.mattermost_webhook_url + "\n", 0o600,
                )
                credential_note = "root-only Mattermost webhook credential installed"
            else:
                credential_lines = ["mattermost_webhook_url:/etc/vbpub/credentials/mattermost_webhook_url"]
                credential_note = "systemd LoadCredential Mattermost webhook configured"
        elif backend == "telegram":
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
                credential_lines = [
                    "telegram_bot_token:/etc/vbpub/credentials/telegram_bot_token",
                    "telegram_chat_id:/etc/vbpub/credentials/telegram_chat_id",
                ]
                credential_note = "systemd LoadCredential Telegram credentials configured"
        else:
            credential_note = "notifications disabled"
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
            entrypoint=entrypoint,
            output=self.config.stage2_output,
            working_directory=working_directory,
            load_credential_lines="".join(f"LoadCredential={line}\n" for line in credential_lines),
            failed_unit=FAILED_UNIT_NAME.replace("@.", "@%n."),  # %n = the failing unit's own name
        )
        failed_service = BOOTSTRAP_FAILED_SERVICE.format(
            env_file=env_file,
            python=python,
            notifier=notifier,
            output=self.config.stage2_output,
        )
        self.actions.write_file(f"/etc/systemd/system/{FAILED_UNIT_NAME}", failed_service)
        self.actions.write_file("/etc/systemd/system/vbpub-bootstrap-stage2.service", service)
        marker = Path(self.config.state_dir) / "stage1_done"
        if not self.actions.dry_run:
            marker.touch(mode=0o600)
        self._run(["/usr/bin/systemctl", "daemon-reload"], "reload stage2 unit")
        self._run(["/usr/bin/systemctl", "enable", "vbpub-bootstrap-stage2.service"], "enable stage2 unit")
        self._mark_step("stage2_unit", "success", f"{self.config.stage2_output}; {credential_note}")

    def _mark_step(self, name: str, status: str, detail: str = "") -> None:
        """Record a step, and - only when telegram_verbose_progress is on -
        also send a one-line Telegram notification for it. The default
        (non-verbose) path stays silent here; the handful of stage-boundary
        messages are separate explicit _notify() calls, not routed through
        this wrapper.
        """
        if self._state_frozen:
            # The state on disk belongs to another run (see _record_failed_state).
            _LOG.warning("step %s: %s %s (not recorded: foreign state left untouched)", name, status, detail)
        else:
            self.state.mark_step(name, status, detail)
        if self._in_failure_handling:
            # The failure post must be the FIRST (and only) post of a failed
            # run: bookkeeping steps done by the guard stay silent.
            return
        if self.config.telegram_verbose_progress:
            self._notify(
                f"<b>{name}</b>: {status}" + (f" — {detail}" if detail else ""),
                event=f"{name}: {status}" + (f" - {detail}" if detail else ""),
                status={"success": "ok", "failed": "fail"}.get(status, "run"),
            )

    def _initial_event_text(self) -> str:
        """One-line start summary for the Mattermost backend."""
        facts = collect_host_facts(self)
        plan = self.show_plan()
        return (
            f"starting: {facts.get('cpu_count')} cores, {facts.get('memory_total_mib')} MiB, "
            f"{facts.get('boot_mode')}, release {self.release or '?'}, root {plan['root_device']}, "
            f"{len(plan['swap_partitions'])} swap partition(s)"
        )

    def _mattermost_text(self, event: str, status: str, excerpt: str) -> str:
        """Build the Mattermost message (layout lives in notify.format_mattermost_message)."""
        try:
            run_id = "" if self._state_frozen else str(self.state.load().get("run_id") or "")
        except Exception:
            run_id = ""
        return format_mattermost_message(
            host_label=self.config.notify_host_label,
            server_name=platform.node() or "unknown-host",
            run_id=run_id,
            stage=self._notify_stage,
            event=event,
            status=status,
            excerpt=excerpt,
            secrets=(self.config.mattermost_webhook_url, self.config.telegram_bot_token),
        )

    def _notify(self, message: str, *, event: str = "", status: str = "run", excerpt: str = "") -> bool:
        """Send one milestone via the selected backend. Never raises.

        ``message`` is the Telegram (HTML) text; ``event``/``status``/``excerpt``
        feed the Mattermost format (a missing ``event`` falls back to the
        tag-stripped Telegram text). Returns True only when the message was
        actually delivered (False: no backend, dry run, or a failed post).
        """
        backend = self._notify_backend()
        if backend == "none" or self.actions.dry_run:
            return False
        if backend == "mattermost":
            text = event or re.sub(r"<[^>]+>", "", message).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            return post_webhook(
                self.config.mattermost_webhook_url,
                self._mattermost_text(text, status, excerpt),
            )
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        thread_id = None
        try:
            if not self._state_frozen:
                thread_id = self.state.load().get("telegram_thread_id") or None
        except Exception:
            pass
        for index, chunk in enumerate(_split_for_telegram(message)):
            if index > 0:
                time.sleep(0.3)  # basic rate-limit courtesy between multi-part sends
            payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
            if thread_id:
                payload["message_thread_id"] = thread_id
            data = urllib.parse.urlencode(payload).encode("utf-8")
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{urllib.parse.quote(token)}/sendMessage", data=data
            )
            try:
                urllib.request.urlopen(request, timeout=15).close()
            except OSError as exc:
                _LOG.warning("Telegram notification failed: %s", exc)
                return False  # don't send later chunks out of order after a failure
        return True

    #: Confirmed live 2026-09-08 against a real Netcup host: a synchronous
    #: reboot from inside the still-running customScript process strands
    #: netcup's own ServerImageSetupTask forever at its CloudinitWait step
    #: (it never sees cloud-init report completion, because the guest goes
    #: down before that signal fires) -- which then PERMANENTLY LOCKS the
    #: server against any further install-image API call
    #: ("server.lock.error"). scripts/debian-install (v1)'s bootstrap.sh
    #: already named and solved this exact failure mode
    #: (stage1_reboot()/stage2_reboot(): "rebooting inline can strand the
    #: provider task (e.g. Netcup CloudinitWait)") by scheduling a delayed,
    #: detached reboot instead of an inline one, so the script can exit
    #: cleanly and cloud-init can report completion first. This constant
    #: mirrors v1's own default delay.
    _REBOOT_DELAY_SECONDS = 60

    def _reboot(self) -> None:
        if self.config.never_reboot or not self.config.auto_reboot_after_stage1:
            # state.mark_step (silent): the explicit warn notification below is
            # the informative post; _mark_step would duplicate it in verbose mode.
            self.state.mark_step(
                "reboot", "deferred",
                "disabled by configuration" + (f"; {self._upgrade_summary}" if self._upgrade_summary else ""),
            )
            kernel = f" ({self._upgrade_summary})" if self._upgrade_summary else ""
            self._notify(
                "<b>Stage1 complete.</b> Reboot is disabled by configuration - stage2 requires a manual resume."
                + kernel,
                event="complete; reboot disabled, stage2 needs a manual resume" + kernel, status="warn",
            )
            return
        self._mark_step(
            "reboot", "scheduled",
            f"stage2 resumes on next boot (reboot delayed {self._REBOOT_DELAY_SECONDS}s to let cloud-init report completion)",
        )
        # The "scheduled" step post (with the delay detail) is the informative
        # one in verbose mode; the milestone post is sent only when it is silent.
        if not self.config.telegram_verbose_progress:
            summary = f" ({self._upgrade_summary})" if self._upgrade_summary else ""
            self._notify(
                "<b>Stage1 complete.</b> Rebooting into stage2." + summary,
                event="complete; rebooting into stage2" + summary, status="ok",
            )
        # systemd-run schedules a transient, detached unit and returns
        # immediately -- this process (and the customScript/cloud-init
        # runcmd it's a child of) gets to exit normally well before the
        # actual reboot fires. Not a raw shell backgrounded job
        # (`sleep N && systemctl reboot &`, v1's own approach): this
        # codebase's action allowlist deliberately has no path for
        # ad hoc shell invocation at all (HostActions._validate() refuses
        # bash/sh outright), so systemd-run -- a single, non-shell argv,
        # allowlisted like any other command -- is the idiomatic
        # equivalent here.
        self._run(
            [
                "/usr/bin/systemd-run",
                "--on-active", str(self._REBOOT_DELAY_SECONDS),
                "--", "/usr/bin/systemctl", "reboot",
            ],
            "schedule delayed reboot into stage2 (avoids stranding the provider's cloud-init tracking)",
            dangerous=True,
        )

    def _configure_controller_ssh_key(self) -> None:
        pubkey_line = self.config.controller_ssh_pubkey.strip()
        if not pubkey_line:
            self._mark_step("controller_ssh_key", "skipped", "no controller_ssh_pubkey configured")
            return
        # mkdir -p's default mode is not group/other-writable (root's own
        # umask), which is all sshd's StrictModes actually requires -- no
        # need for an explicit chmod (neither `install` nor `chmod` is on
        # actions.py's command allowlist, and shouldn't need to be for this).
        self.actions.mkdir("/root/.ssh")
        authorized_keys = Path("/root/.ssh/authorized_keys")
        existing = "" if self.actions.dry_run or not authorized_keys.is_file() else authorized_keys.read_text(encoding="utf-8")
        if pubkey_line in existing:
            self._mark_step("controller_ssh_key", "success", "already present")
            return
        combined = existing if (not existing or existing.endswith("\n")) else existing + "\n"
        self.actions.write_file(str(authorized_keys), combined + pubkey_line + "\n", 0o600)
        self._mark_step("controller_ssh_key", "success", "controller pubkey installed for stage1/stage2 SSH monitoring")

    def _configure_persistent_ssh_keys(self) -> None:
        """Add configured root login keys without tying them to controller cleanup."""
        keys = self.config.ssh_authorized_keys
        if not keys:
            self._mark_step("persistent_ssh_keys", "skipped", "no persistent root SSH keys configured")
            return
        self.actions.mkdir("/root/.ssh")
        authorized_keys = Path("/root/.ssh/authorized_keys")
        existing = (
            "" if self.actions.dry_run or not authorized_keys.is_file()
            else authorized_keys.read_text(encoding="utf-8")
        )
        existing_lines = existing.splitlines()
        added = [line for line in keys if line not in existing_lines]
        if added:
            combined = existing if (not existing or existing.endswith("\n")) else existing + "\n"
            self.actions.write_file(
                str(authorized_keys), combined + "".join(f"{line}\n" for line in added), 0o600
            )
        self._mark_step(
            "persistent_ssh_keys", "success",
            f"{len(keys)} configured key(s); {len(added)} added; retained after stage two",
        )

    def _controller_key_retained(self) -> bool:
        return bool(
            self.config.controller_ssh_pubkey.strip()
            and self.config.retain_controller_ssh_key
        )

    def _remove_controller_ssh_key(self) -> None:
        # Last step of stage2, once everything else has already succeeded --
        # no further controller access is needed. Removes only the exact
        # line _configure_controller_ssh_key() installed, so the operator's
        # own persistent key (however it got there) is never touched.
        #
        # Deliberately NOT called from install()'s/resume()'s own failure
        # handlers (adversarial review finding, 2026-09-08): if stage1 or
        # stage2 fails partway through, the ephemeral key staying in
        # authorized_keys is exactly what lets the controller (or a human)
        # SSH in and diagnose the failure -- removing it on failure would
        # cut off the one access path useful for debugging a broken run.
        # It's removed only on the clean-success path, once there's nothing
        # left to diagnose.
        pubkey_line = self.config.controller_ssh_pubkey.strip()
        if not pubkey_line:
            return
        if self.actions.dry_run:
            self._mark_step("controller_ssh_key_removed", "success", "dry-run: would remove controller pubkey")
            return
        authorized_keys = Path("/root/.ssh/authorized_keys")
        if not authorized_keys.is_file():
            self._mark_step("controller_ssh_key_removed", "skipped", "authorized_keys not present")
            return
        lines = authorized_keys.read_text(encoding="utf-8").splitlines()
        # D2: besides this run's own key, drop EVERY installer-marked ephemeral
        # key (stale ones from earlier failed runs kept for diagnosis). A key
        # without the marker is never touched.
        remaining = [
            line for line in lines
            if line.strip() != pubkey_line and not self._is_ephemeral_controller_key(line)
        ]
        if len(remaining) == len(lines):
            self._mark_step("controller_ssh_key_removed", "skipped", "controller pubkey not found (already removed?)")
            return
        content = "\n".join(remaining) + ("\n" if remaining else "")
        self.actions.write_file(str(authorized_keys), content, 0o600)
        detail = "no further controller access needed"
        stale = len(lines) - len(remaining) - sum(1 for line in lines if line.strip() == pubkey_line)
        if stale:
            detail += f"; also removed {stale} stale {CONTROLLER_KEY_MARKER}* key(s) from earlier runs"
        self._mark_step("controller_ssh_key_removed", "success", detail)

    @staticmethod
    def _is_ephemeral_controller_key(line: str) -> bool:
        """An authorized_keys line carrying the installer's comment marker.

        The reserved marker must be the first comment token. A later comment
        word that happens to start with it is not enough to classify or remove
        an otherwise user-owned persistent key.
        """
        fields = line.split()
        return len(fields) >= 3 and fields[2].startswith(CONTROLLER_KEY_MARKER)

    def _prune_stale_controller_keys(self) -> None:
        """retain_controller_ssh_key=true: keep ONLY this run's key, drop stale
        ephemeral ones from earlier failed runs. Best-effort: never fails a
        successful install (a problem is recorded as a 'warned' step)."""
        pubkey_line = self.config.controller_ssh_pubkey.strip()
        if not pubkey_line or self.actions.dry_run:
            return
        try:
            authorized_keys = Path("/root/.ssh/authorized_keys")
            if not authorized_keys.is_file():
                return
            lines = authorized_keys.read_text(encoding="utf-8").splitlines()
            remaining = [
                line for line in lines
                if line.strip() == pubkey_line or not self._is_ephemeral_controller_key(line)
            ]
            if len(remaining) == len(lines):
                return
            self.actions.write_file(str(authorized_keys), "\n".join(remaining) + "\n", 0o600)
            self._mark_step(
                "controller_ssh_key_pruned", "success",
                f"removed {len(lines) - len(remaining)} stale {CONTROLLER_KEY_MARKER}* key(s) from earlier runs; current key kept",
            )
        except Exception as exc:
            _LOG.warning("could not prune stale controller keys: %s", exc)
            try:
                self._mark_step("controller_ssh_key_pruned", "warned", f"could not prune stale keys: {self._failure_tail(exc)}")
            except Exception:
                pass

    _BOOTSTRAP_DIR = Path("/root")

    def _secure_bootstrap_files(self) -> None:
        """chmod 0600 the provider's customScript and its output files.

        LT-F-v1001-07: /root/custom_script contains the webhook URL and its
        mode was never set by us. Missing files are fine (not every provider
        path creates them).
        """
        if self.actions.dry_run:
            return
        root = self._BOOTSTRAP_DIR
        candidates = {root / "custom_script", Path(self.config.stage2_output)}
        candidates.update(root.glob("custom_script.output*"))
        for path in sorted(candidates):
            try:
                if path.is_file() and not path.is_symlink():
                    path.chmod(0o600)
            except OSError as exc:
                _LOG.warning("could not chmod 0600 %s: %s", path, exc)

    def _stage1(self) -> None:
        self._secure_bootstrap_files()
        self._configure_persistent_ssh_keys()
        self._configure_controller_ssh_key()
        # LT-F-v1001-13: persistent journald FIRST (right after the controller
        # key, which is what keeps a failed host reachable), so the rest of
        # stage1 -- apt, docker, upgrade, the stage1 reboot decision -- is in
        # the persistent journal. The step is a config-file write + a journald
        # restart with no dependency on packages, users or apt state.
        if self.config.run_journald_config:
            self._configure_journald()
        # Held regardless of run_apt_auto_upgrade (Debian ships them enabled);
        # _release_apt_timers() restores them at the end of stage2.
        self._hold_apt_timers()
        if self.config.run_apt_config:
            self._configure_apt()
        self._packages(["python3"], "stage1")
        self._install_notify_helper()
        if self.config.run_user_config:
            self._configure_users()
        if self.config.run_docker_install:
            self._install_docker()
            self._configure_docker_daemon()
            if self.config.run_docker_cleanup:
                self._configure_docker_cleanup()
        if self.config.run_apt_auto_upgrade:
            self._configure_apt_auto_upgrade()
        # LT-UPG: upgrade BEFORE the root-shrink hook install and the reboot.
        self._upgrade_at_install()
        # First validate any root shrink while the old swap backing is still
        # present. In particular, do not let deleting a swapfile lower the
        # measured filesystem usage and hide blocks that may be interleaved
        # with ordinary data. Only after this conservative check may we remove
        # configured files/LVs; the retained pre-root partition is preserved.
        root_shrink_plan = None
        if self.config.swap_policy == "on":
            candidate_plan = self._resolve_root_shrink_plan(validate_shrink_safety=False)
            safety_facts = (
                self._root_shrink_safety_facts()
                if candidate_plan[2]
                else None
            )
            # Validate the same base/benchmark layout before deleting swapfiles
            # can change the filesystem-usage evidence used by shrink safety.
            root_shrink_plan = self._resolve_root_shrink_plan(
                root_safety_facts=safety_facts
            )
            self._remove_managed_swap_backings()
        # Resolve any remaining plan before writing guards: an unsupported
        # cap must refuse without leaving either guard or a shrink hook.
        root_shrink_plan = root_shrink_plan or self._resolve_root_shrink_plan()
        # Disable boot-time root growth before installing the initramfs hook;
        # update-initramfs can take long enough for an unplanned reboot.
        self._configure_root_cap_boot_guards()
        self._plan_root_shrink(root_shrink_plan)
        self._install_stage2()
        self._reboot()

    def _swap_partitions_already_planned_in_table(self) -> bool:
        """Whether the configured swap policy already matches live disk geometry."""
        if self.actions.dry_run:
            return False
        disk_sectors, root_start, root_size = self._disk_facts()
        live = self._live_partition_entries(disk_sectors, root_start, root_size)
        existing_swap = {
            number: entry for number, entry in live.items()
            if self._is_swap_partition(entry)
        }
        if self.config.swap_policy == "off":
            if existing_swap:
                return False
            if self._disk_spec().partition_count:
                before_root = self._partitions_before_root(live, root_start)
                for number, start, size in self._data_area_layout([], before_root):
                    entry = live.get(number, {})
                    if not (
                        self._is_data_partition(entry)
                        and entry.get("start") == str(start) and entry.get("size") == str(size)
                    ):
                        return False
            self._mark_step("partitions", "success", "no swap-type partitions remain on the root disk")
            return True

        planned_partitions, _target_root_size = self._plan_swap_partitions()
        before_root = self._partitions_before_root(live, root_start)
        retained = self._swap_partitions_before_root(live, root_start)
        expected = {
            number: {
                "start": str(entry["start"]), "size": str(entry["size"]),
                "type": (entry.get("type") or "").lower(),
            }
            for number, entry in retained.items()
        }
        expected.update({
            number: {
                "start": str(start), "size": str(size), "type": SWAP_TYPE_GUID,
            }
            for number, (start, size) in zip(
                self._tail_swap_numbers(len(planned_partitions), before_root), planned_partitions
            )
        })
        actual = {
            number: {
                "start": entry.get("start"), "size": entry.get("size"),
                "type": (entry.get("type") or "").lower(),
            }
            for number, entry in existing_swap.items()
        }
        if actual != expected:
            return False
        for number, start, size in self._data_area_layout(planned_partitions, before_root):
            entry = live.get(number, {})
            if not (
                self._is_data_partition(entry)
                and entry.get("start") == str(start) and entry.get("size") == str(size)
            ):
                return False
        self._mark_step("partitions", "success", "swap partitions already match the requested count and geometry")
        return True

    def _cleanup_cloud_init_cache(self) -> None:
        """Apply the configured cloud-init cache policy after install work succeeds."""
        mode = self.config.cloud_init_cleanup_mode
        if mode == "retain":
            return

        step = "cloud_init_cache_cleanup"
        self._mark_step(step, "run", mode)
        try:
            if mode == "userdata-only":
                for path in CLOUD_INIT_USER_DATA_CACHE_FILES:
                    self.actions.remove_file(path)
            else:
                argv = ["/usr/bin/cloud-init", "clean"]
                if mode == "clean-all":
                    # Preserve generated SSH/network/fstab configuration and
                    # machine identity; --seed only clears the guest-side cache,
                    # not an attached NoCloud ISO.
                    argv.extend(["--logs", "--seed"])
                self._run(argv, f"cloud-init clean ({mode})", dangerous=True)
        except Exception:
            self._mark_step(step, "failed", mode)
            raise
        self._mark_step(step, "success", mode)

    def _configure_root_cap_boot_guards(self) -> None:
        """Keep cloud-init and initramfs from growing a capped root later."""
        if not self._root_guarded():
            return
        self.actions.write_file(
            "/etc/cloud/cloud.cfg.d/99-debian-install-v2-root-cap.cfg",
            "growpart: {mode: off}\nresize_rootfs: false\n",
        )
        self.actions.write_file("/etc/growroot-disabled", "")

    def _stage2(self) -> None:
        self._packages(["e2fsprogs", "util-linux"], "stage2")
        # True iff Case B's own hook already wrote the swap partitions as
        # part of shrinking root -- _apply_known_swap_shape() must then be
        # skipped, not re-run (see _verify_and_apply_root_shrink()'s docstring).
        swap_partitions_already_written = self._verify_and_apply_root_shrink()
        # Resume-safety: _verify_and_apply_root_shrink() only returns True on
        # its FIRST pass after the hook succeeded (it then marks root_shrink
        # "success"); on a re-run -- e.g. after the io benchmark's deliberate
        # cleanup-failure stop -- it returns False although the swap
        # partitions are already in the live table. Derive it from the table.
        if not swap_partitions_already_written:
            swap_partitions_already_written = self._swap_partitions_already_planned_in_table()
        # Never rewrite a partition table that still backs active swap. The
        # selected plan first disables or removes managed swap, then writes it.
        self._disable_active_swap()
        if self.config.run_zswap_config:
            self._configure_zswap()
        if self.config.run_cgroup2_config:
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
        self._ensure_disk_transaction_manifest()
        # Run against the final, already-planned raw partition before any data
        # volume or swap partition is formatted or activated.
        self._run_io_benchmark()
        # The iocost model consumes the coefficients produced by the install
        # benchmark. Configure it only after that result is available.
        self._configure_iocost()
        self._grow_root_to_plan()
        self._configure_root_cap_boot_guards()
        self._activate_swap_partitions()
        if self._data_area_cfg().kind != "none":
            self._configure_data_area()
        self._health_gate_swap_devices()
        # While the host is still idle (apt timers not released yet, no workload
        # started): advisory, never stops the install.
        self._run_cpu_benchmark()
        self._release_apt_timers()
        # Set this before `cloud-init clean`: an attached NoCloud ISO remains a
        # source after clean and can otherwise replay its original runcmd.
        self.actions.write_file(INSTALL_COMPLETE_MARKER, "schema_version=1\n", 0o600)
        # Run last so failures in the install work above retain cloud-init's
        # user-data and cache for diagnosis.
        self._cleanup_cloud_init_cache()
        self.state.save(phase="done", status="success")
        # _remove_controller_ssh_key() deliberately does NOT happen here --
        # see resume(), which calls it only after the stage2_done marker
        # file exists. See that comment for why.
