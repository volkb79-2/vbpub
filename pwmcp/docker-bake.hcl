variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

variable "IMAGE_NAME_SERVER" {
  default = "pwmcp-server"
}

variable "IMAGE_NAME_CLIENT" {
  default = "pwmcp-client"
}

variable "VERSION" {
  default = "latest"
}

variable "BUILD_DATE" {
  default = "19700101"
}

variable "OCI_TITLE_SERVER" {
  default = "pwmcp-server"
}

variable "OCI_TITLE_CLIENT" {
  default = "pwmcp-client"
}

variable "OCI_DESCRIPTION_SERVER" {
  default = ""
}

variable "OCI_DESCRIPTION_CLIENT" {
  default = ""
}

variable "OCI_SOURCE" {
  default = "https://github.com/volkb79-2/vbpub"
}

variable "OCI_DOCUMENTATION" {
  default = "https://github.com/volkb79-2/vbpub/tree/main/pwmcp"
}

variable "OCI_URL" {
  default = "https://github.com/volkb79-2/vbpub/tree/main/pwmcp"
}

variable "OCI_LICENSES" {
  default = "MIT"
}

variable "OCI_VENDOR" {
  default = ""
}

variable "OCI_VERSION" {
  default = "${VERSION}"
}

variable "OCI_REVISION" {
  default = "unknown"
}

variable "OCI_CREATED" {
  default = "unknown"
}

function "latest_tag" {
  params = [image_name, version]
  result = "${REGISTRY}/${NAMESPACE}/${image_name}:${version}"
}

function "static_latest_tag" {
  params = [image_name]
  result = "${REGISTRY}/${NAMESPACE}/${image_name}:latest"
}

target "pwmcp-server" {
  context = "."
  dockerfile = "containers/server/Dockerfile"
  args = {
    OCI_TITLE = "${OCI_TITLE_SERVER}"
    OCI_DESCRIPTION = "${OCI_DESCRIPTION_SERVER}"
    OCI_SOURCE = "${OCI_SOURCE}"
    OCI_DOCUMENTATION = "${OCI_DOCUMENTATION}"
    OCI_URL = "${OCI_URL}"
    OCI_LICENSES = "${OCI_LICENSES}"
    OCI_VENDOR = "${OCI_VENDOR}"
    OCI_VERSION = "${OCI_VERSION}"
    OCI_REVISION = "${OCI_REVISION}"
    OCI_CREATED = "${OCI_CREATED}"
  }
  tags = [latest_tag("${IMAGE_NAME_SERVER}", "${VERSION}"), static_latest_tag("${IMAGE_NAME_SERVER}")]
}

target "pwmcp-client" {
  context = "."
  dockerfile = "containers/client/Dockerfile"
  args = {
    OCI_TITLE = "${OCI_TITLE_CLIENT}"
    OCI_DESCRIPTION = "${OCI_DESCRIPTION_CLIENT}"
    OCI_SOURCE = "${OCI_SOURCE}"
    OCI_DOCUMENTATION = "${OCI_DOCUMENTATION}"
    OCI_URL = "${OCI_URL}"
    OCI_LICENSES = "${OCI_LICENSES}"
    OCI_VENDOR = "${OCI_VENDOR}"
    OCI_VERSION = "${OCI_VERSION}"
    OCI_REVISION = "${OCI_REVISION}"
    OCI_CREATED = "${OCI_CREATED}"
  }
  tags = [latest_tag("${IMAGE_NAME_CLIENT}", "${VERSION}"), static_latest_tag("${IMAGE_NAME_CLIENT}")]
}

group "all" {
  targets = ["pwmcp-server", "pwmcp-client"]
}
