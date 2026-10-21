"""11c - Debian testing is visible to apt but never auto-selected (pinned to 100).

The built image carries no apt lists (the Dockerfile removes them) and the lane runs offline, so this
builds a SYNTHETIC index inside the container: a fake `testing` archive that offers a far newer `bash`
and a package that exists only there, and a fake 500-priority stable archive offering the installed
`bash`. The image's REAL `/etc/apt/sources.list.d/testing.sources` and `/etc/apt/preferences.d` are what
apt reads, so the verdict is the image's own configuration applied by apt, not a re-implementation.
Without the preference file the same synthetic index gives testing priority 500 and the newer bash wins
(measured while writing this test), so the assertion can fail.
"""
from __future__ import annotations

import re
import unittest

import harness

SCRIPT = r"""
set -eu
d="$(mktemp -d)"
mkdir -p "$d/lists/partial" "$d/src"
arch="$(dpkg --print-architecture)"
installed="$(dpkg-query -W -f='${Version}' bash)"
cp /etc/apt/sources.list.d/testing.sources "$d/src/testing.sources"
cat > "$d/src/stable.sources" <<SRC
Types: deb
URIs: http://stable.invalid/debian
Suites: stable
Components: main
SRC
printf 'Suite: testing\nCodename: forky\nArchitectures: %s\nComponents: main contrib non-free non-free-firmware\n' "$arch" \
    > "$d/lists/deb.debian.org_debian_dists_testing_Release"
printf 'Package: bash\nVersion: 99:9.9-1\nArchitecture: %s\nDescription: fake\n\nPackage: mdt-preview-only\nVersion: 1\nArchitecture: %s\nDescription: fake\n' "$arch" "$arch" \
    > "$d/lists/deb.debian.org_debian_dists_testing_main_binary-${arch}_Packages"
cat > "$d/src/old.sources" <<SRC
Types: deb
URIs: http://old.invalid/debian
Suites: oldstable oldstable-backports
Components: main
SRC
for suite in oldstable oldstable-backports; do
    printf 'Suite: %s\nCodename: bookworm\nArchitectures: %s\nComponents: main\n' "$suite" "$arch" \
        > "$d/lists/old.invalid_debian_dists_${suite}_Release"
    printf 'Package: mdt-%s-only\nVersion: 1\nArchitecture: %s\nDescription: fake\n' "$suite" "$arch" \
        > "$d/lists/old.invalid_debian_dists_${suite}_main_binary-${arch}_Packages"
done
printf 'Suite: stable\nCodename: trixie\nArchitectures: %s\nComponents: main\n' "$arch" \
    > "$d/lists/stable.invalid_debian_dists_stable_Release"
printf 'Package: bash\nVersion: %s\nArchitecture: %s\nDescription: fake\n' "$installed" "$arch" \
    > "$d/lists/stable.invalid_debian_dists_stable_main_binary-${arch}_Packages"
apt-cache -o Dir::State::lists="$d/lists" -o Dir::Etc::sourcelist=/dev/null -o Dir::Etc::sourceparts="$d/src" \
    -o Dir::Cache::pkgcache= -o Dir::Cache::srcpkgcache= policy bash mdt-preview-only mdt-oldstable-only mdt-oldstable-backports-only
"""


def _section(output: str, package: str) -> str:
    match = re.search(rf"^{re.escape(package)}:\n((?:  .*\n?)+)", output, re.MULTILINE)
    if not match:
        raise AssertionError(f"no apt-cache policy section for {package}:\n{output}")
    return match.group(1)


def _fake_archives_are_all_at_100(output: str) -> dict[str, str]:
    return {p: _section(output, p) for p in ("mdt-oldstable-only", "mdt-oldstable-backports-only")}


class Contract:
    def test_testing_is_configured_and_no_temporary_pin_is_left_behind(self) -> None:
        result = self.sh(
            "test -f /etc/apt/sources.list.d/testing.sources && test -f /etc/apt/preferences.d/preview-archives "
            "&& ! test -e /etc/apt/preferences.d/skopeo-testing && ! test -e /etc/apt/sources.list.d/testing.list"
        )
        self.assertOk(result, "testing source and preview pin files")

    def test_testing_is_visible_at_priority_100_and_never_the_candidate_of_an_installed_package(self) -> None:
        result = self.sh(SCRIPT)
        self.assertOk(result, "synthetic apt-cache policy")
        bash = _section(result.stdout, "bash")
        installed = re.search(r"Installed: (\S+)", bash).group(1)
        self.assertEqual(
            re.search(r"Candidate: (\S+)", bash).group(1), installed,
            f"a package's candidate must not come from testing:\n{bash}",
        )
        self.assertRegex(bash, r"99:9\.9-1 100\n\s+100 http://deb\.debian\.org/debian testing/main", "testing must sit at 100")
        self.assertNotRegex(bash, r"99:9\.9-1 (?:[2-9]\d\d|[1-9]\d{3,})", "testing must never be above 100")

    def test_a_package_only_in_testing_is_still_visible(self) -> None:
        result = self.sh(SCRIPT)
        self.assertOk(result, "synthetic apt-cache policy")
        only = _section(result.stdout, "mdt-preview-only")
        self.assertRegex(only, r"Candidate: 1\n", "visible: `apt install -t testing <pkg>` can reach it")
        self.assertRegex(only, r"1 100\n", "and only at priority 100")

    def test_oldstable_and_oldstable_backports_are_pinned_to_100_too(self) -> None:
        result = self.sh(SCRIPT)
        self.assertOk(result, "synthetic apt-cache policy")
        for package, section in _fake_archives_are_all_at_100(result.stdout).items():
            self.assertRegex(section, r"Candidate: 1\n", f"{package} must stay visible")
            self.assertRegex(section, r"1 100\n", f"{package} must sit at priority 100, not the default 500")


class NativeContract(Contract, harness.NativeCase):
    pass


class VscContract(Contract, harness.VscCase):
    pass


if __name__ == "__main__":
    unittest.main()
