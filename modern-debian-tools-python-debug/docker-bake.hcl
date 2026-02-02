// Defaults are injected from the top-level release.toml via release-manager.
// Keep these empty so config comes from release.toml or explicit env vars.
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

variable "CIU_LATEST_TAG" {
  default = "ciu-wheel-latest"
}

variable "CIU_LATEST_ASSET_NAME" {
  default = ""
}

variable "CIU_INSTALL_REQUIRED" {
  default = "false"
}
variable "DEVCONTAINERS_RELEASE_STABLE" {
  default = ""
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
    CIU_LATEST_TAG = "${CIU_LATEST_TAG}"
    CIU_LATEST_ASSET_NAME = "${CIU_LATEST_ASSET_NAME}"
    CIU_INSTALL_REQUIRED = "${CIU_INSTALL_REQUIRED}"
  }
}

target "bookworm-py311" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.11-bookworm"
    PYTHON_VERSION = "3.11"
    DEBIAN_VERSION = "bookworm"
  }
  tags = [base_tag("bookworm", "3.11"), base_latest_tag("bookworm", "3.11")]
}

target "bookworm-py313" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.13-bookworm"
    PYTHON_VERSION = "3.13"
    DEBIAN_VERSION = "bookworm"
  }
  tags = [base_tag("bookworm", "3.13"), base_latest_tag("bookworm", "3.13")]
}

target "trixie-py311" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.11-trixie"
    PYTHON_VERSION = "3.11"
    DEBIAN_VERSION = "trixie"
  }
  tags = [base_tag("trixie", "3.11"), base_latest_tag("trixie", "3.11")]
}

target "trixie-py313" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.13-trixie"
    PYTHON_VERSION = "3.13"
    DEBIAN_VERSION = "trixie"
  }
  tags = [base_tag("trixie", "3.13"), base_latest_tag("trixie", "3.13")]
}

target "trixie-py314" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.14-trixie"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
  }
  tags = [base_tag("trixie", "3.14"), base_latest_tag("trixie", "3.14"), "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug:latest"]
}



target "trixie-py311-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "mcr.microsoft.com/devcontainers/python:3.11-trixie"
    PYTHON_VERSION = "3.11"
    DEBIAN_VERSION = "trixie"
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
  }
  tags = [vsc_tag("trixie", "3.11"), vsc_latest_tag("trixie", "3.11")]
}

target "trixie-py313-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "mcr.microsoft.com/devcontainers/python:3.13-trixie"
    PYTHON_VERSION = "3.13"
    DEBIAN_VERSION = "trixie"
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
  }
  tags = [vsc_tag("trixie", "3.13"), vsc_latest_tag("trixie", "3.13")]
}

# to manually extract the used devcontainer version:
# `docker image inspect mcr.microsoft.com/devcontainers/python:3.14-trixie --format '{{ index .Config.Labels "dev.containers.release" }}'` 
target "trixie-py314-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "mcr.microsoft.com/devcontainers/python:3.14-trixie"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
  }
  tags = [vsc_tag("trixie", "3.14"), vsc_latest_tag("trixie", "3.14"), "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:latest"]
}


# currently missing newer versions for devcontainer, run `./check-mcr-devcontainer-tags.py` to see what is available
group "all" {
  targets = [
    # "bookworm-py311",
    # "bookworm-py313",
    "trixie-py311",
    #"trixie-py313",
    "trixie-py314",
    "trixie-py314-vsc"
  ]
}
