# tester-unified-rust — sibling Rust gate image for Rust workspaces.
# Keep Rust and its cargo tools out of tester-unified; Python gates keep their
# existing image closure and Rust lanes select this dedicated image.

ARG RUST_IMAGE=rust@sha256:e97df055c54a05160e7c67ba3b2963de6ddb95903911baf8922dbcd1b394f698

FROM ${RUST_IMAGE} AS cargo-tools
ARG RUST_IMAGE
ARG CARGO_NEXTEST_VERSION=0.9.151
ARG CARGO_LLVM_COV_VERSION=0.9.1
ARG CARGO_MUTANTS_VERSION=27.1.0
ARG CARGO_DENY_VERSION=0.20.2
ARG HOSTOBS_LOCK_REGISTRY_SHA256

ENV CARGO_HOME=/usr/local/cargo \
    CARGO_NET_OFFLINE=false

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git libssl-dev pkg-config python3 \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

RUN set -eux; \
    rustup component add clippy rustfmt llvm-tools-preview; \
    rustup target add x86_64-unknown-linux-musl; \
    for spec in \
      "cargo-nextest ${CARGO_NEXTEST_VERSION}" \
      "cargo-llvm-cov ${CARGO_LLVM_COV_VERSION}" \
      "cargo-mutants ${CARGO_MUTANTS_VERSION}" \
      "cargo-deny ${CARGO_DENY_VERSION}"; do \
      set -- $spec; \
      cargo install --locked --target-dir /tmp/cargo-tool-target \
        --version "$2" "$1"; \
    done; \
    rm -rf /tmp/cargo-tool-target /usr/local/cargo/registry /usr/local/cargo/git

WORKDIR /src/hostobs
COPY . /src/hostobs
COPY tools/verify-crate-cache.py /usr/local/lib/hostobs/verify-crate-cache.py

RUN set -eux; \
    registry_sha256="$(python3 /usr/local/lib/hostobs/verify-crate-cache.py --print-registry-digest --lock Cargo.lock)"; \
    test "$registry_sha256" = "$HOSTOBS_LOCK_REGISTRY_SHA256"; \
    cargo fetch --locked; \
    rm -rf /usr/local/cargo/registry/src /usr/local/cargo/git; \
    mkdir -p /usr/local/share; \
    find /usr/local/cargo/registry/cache -type f -name '*.crate' -print0 \
      | sort -z \
      | xargs -0 sha256sum > /usr/local/share/rust-crate-cache.sha256; \
    test -s /usr/local/share/rust-crate-cache.sha256; \
    printf '%s\n' "$registry_sha256" > /usr/local/share/hostobs-Cargo.lock.registry-sha256; \
    cp Cargo.lock /usr/local/share/hostobs-Cargo.lock; \
    python3 /usr/local/lib/hostobs/verify-crate-cache.py \
      --lock /usr/local/share/hostobs-Cargo.lock \
      --manifest /usr/local/share/rust-crate-cache.sha256 \
      --baked-registry-sha256 /usr/local/share/hostobs-Cargo.lock.registry-sha256 \
      --cargo-home /usr/local/cargo

FROM ${RUST_IMAGE}
ARG RUST_IMAGE
ARG HOSTOBS_LOCK_REGISTRY_SHA256

LABEL org.vbpub.tester-unified-rust.base-image="${RUST_IMAGE}" \
      org.vbpub.tester-unified-rust.hostobs-lock-registry-sha256="${HOSTOBS_LOCK_REGISTRY_SHA256}"

ENV CARGO_HOME=/usr/local/cargo \
    CARGO_NET_OFFLINE=true \
    HOME=/home/gate \
    XDG_CONFIG_HOME=/home/gate/.config \
    XDG_CACHE_HOME=/home/gate/.cache \
    XDG_STATE_HOME=/home/gate/.local/state \
    RUSTUP_HOME=/usr/local/rustup

ARG RUN_UID=1003
ARG RUN_GID=1003

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates git libssl3 pkg-config python3 \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
 && groupadd -g "${RUN_GID}" gate \
 && useradd -u "${RUN_UID}" -g "${RUN_GID}" -m -d /home/gate -s /bin/bash gate \
 && mkdir -p /home/gate/.config /home/gate/.cache /home/gate/.local/state \
 && mkdir -p /home/gate/.cache/hostobs-target /usr/local/share /usr/local/lib/hostobs \
 && chown -R "${RUN_UID}:${RUN_GID}" /home/gate /usr/local/cargo

RUN rustup component add clippy rustfmt llvm-tools-preview \
 && rustup target add x86_64-unknown-linux-musl

COPY --from=cargo-tools /usr/local/cargo /usr/local/cargo
COPY --from=cargo-tools /usr/local/share/rust-crate-cache.sha256 /usr/local/share/rust-crate-cache.sha256
COPY --from=cargo-tools /usr/local/share/hostobs-Cargo.lock.registry-sha256 /usr/local/share/hostobs-Cargo.lock.registry-sha256
COPY --from=cargo-tools /usr/local/share/hostobs-Cargo.lock /usr/local/share/hostobs-Cargo.lock
COPY --from=cargo-tools /usr/local/lib/hostobs/verify-crate-cache.py /usr/local/lib/hostobs/verify-crate-cache.py
COPY tools/verify-crate-cache /usr/local/bin/verify-crate-cache

RUN chmod 0755 /usr/local/bin/verify-crate-cache \
 && chown -R "${RUN_UID}:${RUN_GID}" /usr/local/cargo \
 && /usr/local/bin/verify-crate-cache /usr/local/share/hostobs-Cargo.lock \
 && rustc --version \
 && cargo --version \
 && cargo clippy --version \
 && rustfmt --version \
 && cargo nextest --version \
 && cargo llvm-cov --version \
 && cargo mutants --version \
 && cargo deny --version \
 && python3 --version

WORKDIR /work
USER ${RUN_UID}:${RUN_GID}
CMD ["bash"]
