FROM mirror.gcr.io/library/rust:1-slim AS build
RUN rustup target add x86_64-unknown-linux-musl
WORKDIR /src
COPY Cargo.toml Cargo.lock* ./
RUN mkdir src && echo 'fn main(){}' > src/main.rs \
 && cargo build --locked --release --target x86_64-unknown-linux-musl; rm -rf src
COPY src ./src
RUN touch src/main.rs && cargo build --locked --release --target x86_64-unknown-linux-musl \
 && cp target/x86_64-unknown-linux-musl/release/topos-sampler /topos-sampler
