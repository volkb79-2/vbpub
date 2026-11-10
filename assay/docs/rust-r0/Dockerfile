# syntax=docker/dockerfile:1
# R-0 example only. R-1's production image belongs under tester-unified-rust/.
ARG RUST_IMAGE=rust@sha256:e97df055c54a05160e7c67ba3b2963de6ddb95903911baf8922dbcd1b394f698

FROM ${RUST_IMAGE} AS cargo-tools
ARG CARGO_NEXTEST_VERSION=0.9.151
ARG CARGO_LLVM_COV_VERSION=0.9.1
ARG CARGO_MUTANTS_VERSION=27.1.0
ARG CARGO_DENY_VERSION=0.20.2
ARG RUST_R0_BUILD_ID

ENV CARGO_HOME=/usr/local/cargo \
    CARGO_BUILD_JOBS=2

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git pkg-config libssl-dev \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
 && printf '%s\n' "$RUST_R0_BUILD_ID" >/etc/rust-r0-build-id

RUN set -eux; \
    rustup component add clippy rustfmt llvm-tools-preview; \
    rustup target add x86_64-unknown-linux-musl; \
    for spec in \
      "cargo-nextest ${CARGO_NEXTEST_VERSION}" \
      "cargo-llvm-cov ${CARGO_LLVM_COV_VERSION}" \
      "cargo-mutants ${CARGO_MUTANTS_VERSION}" \
      "cargo-deny ${CARGO_DENY_VERSION}"; do \
      set -- $spec; \
      cargo install --locked --jobs 2 --target-dir /tmp/cargo-tool-target \
        --version "$2" "$1"; \
    done; \
    rm -rf /tmp/cargo-tool-target /usr/local/cargo/registry /usr/local/cargo/git

COPY --from=subject /topos /work/topos
COPY assay/src/assay /opt/assay/src/assay
COPY assay/docs/rust-r0/verify-crate-cache.sh /usr/local/bin/verify-crate-cache

WORKDIR /work/topos/sampler
RUN cargo fetch --locked \
 && rm -rf /usr/local/cargo/registry/src /usr/local/cargo/git \
 && chmod 0755 /usr/local/bin/verify-crate-cache \
 && mkdir -p /usr/local/share \
 && find /usr/local/cargo/registry/cache -type f -name '*.crate' -print0 \
      | sort -z | xargs -0 sha256sum > /usr/local/share/rust-crate-cache.sha256

FROM ${RUST_IMAGE}
ARG RUST_IMAGE
ARG RUST_R0_BUILD_ID

LABEL org.vbpub.rust-r0.base-image="${RUST_IMAGE}" \
      org.vbpub.rust-r0.build-id="${RUST_R0_BUILD_ID}"

ENV CARGO_HOME=/usr/local/cargo \
    CARGO_BUILD_JOBS=2 \
    CARGO_NET_OFFLINE=true

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git libssl-dev pkg-config python3 \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
 && printf '%s\n' "$RUST_R0_BUILD_ID" >/etc/rust-r0-build-id

RUN rustup component add clippy rustfmt llvm-tools-preview \
 && rustup target add x86_64-unknown-linux-musl

COPY --from=cargo-tools /usr/local/cargo /usr/local/cargo
COPY --from=cargo-tools /work/topos /work/topos
COPY --from=cargo-tools /opt/assay /opt/assay
COPY --from=cargo-tools /usr/local/bin/verify-crate-cache /usr/local/bin/verify-crate-cache
COPY --from=cargo-tools /usr/local/share/rust-crate-cache.sha256 /usr/local/share/rust-crate-cache.sha256

WORKDIR /work/topos/sampler
RUN rustc --version --verbose \
 && cargo --version \
 && cargo clippy --version \
 && rustfmt --version \
 && cargo nextest --version \
 && cargo llvm-cov --version \
 && cargo mutants --version \
 && cargo deny --version \
 && python3 --version

CMD ["bash"]
