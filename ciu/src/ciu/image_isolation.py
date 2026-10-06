"""Per-worktree image references and exact container-name ownership checks.

The helpers here operate on rendered Compose / Buildx data. They do not infer
ownership from image or container names: project-built Compose images are
identified by a ``build`` declaration, and existing containers are accepted
only when Docker's Compose labels prove the same checkout, project, and
service.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class ImageIsolationError(ValueError):
    """A worktree image reference cannot be isolated safely."""


class ContainerOwnershipError(ValueError):
    """An explicit Compose container name cannot be proven safe to reuse."""


def compose_may_require_image_scoping(compose_yaml: str) -> bool:
    """Whether local or imported Compose definitions may contain built images."""
    import yaml

    try:
        document = yaml.safe_load(compose_yaml) or {}
    except yaml.YAMLError as exc:
        raise ImageIsolationError(f"rendered Compose YAML is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise ImageIsolationError("rendered Compose document must be a YAML mapping")
    if document.get("include"):
        return True
    services = document.get("services", {})
    if not isinstance(services, dict):
        raise ImageIsolationError("rendered Compose services must be a YAML mapping")
    for service in services.values():
        if not isinstance(service, dict):
            raise ImageIsolationError("rendered Compose services must be mappings")
        if service.get("extends") is not None:
            return True
        build = service.get("build")
        if build is not None and build is not False:
            return True
    return False


def _split_image_name_tag(reference: str) -> tuple[str, str]:
    """Split a mutable image reference while preserving registry ports."""
    if not isinstance(reference, str) or not reference.strip():
        raise ImageIsolationError("project-built image reference must be a non-empty string")
    if "@" in reference:
        raise ImageIsolationError(
            f"cannot instance-scope digest image reference {reference!r}; "
            "declare a mutable name:tag for this project-built image"
        )
    depth = 0
    slash = -1
    separator = -1
    i = 0
    while i < len(reference):
        if reference.startswith("${", i):
            depth += 1
            i += 2
            continue
        char = reference[i]
        if char == "}" and depth:
            depth -= 1
        elif char == "/" and depth == 0:
            slash = i
            separator = -1
        elif char == ":" and depth == 0:
            separator = i
        i += 1

    if "$" in reference and slash < 0 and separator < 0:
        raise ImageIsolationError(
            f"cannot safely instance-scope fully interpolated image reference "
            f"{reference!r}; render a concrete project-built image name"
        )

    if slash == 0:
        raise ImageIsolationError(
            f"image reference {reference!r} has an empty image name before its path"
        )

    if separator < 0:
        return reference, "latest"
    name, tag = reference[:separator], reference[separator + 1:]
    if not name:
        raise ImageIsolationError(
            f"image reference {reference!r} has an empty image name"
        )
    if not tag:
        raise ImageIsolationError(f"image reference {reference!r} has an empty tag")
    return name, tag


def append_instance_tag(reference: str, instance_id: str) -> str:
    """Append ``-<instance_id>`` to an image tag, preserving registry ports.

    Untagged references use Docker's implicit ``latest`` tag. Compose
    interpolation expressions may contain colons, so only a colon outside a
    ``${...}`` expression in the final path component is treated as the tag
    separator.
    """
    if not isinstance(instance_id, str) or not instance_id:
        raise ImageIsolationError("worktree image scoping requires a non-empty instance id")
    name, tag = _split_image_name_tag(reference)
    suffix = f"-{instance_id}"
    if tag.endswith(suffix):
        return reference
    return f"{name}:{tag}{suffix}"


def normalize_image_reference(reference: str) -> str:
    """Make implicit ``latest`` explicit for exact tag comparisons."""
    if not isinstance(reference, str) or not reference.strip():
        raise ImageIsolationError("image reference must be a non-empty string")
    if "@" in reference:
        return reference
    name, tag = _split_image_name_tag(reference)
    return f"{name}:{tag}"


def project_built_image_references(compose_yaml: str) -> set[str]:
    """Return normalized image refs owned by build declarations in Compose."""
    import yaml

    try:
        document = yaml.safe_load(compose_yaml) or {}
    except yaml.YAMLError as exc:
        raise ImageIsolationError(f"rendered Compose YAML is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise ImageIsolationError("rendered Compose document must be a YAML mapping")
    if document.get("include"):
        raise ImageIsolationError(
            "Compose include imports image declarations outside the rendered file; "
            "linked-worktree image tags cannot be proven safe"
        )
    services = document.get("services", {})
    if not isinstance(services, dict):
        raise ImageIsolationError("rendered Compose services must be a YAML mapping")

    references: set[str] = set()
    for service_name, service in services.items():
        if not isinstance(service, dict):
            raise ImageIsolationError(
                f"rendered Compose service {service_name!r} must be a YAML mapping"
            )
        if service.get("extends") is not None:
            raise ImageIsolationError(
                f"Compose service {service_name!r} extends a definition outside its "
                "rendered service mapping; linked-worktree image tags cannot be proven safe"
            )
        build = service.get("build")
        if build is None or build is False:
            continue
        reference = service.get("image")
        if not isinstance(reference, str) or not reference.strip():
            raise ImageIsolationError(
                f"project-built service {service_name!r} has no explicit image: "
                "declare image: so CIU can isolate its worktree tag"
            )
        references.add(normalize_image_reference(reference))
    return references


def check_primary_image_collisions(
    candidate_references: set[str], primary_references: set[str]
) -> None:
    """Refuse exact project tags already named by the primary image map."""
    candidates = {normalize_image_reference(ref) for ref in candidate_references}
    primary = {normalize_image_reference(ref) for ref in primary_references}
    unresolved = sorted(ref for ref in candidates | primary if "$" in ref)
    if unresolved:
        raise ImageIsolationError(
            f"cannot prove primary image-map safety for unresolved Compose "
            f"reference {unresolved[0]!r}"
        )
    conflicts = sorted(candidates & primary)
    if conflicts:
        raise ImageIsolationError(
            f"refusing to overwrite {conflicts[0]}, which the primary names"
        )


def scope_compose_images(compose_yaml: str, instance_id: str | None) -> str:
    """Scope built image references in rendered Compose YAML.

    Ownership is reference-level: when one service declares ``build`` for an
    image, every service in that Compose model using the same image receives
    the scoped reference. Pulled images without a matching build declaration
    are left byte-for-byte as written.
    """
    if instance_id is None:
        return compose_yaml
    import yaml
    from yaml.nodes import MappingNode, ScalarNode, SequenceNode

    loader = yaml.SafeLoader(compose_yaml)
    try:
        document = loader.get_single_node()
        if document is not None:
            visited: set[int] = set()

            def flatten_merges(node):
                if id(node) in visited:
                    return
                visited.add(id(node))
                if isinstance(node, MappingNode):
                    loader.flatten_mapping(node)
                    for key_node, value_node in node.value:
                        flatten_merges(key_node)
                        flatten_merges(value_node)
                elif isinstance(node, SequenceNode):
                    for value_node in node.value:
                        flatten_merges(value_node)

            flatten_merges(document)
    except yaml.YAMLError as exc:
        raise ImageIsolationError(f"rendered Compose YAML is invalid: {exc}") from exc
    finally:
        loader.dispose()
    if document is None:
        return compose_yaml
    if not isinstance(document, MappingNode):
        raise ImageIsolationError("rendered Compose document must be a YAML mapping")

    def mapping_value(node: MappingNode, wanted: str):
        found = None
        for key_node, value_node in node.value:
            if isinstance(key_node, ScalarNode) and key_node.value == wanted:
                found = value_node
        return found

    if mapping_value(document, "include") is not None:
        raise ImageIsolationError(
            "Compose include imports image declarations outside the rendered file; "
            "linked-worktree image tags cannot be proven safe"
        )

    services_node = mapping_value(document, "services")
    if services_node is None:
        return compose_yaml
    if not isinstance(services_node, MappingNode):
        raise ImageIsolationError("rendered Compose services must be a YAML mapping")

    built_references: set[str] = set()
    for service_key, service_node in services_node.value:
        service_name = service_key.value if isinstance(service_key, ScalarNode) else repr(service_key)
        if not isinstance(service_node, MappingNode):
            raise ImageIsolationError(
                f"rendered Compose service {service_name!r} must be a YAML mapping"
            )
        if mapping_value(service_node, "extends") is not None:
            raise ImageIsolationError(
                f"Compose service {service_name!r} extends a definition outside its "
                "rendered service mapping; linked-worktree image tags cannot be proven safe"
            )
        build_node = mapping_value(service_node, "build")
        if build_node is None or build_node.tag == "tag:yaml.org,2002:null":
            continue
        if build_node.tag == "tag:yaml.org,2002:bool":
            if not isinstance(build_node, ScalarNode):
                raise ImageIsolationError(
                    "Compose build boolean must be a scalar"
                )
            if build_node.value.lower() == "false":
                continue
            raise ImageIsolationError(
                "Compose build boolean may only disable a service with false"
            )
        image_node = mapping_value(service_node, "image")
        if not isinstance(image_node, ScalarNode) or not image_node.value.strip():
            raise ImageIsolationError(
                f"project-built service {service_name!r} has no explicit image: "
                "declare image: so CIU can isolate its worktree tag"
            )
        built_references.add(image_node.value)

    if not built_references:
        return compose_yaml

    replacements: dict[tuple[int, int], str] = {}
    # Scope all services which refer to a project-built image in this model,
    # including consumers whose own service has no ``build`` stanza.
    for _service_key, service_node in services_node.value:
        image_node = mapping_value(service_node, "image")
        if isinstance(image_node, ScalarNode) and image_node.value in built_references:
            scoped = append_instance_tag(image_node.value, instance_id)
            if scoped == image_node.value:
                continue
            start = image_node.start_mark.index
            end = image_node.end_mark.index
            if image_node.style in {"|", ">"}:
                raise ImageIsolationError(
                    "multiline image references cannot be instance-scoped safely"
                )
            if "\n" in scoped or "\r" in scoped:
                raise ImageIsolationError(
                    "image references containing line breaks cannot be instance-scoped safely"
                )
            # Serialize as a mapping value so PyYAML quotes according to YAML
            # scalar rules without appending a top-level document terminator
            # (`...`) into the middle of the Compose file.
            rendered_entry = yaml.safe_dump(
                {"value": scoped},
                default_style=image_node.style,
                allow_unicode=True,
            )
            rendered_scalar = rendered_entry.split(": ", 1)[1].rstrip("\n")
            replacements[(start, end)] = rendered_scalar

    if not replacements:
        return compose_yaml
    rendered = compose_yaml
    for (start, end), value in sorted(replacements.items(), reverse=True):
        rendered = rendered[:start] + value + rendered[end:]
    return rendered


def compose_image_override(original_yaml: str, scoped_yaml: str) -> str | None:
    """Return a minimal Compose override containing only changed image refs.

    Shipped Compose files stay the first file in the Compose file list so
    their relative paths keep their existing base. The generated second file
    overrides only ``services.<name>.image``; copying full service mappings
    would concatenate list-valued fields such as ports and volumes.
    """
    import yaml

    try:
        original = yaml.safe_load(original_yaml) or {}
        scoped = yaml.safe_load(scoped_yaml) or {}
    except yaml.YAMLError as exc:
        raise ImageIsolationError(
            f"rendered Compose YAML is invalid while building the image override: {exc}"
        ) from exc
    if not isinstance(original, dict) or not isinstance(scoped, dict):
        raise ImageIsolationError("rendered Compose documents must be YAML mappings")
    original_services = original.get("services", {})
    scoped_services = scoped.get("services", {})
    if not isinstance(original_services, dict) or not isinstance(scoped_services, dict):
        raise ImageIsolationError("rendered Compose services must be YAML mappings")

    changed: dict[str, dict[str, str]] = {}
    for service_name, scoped_service in scoped_services.items():
        if not isinstance(service_name, str) or not isinstance(scoped_service, dict):
            raise ImageIsolationError("rendered Compose services must have named mappings")
        original_service = original_services.get(service_name)
        if not isinstance(original_service, dict):
            continue
        scoped_image = scoped_service.get("image")
        if (
            isinstance(scoped_image, str)
            and scoped_image != original_service.get("image")
        ):
            changed[service_name] = {"image": scoped_image}
    if not changed:
        return None
    return yaml.safe_dump(
        {"services": changed},
        sort_keys=False,
        allow_unicode=True,
    )


def bake_tag_overrides(print_output: str, instance_id: str) -> list[str]:
    """Return Buildx ``--set`` arguments that scope every image Bake target.

    ``docker buildx bake --print`` resolves the target/tag set exactly as the
    subsequent build will. Every tagged Bake target is a project build; vendor
    images are Compose inputs and are not Bake targets. Repeating the exact
    ``target.tags=`` override replaces the original tag list while preserving
    aliases; using ``+=`` would retain the shared tag.
    """
    try:
        plan = json.loads(print_output)
    except json.JSONDecodeError as exc:
        raise ImageIsolationError(f"docker buildx bake --print returned invalid JSON: {exc}") from exc
    targets = plan.get("target") if isinstance(plan, dict) else None
    if not isinstance(targets, dict):
        raise ImageIsolationError("docker buildx bake --print omitted its target mapping")

    overrides: list[str] = []
    for target_name, target in sorted(targets.items()):
        if not isinstance(target, dict):
            raise ImageIsolationError(f"Buildx target {target_name!r} must be a mapping")
        tags = target.get("tags")
        if tags is None:
            continue
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ImageIsolationError(f"Buildx target {target_name!r} has malformed tags")
        scoped = [append_instance_tag(tag, instance_id) for tag in tags]
        if scoped == tags:
            continue
        if not target_name or any(ch in target_name for ch in "*.?[]\\"):
            raise ImageIsolationError(
                f"Buildx target name {target_name!r} cannot be addressed safely with --set"
            )
        for tag in scoped:
            overrides.extend(["--set", f"{target_name}.tags={tag}"])
    return overrides


def bake_scoped_tags(print_output: str, instance_id: str) -> set[str]:
    """Return all normalized output tags in a linked-worktree Bake plan."""
    try:
        plan = json.loads(print_output)
    except json.JSONDecodeError as exc:
        raise ImageIsolationError(f"docker buildx bake --print returned invalid JSON: {exc}") from exc
    targets = plan.get("target") if isinstance(plan, dict) else None
    if not isinstance(targets, dict):
        raise ImageIsolationError("docker buildx bake --print omitted its target mapping")
    result: set[str] = set()
    for target_name, target in targets.items():
        if not isinstance(target, dict):
            raise ImageIsolationError(f"Buildx target {target_name!r} must be a mapping")
        tags = target.get("tags")
        if tags is None:
            continue
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ImageIsolationError(f"Buildx target {target_name!r} has malformed tags")
        result.update(normalize_image_reference(append_instance_tag(tag, instance_id)) for tag in tags)
    return result


def explicit_container_services(compose_yaml: str) -> dict[str, str]:
    """Return exact ``container_name`` → service mappings from Compose YAML."""
    import yaml

    try:
        document = yaml.safe_load(compose_yaml) or {}
    except yaml.YAMLError as exc:
        raise ContainerOwnershipError(f"rendered Compose YAML is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise ContainerOwnershipError("rendered Compose document must be a YAML mapping")
    services = document.get("services", {})
    if not isinstance(services, dict):
        raise ContainerOwnershipError("rendered Compose services must be a YAML mapping")
    names: dict[str, str] = {}
    for service_name, service in services.items():
        if not isinstance(service, dict):
            raise ContainerOwnershipError(
                f"rendered Compose service {service_name!r} must be a YAML mapping"
            )
        name = service.get("container_name")
        if name is None:
            continue
        if not isinstance(name, str) or not name.strip():
            raise ContainerOwnershipError(
                f"rendered Compose service {service_name!r} has an invalid container_name"
            )
        if name in names:
            raise ContainerOwnershipError(
                f"rendered Compose services {names[name]!r} and {service_name!r} "
                f"both declare container_name {name!r}"
            )
        names[name] = str(service_name)
    return names


def compose_may_import_service_definitions(compose_yaml: str) -> bool:
    """Whether Compose can add services outside this YAML document."""
    import yaml

    try:
        document = yaml.safe_load(compose_yaml) or {}
    except yaml.YAMLError as exc:
        raise ContainerOwnershipError(f"rendered Compose YAML is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise ContainerOwnershipError("rendered Compose document must be a YAML mapping")
    includes = document.get("include")
    if includes:
        return True
    services = document.get("services", {})
    if not isinstance(services, dict):
        raise ContainerOwnershipError("rendered Compose services must be a YAML mapping")
    return any(
        isinstance(service, dict) and service.get("extends") is not None
        for service in services.values()
    )


def docker_exact_container_ids(listing: str, expected_names: set[str]) -> dict[str, str]:
    """Parse Docker's ``ID NAME`` listing and match names exactly."""
    found: dict[str, str] = {}
    for line_number, line in enumerate(listing.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ContainerOwnershipError(
                f"docker ps returned malformed row {line_number}: {line!r}"
            )
        container_id, name = parts
        if name in expected_names:
            if name in found:
                raise ContainerOwnershipError(
                    f"docker ps listed multiple containers named {name!r}"
                )
            found[name] = container_id
    return found


def inspect_labels(payload: str, container_id: str) -> tuple[str, Mapping[str, Any]]:
    """Parse one ``docker inspect`` response and return name and labels."""
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ContainerOwnershipError(
            f"docker inspect returned invalid JSON for {container_id}: {exc}"
        ) from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ContainerOwnershipError(
            f"docker inspect returned no unique container for {container_id}"
        )
    row = rows[0]
    name = row.get("Name")
    config = row.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(name, str) or not name.startswith("/"):
        raise ContainerOwnershipError(
            f"docker inspect omitted the container name for {container_id}"
        )
    if labels is None:
        labels = {}
    if not isinstance(labels, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in labels.items()
    ):
        raise ContainerOwnershipError(
            f"docker inspect returned malformed labels for {name[1:]}"
        )
    return name[1:], labels


def same_compose_owner(
    labels: Mapping[str, str], *, current_roots: set[str],
    project: str, service: str,
) -> tuple[bool, str | None]:
    """Whether Docker labels prove this exact name belongs to this caller."""
    working_dir = labels.get("com.docker.compose.project.working_dir")
    ciu_root = labels.get("ciu.repo-root")
    owner_roots = {value for value in (working_dir, ciu_root) if value}
    if not owner_roots:
        return False, None
    owner = ", ".join(sorted(owner_roots))
    same = (
        owner_roots <= current_roots
        and labels.get("com.docker.compose.project") == project
        and labels.get("com.docker.compose.service") == service
    )
    return same, owner
