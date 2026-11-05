# Build a static musl binary in a container; the cockpit has no Rust toolchain.
#   docker build -t topos-ratatui-poc-build topos/poc/ratatui-tui
#   docker create --name topos-ratatui-poc-extract topos-ratatui-poc-build
#   docker cp topos-ratatui-poc-extract:/out/topos-ratatui-poc ./topos-ratatui-poc
#   docker rm topos-ratatui-poc-extract
FROM mirror.gcr.io/library/rust:1-slim
RUN rustup target add x86_64-unknown-linux-musl
WORKDIR /b
# dependency layer first: the lock file is committed, so --locked keeps it honest
COPY Cargo.toml Cargo.lock* ./
RUN mkdir src && echo 'fn main(){}' > src/main.rs \
    && cargo build --locked --release --target x86_64-unknown-linux-musl ; rm -rf src
COPY columns.example.toml ./
COPY src src
RUN touch src/main.rs \
    && (cargo test --locked --release --target x86_64-unknown-linux-musl > /tmp/test.log 2>&1 || { tail -n 80 /tmp/test.log; exit 1; }) \
    && grep -E "^test |test result" /tmp/test.log \
    && cargo build --locked --release --target x86_64-unknown-linux-musl \
    && mkdir -p /out && cp target/x86_64-unknown-linux-musl/release/topos-ratatui-poc /out/ \
    && cp Cargo.lock /out/Cargo.lock && rustc --version
CMD ["/out/topos-ratatui-poc"]
