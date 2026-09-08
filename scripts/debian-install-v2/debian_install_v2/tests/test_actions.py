from __future__ import annotations

import pytest

from debian_install_v2.actions import ActionError, HostActions


def test_relative_command_is_refused_before_execution():
    actions = HostActions(dry_run=True)
    with pytest.raises(ActionError, match="absolute executable"):
        actions.run(["reboot"])
    assert actions.planned == []


def test_unallowlisted_executable_and_option_are_refused():
    actions = HostActions(dry_run=True)
    with pytest.raises(ActionError, match="not on the action allowlist"):
        actions.run(["/bin/rm", "-rf", "/"])
    with pytest.raises(ActionError, match="not allowlisted"):
        actions.run(["/usr/bin/sfdisk", "--apocalypse", "/dev/vda"])
    assert not any(action.argv[0] == "/bin/rm" for action in actions.planned)


def test_host_facts_new_allowlist_entries_accept_only_their_declared_flags():
    actions = HostActions(dry_run=True)
    # Accepted (host_facts.py's exact call shapes):
    actions.run(["/usr/bin/lspci"])
    actions.run(["/usr/bin/systemd-detect-virt"])
    actions.run(["/usr/sbin/ip", "-j", "addr", "show"])
    actions.run(["/usr/sbin/ip", "-j", "route", "show", "default"])
    actions.run(["/usr/sbin/sshd", "-T"])
    assert len(actions.planned) == 5
    # ip/sshd declare a specific flag set, so an out-of-set flag is refused
    # (unlike lspci/systemd-detect-virt, which - like curl/dd/blkid elsewhere
    # in this allowlist - declare an empty set, meaning "no flag validation
    # for this simple/harmless command", not "zero flags accepted").
    with pytest.raises(ActionError, match="not allowlisted"):
        actions.run(["/usr/sbin/ip", "-6", "addr", "show"])
    with pytest.raises(ActionError, match="not allowlisted"):
        actions.run(["/usr/sbin/sshd", "-t"])


def test_dry_run_records_but_does_not_execute():
    actions = HostActions(dry_run=True)
    actions.run(["/usr/sbin/swapoff", "-a"], description="disable existing swap before formatting fresh devices", dangerous=True)
    actions.write_file("/tmp/vbpub-dry-run-test", "planned")
    assert [action.description for action in actions.planned] == [
        "disable existing swap before formatting fresh devices",
        "write /tmp/vbpub-dry-run-test",
    ]
    from pathlib import Path
    assert not Path("/tmp/vbpub-dry-run-test").exists()
