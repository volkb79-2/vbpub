"""Hermetic tests for the explicit disk plan (`partitioning`): schema, pool planner, LV planner.

Pure arithmetic, no devices. The installer-level behaviour (numbering, sfdisk plan, stage-two commands)
is in test_data_area.py; the real loop-device proof is in test_data_area_vm.py.
"""

from __future__ import annotations

import random

import pytest

from debian_install_v2 import layout_policy as lp
from debian_install_v2.config import ConfigError, load_config

S = lp.SECTORS_PER_GIB
BOOT_END = 2_500_608  # saved v1001 root start after ESP + /boot (1.19 GiB), 1 MiB aligned


def vol(**kw):
    return {"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW", **kw}


def parts(*volumes, root="*", reserve=0):
    return {
        "root": root, "reserve_gb": reserve, "iobench_raw_gb": False,
        "data": {"kind": "partitions", "volumes": list(volumes)},
    }


def pool(partitioning, *, disk_gib=512, swap_gib=16, root_start=BOOT_END, current=3 * S, min_root=0):
    spec = lp.disk_spec(partitioning)
    disk = disk_gib * S
    pool_end = ((disk - 2048 - swap_gib * S) // 2048) * 2048
    return lp.plan_pool(spec, root_start=root_start, pool_end=pool_end, root_current_size=current,
                        min_root_sectors=min_root), pool_end


# ---------------------------------------------------------------- swap-last (unchanged policy)

def test_swap_in_the_middle_is_refused():
    disk = 512 * S
    root = (2048 + 1220608, 40 * S)
    swap = [(root[0] + root[1] + i * 4 * S, 4 * S) for i in range(4)]
    cow = (swap[-1][0] + swap[-1][1], disk - (swap[-1][0] + swap[-1][1]) - 2048)
    with pytest.raises(lp.LayoutPolicyError, match="last allocation"):
        lp.validate_swap_last(swap, [root, cow], disk)


def test_swap_with_free_space_after_it_is_refused():
    with pytest.raises(lp.LayoutPolicyError, match="END of the disk"):
        lp.validate_swap_last([(100 * S, 16 * S)], [(2048, 50 * S)], 512 * S)


def test_swap_end_slack_boundary():
    disk = 512 * S
    size = 16 * S
    lp.validate_swap_last([(disk - lp.SWAP_END_SLACK_SECTORS - size, size)], [(2048, 100 * S)], disk)
    with pytest.raises(lp.LayoutPolicyError, match="END of the disk"):
        lp.validate_swap_last([(disk - lp.SWAP_END_SLACK_SECTORS - 1 - size, size)], [(2048, 100 * S)], disk)
    start = disk - lp.SWAP_END_SLACK_SECTORS - size
    lp.validate_swap_last([(start, size)], [(2048, start - 2048)], disk)
    with pytest.raises(lp.LayoutPolicyError, match="last allocation"):
        lp.validate_swap_last([(start, size)], [(2048, start - 2048 + 1)], disk)


# ---------------------------------------------------------------- schema

@pytest.mark.parametrize(
    ("partitioning", "message"),
    [
        ({"bogus": 1}, "unknown key"),
        ({"root": "fast"}, r"size must be an integer GiB value"),
        ({"root": 0}, "from 1 to"),
        ({"root": "0%"}, r"size must be an integer GiB value"),
        ({"root": "101%"}, r"size must be an integer GiB value"),
        ({"root": "12.345%"}, r"size must be an integer GiB value"),
        ({"root": True}, r"size must be an integer GiB value"),
        ({"reserve_gb": -1}, "reserve_gb"),
        ({"reserve_gb": "all"}, "reserve_gb"),
        ({"root": "*", "reserve_gb": "*"}, r'exactly one "\*"'),
        ({"data": "x"}, "must be an object"),
        ({"data": {"kind": "zfs", "volumes": [vol()]}}, "kind must be partitions or lvm"),
        ({"data": {"kind": "partitions"}}, "non-empty list"),
        ({"data": {"kind": "partitions", "volumes": [], "vg": "v"}}, "unknown key"),
        (parts(vol(), vol(label="B", mount="/srv/b")), r'exactly one "\*"'),  # root and both volumes are "*"
        (parts(vol(fs="zfs")), "zfs is not supported yet"),
        (parts(vol(fs="ntfs")), "fs must be one of"),
        (parts(vol(mount="srv/cow")), "absolute path"),
        (parts(vol(mount="/srv/cow/")), "trailing slash"),
        (parts(vol(mount="/")), "protected system path"),
        (parts(vol(mount="/boot/data")), "protected system path"),
        (parts(vol(mount="/srv/../etc")), "dot segments"),
        (parts(vol(mount="/srv/tree-cache")), "protected system path"),
        (parts(vol(label="THIS_LABEL_IS_TOO_LONG")), "label must be"),
        (parts(vol(label="boot")), "collides with a system partition"),
        (parts(vol(label="vbpub-swap1")), "collides with a system partition"),
        (parts(vol(), vol(mount="/srv/b", size=10), root=10), "used twice"),
        (parts(vol(label=None)), "needs a label"),
        (parts({"fs": "btrfs", "label": "A"}), "needs a mount point"),
        (parts(vol(size=10, name="x"), root=10), "unknown key"),
        (parts(vol(subvol="@cow")), "subvol is not supported yet"),
        (parts(vol(fs="ext4", type="zfs")), "only for a raw partition"),
        (parts(vol(fs="none", mount="/x")), "takes no mount"),
        (parts(vol(size="60%"), root="50%"), "add up to more than 100%"),
        (parts(vol(size=10), vol(size=10, label="B", mount="/srv/b"), root=10, reserve=1) | {"root": "*", "reserve_gb": "*"},
         r'exactly one "\*"'),
        ({"data": {"kind": "lvm", "vg": "bad name", "volumes": [{"name": "a", "size": 1, "fs": "none"}]}}, "volume group"),
        ({"data": {"kind": "lvm", "vg": "v", "volumes": [{"size": 1}]}}, "lv name"),
        ({"data": {"kind": "lvm", "vg": "v", "volumes": [{"name": "a"}, {"name": "b"}]}}, r'exactly one "\*"'),
        ({"data": {"kind": "lvm", "vg": "v", "vg_free_gib": -1, "volumes": [{"name": "a", "size": 1}]}}, "vg_free_gib"),
        ({"data": {"kind": "lvm", "vg": "v", "volumes": [{"name": "a", "size": 1}, {"name": "a", "size": 1}]}}, "unique"),
        ({"root": 10, "data": {"kind": "lvm", "vg": "v", "volumes": [
            {"name": "a", "size": "60%", "fs": "ext4", "mount": "/srv/a"},
            {"name": "b", "size": "60%", "fs": "ext4", "mount": "/srv/b"}]}}, "more than 100%"),
        ({"root": 10, "data": {"kind": "lvm", "vg": "v", "volumes": [
            {"name": "a", "size": 1, "fs": "ext4", "mount": "/srv"},
            {"name": "b", "size": 1, "fs": "ext4", "mount": "/srv/b"}]}}, "protected system path"),
    ],
)
def test_schema_refusals(partitioning, message):
    with pytest.raises(lp.LayoutPolicyError, match=message):
        lp.disk_spec(partitioning)


@pytest.mark.parametrize(
    "partitioning,message",
    [
        (parts(vol(size=20), root=10), "fixed sizes without a growable entry"),
        (parts(vol(size="50%"), root="40%") | {"iobench_raw_gb": False}, "percentages must add up to 100%"),
        (parts(vol(size="50%"), root=10), "mixing percent and fixed sizes is ambiguous"),
    ],
)
def test_impossible_pool_shapes_are_refused_at_schema_validation(partitioning, message):
    with pytest.raises(lp.LayoutPolicyError, match=message):
        lp.disk_spec(partitioning)


@pytest.mark.parametrize(
    ("mount", "protected"), [
        ("/", "/"), ("/var", "/var"), ("/var/lib", "/var/lib"),
        ("/var/lib/dpkg", "/var/lib/dpkg"), ("/var/lib/dpkg/status", "/var/lib/dpkg"),
        ("/var/lib/apt", "/var/lib/apt"), ("/var/lib/apt/lists", "/var/lib/apt"),
        ("/var/cache/apt", "/var/cache/apt"), ("/var/log", "/var/log"),
        ("/srv", "/srv"), ("/home", "/home"),
        ("/usr", "/usr"), ("/usr/local", "/usr"),
        ("/etc", "/etc"), ("/etc/vbpub", "/etc"),
        ("/boot", "/boot"), ("/boot/efi", "/boot"),
        ("/tmp", "/tmp"), ("/root", "/root"), ("/proc/cpuinfo", "/proc"),
        ("/run/secrets", "/run"), ("/lib", "/lib"),
        ("/srv/tree-cache", "/srv/tree-cache"), ("/srv/tree-upper", "/srv/tree-upper"),
    ]
)
def test_mounts_that_cover_protected_system_state_are_refused(mount, protected):
    with pytest.raises(lp.LayoutPolicyError) as exc:
        lp.disk_spec(parts(vol(mount=mount)))
    assert mount in str(exc.value)
    assert protected in str(exc.value)
    assert "would hide protected system path" in str(exc.value)


@pytest.mark.parametrize(
    "mount",
    [
        "/var/lib/docker", "/var/lib/docker/volumes", "/var/lib/containerd",
        "/var/lib/containerd/io.containerd", "/var/cache/vbpub",
        "/srv/cow", "/srv/data/archive", "/home/runner", "/home/runner/cache",
    ],
)
def test_data_mounts_under_var_srv_and_home_are_allowed(mount):
    assert lp.disk_spec(parts(vol(mount=mount), root=10)).data.volumes[0].mount == mount


def test_fixed_reserve_is_subtracted_before_percent_sizes():
    plan, pool_end = pool(parts(vol(size="60%"), root="40%", reserve=5))
    total = pool_end - BOOT_END - 5 * S
    assert plan.root_size == (total * 40 // 100) // 2048 * 2048
    assert plan.root_size + plan.extents[0][1] == total
    assert plan.reserve_sectors == 5 * S


def test_keep_without_an_entry_refuses_instead_of_indexing_an_empty_list():
    with pytest.raises(lp.LayoutPolicyError, match="root keep leaves .* unallocated"):
        lp.disk_spec({"root": "keep"})
    with pytest.raises(lp.LayoutPolicyError, match="root keep leaves .* unallocated"):
        lp.disk_spec({"root": "keep", "reserve_gb": 5})


def test_plan_pool_rechecks_that_keep_has_a_remainder_owner():
    spec = lp.DiskSpec(lp.KEEP, lp.Size("gib", 0), lp.DataArea("none"))
    with pytest.raises(lp.LayoutPolicyError, match="root keep leaves .* unallocated"):
        lp.plan_pool(
            spec,
            root_start=BOOT_END,
            pool_end=512 * S - 16 * S,
            root_current_size=3 * S,
        )


def test_no_space_message_accounts_for_the_raw_iobench_partition():
    with pytest.raises(lp.LayoutPolicyError, match=r"raw iobench \(8 GiB\)"):
        lp.plan_pool(
            lp.DiskSpec(lp.KEEP, lp.Size("gib", 0), lp.DataArea("none")),
            root_start=BOOT_END,
            pool_end=4 * S,
            root_current_size=S,
        )


def test_valid_forms_parse():
    spec = lp.disk_spec({"root": 160, "data": {"kind": "partitions", "volumes": [vol()]}})
    assert spec.root == lp.Size("gib", 160) and spec.data.volumes[0].size.is_star
    assert lp.disk_spec({}).root.is_star and lp.disk_spec({}).iobench_raw_gb == lp.DEFAULT_IOBENCH_RAW_GB
    assert lp.disk_spec(None).root.is_star
    assert lp.disk_spec({"root": "*"}).data.kind == "none"
    with pytest.raises(lp.LayoutPolicyError, match="root keep leaves space unallocated"):
        lp.disk_spec({"root": "keep"})
    with pytest.raises(lp.LayoutPolicyError, match="percentages must add up to 100%"):
        lp.disk_spec({"root": "33.33%"})
    assert lp.disk_spec({"root": "100%"}).root == lp.Size("percent", 10000)
    raw = lp.disk_spec(
        parts(vol(fs="none", type="zfs", mount=None, label="ZPOOL", size=20), root=10, reserve="*")
        | {"iobench_raw_gb": False}
    )
    assert raw.data.volumes[0].type_guid == lp.ZFS_TYPE_GUID and raw.data.volumes[0].mount is None
    raw_linux = lp.disk_spec(
        parts(vol(fs="none", type="linux", mount=None, label="RAW"), root=10) | {"iobench_raw_gb": False}
    )
    assert raw_linux.data.volumes[0].type_guid == lp.LINUX_FS_TYPE_GUID
    assert lp.disk_spec({"root": 10, "reserve_gb": "*"}).reserve.is_star


def test_removed_keys_are_refused_in_one_message_without_translation():
    for key in lp.REMOVED_KEYS:
        with pytest.raises(ConfigError, match=key):
            load_config(raw_json=f'{{"{key}": null}}')
    with pytest.raises(ConfigError) as exc:
        load_config(raw_json='{"disk_layout":"dev-host","root_max_size_gb":40,"data_area":"partition"}')
    assert all(key in str(exc.value) for key in ("disk_layout", "root_max_size_gb", "data_area"))
    assert "partitioning" in str(exc.value)


def test_config_prerequisites():
    ok = load_config(raw_json='{"partitioning": {"root": 100, "data": {"kind": "partitions", "volumes": [{"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}')
    assert ok.partitioning["root"] == 100
    with pytest.raises(ConfigError, match="untouched was removed"):
        load_config(raw_json='{"swap_policy":"untouched","partitioning":{"root":"*"}}')
    with pytest.raises(ConfigError, match="root_resize_policy.*removed"):
        load_config(raw_json='{"root_resize_policy":"untouched","partitioning":{"root":"*"}}')
    assert load_config(raw_json='{"swap_policy":"off","partitioning":{"root":"*"}}').swap_policy == "off"
    assert load_config(raw_json="{}").partitioning == {}


# ---------------------------------------------------------------- the pool planner (512 GiB, 16 GiB swap)

def test_root_fixed_data_star():
    plan, pool_end = pool(parts(vol(), root=160))
    assert plan.root_size == 160 * S
    (start, size), = plan.extents
    assert start == BOOT_END + 160 * S and start + size == pool_end and plan.reserve_sectors == 0


def test_root_star_data_fixed():
    plan, pool_end = pool(parts(vol(size=200)))
    assert plan.extents[0][1] == 200 * S and BOOT_END + plan.root_size == plan.extents[0][0]
    assert plan.extents[0][0] + plan.extents[0][1] == pool_end


def test_percent_split_adds_up_and_goes_to_the_pool():
    plan, pool_end = pool(parts(vol(size="60%"), root="40%"))
    total = pool_end - BOOT_END
    assert plan.root_size == (total * 40 // 100) // 2048 * 2048
    assert plan.root_size + plan.extents[0][1] == total


def test_two_data_partitions_and_decimal_percent():
    plan, pool_end = pool(parts(vol(size="33.34%"), vol(label="B", mount="/srv/b", size="33.33%"), root="33.33%"))
    assert BOOT_END + plan.root_size + sum(z for _s, z in plan.extents) == pool_end


def test_fixed_plus_fixed_is_refused_at_static_validation():
    with pytest.raises(lp.LayoutPolicyError) as exc:
        pool(parts(vol(size=200), root=100))
    text = str(exc.value)
    assert "fixed sizes without a growable entry" in text
    assert 'set one size to "*"' in text


def test_reserve_star_keeps_the_remainder_free_on_purpose():
    plan, pool_end = pool(parts(vol(size=200), root=100, reserve="*"))
    assert plan.reserve_sectors == pool_end - BOOT_END - 300 * S
    assert plan.extents[0][0] + plan.extents[0][1] + plan.reserve_sectors == pool_end


def test_explicit_fixed_reserve_with_percent_sizes_is_accepted_at_config_load():
    config = load_config(raw_json=(
        '{"partitioning":{"root":"40%","reserve_gb":5,"data":{"kind":"partitions",'
        '"volumes":[{"size":"60%","fs":"btrfs","mount":"/srv/cow","label":"MDT_COW"}]}}}'
    ))
    assert config.partitioning["reserve_gb"] == 5


def test_iobench_raw_lower_bound_is_checked_at_config_load():
    with pytest.raises(ConfigError, match="iobench_raw_gb must be an integer GiB value from 2"):
        load_config(raw_json='{"partitioning":{"iobench_raw_gb":1}}')
    assert load_config(raw_json='{"partitioning":{"iobench_raw_gb":2}}').partitioning["iobench_raw_gb"] == 2


def test_iobench_raw_label_is_reserved_at_config_load():
    with pytest.raises(ConfigError, match="label 'iobench-raw' collides"):
        load_config(raw_json=(
            '{"partitioning":{"root":10,"iobench_raw_gb":false,"data":{"kind":"partitions",'
            '"volumes":[{"size":"*","fs":"none","label":"iobench-raw"}]}}}'
        ))


def test_config_load_refuses_more_than_128_gpt_partitions():
    def raw_config(data_volumes: int) -> str:
        volumes = [
            {"size": 1, "fs": "none", "label": f"V{index}"}
            for index in range(data_volumes)
        ]
        import json
        return json.dumps({
            "swap_disk_total_gb": 4,
            "swap_partition_count": 4,
            "partitioning": {
                "root": "*",
                "data": {"kind": "partitions", "volumes": volumes},
            },
        })

    # root + data + the default raw partition + four swaps = 128.
    assert load_config(raw_json=raw_config(122)).partitioning["root"] == "*"
    with pytest.raises(ConfigError, match=r"above the GPT limit of 128"):
        load_config(raw_json=raw_config(123))


def test_mixed_percent_and_fixed_without_star_is_ambiguous():
    with pytest.raises(lp.LayoutPolicyError, match="mixing percent and fixed sizes is ambiguous"):
        pool(parts(vol(size="50%"), root=100))


def test_percent_without_star_must_add_up():
    with pytest.raises(lp.LayoutPolicyError, match=r"add up to 100% .* = 90%"):
        pool(parts(vol(size="50%"), root="40%"))


def test_percent_cannot_combine_with_reserve_star():
    with pytest.raises(lp.LayoutPolicyError, match="cannot be combined with reserve_gb"):
        pool(parts(vol(size=10), root="40%", reserve="*"))


def test_over_allocation_names_the_numbers_for_every_negative_remainder():
    with pytest.raises(lp.LayoutPolicyError, match="fixed sizes without a growable entry"):
        pool(parts(vol(size=500), root=100))
    with pytest.raises(lp.LayoutPolicyError, match="over-allocates the pool"):
        pool(parts(vol(size=500), root="*"))


def test_data_partition_below_minimum_is_refused():
    # 6 GiB disk, 1 GiB swap, root 1: the data partition gets what is left (~2.75 GiB): fine; root 99.2% leaves ~30 MiB
    plan, _end = pool(parts(vol(), root=1), disk_gib=6, swap_gib=1)
    assert plan.extents[0][1] > 2.7 * S
    with pytest.raises(lp.LayoutPolicyError, match=r"below the minimum of 512 MiB"):
        pool(parts(vol(), root="99.2%"), disk_gib=6, swap_gib=1)
    with pytest.raises(lp.LayoutPolicyError, match=r"root is .* below the minimum of 10240 MiB"):
        pool(parts(vol(), root=5), min_root=10 * S)
    with pytest.raises(lp.LayoutPolicyError, match=r"PV is .* below the minimum of 2048 MiB"):
        pool({"root": "99.6%", "iobench_raw_gb": False,
              "data": {"kind": "lvm", "vg": "v", "volumes": [{"name": "a", "size": 1, "fs": "none"}]}},
             disk_gib=6, swap_gib=1)


def test_star_with_nothing_left_is_the_over_allocation_message():
    with pytest.raises(lp.LayoutPolicyError, match="over-allocates"):
        pool(parts(vol(), root=160), disk_gib=128, swap_gib=8)


def test_fixed_root_with_growable_reserve_refuses_when_the_root_does_not_fit():
    """A negative reserve must not mask a fixed root that overlaps the pool boundary."""
    with pytest.raises(lp.LayoutPolicyError, match="over-allocates the pool"):
        pool({"root": 20, "reserve_gb": "*", "iobench_raw_gb": False}, disk_gib=20, swap_gib=0)


def test_root_keep_pool_starts_behind_the_current_root():
    plan, pool_end = pool(parts(vol(), root="keep"), current=3 * S)
    assert plan.root_size == 3 * S
    assert plan.extents[0][0] == BOOT_END + 3 * S and plan.extents[0][0] + plan.extents[0][1] == pool_end
    # an unaligned kept root: the data partition starts on the next MiB
    plan, _end = pool(parts(vol(), root="keep"), root_start=BOOT_END + 5, current=3 * S + 3)
    assert plan.extents[0][0] % 2048 == 0 and plan.extents[0][0] >= BOOT_END + 5 + 3 * S + 3


def test_unaligned_root_start_aligns_the_root_end_and_loses_nothing():
    for plan_in in (parts(vol(), root=160), parts(vol(size=200)), parts(vol(size="60%"), root="40%")):
        plan, pool_end = pool(plan_in, root_start=BOOT_END + 37)
        assert (BOOT_END + 37 + plan.root_size) % 2048 == 0
        assert BOOT_END + 37 + plan.root_size + sum(z for _s, z in plan.extents) == pool_end


def test_no_data_area_root_star_takes_the_whole_pool():
    plan, pool_end = pool({"root": "*", "iobench_raw_gb": False})
    assert plan.extents == () and BOOT_END + plan.root_size == pool_end
    plan, pool_end = pool({"root": 100, "reserve_gb": "*", "iobench_raw_gb": False})
    assert plan.root_size == 100 * S and plan.reserve_sectors == pool_end - BOOT_END - 100 * S


def test_tiny_test_disk_examples_from_the_design_note():
    plan, pool_end = pool(parts(vol(mount="/srv/div2-vm-cow"), root=1), disk_gib=6, swap_gib=1)
    assert plan.root_size == S and abs(plan.extents[0][1] - 2.81 * S) < 0.02 * S
    lvm = {"root": 1, "iobench_raw_gb": False, "data": {"kind": "lvm", "vg": "vgdiv2vm", "volumes": [
        {"name": "cow", "size": 1, "fs": "btrfs", "mount": "/srv/c", "label": "MDT_COW"},
        {"name": "rest", "fs": "xfs", "mount": "/srv/r", "label": "REST"}]}}
    plan, pool_end = pool(lvm, disk_gib=6, swap_gib=1)
    assert plan.extents[0][0] + plan.extents[0][1] == pool_end


@pytest.mark.parametrize("seed", range(8))
def test_property_whole_disk_accounted_ordered_and_aligned(seed):
    rng = random.Random(seed)
    checked = 0
    for _ in range(250):
        disk_gib = rng.choice([8, 16, 40, 100, 128, 512, 1024, 4096])
        disk = disk_gib * S + rng.choice([0, 0, 777, 2048 * 3 + 5])
        swap_gib = rng.choice([0, 1, 4, 16]) if disk_gib > 8 else rng.choice([0, 1])
        root_start = rng.choice([2048, BOOT_END, BOOT_END + 37, 2049])
        pool_end = ((disk - 2048 - swap_gib * S) // 2048) * 2048
        n_data = rng.choice([0, 1, 1, 2, 3])
        entries = ["root"] + [f"v{i}" for i in range(n_data)]
        star_at = rng.randrange(len(entries) + 1)  # last slot == reserve
        mode = rng.choice(["fixed", "percent"])
        sizes = {}
        remaining = 100
        for i, name in enumerate(entries):
            if i == star_at:
                sizes[name] = "*"
            elif mode == "fixed":
                sizes[name] = rng.choice([1, 2, 5, 10, 20, 50])
            else:
                share = rng.choice([5, 10, 20, 25])
                sizes[name] = f"{min(share, remaining)}%"
        reserve = "*" if star_at == len(entries) else rng.choice([0, 0, 1, 2])
        if mode == "percent" and reserve == "*":
            continue
        volumes = [vol(size=sizes[f"v{i}"], label=f"L{i}", mount=f"/srv/v{i}") for i in range(n_data)]
        partitioning = {"root": sizes["root"], "reserve_gb": reserve}
        if volumes:
            partitioning["data"] = {"kind": "partitions", "volumes": volumes}
        try:
            spec = lp.disk_spec(partitioning)
            plan = lp.plan_pool(spec, root_start=root_start, pool_end=pool_end, root_current_size=3 * S)
        except lp.LayoutPolicyError:
            continue  # a refusal is a valid outcome; accepted plans must satisfy the properties below
        checked += 1
        extents = [(root_start, plan.root_size), *plan.extents]
        if plan.reserve_sectors:
            extents.append((plan.reserve_start, plan.reserve_sectors))
        extents.sort()
        # Data, intentional reserve, and raw benchmark extents account for the pool in order.
        for (start, size), (next_start, _next) in zip(extents, extents[1:]):
            assert start + size == next_start and size > 0
        assert all(start % 2048 == 0 for start, _size in extents[1:])
        assert (root_start + plan.root_size) % 2048 == 0
        last_start, last_size = extents[-1]
        assert last_start + last_size == pool_end
        raw_sectors = (spec.iobench_raw_gb or 0) * S
        assert plan.reserve_start == pool_end - raw_sectors - plan.reserve_sectors
        assert sum(size for _s, size in extents) == pool_end - root_start
    assert checked > 20  # the generator really exercises accepted plans


# ---------------------------------------------------------------- the LV planner

LV_AREA = {"kind": "lvm", "vg": "v", "volumes": [
    {"name": "cow", "size": "60%", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
    {"name": "scratch", "fs": "xfs", "mount": "/srv/scratch", "label": "SCRATCH"}]}


def area(**kw):
    return lp.disk_spec({"root": 10, "data": {**LV_AREA, **kw}}).data


def test_lv_plan_star_is_created_last_and_uses_the_free_space():
    plans = lp.plan_lvs(area(volumes=list(reversed(LV_AREA["volumes"]))), 1000)
    assert [p.name for p in plans] == ["cow", "scratch"]
    assert plans[0].args == ("-l", "600") and plans[1].args == ("-l", "100%FREE") and plans[1].star


def test_lv_plan_fixed_and_vg_free():
    a = area(vg_free_gib=1, volumes=[
        {"name": "a", "size": 1, "fs": "ext4", "mount": "/srv/a"}, {"name": "b", "fs": "ext4", "mount": "/srv/b"}])
    plans = lp.plan_lvs(a, 2048)  # 8 GiB VG
    assert plans[0].args == ("-L", "1G")
    assert plans[1].args == ("-l", str(2048 - 256 - 256))  # keeps 1 GiB free


def test_lv_plan_without_star_leaves_the_rest_free_and_refuses_overflow():
    a = area(volumes=[{"name": "a", "size": 1, "fs": "none"}])
    assert lp.plan_lvs(a, 1000)[0].args == ("-L", "1G")
    with pytest.raises(lp.LayoutPolicyError, match=r"LV sizes \(200\.00 GiB\) exceed the VG \(168\.00 GiB\)"):
        lp.plan_lvs(area(volumes=[{"name": "a", "size": 200, "fs": "none"}]), 168 * 256)
    with pytest.raises(lp.LayoutPolicyError, match="below the minimum of 64 MiB"):
        lp.plan_lvs(area(volumes=[{"name": "a", "size": "0.01%", "fs": "none"}]), 1000)
    with pytest.raises(lp.LayoutPolicyError, match='"\\*" LV would get'):
        lp.plan_lvs(area(volumes=[{"name": "a", "size": "99.99%", "fs": "none"}, {"name": "b", "fs": "none"}]), 1000)


def test_estimated_vg_extents():
    assert lp.estimate_vg_extents(4 * S) == (4 * 1024 - 1) // 4


def test_data_area_packages():
    assert lp.data_area_packages(lp.disk_spec({"root": "*"}).data) == []
    assert lp.data_area_packages(lp.disk_spec(parts(vol(fs="xfs"), root=5)).data) == ["xfsprogs"]
    assert lp.data_area_packages(lp.disk_spec(parts(vol(fs="ext4"), root=5)).data) == ["util-linux"]
    assert lp.data_area_packages(lp.disk_spec(parts(vol(), root=5)).data) == ["btrfs-progs"]
    assert lp.data_area_packages(area()) == ["lvm2", "btrfs-progs", "xfsprogs"]
