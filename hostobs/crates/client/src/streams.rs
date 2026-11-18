//! Block streams: the BLOCKs of a block verb and the ticks of a subscription (WIRE.md §2.4, §4.7,
//! §6.1, §6.2).
//!
//! A stream borrows its [`Client`], so a client has one live stream at a time and nothing is
//! queued on the client side: the next block is read from the connection when the caller asks
//! for it, and the kernel socket buffer and the server's send hub (WIRE.md §5.3, which keeps the
//! newest tick of a lagging subscriber and counts the rest as `dropped`) are the only queues.
//! Each stream holds one dictionary per `corr` in the client's `StreamDicts`, judges every BLOCK
//! with `decode_block_with` and `accept` (WIRE.md §6.2) and checks the END counts exactly. A
//! decoder, stream or protocol error is returned once, closes the connection (WIRE.md §6.5) and
//! ends the iteration.

use hostobs_proto::frame::Block;

use crate::client::{Client, Reply};
use crate::error::ClientError;

/// How a stream ended: the fields of an `END ok:true` (WIRE.md §2.2).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StreamEnd {
    /// BLOCK frames of the stream the server sent before the END (equal to the number the
    /// client received: a mismatch is a protocol error).
    pub blocks: u32,
    /// Blocks of the stream the server dropped after the last pinned one.
    pub dropped: u32,
    /// `complete` or `page` for a query, `unsubscribed` or `client-eof` for a subscription.
    pub reason: String,
    /// The continuation cursor of a `series_batch` page (`reason` is `page`), else `None`.
    pub next: Option<String>,
}

/// The per-stream state the client's receive functions update.
pub(crate) struct StreamCtx {
    pub(crate) corr: u32,
    pub(crate) live: bool,
    pub(crate) received: u32,
    pub(crate) header_dropped: u64,
    pub(crate) end: Option<StreamEnd>,
}

impl StreamCtx {
    fn new(corr: u32, live: bool) -> StreamCtx {
        StreamCtx {
            corr,
            live,
            received: 0,
            header_dropped: 0,
            end: None,
        }
    }
}

/// The BLOCKs of one block verb (`current`, `snapshot_at`, `series_batch`): an iterator of
/// decoded blocks that ends with the END.
///
/// An `END ok:false` is yielded as a final `Err(Server)` after the blocks queued ahead of it.
/// Dropping the stream before its END reads and discards the rest (bounded by the client's
/// timeout per frame); [`close`](BlockStream::close) does the same and reports its error.
pub struct BlockStream<'c> {
    client: &'c mut Client,
    ctx: StreamCtx,
    reply: Reply,
    done: bool,
}

impl<'c> BlockStream<'c> {
    pub(crate) fn new(client: &'c mut Client, corr: u32, reply: Reply) -> BlockStream<'c> {
        BlockStream {
            client,
            ctx: StreamCtx::new(corr, false),
            reply,
            done: false,
        }
    }

    /// The REP that opened the stream (its `result` is the stream header).
    #[must_use]
    pub fn reply(&self) -> &Reply {
        &self.reply
    }

    /// The BLOCK frames received so far.
    #[must_use]
    pub fn received(&self) -> u32 {
        self.ctx.received
    }

    /// The END, once the iteration has reached it.
    #[must_use]
    pub fn end(&self) -> Option<&StreamEnd> {
        self.ctx.end.as_ref()
    }

    /// Stop delivering: read and discard the rest of the stream up to its END; idempotent.
    ///
    /// # Errors
    ///
    /// The transport and protocol errors of the client; an `END ok:false` as `Server`.
    pub fn close(&mut self) -> Result<(), ClientError> {
        if self.done || self.client.is_closed() {
            self.done = true;
            return Ok(());
        }
        self.done = true;
        self.client.drain(&mut self.ctx)
    }
}

impl Iterator for BlockStream<'_> {
    type Item = Result<Block, ClientError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        let deadline = self.client.deadline_after(0);
        match self.client.next_block(&mut self.ctx, deadline) {
            Ok(Some(block)) => Some(Ok(block)),
            Ok(None) => {
                self.done = true;
                None
            }
            Err(error) => {
                self.done = true;
                Some(Err(error))
            }
        }
    }
}

impl Drop for BlockStream<'_> {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

/// A live subscription: an iterator of the blocks of its ticks (WIRE.md §4.7).
///
/// Each wait for a block is bounded by the client's timeout plus the subscription's interval.
/// The first block of the stream carries its dictionary and later ones are judged against it
/// (WIRE.md §6.2). A server that ends the subscription (`client-eof`, `internal` at the
/// generation bound) ends the iteration, with a final `Err(Server)` for an `END ok:false`; a lost
/// connection yields `Err(ConnectionLost)` once, after which the caller reconnects and
/// resubscribes (WIRE.md §6.4). Dropping the subscription unsubscribes best-effort;
/// [`close`](Subscription::close) reports the outcome.
pub struct Subscription<'c> {
    client: &'c mut Client,
    ctx: StreamCtx,
    interval_ms: u32,
    done: bool,
}

impl<'c> Subscription<'c> {
    pub(crate) fn new(client: &'c mut Client, corr: u32, interval_ms: u32) -> Subscription<'c> {
        Subscription {
            client,
            ctx: StreamCtx::new(corr, true),
            interval_ms,
            done: false,
        }
    }

    /// The `corr` of the `subscribe` request, which every BLOCK of the stream carries.
    #[must_use]
    pub fn corr(&self) -> u32 {
        self.ctx.corr
    }

    /// The interval the server confirmed.
    #[must_use]
    pub fn interval_ms(&self) -> u32 {
        self.interval_ms
    }

    /// The BLOCK frames received so far.
    #[must_use]
    pub fn received(&self) -> u32 {
        self.ctx.received
    }

    /// Blocks the server dropped for this subscription because the client lagged: the sum of
    /// the `dropped` counters of the block headers received and of the END.
    #[must_use]
    pub fn dropped(&self) -> u64 {
        let from_end = match &self.ctx.end {
            Some(end) => u64::from(end.dropped),
            None => 0,
        };
        self.ctx.header_dropped.saturating_add(from_end)
    }

    /// The END, once the iteration has reached it.
    #[must_use]
    pub fn end(&self) -> Option<&StreamEnd> {
        self.ctx.end.as_ref()
    }

    /// Unsubscribe: send `unsubscribe`, read its REP and the stream's END, dropping the blocks
    /// that arrive meanwhile; idempotent. A subscription the server already ended is not an
    /// error (`not-found` is tolerated).
    ///
    /// # Errors
    ///
    /// The transport and protocol errors of the client; any other server refusal as `Server`.
    pub fn close(&mut self) -> Result<(), ClientError> {
        if self.done || self.client.is_closed() {
            self.done = true;
            return Ok(());
        }
        self.done = true;
        self.client.unsubscribe(&mut self.ctx)
    }
}

impl Iterator for Subscription<'_> {
    type Item = Result<Block, ClientError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        let extra = u64::from(self.interval_ms) * 1_000_000;
        let deadline = self.client.deadline_after(extra);
        match self.client.next_block(&mut self.ctx, deadline) {
            Ok(Some(block)) => Some(Ok(block)),
            Ok(None) => {
                self.done = true;
                None
            }
            Err(error) => {
                self.done = true;
                Some(Err(error))
            }
        }
    }
}

impl Drop for Subscription<'_> {
    fn drop(&mut self) {
        let _ = self.close();
    }
}
