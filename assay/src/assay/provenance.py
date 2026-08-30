"""(B018/A-327) Judge provenance: WHICH build of assay is running right now.

``assay_version`` names a version string, and any process at all can print one.
CIU V8's central tool resolution (``ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md``
§11.3) verifies a *download* -- it resolves a judge from ``[testing.judge]``,
fetches an artifact, checks its digest -- and then has nothing binding the
verdict in front of it to that artifact. This module produces that binding:
the running process identifies the build artifact it was installed FROM, and
records its ``sha256``.

**Nothing here is inferred from a naming convention.** Each of assay's three
real invocation forms was measured before this module was written (A-327), and
each is identified through a handle that form actually exposes:

* **zipapp** (``assay-<version>.pyz``) -- ``assay.__loader__`` is a
  :class:`zipimport.zipimporter` whose ``archive`` attribute is the absolute
  path of the ``.pyz`` itself. That file IS the release artifact, so it is
  hashed directly, and the digest equals the ``.pyz.sha256`` sidecar
  ``gate/distribution/build_release.py`` writes beside it.
  ``sys.argv[0]`` is deliberately NOT used: it is whatever the caller's
  ``execve`` said, and under ``python -c`` it is the literal ``'-c'``.
  ``build_release._strip_installation_metadata`` DELETES ``direct_url.json``
  from the archived ``.dist-info`` (it embedded the builder's own output
  directory and broke reproducibility), so the wheel path below is genuinely
  unavailable inside a zipapp -- measured, not assumed.

* **installed wheel** -- the wheel file does not survive its own installation,
  so there is nothing left on disk to hash. What DOES survive is PEP 610's
  ``direct_url.json``, which the installer writes into the ``.dist-info`` and
  which records the archive's own ``sha256``. Measured against a real
  ``pip install --no-index --no-deps <wheel>`` -- exactly what
  ``tools/tester-unified-gate.sh`` does -- the recorded value is byte-equal to
  ``sha256sum`` of the wheel. That record is the installer's attestation of
  what it installed, and after the install it is the only statement about the
  wheel that exists anywhere.

  The URL it names is deliberately NOT re-hashed. The file at that path is not
  part of the installation: it may have been deleted, or legitimately rebuilt
  in place by a later build into the same output directory. Re-hashing it
  would turn an ordinary rebuild into a spurious refusal while adding nothing
  -- the installer's record is the authoritative statement about the artifact
  this process came from, and a mismatch there proves nothing about it.

* **source checkout** (an editable install, or ``PYTHONPATH=src``) -- there is
  no build artifact at all. This module returns no identity and names why. It
  never hashes ``src/`` and calls the result an artifact digest.

The refusal is total, never partial: a caller gets a whole
:class:`~assay.verdict.JudgeProvenance` or a reason string, and no shape exists
in between. Whether an absent identity is fatal is the CALLER's policy, not
this module's -- see ``assay run --require-judge-provenance``.

**Zero runtime dependencies** (A-005): stdlib only, like every other module
here.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipimport
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

from .verdict import JUDGE_DIGEST_ALGORITHMS, JudgeProvenance

__all__ = ["DIGEST_ALGORITHM", "DISTRIBUTION_NAME", "identify_judge"]

#: The algorithm this build hashes with. `JUDGE_DIGEST_ALGORITHMS` is the
#: closed vocabulary the artifact may name; this is the single member of it
#: that a producer in this build actually uses.
DIGEST_ALGORITHM = JUDGE_DIGEST_ALGORITHMS[0]

#: The distribution `importlib.metadata` is asked about. The NAME recorded in
#: the artifact is read back out of that distribution's own metadata rather
#: than being this constant, so the artifact records what the installed build
#: claims to be instead of what this source file expected it to claim.
DISTRIBUTION_NAME = "assay"

_CHUNK = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(candidate: Path, root: Path) -> bool:
    """``candidate`` is ``root`` or lives under it.

    ``Path.is_relative_to`` on already-resolved paths, spelled out because
    both operands can be *inside a zip archive* -- ``.../assay-1.0.pyz/assay/
    __init__.py`` under ``.../assay-1.0.pyz`` -- where nothing on the
    filesystem exists to stat and only the lexical relationship is available.
    """
    return candidate == root or root in candidate.parents


def _zipapp_archive(module: Any) -> Path | None:
    """The ``.pyz`` *module* was imported out of, or ``None``.

    ``zipimport.zipimporter.archive`` is the whole handle: it is the absolute
    path the interpreter itself opened, so it needs no reconciliation against
    argv, the working directory or ``__file__``.
    """
    loader = getattr(module, "__loader__", None)
    if not isinstance(loader, zipimport.zipimporter):
        return None
    archive = getattr(loader, "archive", None)
    if not archive:
        return None
    return Path(archive)


def _installed_wheel_digest(dist: Distribution) -> str | None:
    """The installed wheel's ``sha256`` from PEP 610 ``direct_url.json``, or
    ``None`` when this installation records no wheel it can name.

    Only an ``archive_info`` record naming a ``.whl`` AND carrying a ``sha256``
    qualifies. A ``dir_info`` record is a directory or editable install -- a
    source checkout wearing ``.dist-info``, with no build artifact behind it --
    and a ``vcs_info`` record names a repository, not a build. Both are
    unidentifiable here, which is the correct answer for both.
    """
    try:
        raw = dist.read_text("direct_url.json")
    except (OSError, ValueError):  # pragma: no cover - unreadable metadata dir
        return None
    if not raw:
        return None
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict):
        return None
    archive_info = document.get("archive_info")
    if not isinstance(archive_info, dict):
        return None
    url = document.get("url")
    # A wheel is the only archive form assay is installed from; refusing an
    # sdist or a bare tarball here is the point, not an oversight.
    #
    # (A-332) The fragment MUST be stripped before this test, and the query
    # with it. `pip install https://.../assay-1.0-py3-none-any.whl#sha256=...`
    # is the ordinary way to pin a wheel by URL and digest -- it is the shape
    # an index page's own links carry, and the shape a gate pinning its judge
    # by artifact URL would use. PEP 610 records that URL verbatim, fragment
    # included, so an `endswith(".whl")` test against the raw string answers
    # False and the install is reported unidentifiable. Measured against the
    # four real URL forms; only the fragment one was refused, and it was the
    # one this feature's own consumer is most likely to use.
    if not isinstance(url, str):
        return None
    if not url.split("#", 1)[0].split("?", 1)[0].endswith(".whl"):
        return None
    hashes = archive_info.get("hashes")
    digest = hashes.get("sha256") if isinstance(hashes, dict) else None
    if not isinstance(digest, str):
        # PEP 610's superseded single-hash spelling. pip still writes it
        # alongside `hashes`, and it is the only spelling older installers
        # wrote, so it is read as a fallback rather than required.
        legacy = archive_info.get("hash")
        if isinstance(legacy, str) and legacy.startswith("sha256="):
            digest = legacy.split("=", 1)[1]
    if not isinstance(digest, str) or not digest:
        return None
    digest = digest.lower()
    # The installer's record is metadata on disk, not a computed value, so it
    # is checked rather than trusted: a malformed one is an unidentifiable
    # installation, never a `JudgeProvenance` whose `digest` is garbage.
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        return None
    return digest


def identify_judge(
    module: Any = None, dist: Distribution | None = None
) -> tuple[JudgeProvenance | None, str | None]:
    """``(provenance, None)`` when the running build identifies itself, or
    ``(None, reason)`` when it cannot. Exactly one half is ever populated.

    *module* is the imported ``assay`` package and *dist* its installed
    distribution; both default to the live ones. They are parameters so each
    branch can be driven by a test with a stand-in, instead of only by
    reinstalling the interpreter -- but the two forms that matter most are also
    exercised against genuinely built artifacts, because a stand-in cannot show
    that ``pip`` really writes the hash this reads, nor that ``zipimport``
    really reports the archive a running ``.pyz`` came from: the wheel in
    ``tests/test_standalone.py``
    (``test_the_installed_wheels_own_sha256_is_what_the_verdict_records``) and
    the zipapp in ``tests/test_distribution_build_release.py``
    (``test_the_zipapps_own_sha256_is_what_identify_judge_records``).

    Never raises for an unidentifiable invocation. That state is legitimate and
    common -- every developer running out of a checkout is in it -- so the
    caller, not this function, decides whether it is fatal.
    """
    if module is None:
        module = sys.modules.get(__package__ or DISTRIBUTION_NAME)
    if module is None:  # pragma: no cover - `assay` is imported to get here
        return None, (
            f"the {DISTRIBUTION_NAME!r} package is not imported in this "
            f"interpreter, so nothing here can be identified"
        )

    if dist is None:
        try:
            dist = distribution(DISTRIBUTION_NAME)
        except PackageNotFoundError:
            return None, (
                f"no installed distribution metadata for {DISTRIBUTION_NAME!r} "
                f"was found, so this process is running from a source tree; a "
                f"source tree is not a build artifact and has no digest to "
                f"record"
            )

    name = dist.metadata["Name"]
    version = dist.metadata["Version"]
    if not name or not version:
        return None, (
            f"the installed {DISTRIBUTION_NAME!r} distribution metadata "
            f"declares no Name/Version pair, so the running build cannot name "
            f"itself"
        )

    # The distribution being FINDABLE is not evidence that it is the code
    # running. `sys.path.insert(0, ".../assay/src")` beside an installed
    # assay -- which is exactly how `gate/python/qualify_dstdns_sql.py`
    # invokes the CLI, and how any developer with an editable install and a
    # checkout runs it -- imports the SOURCE while
    # `importlib.metadata.distribution` still answers with the INSTALLED
    # `.dist-info`. Without this check the process would then report the
    # installed wheel's digest for code that wheel never contained: a
    # provenance record that is not merely absent but false, which is worse
    # than the absence this module is otherwise careful to prefer.
    origin = getattr(module, "__file__", None)
    if not origin:
        return None, (
            f"the imported {DISTRIBUTION_NAME!r} module has no __file__, so "
            f"it cannot be shown to be the installed distribution's own code"
        )
    # `str()` first, deliberately: inside a zipapp `locate_file` returns a
    # `zipp.Path`, which is NOT `os.PathLike` and which `pathlib.Path()`
    # refuses outright (measured -- it raised `TypeError` on the first real
    # zipapp run). Its string form is the ordinary `.../assay-1.0.pyz/` path
    # both operands share.
    installed_root = Path(str(dist.locate_file(""))).resolve()
    imported_from = Path(str(origin)).resolve()
    if not _is_within(imported_from, installed_root):
        return None, (
            f"this process imported {DISTRIBUTION_NAME!r} from "
            f"{str(imported_from)!r}, which is outside the installed "
            f"distribution at {str(installed_root)!r} -- the running code is "
            f"not the code that artifact contains, so its digest would name "
            f"the wrong build"
        )

    archive = _zipapp_archive(module)
    if archive is not None:
        if not archive.is_file():
            return None, (
                f"this process was imported from the zipapp {str(archive)!r}, "
                f"which is not a readable file now, so its digest cannot be "
                f"taken"
            )
        return (
            JudgeProvenance(
                name=name,
                version=version,
                artifact="zipapp",
                digest_algorithm=DIGEST_ALGORITHM,
                digest=_sha256_file(archive),
            ),
            None,
        )

    digest = _installed_wheel_digest(dist)
    if digest is None:
        return None, (
            f"the installed {DISTRIBUTION_NAME!r} distribution records no PEP "
            f"610 direct_url.json naming an installed wheel and its "
            f"{DIGEST_ALGORITHM}, so the artifact this process was installed "
            f"from cannot be identified. The most common cause is an INDEX "
            f"install (`pip install {DISTRIBUTION_NAME}` resolved from PyPI "
            f"or a private index): PEP 610 writes direct_url.json only for "
            f"DIRECT installs, so an index install records no artifact "
            f"identity anywhere on disk and none can be recovered after the "
            f"fact. Install from the wheel FILE or its URL instead "
            f"(`pip install ./{DISTRIBUTION_NAME}-<version>-py3-none-any.whl`, "
            f"or `pip install https://.../{DISTRIBUTION_NAME}-<version>"
            f"-py3-none-any.whl#sha256=<digest>`), or run the zipapp. An "
            f"editable install, a directory install, and a source checkout "
            f"carrying `*.egg-info` build residue are also this case, and "
            f"cannot be identified either"
        )
    return (
        JudgeProvenance(
            name=name,
            version=version,
            artifact="wheel",
            digest_algorithm=DIGEST_ALGORITHM,
            digest=digest,
        ),
        None,
    )
