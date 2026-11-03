"""TB-04: the tester-base source hash covers every build input, or the build has none besides the Dockerfile.

`org.vbpub.tester-base.source-hash` is the sha256 of `tester-base/Dockerfile` alone
(`cmru/build-initial-standalone.sh`), justified because the Dockerfile is the build's ONLY input. A future
`COPY`, `ADD`, `RUN --mount=type=bind`, `--build-context` or a bake `contexts` entry would change the image
without changing the hash, and the stale local `tester-base:local` would be reused. This test fails the day
such an input appears, until the hash is widened (for example to the sorted `git ls-tree -r HEAD tester-base`
of the context files) AND this test is updated to say so. No docker needed.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
BOOTSTRAP = REPO / "cmru" / "build-initial-standalone.sh"

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
    def test_the_real_build_has_no_input_besides_the_dockerfile(self) -> None:
        found = undeclared_inputs(
            (ROOT / "Dockerfile").read_text(encoding="utf-8"),
            (ROOT / "docker-bake.hcl").read_text(encoding="utf-8"),
        )
        self.assertEqual(found, [], "a build input appeared: widen the source-hash (the producer in "
                                   f"{BOOTSTRAP.name}) to cover it before changing this test")

    def test_the_hash_producer_hashes_the_dockerfile_alone(self) -> None:
        """The premise above: if the producer starts hashing more, this test and the docs are due an update."""
        script = BOOTSTRAP.read_text(encoding="utf-8")
        producers = re.findall(r'tester_base_source_hash="\$\(([^)]*)\)"', script)
        self.assertEqual(len(producers), 1, producers)
        self.assertRegex(producers[0], r'^sha256sum "\$\{repo_root\}/tester-base/Dockerfile" \| cut -d\' \' -f1$')

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
