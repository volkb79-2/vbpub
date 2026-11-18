//! The hostobs/1 client (WIRE.md §1, §4, §6, §7.3; work package P5a).
//!
//! A [`Client`] is one binary-mode connection to a hostobs socket at one [`Level`]: it
//! completes the handshake (the 8-byte prefix, then `hello` with `corr` 1), then serves JSON
//! verbs ([`Client::request`]), block verbs ([`Client::blocks`]) and live subscriptions
//! ([`Client::subscribe`]) from the caller's thread. It is the Rust twin of the Python client
//! `python/hostobs_client` and reuses the stream layer of `hostobs-proto`: `FrameReader` for
//! the §5.2 frame machine, `StreamDicts` for the §6.2 dictionary rules and the byte-level
//! envelope scanner for REP, END and EVT; JSON text is read by hand with those scanners'
//! member ranges (no JSON library).
//!
//! # Contracts
//!
//! - **No panic on any server byte.** Every slice is taken with `get`, every length is
//!   converted with `try_from`, there is no `unwrap`, `expect` or index outside tests.
//! - **Every wait is bounded by the injected [`Clock`].** An operation waits at most the
//!   [`ClientInfo::timeout`]; a partly received frame is bounded by its completion age (30 s
//!   from its first byte, strict `>`). A transport timeout is never below 1 ms. A timeout
//!   closes the connection, because a late reply could no longer be matched.
//! - **A failure closes the connection.** A decoder, envelope, protocol or timeout error
//!   closes it; later calls are [`ClientError::ConnectionLost`]. Only [`ClientError::Usage`]
//!   (nothing was sent) and a non-fatal [`ClientError::Server`] keep it open.
//! - **Arguments are checked locally.** `args_json` and the selection are JSON text the caller
//!   owns; the §4.2 envelope rules (UTF-8, one object, depth at most 8) are applied before
//!   anything is sent.
//! - **One stream at a time.** [`BlockStream`] and [`Subscription`] borrow the client; the
//!   client keeps no queue of undelivered blocks (see the `streams` module docs).
//!
//! # Use
//!
//! ```text
//! let info = ClientInfo::new("my-tool");
//! let mut client = Client::connect("/run/hostobs/observe.sock", Level::Observe, info)?;
//! let status = client.request("status", "{}")?;
//! for block in client.subscribe(selection_json, 1000)?.take(10) { /* ... */ }
//! ```

#![warn(missing_docs)]
#![warn(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::checked_conversions
)]

mod client;
mod error;
mod hello;
mod json;
mod level;
mod streams;
mod transport;

pub use client::{Client, ClientInfo, Redial, Reply};
pub use error::{is_fatal_code, ClientError, ProtocolKind, ERROR_CODES, FATAL_CODES};
pub use hello::{HelloResult, Limits};
pub use hostobs_proto::frame::Block;
pub use hostobs_proto::stream_dicts::Session;
pub use level::Level;
pub use streams::{BlockStream, StreamEnd, Subscription};
pub use transport::{Clock, MonotonicClock, Transport};
