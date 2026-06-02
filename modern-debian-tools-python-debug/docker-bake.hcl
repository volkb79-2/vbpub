// Defaults are injected from project-local build-push.toml [env] via step_runner.
// Keep these empty so config comes from project env defaults or explicit env vars.
variable "REGISTRY" {
  default = ""
}

// Set GITHUB_USERNAME to your GHCR org/user (e.g., volkb79-2) so pushes land at
// ghcr.io/<username>/modern-debian-tools-python-debug:<variant> and
// ghcr.io/<username>/modern-debian-tools-python-debug-vsc-devcontainer:<variant>.
variable "GITHUB_USERNAME" {
  default = ""
}

variable "GITHUB_REPO" {
  default = ""
}


// keep updated with the latest known versions to get info on the latest devcontainer base images and to set the default for the detection test target
variable "LATEST_KNOWN_DEBIAN" {
  default = "trixie"
}

// keep updated with the latest known versions to get info on the latest devcontainer base images and to set the default for the detection test target
variable "LATEST_KNOWN_PYTHON" {
  default = "3.14"
}

variable "DEVCONTAINERS_BASE_PINNED" {
  default = "mcr.microsoft.com/devcontainers/python:${LATEST_KNOWN_PYTHON}-${LATEST_KNOWN_DEBIAN}"
}

variable "DEVCONTAINERS_BASE_DYNAMIC_LATEST" {
  default = "mcr.microsoft.com/devcontainers/python:${LATEST_KNOWN_PYTHON}-${LATEST_KNOWN_DEBIAN}"
}

variable "DEVCONTAINERS_DYNAMIC_LATEST_TAG" {
  default = "${LATEST_KNOWN_PYTHON}-${LATEST_KNOWN_DEBIAN}"
}

variable "DEVCONTAINERS_DYNAMIC_LATEST_PYTHON" {
  default = "${LATEST_KNOWN_PYTHON}"
}

variable "DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN" {
  default = "${LATEST_KNOWN_DEBIAN}"
}



// Used in tags; build script sets BUILD_DATE if not provided.
variable "BUILD_DATE" {
  default = "19700101"
}

variable "BACKPORTS_URI" {
  default = "http://debian.anexia.at/debian"
}

variable "OCI_TITLE" {
  default = "modern-debian-tools-python-debug"
}

variable "OCI_DESCRIPTION" {
  default = ""
}

variable "OCI_DESCRIPTION_BASE" {
  default = "${OCI_DESCRIPTION}"
}

variable "OCI_DESCRIPTION_VSC" {
  default = "${OCI_DESCRIPTION}"
}

variable "OCI_SOURCE" {
  default = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}"
}

variable "OCI_DOCUMENTATION" {
  default = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/tree/main/modern-debian-tools-python-debug"
}

variable "OCI_URL" {
  default = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/tree/main/modern-debian-tools-python-debug"
}

variable "OCI_LICENSES" {
  default = "MIT"
}

variable "OCI_VENDOR" {
  default = ""
}

variable "OCI_VERSION" {
  default = "${BUILD_DATE}"
}

variable "OCI_REVISION" {
  default = "unknown"
}

variable "OCI_CREATED" {
  default = "unknown"
}

variable "DEVCONTAINERS_RELEASE_STABLE" {
  default = ""
}

variable "DEVCONTAINERS_RELEASE_DEV" {
  default = ""
}

variable "DEVCONTAINERS_VERSION_STABLE" {
  default = ""
}

variable "DEVCONTAINERS_VERSION_DEV" {
  default = ""
}


variable "DELTA_VERSION" {
  default = "latest"
}

variable "GH_VERSION" {
  default = "latest"
}

variable "RGA_VERSION" {
  default = "latest"
}

variable "AWSCLI_VERSION" {
  default = "latest"
}

variable "B2_VERSION" {
  default = "latest"
}

variable "BAT_VERSION" {
  default = "latest"
}

variable "CONSUL_VERSION" {
  default = "latest"
}

variable "FD_VERSION" {
  default = "latest"
}

variable "FZF_VERSION" {
  default = "latest"
}

variable "POSTGRESQL_CLIENT_VERSION" {
  default = "latest"
}

variable "REDIS_TOOLS_VERSION" {
  default = "latest"
}

variable "RIPGREP_VERSION" {
  default = "latest"
}

variable "SHELLCHECK_VERSION" {
  default = "latest"
}

variable "VAULT_VERSION" {
  default = "latest"
}

variable "YQ_VERSION" {
  default = "latest"
}

variable "CIU_WHEEL_TAG" {
  default = ""
}

variable "CIU_WHEEL_ASSET_NAME" {
  default = ""
}

variable "CIU_INSTALL_REQUIRED" {
  default = "false"
}

variable "PACKAGE_MANIFEST_ROOT" {
  default = "package-manifests-versioned"
}

function "base_tag" {
  params = [debian, python]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug:${debian}-py${python}-${BUILD_DATE}"
}

function "base_latest_tag" {
  params = [debian, python]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug:${debian}-py${python}-latest"
}

function "vsc_tag" {
  params = [debian, python]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:${debian}-py${python}-${BUILD_DATE}"
}

function "vsc_latest_tag" {
  params = [debian, python]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:${debian}-py${python}-latest"
}

function "package_docs_dir" {
  params = [package_name]
  result = "${PACKAGE_MANIFEST_ROOT}/${package_name}"
}

function "package_docs_readme_relpath" {
  params = [package_name]
  result = "${package_docs_dir(package_name)}/README.md"
}

function "package_manifest_relpath" {
  params = [package_name, debian, python]
  result = "${package_docs_dir(package_name)}/${debian}-py${python}-${BUILD_DATE}.md"
}

function "package_docs_readme_url" {
  params = [package_name]
  result = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/blob/main/modern-debian-tools-python-debug/${package_docs_readme_relpath(package_name)}"
}

function "package_latest_relpath" {
  params = [package_name]
  result = "${package_docs_dir(package_name)}/latest.md"
}

function "package_latest_url" {
  params = [package_name]
  result = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/blob/main/modern-debian-tools-python-debug/${package_latest_relpath(package_name)}"
}

function "package_manifest_url" {
  params = [package_name, debian, python]
  result = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/blob/main/modern-debian-tools-python-debug/${package_manifest_relpath(package_name, debian, python)}"
}

function "description_with_manifest_docs" {
  params = [description, package_name, debian, python]
  result = "${description} Docs: ${package_manifest_url(package_name, debian, python)}"
}

target "base" {
  context = "."
  dockerfile = "Dockerfile"
  args = {
    BACKPORTS_URI = "${BACKPORTS_URI}"
    AWSCLI_VERSION = "${AWSCLI_VERSION}"
    B2_VERSION = "${B2_VERSION}"
    BAT_VERSION = "${BAT_VERSION}"
    CONSUL_VERSION = "${CONSUL_VERSION}"
    DELTA_VERSION = "${DELTA_VERSION}"
    FD_VERSION = "${FD_VERSION}"
    FZF_VERSION = "${FZF_VERSION}"
    GH_VERSION = "${GH_VERSION}"
    OCI_TITLE = "${OCI_TITLE}"
    OCI_DESCRIPTION = "${OCI_DESCRIPTION}"
    OCI_DESCRIPTION_BASE = "${OCI_DESCRIPTION_BASE}"
    OCI_DESCRIPTION_VSC = "${OCI_DESCRIPTION_VSC}"
    OCI_SOURCE = "${OCI_SOURCE}"
    OCI_DOCUMENTATION = "${OCI_DOCUMENTATION}"
    OCI_URL = "${OCI_URL}"
    OCI_LICENSES = "${OCI_LICENSES}"
    OCI_VENDOR = "${OCI_VENDOR}"
    OCI_VERSION = "${OCI_VERSION}"
    OCI_REVISION = "${OCI_REVISION}"
    OCI_CREATED = "${OCI_CREATED}"
    POSTGRESQL_CLIENT_VERSION = "${POSTGRESQL_CLIENT_VERSION}"
    REDIS_TOOLS_VERSION = "${REDIS_TOOLS_VERSION}"
    RIPGREP_VERSION = "${RIPGREP_VERSION}"
    RGA_VERSION = "${RGA_VERSION}"
    SHELLCHECK_VERSION = "${SHELLCHECK_VERSION}"
    VAULT_VERSION = "${VAULT_VERSION}"
    YQ_VERSION = "${YQ_VERSION}"
    GITHUB_USERNAME = "${GITHUB_USERNAME}"
    GITHUB_REPO = "${GITHUB_REPO}"
    CIU_WHEEL_TAG = "${CIU_WHEEL_TAG}"
    CIU_WHEEL_ASSET_NAME = "${CIU_WHEEL_ASSET_NAME}"
    CIU_INSTALL_REQUIRED = "${CIU_INSTALL_REQUIRED}"
    PACKAGE_MANIFEST_SOURCE = ""
    DEVCONTAINERS_RELEASE_STABLE = "${DEVCONTAINERS_RELEASE_STABLE}"
    DEVCONTAINERS_RELEASE_DEV = "${DEVCONTAINERS_RELEASE_DEV}"
    DEVCONTAINERS_VERSION_STABLE = "${DEVCONTAINERS_VERSION_STABLE}"
    DEVCONTAINERS_VERSION_DEV = "${DEVCONTAINERS_VERSION_DEV}"
  }
}

target "bookworm-py311" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.11-bookworm"
    PYTHON_VERSION = "3.11"
    DEBIAN_VERSION = "bookworm"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_BASE, "modern-debian-tools-python-debug", "bookworm", "3.11")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug", "bookworm", "3.11")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug", "bookworm", "3.11")
  }
  tags = [base_tag("bookworm", "3.11"), base_latest_tag("bookworm", "3.11")]
}

target "bookworm-py313" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.13-bookworm"
    PYTHON_VERSION = "3.13"
    DEBIAN_VERSION = "bookworm"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_BASE, "modern-debian-tools-python-debug", "bookworm", "3.13")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug", "bookworm", "3.13")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug", "bookworm", "3.13")
  }
  tags = [base_tag("bookworm", "3.13"), base_latest_tag("bookworm", "3.13")]
}

target "trixie-py311" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.11-trixie"
    PYTHON_VERSION = "3.11"
    DEBIAN_VERSION = "trixie"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_BASE, "modern-debian-tools-python-debug", "trixie", "3.11")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug", "trixie", "3.11")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug", "trixie", "3.11")
  }
  tags = [base_tag("trixie", "3.11"), base_latest_tag("trixie", "3.11")]
}

target "trixie-py313" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.13-trixie"
    PYTHON_VERSION = "3.13"
    DEBIAN_VERSION = "trixie"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_BASE, "modern-debian-tools-python-debug", "trixie", "3.13")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug", "trixie", "3.13")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug", "trixie", "3.13")
  }
  tags = [base_tag("trixie", "3.13"), base_latest_tag("trixie", "3.13")]
}

target "trixie-py314" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.14-trixie"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_BASE, "modern-debian-tools-python-debug", "trixie", "3.14")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug", "trixie", "3.14")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug", "trixie", "3.14")
  }
  tags = [base_tag("trixie", "3.14"), base_latest_tag("trixie", "3.14"), "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug:latest"]
}



target "trixie-py311-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "mcr.microsoft.com/devcontainers/python:3.11-trixie"
    PYTHON_VERSION = "3.11"
    DEBIAN_VERSION = "trixie"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_VSC, "modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.11")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.11")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug-vsc-devcontainer")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.11")
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
    DEVCONTAINERS_VERSION = "${DEVCONTAINERS_VERSION_STABLE}"
  }
  tags = [vsc_tag("trixie", "3.11"), vsc_latest_tag("trixie", "3.11")]
}

target "trixie-py313-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "mcr.microsoft.com/devcontainers/python:3.13-trixie"
    PYTHON_VERSION = "3.13"
    DEBIAN_VERSION = "trixie"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_VSC, "modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.13")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.13")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug-vsc-devcontainer")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.13")
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
    DEVCONTAINERS_VERSION = "${DEVCONTAINERS_VERSION_STABLE}"
  }
  tags = [vsc_tag("trixie", "3.13"), vsc_latest_tag("trixie", "3.13")]
}


# to manually extract the used devcontainer version:
# `docker image inspect <image> --format '{{ index .Config.Labels "net.volkb79.base-devcontainers-release" }}'`
# `docker image inspect <image> --format '{{ index .Config.Labels "net.volkb79.base-devcontainers-version" }}'`
target "trixie-py314-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "${DEVCONTAINERS_BASE_PINNED}"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_VSC, "modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug-vsc-devcontainer")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14")
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
    DEVCONTAINERS_VERSION = "${DEVCONTAINERS_VERSION_STABLE}"
  }
  tags = [vsc_tag("trixie", "3.14"), vsc_latest_tag("trixie", "3.14"), "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:latest"]
}


# Dynamic target that picks latest stable python/debian combination from live resolver output.
# Resolver sets DEVCONTAINERS_BASE_DYNAMIC_LATEST, DEVCONTAINERS_DYNAMIC_LATEST_PYTHON,
# and DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN.
target "latest-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "${DEVCONTAINERS_BASE_DYNAMIC_LATEST}"
    PYTHON_VERSION = "${DEVCONTAINERS_DYNAMIC_LATEST_PYTHON}"
    DEBIAN_VERSION = "${DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN}"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_VSC, "modern-debian-tools-python-debug-vsc-devcontainer", DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN, DEVCONTAINERS_DYNAMIC_LATEST_PYTHON)
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug-vsc-devcontainer", DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN, DEVCONTAINERS_DYNAMIC_LATEST_PYTHON)
    OCI_URL = package_latest_url("modern-debian-tools-python-debug-vsc-devcontainer")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug-vsc-devcontainer", DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN, DEVCONTAINERS_DYNAMIC_LATEST_PYTHON)
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
    DEVCONTAINERS_VERSION = "${DEVCONTAINERS_VERSION_STABLE}"
  }
  tags = ["${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:latest-stable"]
}

group "detection" {
  targets = [
    "latest-vsc"
  ]
}



# currently missing newer versions for devcontainer, run `./check-mcr-devcontainer-tags.py` to see what is available
group "all" {
  targets = [
    # "bookworm-py311",
    # "bookworm-py313",
    "trixie-py311",
    "trixie-py314-vsc",
    "latest-vsc",
    #"trixie-py314",
    #"trixie-py314-vsc",
    #"latest-vsc"
  ]
}
