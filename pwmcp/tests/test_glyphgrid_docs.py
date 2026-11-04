"""Keep every committed GlyphGrid font atlas traceable from the human-readable NOTICE."""
import ast
import json
import re
import sys
from urllib.parse import unquote, urlsplit
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "glyphgrid"
NOTICE = ROOT / "tools" / "glyphgrid" / "NOTICE.md"
INVENTORY_START = "<!-- committed-font-inventory:start -->"
INVENTORY_END = "<!-- committed-font-inventory:end -->"
DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "DESIGN-GUIDE.md",
    ROOT / "docs" / "CONSUMERS.md",
    ROOT / "tools" / "glyphgrid" / "README.md",
)

BASE_LICENCES = {
    "font:edk2-8x19": "BSD-2-Clause-Patent",
    "font:unifont-16": "SIL OFL-1.1",
    "font:vga-8x16": "GPL-2.0",
}


def _inventory():
    notice = NOTICE.read_text(encoding="utf-8")
    body = notice.split(INVENTORY_START, 1)[1].split(INVENTORY_END, 1)[0]
    rows = {}
    for line in body.splitlines():
        if not line.startswith("| `font:"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == 6, line
        name = cells[0].strip("`")
        assert name not in rows, name
        rows[name] = cells
    return rows


def test_notice_lists_each_committed_font_with_source_version_hash_and_licence():
    rows = _inventory()
    expected = {}
    for atlas_path in sorted(FIXTURES.glob("*.atlas.json")):
        atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
        for name, font in atlas["fonts"].items():
            assert name not in expected, name
            expected[name] = (atlas_path.name, font["origin"])

    assert set(rows) == set(expected)
    for name, (atlas_name, origin) in expected.items():
        _, actual_atlas, source_file, source_version, source_sha256, licence = rows[name]
        provenance = origin.get("provenance", {})
        path = provenance.get("path") or provenance.get("path_in_iso") or provenance.get("path_in_image")
        assert actual_atlas.strip("`") == atlas_name
        assert path and path in source_file, name
        assert origin["sha256"] in source_sha256, name
        assert licence and not any(word in licence.lower() for word in ("missing", "tbd", "unknown")), name

        if provenance.get("package"):
            assert provenance["package"] in source_version, name
        if provenance.get("version"):
            assert provenance["version"] in source_version, name
        if provenance.get("iso"):
            assert provenance["iso"] in source_version, name
        if provenance.get("image"):
            assert "13.7.0" in source_version and provenance.get("kernel") in source_version, name
        stated_licence = provenance.get("licence", "")
        licence_terms = re.findall(
            r"(?:GPL-[0-9.]+(?:-or-later)?|(?:SIL )?OFL-[0-9.]+|BSD-[0-9A-Za-z.-]+|MIT/X11-style|public domain)",
            stated_licence,
            re.IGNORECASE,
        )
        for term in licence_terms:
            assert term.lower() in licence.lower(), (name, term)
        if name in BASE_LICENCES:
            assert BASE_LICENCES[name].lower() in licence.lower(), name
        if str(provenance.get("licence", "")).startswith("UNVERIFIED"):
            assert "unverified" in licence.lower(), name


def test_notice_source_hash_whitelist_matches_the_notice():
    sys.path.insert(0, str(ROOT / "tools" / "glyphgrid"))
    import notice_hashes

    problems = notice_hashes.check_notice(NOTICE.read_text(encoding="utf-8"))
    assert problems == []


def test_imagegen_docs_keep_generated_pngs_under_ignored_scratch():
    tool_root = ROOT / "tools" / "glyphgrid"
    ignore = (tool_root / ".gitignore").read_text(encoding="utf-8")
    readme = (tool_root / "README.md").read_text(encoding="utf-8")
    findings = (tool_root / "imagegen" / "IMAGEGEN-FINDINGS.md").read_text(encoding="utf-8")
    assert "scratch/" in ignore
    assert "-o scratch/" in readme
    assert "-o scratch/" in findings
    assert "imagegen/IMAGEGEN-FINDINGS.md" in readme


def _assigned_node(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
    raise AssertionError(f"{name} assignment not found in {path}")


def _dict_keys(path: Path, name: str) -> set[str]:
    value = _assigned_node(path, name)
    assert isinstance(value, ast.Dict)
    return {ast.literal_eval(key) for key in value.keys}


def _parser_values(path: Path, method: str, option: str | None = None) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != method:
            continue
        first = ast.literal_eval(node.args[0]) if node.args else None
        if option is not None:
            options = {
                ast.literal_eval(arg)
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("--")
            }
            if option not in options:
                continue
            choice = next((kw.value for kw in node.keywords if kw.arg == "choices"), None)
            if choice is not None:
                values.update(ast.literal_eval(choice))
        elif first is not None:
            values.add(first)
    return values


def _markdown_anchors(path: Path) -> set[str]:
    anchors = set()
    duplicates = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", match.group(1))
        title = re.sub(r"`([^`]+)`", r"\1", title)
        slug = re.sub(r"\s", "-", re.sub(r"[^\w -]", "", title.lower()).strip())
        count = duplicates.get(slug, 0)
        duplicates[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    anchors.update(re.findall(r"\bid=[\"']([^\"']+)[\"']", path.read_text(encoding="utf-8")))
    return anchors


def test_glyphgrid_docs_cross_links_resolve():
    markdown_link = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for source in DOCS:
        text = source.read_text(encoding="utf-8")
        for raw_target in markdown_link.findall(text):
            target_url = urlsplit(raw_target.strip().split(maxsplit=1)[0].strip("<>"))
            if target_url.scheme or target_url.netloc or not target_url.fragment:
                continue
            target = (source.parent / unquote(target_url.path)).resolve() if target_url.path else source
            if target.suffix != ".md":
                continue
            assert target.is_file(), f"{source.relative_to(ROOT)} links to missing {target_url.path}"
            assert unquote(target_url.fragment) in _markdown_anchors(target), (
                f"{source.relative_to(ROOT)} links to missing anchor {target_url.fragment!r} in {target}"
            )


def test_imagegen_closed_cli_vocabulary_is_documented():
    tool_root = ROOT / "tools" / "glyphgrid"
    imagegen = tool_root / "imagegen"
    main = imagegen / "__main__.py"
    screens = ast.literal_eval(_assigned_node(imagegen / "suite.py", "SCREENS"))
    fonts = ast.literal_eval(_assigned_node(imagegen / "fonts.py", "BITMAP_IDS"))
    vocabulary = set(screens) | set(fonts)
    vocabulary |= _dict_keys(imagegen / "transforms.py", "TRANSFORMS")
    vocabulary |= _dict_keys(imagegen / "transforms.py", "FILTERS")
    vocabulary |= _parser_values(main, "add_parser")
    vocabulary |= _parser_values(main, "add_argument", "--engine")
    vocabulary |= _parser_values(main, "add_argument", "--by")
    documentation = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    missing = sorted(value for value in vocabulary if value not in documentation)
    assert missing == []


def test_eval_glyphgrid_defaults_derive_from_the_script_location():
    """PWMCP-21: no home-directory or worktree path; the series default is the documented artifacts folder."""

    sys.path.insert(0, str(ROOT / "tools" / "glyphgrid"))
    try:
        import eval_glyphgrid
    finally:
        sys.path.pop(0)
    assert Path(eval_glyphgrid.DEFAULT_SERIES) == ROOT / "artifacts" / "console-corpus" / "frames"
    assert Path(eval_glyphgrid.DEFAULT_CORPUS) == ROOT / "tests" / "corpus" / "console" / "truenas-27-vanilla"
    assert Path(eval_glyphgrid.DEFAULT_CORPUS).is_dir()
