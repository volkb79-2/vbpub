// === Registry / identity ===================================================
// step_runner seeds these from project-local env defaults; empty values keep local
// builds portable and let explicit env vars / release automation override them.
variable "REGISTRY" {
  default = ""
}

// GHCR owner used in image tags and OCI URLs; build-push / cmru fill this from env.
variable "GITHUB_USERNAME" {
  default = ""
}

// Repository name used in OCI source/documentation URLs and package-manifest links.
variable "GITHUB_REPO" {
  default = ""
}

// === Base-image selection ==================================================
// Human-maintained baseline Debian codename for the "known latest" devcontainer base.
// Update this when MCR publishes a newer stable devcontainer Debian release.
variable "LATEST_KNOWN_DEBIAN" {
  default = "trixie"
}

// Human-maintained baseline Python major.minor for the "known latest" devcontainer base.
// Update this when MCR publishes a newer stable devcontainer Python release.
variable "LATEST_KNOWN_PYTHON" {
  default = "3.14"
}

// Hard-pinned devcontainer base image for the release baseline; build-push defaults to this.
variable "DEVCONTAINERS_BASE_PINNED" {
  default = "mcr.microsoft.com/devcontainers/python:${LATEST_KNOWN_PYTHON}-${LATEST_KNOWN_DEBIAN}"
}

// Resolver injection point for the live latest devcontainer base; build-push overrides it.
variable "DEVCONTAINERS_BASE_DYNAMIC_LATEST" {
  default = "mcr.microsoft.com/devcontainers/python:${LATEST_KNOWN_PYTHON}-${LATEST_KNOWN_DEBIAN}"
}

// Resolver-injected tag suffix for the live latest devcontainer base image.
variable "DEVCONTAINERS_DYNAMIC_LATEST_TAG" {
  default = "${LATEST_KNOWN_PYTHON}-${LATEST_KNOWN_DEBIAN}"
}

// Resolver-injected Python version for the live latest devcontainer base image.
variable "DEVCONTAINERS_DYNAMIC_LATEST_PYTHON" {
  default = "${LATEST_KNOWN_PYTHON}"
}

// Resolver-injected Debian codename for the live latest devcontainer base image.
variable "DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN" {
  default = "${LATEST_KNOWN_DEBIAN}"
}

// === Build / OCI metadata ==================================================
// Date stamp used in tags; build-push sets this when it orchestrates a release.
variable "BUILD_DATE" {
  default = "19700101"
}

// Wall-clock UTC timestamp for this concrete image build. Unlike OCI_CREATED,
// this is not commit-derived reproducibility metadata.
variable "BUILD_TIMESTAMP" {
  default = "unknown"
}

// Mirror used for backports installs; callers override only when they need a different mirror.
variable "BACKPORTS_URI" {
  default = "http://debian.anexia.at/debian"
}

// Short image title recorded in OCI metadata labels.
variable "OCI_TITLE" {
  default = "modern-debian-tools-python-debug"
}

// Base human-readable image description; targets extend this with manifest links.
variable "OCI_DESCRIPTION" {
  default = ""
}

// Base-package description placeholder passed through so target-specific docs can append details.
variable "OCI_DESCRIPTION_BASE" {
  default = "${OCI_DESCRIPTION}"
}

// VSC-package description placeholder passed through so target-specific docs can append details.
variable "OCI_DESCRIPTION_VSC" {
  default = "${OCI_DESCRIPTION}"
}

// OCI source URL shown in image metadata; usually the GitHub repository root.
variable "OCI_SOURCE" {
  default = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}"
}

// OCI documentation URL for the release docs page; targets override this per manifest.
variable "OCI_DOCUMENTATION" {
  default = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/tree/main/modern-debian-tools-python-debug"
}

// Canonical project URL mirrored into OCI metadata.
variable "OCI_URL" {
  default = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/tree/main/modern-debian-tools-python-debug"
}

// SPDX license identifier for the published images.
variable "OCI_LICENSES" {
  default = "MIT"
}

// Optional vendor string; build-push / env may fill this in.
variable "OCI_VENDOR" {
  default = ""
}

// Image version label; build-push populates this with the release date or git-derived version.
variable "OCI_VERSION" {
  default = "${BUILD_DATE}"
}

// Git revision label written into image metadata.
variable "OCI_REVISION" {
  default = "unknown"
}

// Creation timestamp label written into image metadata.
variable "OCI_CREATED" {
  default = "unknown"
}

// === Devcontainer release metadata =======================================
// Stable devcontainer release identifier copied into image labels by the resolver.
variable "DEVCONTAINERS_RELEASE_STABLE" {
  default = ""
}

// Dev-only devcontainer release identifier; resolver still emits it for compatibility/debugging.
variable "DEVCONTAINERS_RELEASE_DEV" {
  default = ""
}

// Stable devcontainer version identifier copied into image labels by the resolver.
variable "DEVCONTAINERS_VERSION_STABLE" {
  default = ""
}

// Dev-only devcontainer version identifier; resolver still emits it for compatibility/debugging.
variable "DEVCONTAINERS_VERSION_DEV" {
  default = ""
}

// === Tool versions =========================================================
// Version pin for delta; "latest" means resolve the newest release during staging.
variable "DELTA_VERSION" {
  default = "latest"
}

// Version pin for GitHub CLI; "latest" resolves at staging time for reproducibility control.
variable "GH_VERSION" {
  default = "latest"
}

// Version pin for grpcurl; "latest" resolves at staging time for reproducibility control.
variable "GRPCURL_VERSION" {
  default = "latest"
}

// Version pin for ripgrep-all; "latest" resolves at staging time for reproducibility control.
variable "RGA_VERSION" {
  default = "latest"
}

// Version pin for AWS CLI; "latest" resolves at staging time for reproducibility control.
variable "AWSCLI_VERSION" {
  default = "latest"
}

// Version pin for Backblaze B2 CLI; "latest" resolves at staging time for reproducibility control.
variable "B2_VERSION" {
  default = "latest"
}

// Version pin for bat; "latest" resolves at staging time for reproducibility control.
variable "BAT_VERSION" {
  default = "latest"
}

// Version pin for crane; "latest" resolves the newest official GitHub release during staging.
variable "CRANE_VERSION" {
  default = "latest"
}

// Version pin for regctl; "latest" resolves the newest official GitHub release during staging.
variable "REGCTL_VERSION" {
  default = "latest"
}

// Tool versions stay explicit even when defaulting to "latest" so the release
// pipeline can override, record, and reproduce the exact staged artifact set.

// Version pin for Consul; "latest" resolves at staging time for reproducibility control.
variable "CONSUL_VERSION" {
  default = "latest"
}

// Version pin for fd; "latest" resolves at staging time for reproducibility control.
variable "FD_VERSION" {
  default = "latest"
}

// Version pin for fzf; "latest" resolves at staging time for reproducibility control.
variable "FZF_VERSION" {
  default = "latest"
}

// Version pin for PostgreSQL client packages; "latest" leaves apt to choose the newest.
variable "POSTGRESQL_CLIENT_VERSION" {
  default = "latest"
}

// Version pin for redis-tools; "latest" leaves apt to choose the newest.
variable "REDIS_TOOLS_VERSION" {
  default = "latest"
}

// Version pin for ripgrep; "latest" resolves at staging time for reproducibility control.
variable "RIPGREP_VERSION" {
  default = "latest"
}

// Version pin for shellcheck; "latest" resolves at staging time for reproducibility control.
variable "SHELLCHECK_VERSION" {
  default = "latest"
}

// Version pin for Vault; "latest" resolves at staging time for reproducibility control.
variable "VAULT_VERSION" {
  default = "latest"
}

// Version pin for yq; "latest" resolves at staging time for reproducibility control.
variable "YQ_VERSION" {
  default = "latest"
}

// Version pin for Codex; "latest" resolves at staging time for reproducibility control.
variable "CODEX_VERSION" {
  default = "latest"
}

// Version pin for Claude Code; "latest" resolves at staging time for reproducibility control.
variable "CLAUDE_CODE_VERSION" {
  default = "latest"
}

// Version pin for Antigravity; "latest" resolves at staging time for reproducibility control.
variable "ANTIGRAVITY_VERSION" {
  default = "latest"
}

// Version pin for Aider; "main" tracks upstream main unless callers override it.
variable "AIDER_VERSION" {
  default = "main"
}

// Version pin for OpenCode; "latest" resolves at staging time for reproducibility control.
variable "OPENCODE_VERSION" {
  default = "latest"
}

// Version pin for Neovim; "latest" resolves at staging time for reproducibility control.
variable "NVIM_VER" {
  default = ""
}

// Version pin for NvChad; "latest" resolves at staging time for reproducibility control.
variable "NVCHAD_VER" {
  default = ""
}

// Boolean toggle for whether Codex is installed into the image.
variable "INSTALL_CODEX" {
  default = "true"
}

// Boolean toggle for whether Claude Code is installed into the image.
variable "INSTALL_CLAUDE_CODE" {
  default = "true"
}

// Boolean toggle for whether Antigravity is installed into the image.
variable "INSTALL_ANTIGRAVITY" {
  default = "true"
}

// Boolean toggle for whether Aider is installed into the image.
variable "INSTALL_AIDER" {
  default = "true"
}

// Boolean toggle for whether OpenCode is installed into the image.
variable "INSTALL_OPENCODE" {
  default = "true"
}

// Node.js major version (e.g. 26 for Node 26.x from nodesource).
// Architectural decision set in docker-bake.hcl, not in the Dockerfile.
variable "NODE_MAJOR" {
  default = "26"
}

// CIU release coordinates are explicit because the resolver stages a concrete wheel,
// records its tag/asset/shasum, and the image metadata needs to point at the exact artifact.
variable "CIU_WHEEL_TAG" {
  default = ""
}

// CIU release asset name resolved by the release resolver; used to inject the first-party wheel.
variable "CIU_WHEEL_ASSET_NAME" {
  default = ""
}

// CIU wheel URL injected by the resolver; empty means the Dockerfile must resolve it at build time.
variable "CIU_WHEEL_URL" {
  default = ""
}

// CIU wheel SHA256 injected by the resolver; empty skips verification and weakens reproducibility.
variable "CIU_WHEEL_SHA256" {
  default = ""
}

// CIU install gate; resolver flips this when the wheel is required for the chosen release.
variable "CIU_INSTALL_REQUIRED" {
  default = "false"
}

// Space-separated Python versions to install as lean secondary environments.
// Each target sets this explicitly, and the value controls which .venv-py* directories are added.
variable "SECONDARY_PYTHON_VERSIONS" {
  default = ""
}

// Root directory for versioned package manifest markdown files in the repo checkout.
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

// Variant-aware tags: a flavor like PHP 8.5 is a TAG dimension of the base package
// families, not a separate package name/family. `variant` is inserted between the
// py<python> segment and the date/latest segment, e.g. "trixie-py3.14-php8.5-20260707-10"
// and "trixie-py3.14-php8.5-latest". Use these instead of base_tag/vsc_tag whenever a
// target layers an optional flavor on top of the base or vsc-devcontainer image.
function "base_tag_variant" {
  params = [debian, python, variant]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug:${debian}-py${python}-${variant}-${BUILD_DATE}"
}

function "base_latest_tag_variant" {
  params = [debian, python, variant]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug:${debian}-py${python}-${variant}-latest"
}

function "vsc_tag_variant" {
  params = [debian, python, variant]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:${debian}-py${python}-${variant}-${BUILD_DATE}"
}

function "vsc_latest_tag_variant" {
  params = [debian, python, variant]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:${debian}-py${python}-${variant}-latest"
}

// Tag helper: multi-Python labels keep the full encoded interpreter set in one string.
// Example label: "py314-py311".
function "vsc_multi_tag" {
  params = [debian, pythons_label]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:${debian}-${pythons_label}-${BUILD_DATE}"
}

// Floating alias for the same multi-Python matrix entry.
function "vsc_multi_latest_tag" {
  params = [debian, pythons_label]
  result = "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:${debian}-${pythons_label}-latest"
}

// Build helper: package manifest directories are grouped by package family.
function "package_docs_dir" {
  params = [package_name]
  result = "${PACKAGE_MANIFEST_ROOT}/${package_name}"
}

// Relative path to the family README inside package-manifests-versioned/.
function "package_docs_readme_relpath" {
  params = [package_name]
  result = "${package_docs_dir(package_name)}/README.md"
}

// Relative path to the immutable, versioned release manifest.
function "package_manifest_relpath" {
  params = [package_name, debian, python]
  result = "${package_docs_dir(package_name)}/${debian}-py${python}-${BUILD_DATE}.md"
}

// GitHub blob URL for the family README.
function "package_docs_readme_url" {
  params = [package_name]
  result = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/blob/main/modern-debian-tools-python-debug/${package_docs_readme_relpath(package_name)}"
}

// Stable landing page path for the family docs.
function "package_latest_relpath" {
  params = [package_name]
  result = "${package_docs_dir(package_name)}/latest.md"
}

// Stable landing page URL for the family docs.
function "package_latest_url" {
  params = [package_name]
  result = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/blob/main/modern-debian-tools-python-debug/${package_latest_relpath(package_name)}"
}

// GitHub blob URL for a versioned release manifest.
function "package_manifest_url" {
  params = [package_name, debian, python]
  result = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/blob/main/modern-debian-tools-python-debug/${package_manifest_relpath(package_name, debian, python)}"
}

// Small metadata helper so OCI descriptions can point readers at the manifest.
function "description_with_manifest_docs" {
  params = [description, package_name, debian, python]
  result = "${description} Docs: ${package_manifest_url(package_name, debian, python)}"
}

// Variant-aware manifest path: appends the tag variant (e.g. "php8.5") to the filename so
// a flavor build sharing (debian, python) with the plain build never collides on disk —
// both now live in the same package family directory.
function "package_manifest_relpath_variant" {
  params = [package_name, debian, python, variant]
  result = "${package_docs_dir(package_name)}/${debian}-py${python}-${variant}-${BUILD_DATE}.md"
}

// GitHub blob URL for a versioned, variant-flavored release manifest.
function "package_manifest_url_variant" {
  params = [package_name, debian, python, variant]
  result = "https://github.com/${GITHUB_USERNAME}/${GITHUB_REPO}/blob/main/modern-debian-tools-python-debug/${package_manifest_relpath_variant(package_name, debian, python, variant)}"
}

// Variant-aware counterpart of description_with_manifest_docs.
function "description_with_manifest_docs_variant" {
  params = [description, package_name, debian, python, variant]
  result = "${description} Docs: ${package_manifest_url_variant(package_name, debian, python, variant)}"
}

// Shared argument bag only: each concrete target still builds the whole Dockerfile
// from its own BASE_IMAGE, so this is not a reusable image layer/stage.
target "base" {
  context = "."
  dockerfile = "Dockerfile"
  args = {
    REGISTRY = "${REGISTRY}"
    BACKPORTS_URI = "${BACKPORTS_URI}"
    AWSCLI_VERSION = "${AWSCLI_VERSION}"
    B2_VERSION = "${B2_VERSION}"
    BAT_VERSION = "${BAT_VERSION}"
    CRANE_VERSION = "${CRANE_VERSION}"
    REGCTL_VERSION = "${REGCTL_VERSION}"
    CONSUL_VERSION = "${CONSUL_VERSION}"
    DELTA_VERSION = "${DELTA_VERSION}"
    FD_VERSION = "${FD_VERSION}"
    FZF_VERSION = "${FZF_VERSION}"
    GH_VERSION = "${GH_VERSION}"
    GRPCURL_VERSION = "${GRPCURL_VERSION}"
    CODEX_VERSION = "${CODEX_VERSION}"
    CLAUDE_CODE_VERSION = "${CLAUDE_CODE_VERSION}"
    ANTIGRAVITY_VERSION = "${ANTIGRAVITY_VERSION}"
    AIDER_VERSION = "${AIDER_VERSION}"
    NVIM_VER = "${NVIM_VER}"
    NVCHAD_VER = "${NVCHAD_VER}"
    INSTALL_CODEX = "${INSTALL_CODEX}"
    INSTALL_CLAUDE_CODE = "${INSTALL_CLAUDE_CODE}"
    INSTALL_ANTIGRAVITY = "${INSTALL_ANTIGRAVITY}"
    INSTALL_AIDER = "${INSTALL_AIDER}"
    OPENCODE_VERSION = "${OPENCODE_VERSION}"
    INSTALL_OPENCODE = "${INSTALL_OPENCODE}"
    NODE_MAJOR = "${NODE_MAJOR}"
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
    BUILD_TIMESTAMP = "${BUILD_TIMESTAMP}"
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
    CIU_WHEEL_URL = "${CIU_WHEEL_URL}"
    CIU_WHEEL_SHA256 = "${CIU_WHEEL_SHA256}"
    CIU_INSTALL_REQUIRED = "${CIU_INSTALL_REQUIRED}"
    SECONDARY_PYTHON_VERSIONS = "${SECONDARY_PYTHON_VERSIONS}"
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

// PHP 8.5 flavor of the BASE package family. The variant lives in the TAG
// ("trixie-py3.14-php8.5-<date>" / "-php8.5-latest"), not in the package name —
// this publishes to the same `modern-debian-tools-python-debug` GHCR package as
// trixie-py314, distinguished only by the "-php8.5-" tag segment.
target "trixie-py314-php85" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "python:3.14-trixie"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
    PHP_VERSION = "8.5"
    INSTALL_PHP = "true"
    IMAGE_VARIANT = "php8.5"
    OCI_DESCRIPTION = description_with_manifest_docs_variant("Modern Debian Tools + Python Debug + PHP 8.5 base image. Adds PHP 8.5 CLI/FPM, composer, Xdebug, and the common PHP extensions needed for web debugging.", "modern-debian-tools-python-debug", "trixie", "3.14", "php8.5")
    OCI_DOCUMENTATION = package_manifest_url_variant("modern-debian-tools-python-debug", "trixie", "3.14", "php8.5")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath_variant("modern-debian-tools-python-debug", "trixie", "3.14", "php8.5")
  }
  tags = [base_tag_variant("trixie", "3.14", "php8.5"), base_latest_tag_variant("trixie", "3.14", "php8.5")]
}

// PHP 8.5 flavor of the VSC-DEVCONTAINER package family — same variant-in-tag
// treatment as trixie-py314-php85 above, published to
// `modern-debian-tools-python-debug-vsc-devcontainer`.
target "trixie-py314-php85-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "${DEVCONTAINERS_BASE_PINNED}"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
    PHP_VERSION = "8.5"
    INSTALL_PHP = "true"
    IMAGE_VARIANT = "php8.5"
    OCI_DESCRIPTION = description_with_manifest_docs_variant("Modern Debian Tools + Python Debug + PHP 8.5 VS Code devcontainer image. Adds PHP 8.5 CLI/FPM, composer, Xdebug, and the common PHP extensions needed for web debugging.", "modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14", "php8.5")
    OCI_DOCUMENTATION = package_manifest_url_variant("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14", "php8.5")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug-vsc-devcontainer")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath_variant("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14", "php8.5")
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
    DEVCONTAINERS_VERSION = "${DEVCONTAINERS_VERSION_STABLE}"
  }
  tags = [vsc_tag_variant("trixie", "3.14", "php8.5"), vsc_latest_tag_variant("trixie", "3.14", "php8.5")]
}


# Multi-Python devcontainers: primary Python (full toolkit) + secondary Pythons (lean venvs).
# Secondary venvs at /home/vscode/.venv-py{nodot}; switch via VS Code "Python: Select Interpreter".
# SECONDARY_PYTHON_VERSIONS: space-separated dotted versions, e.g. "3.11 3.9".
# See README.md § "Multi-Python Devcontainer Variants" for full docs and package lists.

target "trixie-py314-py311-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "${DEVCONTAINERS_BASE_PINNED}"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
    SECONDARY_PYTHON_VERSIONS = "3.11"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_VSC, "modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug-vsc-devcontainer")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14")
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
    DEVCONTAINERS_VERSION = "${DEVCONTAINERS_VERSION_STABLE}"
  }
  tags = [vsc_multi_tag("trixie", "py314-py311"), vsc_multi_latest_tag("trixie", "py314-py311")]
}

target "trixie-py314-py311-py39-vsc" {
  inherits = ["base"]
  args = {
    BASE_IMAGE = "${DEVCONTAINERS_BASE_PINNED}"
    PYTHON_VERSION = "3.14"
    DEBIAN_VERSION = "trixie"
    SECONDARY_PYTHON_VERSIONS = "3.11 3.9"
    OCI_DESCRIPTION = description_with_manifest_docs(OCI_DESCRIPTION_VSC, "modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14")
    OCI_DOCUMENTATION = package_manifest_url("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14")
    OCI_URL = package_latest_url("modern-debian-tools-python-debug-vsc-devcontainer")
    PACKAGE_MANIFEST_SOURCE = package_manifest_relpath("modern-debian-tools-python-debug-vsc-devcontainer", "trixie", "3.14")
    DEVCONTAINERS_RELEASE = "${DEVCONTAINERS_RELEASE_STABLE}"
    DEVCONTAINERS_VERSION = "${DEVCONTAINERS_VERSION_STABLE}"
  }
  tags = [vsc_multi_tag("trixie", "py314-py311-py39"), vsc_multi_latest_tag("trixie", "py314-py311-py39"), "${REGISTRY}/${GITHUB_USERNAME}/modern-debian-tools-python-debug-vsc-devcontainer:latest"]
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

// === Build groups ==========================================================
// Run a group with: docker buildx bake -f docker-bake.hcl <group> --push
//
// Two image families are published to GHCR:
//   - modern-debian-tools-python-debug
//   - modern-debian-tools-python-debug-vsc-devcontainer
//
// "vsc" is the VSC devcontainer family. "php" is the PHP 8.5 FLAVOR, not a
// separate family — its targets publish into the two families above with
// "-php8.5-" in the tag (see base_tag_variant/vsc_tag_variant). Do not add a
// third/fourth package name for a flavor; add a tag-variant target instead.
// The publish matrix is group "all" below.
group "vsc" {
  targets = ["trixie-py311-vsc", "trixie-py314-vsc"]
}

group "php" {
  targets = ["trixie-py314-php85", "trixie-py314-php85-vsc"]
}

group "base" {
  targets = ["trixie-py311", "trixie-py313", "trixie-py314"]
}

group "multi" {
  targets = ["trixie-py314-py311-vsc", "trixie-py314-py311-py39-vsc"]
}

// "all" is the publish matrix. `docker buildx bake all` and build-push.py use this.
group "all" {
  //targets = ["trixie-py311-vsc", "trixie-py314-vsc", "trixie-py314-php85", "trixie-py314-php85-vsc"]
  targets = [ "trixie-py314-php85", "trixie-py314-php85-vsc"]
}

// Exhaustive local superset: release matrix + base images + multi-Python variants + PHP flavor.
group "everything" {
  targets = ["all", "base", "multi", "php"]
}

group "detection" {
  targets = [
    "latest-vsc"
  ]
}
