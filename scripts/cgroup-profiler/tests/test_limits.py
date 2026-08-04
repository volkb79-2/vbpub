"""Tests for lib.limits: per-cgroup declared values and chain resolution.

cgroup_root (conftest.py) is shaped specifically for this module: dev.slice
caps IO but not memory, its tier children cap memory but not each other's
siblings, and wings.slice/wings-prod.slice/<leaf> is the protection case —
the leaf redeclares memory.min but leaves memory.low at 0 while its parent
promises 6G low, which is exactly the "discarded without recursiveprot"
scenario effective() must report.
"""

from __future__ import annotations

from conftest import cgroup_files, write_cgroup

from lib import limits, util


GIB = 1024**3
MIB = 1024**2

LEAF = "/wings.slice/wings-prod.slice/docker-" + "b" * 64 + ".scope"


# ── read_limits ──────────────────────────────────────────────────────────────


def test_read_limits_uncapped_defaults(cgroup_root):
    ls = limits.read_limits(str(cgroup_root / "dev.slice"))
    assert ls.memory_max is None  # "max" sentinel
    assert ls.memory_high is None
    assert ls.memory_min == 0
    assert ls.memory_low == 0
    assert ls.cpu_quota_usec is None  # "max 100000"
    assert ls.cpu_period_usec == 100000
    assert ls.cpu_weight == 100
    assert ls.io_max == {
        "254:0": {"rbps": 1289421332, "wbps": 309765974, "riops": 54103, "wiops": 35217}
    }
    assert ls.io_weight == 100
    assert ls.io_bfq_weight is None  # file not present in the fixture


def test_read_limits_declared_caps(cgroup_root):
    ls = limits.read_limits(str(cgroup_root / "dev.slice" / "dev-interactive.slice"))
    assert ls.memory_low == 2 * GIB
    assert ls.memory_high == 5 * GIB
    assert ls.memory_max == 8 * GIB
    assert ls.cpu_weight == 200


def test_read_limits_vanished_cgroup_is_all_absent(tmp_path):
    ls = limits.read_limits(str(tmp_path / "does" / "not" / "exist"))
    assert ls.memory_max is None
    assert ls.memory_min is None
    assert ls.io_max == {}
    assert ls.pids_max is None


def test_read_limits_zswap_writeback_bool(tmp_path):
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    (leaf / "memory.zswap.writeback").write_text("0\n")
    assert limits.read_limits(str(leaf)).memory_zswap_writeback is False

    (leaf / "memory.zswap.writeback").write_text("1\n")
    assert limits.read_limits(str(leaf)).memory_zswap_writeback is True

    (leaf / "memory.zswap.writeback").unlink()
    assert limits.read_limits(str(leaf)).memory_zswap_writeback is None


def test_read_limits_zswap_writeback_malformed_value_is_none(tmp_path):
    # A value that is neither "0" nor "1" — a kernel field this code does
    # not understand must read back as "unknown", not as a guessed bool.
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    (leaf / "memory.zswap.writeback").write_text("2\n")
    assert limits.read_limits(str(leaf)).memory_zswap_writeback is None

    (leaf / "memory.zswap.writeback").write_text("  \n")  # whitespace only
    assert limits.read_limits(str(leaf)).memory_zswap_writeback is None


def test_default_weight_device_only_override_without_default_line(tmp_path):
    # A weight file with only a per-device override and no "default <n>"
    # line at all — malformed relative to what this parser expects, and
    # must come back as "not read", not as a bogus device-id-as-weight.
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    (leaf / "io.weight").write_text("8:0 200\n")
    assert limits.read_limits(str(leaf)).io_weight is None


def test_default_weight_whitespace_only_file(tmp_path):
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    (leaf / "io.weight").write_text("   \n")
    assert limits.read_limits(str(leaf)).io_weight is None


# ── mount_flags ──────────────────────────────────────────────────────────────


def test_mount_flags_without_recursiveprot(proc_root):
    flags = limits.mount_flags(str(proc_root))
    assert "memory_recursiveprot" not in flags
    assert "rw" in flags


def test_mount_flags_with_recursiveprot(mounts_recursiveprot):
    flags = limits.mount_flags(str(mounts_recursiveprot))
    assert "memory_recursiveprot" in flags


def test_mount_flags_missing_mounts_file_is_empty_set(tmp_path):
    empty = tmp_path / "no-proc"
    empty.mkdir()
    assert limits.mount_flags(str(empty)) == set()


# ── effective(): caps, tightest-wins with attribution ────────────────────────


def test_effective_leaf_is_the_tightest(cgroup_root):
    cgroup = "/dev.slice/dev-interactive.slice"
    eff = limits.effective(cgroup, root=str(cgroup_root))
    assert eff.memory_max == 8 * GIB
    assert eff.memory_max_by == cgroup


def test_effective_mid_chain_ancestor_is_the_tightest(cgroup_root):
    # The docker scope under dev-background.slice declares no memory caps of
    # its own; dev-background.slice (its parent, not the leaf) is what binds.
    cgroup = "/dev.slice/dev-background.slice/docker-" + "a" * 64 + ".scope"
    eff = limits.effective(cgroup, root=str(cgroup_root))
    assert eff.memory_max == 8 * GIB
    assert eff.memory_max_by == "/dev.slice/dev-background.slice"
    assert eff.memory_high == 6 * GIB
    assert eff.memory_high_by == "/dev.slice/dev-background.slice"


def test_effective_nothing_capped(cgroup_root):
    eff = limits.effective("/system.slice", root=str(cgroup_root))
    assert eff.memory_max is None
    assert eff.memory_max_by is None
    assert eff.memory_high is None
    assert eff.memory_high_by is None
    assert eff.cpu_cores is None
    assert eff.cpu_cores_by is None


def test_effective_io_max_attributed_to_declaring_ancestor(cgroup_root):
    cgroup = "/dev.slice/dev-interactive.slice"
    eff = limits.effective(cgroup, root=str(cgroup_root))
    assert eff.io_max == {
        "254:0": {"rbps": 1289421332, "wbps": 309765974, "riops": 54103, "wiops": 35217}
    }
    assert eff.io_max_by == {"254:0": "/dev.slice"}


def test_effective_swap_max_tightest_wins(cgroup_root):
    cgroup = "/dev.slice/dev-background.slice/docker-" + "a" * 64 + ".scope"
    eff = limits.effective(cgroup, root=str(cgroup_root))
    assert eff.memory_swap_max == 48 * GIB
    assert eff.memory_swap_max_by == "/dev.slice/dev-background.slice"


def test_effective_cpu_cores_mid_chain_ancestor(tmp_path):
    # A minimal tree built with the same conftest helpers: leaf uncapped,
    # parent caps to 2 cores, root uncapped.
    write_cgroup(tmp_path, "", cgroup_files())
    write_cgroup(tmp_path, "parent", cgroup_files(cpu_max="200000 100000"))
    write_cgroup(tmp_path, "parent/leaf", cgroup_files())

    eff = limits.effective("/parent/leaf", root=str(tmp_path))
    assert eff.cpu_cores == 2.0
    assert eff.cpu_cores_by == "/parent"


def test_effective_pids_max_tightest_wins(tmp_path):
    write_cgroup(tmp_path, "", cgroup_files())
    write_cgroup(tmp_path, "parent", cgroup_files())
    (tmp_path / "parent" / "pids.max").write_text("50\n")
    write_cgroup(tmp_path, "parent/leaf", cgroup_files())

    eff = limits.effective("/parent/leaf", root=str(tmp_path))
    assert eff.pids_max == 50


def test_effective_io_max_second_ancestor_not_tighter_is_not_credited(tmp_path):
    # Two ancestors both declare io.max for the SAME device+field: the
    # leaf-closer one (dev-background.slice-equivalent "parent") is already
    # tighter, so the higher-up "grandparent" declaration for the same field
    # must not overwrite it or steal the attribution — this is the branch
    # that never fires when only one ancestor ever declares io.max.
    write_cgroup(tmp_path, "", cgroup_files())
    write_cgroup(
        tmp_path, "grandparent", cgroup_files(io_max="8:0 rbps=9000000 wbps=500")
    )
    write_cgroup(
        tmp_path,
        "grandparent/parent",
        cgroup_files(io_max="8:0 rbps=1000000 wbps=999999999"),
    )
    write_cgroup(tmp_path, "grandparent/parent/leaf", cgroup_files())

    eff = limits.effective("/grandparent/parent/leaf", root=str(tmp_path))
    # rbps: parent's 1000000 is already tighter than grandparent's 9000000,
    # so grandparent's later, looser rbps value must be skipped outright
    # (the branch that never fires with only one io.max-declaring ancestor).
    assert eff.io_max["8:0"]["rbps"] == 1000000
    # wbps: grandparent's 500 *is* tighter than parent's 999999999, so it
    # does overwrite — proving the skip above was a real decision, not a
    # merge that happens to never update anything.
    assert eff.io_max["8:0"]["wbps"] == 500
    # Per-device attribution is last-write-wins while scanning leaf-first;
    # since grandparent made the final update (on wbps), it is credited.
    assert eff.io_max_by["8:0"] == "/grandparent"


def test_read_limits_io_max_independent_of_io_stat_activity(tmp_path):
    # io.max (a cap) and io.stat (activity) are separate files with no
    # cross-reference in the kernel or in this reader: a device can be
    # capped without ever showing activity (9:0 here), and a device can be
    # active without any cap at all (254:0 here) — read_limits must not
    # filter io_max by what happens to appear in io.stat, or vice versa.
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    (leaf / "io.max").write_text("9:0 rbps=5000000\n")
    (leaf / "io.stat").write_text("254:0 rbytes=1024 wbytes=0 rios=1 wios=0\n")
    ls = limits.read_limits(str(leaf))
    assert ls.io_max == {"9:0": {"rbps": 5000000}}
    assert "254:0" not in ls.io_max


def test_effective_protection_absent_ancestor_is_not_coerced_to_zero(tmp_path):
    # The real cgroupfs root has no memory.min/memory.low files at all (they
    # are meaningless for a cgroup with no parent) — it must be *excluded*
    # from the strict-mode calculation, not treated as an explicit "0
    # protection" declaration the way memory.min=0 legitimately is. This is
    # the crux of "absent is not zero" applied to protections rather than
    # caps: getting it wrong makes every leaf's strict protection collapse
    # to 0 regardless of what it actually declares.
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    (leaf / "memory.min").write_text(f"{5 * GIB}\n")
    (leaf / "memory.low").write_text(f"{3 * GIB}\n")
    # tmp_path itself (the chain's root ancestor) has no memory.min/low file.

    eff = limits.effective("/leaf", root=str(tmp_path))
    assert eff.strict_min == 5 * GIB
    assert eff.strict_low == 3 * GIB
    assert eff.recursive_min == 5 * GIB
    assert eff.recursive_low == 3 * GIB


def test_effective_protection_declared_zero_vs_absent_differ(tmp_path):
    # Same leaf declaration as above, but this time the root *does*
    # explicitly declare memory.min=0 (a real, meaningful "no protection"
    # statement) rather than lacking the file. That declared 0 must drag
    # strict_min down to 0, proving the two states are not interchangeable.
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    (leaf / "memory.min").write_text(f"{5 * GIB}\n")
    (tmp_path / "memory.min").write_text("0\n")

    eff = limits.effective("/leaf", root=str(tmp_path))
    assert eff.strict_min == 0
    assert eff.recursive_min == 5 * GIB  # recursive still skips the declared 0


# ── effective(): protections, strict vs recursive ────────────────────────────


def test_effective_protection_strict_mode_discards_ancestor_low(cgroup_root, proc_root):
    flags = limits.mount_flags(str(proc_root))  # no memory_recursiveprot
    eff = limits.effective(LEAF, root=str(cgroup_root), flags=flags)

    assert eff.protection_mode == "strict"
    # The leaf's own memory.low=0 collapses the strict reading to 0, even
    # though wings-prod.slice (the immediate parent) promises 6G.
    assert eff.strict_low == 0
    assert eff.recursive_low == 6 * GIB
    assert any(
        "memory.low" in w and "/wings.slice/wings-prod.slice" in w for w in eff.warnings
    ), eff.warnings


def test_effective_protection_recursive_mode_no_warning(cgroup_root, mounts_recursiveprot):
    flags = limits.mount_flags(str(mounts_recursiveprot))
    eff = limits.effective(LEAF, root=str(cgroup_root), flags=flags)

    assert eff.protection_mode == "recursive"
    assert eff.warnings == []


def test_effective_protection_min_values(cgroup_root, proc_root):
    flags = limits.mount_flags(str(proc_root))
    eff = limits.effective(LEAF, root=str(cgroup_root), flags=flags)
    # declared over the chain: leaf 6G, wings-prod.slice 5500M, wings.slice
    # 8G, root 0 -> strict (plain min) is dragged to 0 by the root's own
    # declared 0; recursive (min of the nonzero declarations) is 5500M.
    assert eff.recursive_min == 5500 * MIB
    assert eff.strict_min != eff.recursive_min


def test_effective_no_flags_defaults_to_strict(cgroup_root):
    # flags=None (caller never looked up mount_flags) must not silently
    # assume the more permissive recursive mode.
    eff = limits.effective(LEAF, root=str(cgroup_root))
    assert eff.protection_mode == "strict"


# ── fingerprint / describe ────────────────────────────────────────────────────


def test_fingerprint_stable_and_sensitive(cgroup_root, proc_root):
    flags = limits.mount_flags(str(proc_root))
    eff_a = limits.effective(LEAF, root=str(cgroup_root), flags=flags)
    eff_b = limits.effective(LEAF, root=str(cgroup_root), flags=flags)
    assert limits.fingerprint(eff_a) == limits.fingerprint(eff_b)

    eff_a.memory_max = (eff_a.memory_max or 0) + 1
    assert limits.fingerprint(eff_a) != limits.fingerprint(eff_b)


def test_describe_reports_bound_ancestor_and_warning(cgroup_root, proc_root):
    flags = limits.mount_flags(str(proc_root))
    eff = limits.effective(LEAF, root=str(cgroup_root), flags=flags)
    lines = limits.describe(eff)
    assert any("memory.max" in line for line in lines)
    assert any("/wings.slice/wings-prod.slice" in line and "memory.low" in line for line in lines)


def test_describe_uncapped_cgroup_says_unlimited(cgroup_root):
    eff = limits.effective("/system.slice", root=str(cgroup_root))
    lines = limits.describe(eff)
    assert any("unlimited" in line for line in lines if line.startswith("cpu"))
    assert any(line == "memory.max: max" for line in lines)


def test_describe_recursive_mode_shows_recursive_values_not_strict(cgroup_root, mounts_recursiveprot):
    flags = limits.mount_flags(str(mounts_recursiveprot))
    eff = limits.effective(LEAF, root=str(cgroup_root), flags=flags)
    lines = limits.describe(eff)
    recursive_lines = [l for l in lines if l.startswith("memory protection")]
    assert recursive_lines == [
        f"memory protection (recursive): min={util.fmt_bytes(eff.recursive_min)} "
        f"low={util.fmt_bytes(eff.recursive_low)}"
    ]
    # Confirms this genuinely differs from what strict mode would have shown
    # (line content asserts recursive_low, not strict_low, which is 0 here).
    assert eff.strict_low != eff.recursive_low


def test_describe_cpu_capped_with_attribution(tmp_path):
    write_cgroup(tmp_path, "", cgroup_files())
    write_cgroup(tmp_path, "parent", cgroup_files(cpu_max="200000 100000"))
    write_cgroup(tmp_path, "parent/leaf", cgroup_files())
    eff = limits.effective("/parent/leaf", root=str(tmp_path))
    lines = limits.describe(eff)
    assert "cpu: 2.00 cores (bound by /parent)" in lines


def test_describe_cpu_capped_without_attribution(tmp_path):
    # A hand-built Effective where the cores figure is known but its source
    # ancestor is not — describe() must degrade gracefully, not KeyError or
    # print "(bound by None)". Nothing in the Effective type guarantees
    # cpu_cores and cpu_cores_by travel together; only effective() happens
    # to keep them in sync, and describe() must not assume its caller is
    # always effective()'s own output.
    write_cgroup(tmp_path, "", cgroup_files())
    write_cgroup(tmp_path, "parent", cgroup_files(cpu_max="200000 100000"))
    write_cgroup(tmp_path, "parent/leaf", cgroup_files())
    eff = limits.effective("/parent/leaf", root=str(tmp_path))
    eff.cpu_cores_by = None
    lines = limits.describe(eff)
    assert "cpu: 2.00 cores" in lines
    assert not any("bound by" in l and l.startswith("cpu:") for l in lines)


def test_describe_io_max_with_attribution(cgroup_root):
    eff = limits.effective("/dev.slice/dev-interactive.slice", root=str(cgroup_root))
    lines = limits.describe(eff)
    assert any(
        line.startswith("io.max 254:0") and "(bound by /dev.slice)" in line
        for line in lines
    )


def test_describe_io_max_without_attribution(cgroup_root):
    # Same reasoning as the cpu case: io_max_by is a separate field from
    # io_max, and describe() must not assume every device in one has an
    # entry in the other.
    eff = limits.effective("/dev.slice/dev-interactive.slice", root=str(cgroup_root))
    eff.io_max_by = {}
    lines = limits.describe(eff)
    assert "io.max 254:0: rbps=1289421332, riops=54103, wbps=309765974, wiops=35217" in lines
