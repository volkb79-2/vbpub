import re
from pathlib import Path
import tomllib

import pytest

from tnt.profile import (AUTH_MODES, CONFIG_SCHEMA_VERSION, HOST_KEYS, SECRET_KEYS,
                         SECRETS_SCHEMA_VERSION, TLS_MODES, load_profiles, load_secrets)


ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "README.md", ROOT / "docs/DESIGN.md", ROOT / "docs/CONSUMERS.md")


@pytest.mark.parametrize("doc", DOCS, ids=lambda path: path.name)
def test_documented_toml_host_and_secret_examples_parse_with_the_shipped_loader(tmp_path, doc):
    blocks = re.findall(r"```toml\s*\n(.*?)```", doc.read_text(), flags=re.DOTALL)
    assert blocks, f"{doc} has no parseable TOML example"
    for index, body in enumerate(blocks):
        parsed = tomllib.loads(body)
        assert parsed.get("schema_version") == CONFIG_SCHEMA_VERSION
        path = tmp_path / f"{doc.stem}-{index}.toml"
        path.write_text(body)
        path.chmod(0o600)
        host_tables = parsed.get("hosts", {})
        is_secrets = any(isinstance(table, dict) and set(table) & SECRET_KEYS
                         for table in host_tables.values()) if isinstance(host_tables, dict) else False
        if is_secrets:
            assert parsed.get("schema_version") == SECRETS_SCHEMA_VERSION
            assert load_secrets(path)
        else:
            profiles, _ = load_profiles(path)
            assert profiles


def test_committed_profile_example_parses_with_the_shipped_loader(tmp_path):
    path = tmp_path / "tnt.toml"
    body = (ROOT / "tnt.toml.example").read_text()
    assert tomllib.loads(body).get("schema_version") == CONFIG_SCHEMA_VERSION
    path.write_text(body)
    path.chmod(0o600)
    profiles, _ = load_profiles(path)
    assert set(profiles) == {"lab", "password-auth"}


def test_committed_secrets_example_parses_with_the_shipped_loader(tmp_path):
    path = tmp_path / "tnt.secrets.toml"
    body = (ROOT / "tnt.secrets.toml.example").read_text()
    assert tomllib.loads(body).get("schema_version") == SECRETS_SCHEMA_VERSION
    path.write_text(body)
    path.chmod(0o600)
    entries = load_secrets(path)
    assert set(entries) == {"lab", "password-auth"}


def _anchor(text, label):
    slug = re.sub(r"[^\w -]", "", label.lower()).strip().replace(" ", "-")
    slug = re.sub(r"-+", "-", slug)
    return slug in {m.group(1) for m in re.finditer(r"<a\s+id=[\"']([^\"']+)", text, re.I)} | {
        re.sub(r"[^\w -]", "", heading.lower()).strip().replace(" ", "-")
        for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M)
    }


def test_cross_document_links_and_anchors_resolve():
    for source in DOCS:
        text = source.read_text()
        for target, anchor in re.findall(r"\]\(([^)#]+\.md)(?:#([^)]*))?\)", text):
            resolved = (source.parent / target).resolve()
            assert resolved.is_file(), f"{source}: missing linked document {target}"
            if anchor:
                linked_text = resolved.read_text()
                assert _anchor(linked_text, anchor.replace("-", " ")), f"{source}: missing anchor {target}#{anchor}"


def test_closed_public_workflow_values_are_documented():
    documentation = "\n".join(path.read_text() for path in DOCS)
    values = [*AUTH_MODES, *TLS_MODES, "filesystem", "zvol", "FILESYSTEM", "VOLUME", "ON", "OFF", "INHERIT",
              "disk", "iso", "nic", "DISK", "CDROM", "NIC", "DISPLAY", "PCI", "RAW", "USB",
              "suspend", "resume"]
    for value in values:
        assert re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", documentation), (
            f"closed public value {value!r} is absent from README/DESIGN/CONSUMERS"
        )
    for key in (*HOST_KEYS, *SECRET_KEYS):
        assert re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", documentation), (
            f"public config key {key!r} is absent from README/DESIGN/CONSUMERS"
        )

