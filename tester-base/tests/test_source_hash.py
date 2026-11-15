"""TB-04: the tester-base source hash covers every build input.

`org.vbpub.tester-base.source-hash` covers `Dockerfile` and `ksm-optin.c`
(`cmru/build-initial-standalone.sh`). The latter is byte-for-byte synchronized
with CIU's canonical source. This test fails when another build input appears
without widening the hash. No docker needed for these source-boundary checks.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
BOOTSTRAP = REPO / "cmru" / "build-initial-standalone.sh"
NETCUP_BUILDER = REPO / "scripts" / "netcup" / "runner" / "build-tester-base.sh"

#: Input-introducing constructs a Dockerfile can carry. `COPY --from=<stage>` copies from another stage and
#: still needs the stage's inputs hashed, so it is refused too.
_INPUT_INSTRUCTION = re.compile(r"^\s*(?:COPY|ADD)\b", re.IGNORECASE)
_INPUT_FLAG = re.compile(r"--mount\b|--build-context\b", re.IGNORECASE)


def code_lines(text: str) -> list[str]:
    """Dockerfile text without comment lines, with backslash continuations joined."""
    joined = re.sub(r"\\\n", " ", text)
    return [line for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def undeclared_inputs(dockerfile_text: str, bake_text: str = "") -> list[str]:
    """Every line that makes the build depend on something other than the Dockerfile's own bytes."""
    found = [line.strip() for line in code_lines(dockerfile_text)
             if _INPUT_INSTRUCTION.match(line) or _INPUT_FLAG.search(line)]
    found += [line.strip() for line in code_lines(bake_text.replace("//", "#"))
              if re.match(r"\s*contexts\s*=", line)]
    return found


class SourceHashCoversEveryBuildInput(unittest.TestCase):
    def test_the_real_build_uses_only_declared_dockerfile_and_shim_inputs(self) -> None:
        found = undeclared_inputs(
            (ROOT / "Dockerfile").read_text(encoding="utf-8"),
            (ROOT / "docker-bake.hcl").read_text(encoding="utf-8"),
        )
        self.assertEqual(found, ["COPY ksm-optin.c /tmp/ksm-optin.c"],
                         "a build input appeared: widen the source-hash (the producer in "
                         f"{BOOTSTRAP.name}) to cover it before changing this test")

    def test_the_hash_producer_hashes_both_build_inputs(self) -> None:
        script = BOOTSTRAP.read_text(encoding="utf-8")
        producers = re.findall(r'tester_base_source_hash="\$\(([^)]*)\)"', script)
        self.assertEqual(len(producers), 1, producers)
        self.assertRegex(producers[0], r'^cd "\$\{repo_root\}/tester-base" && sha256sum Dockerfile ksm-optin\.c \| sha256sum \| cut -d\' \' -f1$')

    def test_netcup_builder_hashes_both_build_inputs(self) -> None:
        script = NETCUP_BUILDER.read_text(encoding="utf-8")
        self.assertIn('base_hash="$(cd "$repo/tester-base" &&', script)
        self.assertIn("sha256sum Dockerfile ksm-optin.c | sha256sum | awk '{print $1}'",
                      script)

    def test_tester_base_source_is_the_ci_u_shipped_source(self) -> None:
        canonical = REPO / "ciu" / "src" / "ciu" / "data" / "ksm-optin.c"
        self.assertEqual((ROOT / "ksm-optin.c").read_bytes(), canonical.read_bytes())

    def test_the_detector_sees_every_kind_of_new_input(self) -> None:
        for text in (
            "FROM x\nCOPY a /a\n",
            "FROM x\ncopy a /a\n",
            "FROM x\nADD https://example.invalid/f /f\n",
            "FROM x\nRUN --mount=type=bind,source=.,target=/src true\n",
            "FROM x\nRUN \\\n  --mount=type=cache,target=/c true\n",
            "FROM x\nRUN --network=none \\\n --mount=type=secret,id=a true\n",
            "FROM x\nRUN docker build --build-context a=b .\n",
        ):
            self.assertTrue(undeclared_inputs(text), text)
        self.assertTrue(undeclared_inputs("FROM x\n", 'target "t" {\n  contexts = { a = "b" }\n}\n'))

    def test_the_detector_ignores_comments_and_plain_instructions(self) -> None:
        self.assertEqual(undeclared_inputs("# COPY a /a\nFROM x\nRUN echo copy\nARG A\n", "// contexts = {}\n"), [])


if __name__ == "__main__":
    unittest.main()
