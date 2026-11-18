//! The client's per-stream dictionary state (WIRE.md §6.1, §6.2, §6.4, §6.5).
//!
//! A dictionary is held per stream, a stream being `(connection, corr)`. The caller drives it
//! in three steps for every BLOCK frame it receives:
//!
//! 1. decode with the dictionary the stream holds: `decode_block_with(bytes, dicts.held(corr))`
//!    (the codec judges a bare block's generation, references, kinds and masks against it);
//! 2. on success hand the block to [`StreamDicts::accept`], which applies the stream rules of
//!    §6.2 and stores a dictionary-bearing block's dictionary;
//! 3. on any decoder error (an unknown required section included) call
//!    [`StreamDicts::close`] and then close the connection: the server is the only producer, so
//!    the stream is no longer trustworthy (§6.5).
//!
//! # Contracts
//!
//! - **The rules are relative to what the stream holds, never absolute.** A stream that holds
//!   nothing holds generation 0 and generations start at 1, so the first block of a stream is
//!   the dictionary-bearing block of any generation at least 1.
//! - **A dictionary-bearing block must exceed the held generation** (`generation-regressed`,
//!   an equal generation included), **a bare block must carry exactly the held one** and a
//!   bare block with nothing held is `stale-dictionary-generation` too.
//! - **A refusal closes the stream.** An `accept` that fails forgets the stream's dictionary,
//!   so a caller that ignores §6.5 cannot go on decoding against a dictionary the server has
//!   just contradicted.
//! - **A reconnect drops every dictionary** ([`StreamDicts::on_hello`]); the probe identity of
//!   the previous hello is kept so the caller learns whether its cursors and sessions
//!   survive (§6.4).
//! - **The number of streams is the caller's bound.** A client opens at most `max_outstanding`
//!   requests and `max_subscriptions` subscriptions (§5.1) and routes only the blocks of
//!   those corrs here; it calls [`StreamDicts::close`] at every END.

use std::collections::HashMap;

use crate::dict::Dictionary;
use crate::error::ProtoError;
use crate::frame::Block;

/// What a hello after a (re)connect says about the probe the client talked to before
/// (WIRE.md §6.4).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Session {
    /// The first hello this state has seen.
    First,
    /// The same `boot_id` and `epoch` as the previous hello: cursors stay valid.
    Same,
    /// A different `boot_id` or `epoch`: the caller drops its cursors and open span keys and
    /// reports `session-lost` / `window-lost` for any session or window it held.
    Changed,
}

/// The dictionaries a client holds, one per live stream, and the probe identity it last saw.
#[derive(Debug, Default)]
pub struct StreamDicts {
    streams: HashMap<u32, Dictionary>,
    identity: Option<(String, u64)>,
}

impl StreamDicts {
    /// A state that holds nothing and has seen no hello.
    #[must_use]
    pub fn new() -> StreamDicts {
        StreamDicts::default()
    }

    /// The dictionary stream `corr` holds, to pass to `decode_block_with`; `None` when it
    /// holds nothing (a new, closed or refused stream).
    #[must_use]
    pub fn held(&self, corr: u32) -> Option<&Dictionary> {
        self.streams.get(&corr)
    }

    /// The number of streams that hold a dictionary.
    #[must_use]
    pub fn stream_count(&self) -> usize {
        self.streams.len()
    }

    /// Apply the §6.2 rules of stream `corr` to a decoded block and update its dictionary.
    ///
    /// # Errors
    ///
    /// [`ProtoError::GenerationRegressed`] for a dictionary-bearing block whose generation does
    /// not exceed the held one (0 when nothing is held), and
    /// [`ProtoError::StaleDictionaryGeneration`] for a bare block whose generation is not the
    /// held one or that arrives when nothing is held. The stream's dictionary is forgotten on
    /// every error.
    pub fn accept(&mut self, corr: u32, block: &Block) -> Result<(), ProtoError> {
        let held = self.streams.get(&corr).map(Dictionary::generation);
        let verdict = match &block.dictionary {
            Some(dictionary) => {
                let got = dictionary.generation();
                let floor = held.unwrap_or(0);
                if got > floor {
                    self.streams.insert(corr, dictionary.clone());
                    Ok(())
                } else {
                    Err(ProtoError::GenerationRegressed { held: floor, got })
                }
            }
            None => {
                let needed = block.header.dict_gen;
                match held {
                    Some(generation) if generation == needed => Ok(()),
                    _ => Err(ProtoError::StaleDictionaryGeneration {
                        needed,
                        held: held.unwrap_or(0),
                    }),
                }
            }
        };
        if verdict.is_err() {
            self.streams.remove(&corr);
        }
        verdict
    }

    /// Forget stream `corr`: at its END, at an unsubscribe and after any decoder error on it.
    pub fn close(&mut self, corr: u32) {
        self.streams.remove(&corr);
    }

    /// Forget every dictionary (a dropped connection).
    pub fn reset(&mut self) {
        self.streams.clear();
    }

    /// Record the hello of a (re)connection: every dictionary is dropped (§6.4) and the
    /// answer says whether the probe is the one the previous hello named. `boot_id` is the
    /// hello's UUID text.
    pub fn on_hello(&mut self, boot_id: &str, epoch: u64) -> Session {
        self.streams.clear();
        let session = match &self.identity {
            None => Session::First,
            Some((known_boot, known_epoch)) if known_boot == boot_id && *known_epoch == epoch => {
                Session::Same
            }
            Some(_) => Session::Changed,
        };
        if session != Session::Same {
            self.identity = Some((boot_id.to_owned(), epoch));
        }
        session
    }
}

#[cfg(test)]
mod tests {
    use super::{Session, StreamDicts};
    use crate::dict::Dictionary;
    use crate::error::ProtoError;
    use crate::frame::{Block, BlockHeaderFacts, FrameType};

    const BOOT: &str = "00112233-4455-6677-8899-aabbccddeeff";

    fn block(generation: u32, with_dictionary: bool) -> Block {
        Block {
            frame_type: FrameType::Block,
            corr: 1,
            header: BlockHeaderFacts {
                boot_id: [0; 16],
                epoch: 1,
                seq: 1,
                mono_start_ns: 0,
                mono_end_ns: 0,
                boot_ns: 0,
                real_ns: 0,
                dict_gen: generation,
                n_rows: 0,
                dropped: 0,
                flags: 0,
            },
            rows: Vec::new(),
            time: Vec::new(),
            columns: Vec::new(),
            dictionary: with_dictionary.then(|| Dictionary::new(generation)),
            subject: None,
            skipped_sections: Vec::new(),
        }
    }

    fn held_generation(dicts: &StreamDicts, corr: u32) -> Option<u32> {
        dicts.held(corr).map(Dictionary::generation)
    }

    #[test]
    fn a_first_block_is_dictionary_bearing_at_any_generation() {
        let mut dicts = StreamDicts::new();
        assert_eq!(held_generation(&dicts, 7), None);
        assert_eq!(dicts.accept(7, &block(5, true)), Ok(()));
        assert_eq!(held_generation(&dicts, 7), Some(5));
        assert_eq!(dicts.stream_count(), 1);
    }

    #[test]
    fn a_first_bare_block_is_stale_with_nothing_held() {
        let mut dicts = StreamDicts::new();
        assert_eq!(
            dicts.accept(7, &block(5, false)),
            Err(ProtoError::StaleDictionaryGeneration { needed: 5, held: 0 })
        );
        assert_eq!(dicts.stream_count(), 0);
    }

    #[test]
    fn a_first_block_at_generation_zero_is_regressed() {
        let mut dicts = StreamDicts::new();
        assert_eq!(
            dicts.accept(7, &block(0, true)),
            Err(ProtoError::GenerationRegressed { held: 0, got: 0 })
        );
        assert_eq!(
            dicts.accept(7, &block(0, false)),
            Err(ProtoError::StaleDictionaryGeneration { needed: 0, held: 0 })
        );
    }

    #[test]
    fn a_bare_block_must_carry_exactly_the_held_generation() {
        let mut dicts = StreamDicts::new();
        assert_eq!(dicts.accept(1, &block(2, true)), Ok(()));
        assert_eq!(dicts.accept(1, &block(2, false)), Ok(()));
        assert_eq!(dicts.accept(1, &block(2, false)), Ok(()));
        assert_eq!(dicts.accept(1, &block(3, true)), Ok(()));
        assert_eq!(held_generation(&dicts, 1), Some(3));
        assert_eq!(
            dicts.accept(1, &block(2, false)),
            Err(ProtoError::StaleDictionaryGeneration { needed: 2, held: 3 })
        );
        assert_eq!(held_generation(&dicts, 1), None, "closed");
    }

    #[test]
    fn a_bare_block_above_the_held_generation_is_stale_too() {
        let mut dicts = StreamDicts::new();
        assert_eq!(dicts.accept(1, &block(2, true)), Ok(()));
        assert_eq!(
            dicts.accept(1, &block(4, false)),
            Err(ProtoError::StaleDictionaryGeneration { needed: 4, held: 2 })
        );
    }

    #[test]
    fn a_dictionary_bearing_block_must_exceed_the_held_generation() {
        let mut dicts = StreamDicts::new();
        assert_eq!(dicts.accept(1, &block(2, true)), Ok(()));
        assert_eq!(
            dicts.accept(1, &block(2, true)),
            Err(ProtoError::GenerationRegressed { held: 2, got: 2 })
        );
        assert_eq!(held_generation(&dicts, 1), None, "closed");
        assert_eq!(dicts.accept(1, &block(4, true)), Ok(()));
        assert_eq!(
            dicts.accept(1, &block(3, true)),
            Err(ProtoError::GenerationRegressed { held: 4, got: 3 })
        );
    }

    #[test]
    fn a_generation_may_skip_forward() {
        let mut dicts = StreamDicts::new();
        assert_eq!(dicts.accept(1, &block(2, true)), Ok(()));
        assert_eq!(dicts.accept(1, &block(9, true)), Ok(()));
        assert_eq!(dicts.accept(1, &block(u32::MAX, true)), Ok(()));
        assert_eq!(dicts.accept(1, &block(u32::MAX, false)), Ok(()));
    }

    #[test]
    fn streams_are_independent_and_a_refusal_closes_only_its_own() {
        let mut dicts = StreamDicts::new();
        assert_eq!(dicts.accept(1, &block(2, true)), Ok(()));
        assert_eq!(dicts.accept(2, &block(5, true)), Ok(()));
        assert!(dicts.accept(1, &block(3, false)).is_err());
        assert_eq!(held_generation(&dicts, 1), None);
        assert_eq!(held_generation(&dicts, 2), Some(5));
        assert_eq!(dicts.accept(2, &block(5, false)), Ok(()));
        assert_eq!(dicts.stream_count(), 1);
    }

    #[test]
    fn close_forgets_one_stream_and_reset_every_stream() {
        let mut dicts = StreamDicts::new();
        assert_eq!(dicts.accept(1, &block(2, true)), Ok(()));
        assert_eq!(dicts.accept(2, &block(2, true)), Ok(()));
        dicts.close(1);
        dicts.close(99);
        assert_eq!(held_generation(&dicts, 1), None);
        assert_eq!(held_generation(&dicts, 2), Some(2));
        dicts.reset();
        assert_eq!(dicts.stream_count(), 0);
        assert!(dicts.accept(2, &block(2, false)).is_err());
    }

    #[test]
    fn a_hello_reports_whether_the_probe_is_the_same_and_drops_every_dictionary() {
        let mut dicts = StreamDicts::new();
        assert_eq!(dicts.on_hello(BOOT, 7), Session::First);
        assert_eq!(dicts.accept(1, &block(2, true)), Ok(()));
        assert_eq!(dicts.on_hello(BOOT, 7), Session::Same);
        assert_eq!(dicts.stream_count(), 0, "dropped, same epoch too");
        assert_eq!(dicts.accept(1, &block(2, true)), Ok(()));
        assert_eq!(dicts.on_hello(BOOT, 8), Session::Changed);
        assert_eq!(dicts.stream_count(), 0);
        assert_eq!(dicts.on_hello(BOOT, 8), Session::Same, "remembered");
        let other = "ffeeddcc-bbaa-9988-7766-554433221100";
        assert_eq!(dicts.on_hello(other, 8), Session::Changed, "boot differs");
        assert_eq!(dicts.on_hello(other, 8), Session::Same);
        assert_eq!(dicts.on_hello(BOOT, 8), Session::Changed);
    }
}
