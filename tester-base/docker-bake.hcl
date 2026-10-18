// tester-base - the minimal gate base image. Two targets, one Dockerfile:
//   tester-base-local    the candidate every consumer and the `contract` lane build/test FIRST
//                        (loaded as tester-base:local; never pushed)
//   tester-base-release  what CMRU builds and pushes: ghcr.io/volkb79-2/tester-base:<VERSION>
variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

// The CMRU release coordinate; CMRU writes ./VERSION before the build step.
variable "TESTER_BASE_VERSION" {
  default = "0.1.0"
}

target "tester-base-local" {
  context    = "."
  dockerfile = "Dockerfile"
  tags       = ["tester-base:local"]
}

target "tester-base-release" {
  inherits = ["tester-base-local"]
  tags = [
    "${REGISTRY}/${NAMESPACE}/tester-base:${TESTER_BASE_VERSION}",
    "${REGISTRY}/${NAMESPACE}/tester-base:latest",
  ]
  labels = {
    "org.opencontainers.image.version" = "${TESTER_BASE_VERSION}"
  }
}
