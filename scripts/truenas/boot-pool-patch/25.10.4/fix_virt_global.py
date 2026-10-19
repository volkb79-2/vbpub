#!/usr/bin/env python3
# Patch TrueNAS 25.10 virt/global.py to allow boot-pool in Incus pool choices.
# Run after: zfs set readonly=off boot-pool/ROOT/25.10.4/usr && mount -o remount,rw /usr


# Note: doesnt work yet , somethign else neeeds to be fixed.

import re, sys

path = '/usr/lib/python3/dist-packages/middlewared/plugins/virt/global.py'
content = open(path).read()
changed = False

# --- Change 1: remove boot-pool exclusion from pool_choices() ---
C1_OLD = "BOOT_POOL_NAME_VALID or "
C1_NEW = ""
if C1_OLD in content:
    content = content.replace(C1_OLD, C1_NEW)
    print("Change 1 applied: removed BOOT_POOL_NAME_VALID guard")
    changed = True
else:
    print("Change 1 already applied (or not found) - skipping")

# --- Change 2: wrap get_instance_quick in try/except ---
# Find the block by landmark phrases rather than exact whitespace
C2_MARKER = "pool.dataset.get_instance_quick"
if C2_MARKER not in content:
    print("ERROR: cannot find get_instance_quick call - wrong file?")
    sys.exit(1)

if "except Exception" in content:
    print("Change 2 already applied - skipping")
else:
    # Use regex to find and replace the block regardless of indent width
    pattern = re.compile(
        r'( +)(ds = await self\.middleware\.call\(\s*'
        r"'pool\.dataset\.get_instance_quick'.*?\)\s*"
        r'if not ds\[.locked.\]:\s*'
        r"pools\[p\[.name.\]\] = p\[.name.\]\s*)",
        re.DOTALL
    )
    m = pattern.search(content)
    if not m:
        print("ERROR: could not locate the get_instance_quick block with regex.")
        print("Showing 10 lines around the marker for manual inspection:")
        idx = content.index(C2_MARKER)
        snippet = content[max(0, idx-200):idx+300]
        print(repr(snippet))
        sys.exit(1)

    indent = m.group(1)
    old_block = m.group(0)
    i = indent
    i4 = indent + "    "
    new_block = (
        f"{i}try:\n"
        f"{i4}ds = await self.middleware.call(\n"
        f"{i4}    'pool.dataset.get_instance_quick', p['name'], {{'encryption': True}},\n"
        f"{i4})\n"
        f"{i4}if not ds['locked']:\n"
        f"{i4}    pools[p['name']] = p['name']\n"
        f"{i}except Exception:\n"
        f"{i4}pools[p['name']] = p['name']\n"
    )
    content = content.replace(old_block, new_block)
    print("Change 2 applied: wrapped get_instance_quick in try/except")
    changed = True

if changed:
    open(path, 'w').write(content)
    print("File written. Run: systemctl restart middlewared")
else:
    print("No changes needed.")
