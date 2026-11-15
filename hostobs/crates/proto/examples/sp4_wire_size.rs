//! SP-4 wire-size measurement (plan v4 §F.3): bytes per live frame for the typed column
//! codec against the sampler's varint TLV, on 100- and 1,000-cgroup shapes.
//!
//! Criterion-free and deterministic: no clock, no benchmark harness, no host data. It
//! builds both encodings for the same facts and counts the bytes, so the number is a
//! property of the two formats, not of a machine.
//!
//! Run it in the lane image:
//!
//! ```text
//! tools/cargo-offline.sh run -p hostobs-proto --example sp4_wire_size --offline --locked
//! ```
//!
//! The published numbers and the shapes behind them are in
//! `hostobs/docs/SP-4-WIRE-SIZES.md`.
//!
//! The sampler's encoding is reproduced here exactly as it is in
//! `topos/sampler/src/store.rs`: `u64 ts_ms | u32 dt_ms | varint n_ents | per entity:
//! varint id, varint n, n × value`, where a value is `varint(metric << 2 | kind)` followed
//! by a zigzag varint (kind 0), eight f64 bytes (kind 1) or one state byte (kind 2). It is
//! measured, not adopted: nothing in the product uses it.

use hostobs_proto::dict::{
    Dictionary, Entry, MetricFacts, MetricKind, MetricScope, MetricSource, MetricUnit,
    MetricValueType,
};
use hostobs_proto::frame::{encode_block, Block, BlockHeaderFacts, Column};

/// The columns every cgroup row carries in the measured shape (the sampler's per-cgroup
/// set, taken from `topos/sampler/src/collect.rs` `Ids::new`).
const COLUMNS: [(&str, Unit_, Kind_, &str); 8] = [
    ("cpu.usage_usec", Unit_::Usec, Kind_::Counter, "u64"),
    ("mem.current", Unit_::Bytes, Kind_::Gauge, "u64"),
    ("mem.anon", Unit_::Bytes, Kind_::Gauge, "u64"),
    ("pids.current", Unit_::Count, Kind_::Gauge, "u64"),
    ("io.rbytes", Unit_::Bytes, Kind_::Counter, "u64"),
    ("io.wbytes", Unit_::Bytes, Kind_::Counter, "u64"),
    ("psi.cpu.some.avg10", Unit_::Ratio, Kind_::Gauge, "f64"),
    ("psi.io.some.total", Unit_::Usec, Kind_::Counter, "u64"),
];

#[derive(Clone, Copy)]
enum Unit_ {
    Usec,
    Bytes,
    Count,
    Ratio,
}

#[derive(Clone, Copy, PartialEq)]
enum Kind_ {
    Counter,
    Gauge,
}

fn facts(unit: Unit_, kind: Kind_) -> MetricFacts {
    MetricFacts {
        unit: match unit {
            Unit_::Usec => MetricUnit::Microseconds,
            Unit_::Bytes => MetricUnit::Bytes,
            Unit_::Count => MetricUnit::Count,
            Unit_::Ratio => MetricUnit::Ratio,
        },
        kind: match kind {
            Kind_::Counter => MetricKind::Counter,
            Kind_::Gauge => MetricKind::Gauge,
        },
        scope: MetricScope::Cgroup,
        source: MetricSource::CgroupFs,
    }
}

fn dictionary(cgroups: usize) -> Dictionary {
    let mut dictionary = Dictionary::new(1);
    for inc in 1..=cgroups as u64 {
        let label = format!("/system.slice/unit-{inc}.scope");
        let _ = dictionary.intern(Entry::Entity { inc, label });
    }
    for (name, unit, kind, _) in COLUMNS {
        let _ = dictionary.intern(Entry::Metric {
            facts: facts(unit, kind),
            name: name.to_string(),
        });
    }
    dictionary
}

fn header(n_rows: usize) -> BlockHeaderFacts {
    BlockHeaderFacts {
        boot_id: [0x5a; 16],
        epoch: 1,
        seq: 1,
        mono_start_ns: 0,
        mono_end_ns: 1_000_000_000,
        boot_ns: 0,
        real_ns: 0,
        dict_gen: 1,
        n_rows: u32::try_from(n_rows).expect("rows fit"),
        dropped: 0,
        flags: 0,
    }
}

fn counter_value(row: usize, column: usize) -> u64 {
    // A plausible cumulative counter: differs per (row, column) and is far above any
    // varint-friendly magnitude, which is what a real cgroup counter looks like.
    1_000_000_000u64 + (row as u64) * 1_000 + (column as u64) * 17
}

fn gauge_bits(row: usize) -> u64 {
    // A fractional PSI average (`avg10`), stored as f64 like the sampler stores it.
    (1.0f64 + (row % 100) as f64 / 100.0).to_bits()
}

fn column_block(cgroups: usize) -> Block {
    column_block_with_dictionary(cgroups, true)
}

/// The same block with or without the dictionary section. A live tick carries the
/// dictionary only when the generation changed, so both numbers are published.
fn column_block_with_dictionary(cgroups: usize, with_dictionary: bool) -> Block {
    let rows: Vec<hostobs_proto::frame::Row> = (0..cgroups)
        .map(|row| hostobs_proto::frame::Row {
            ent: u32::try_from(row).expect("rows fit"),
            t_off_us: u32::try_from(row).unwrap_or(0),
        })
        .collect();
    let columns: Vec<Column> = COLUMNS
        .iter()
        .enumerate()
        .map(|(index, (_, unit, kind, value_type))| {
            let (value_type, values): (MetricValueType, Vec<u64>) = match *value_type {
                "f64" => (MetricValueType::F64, (0..cgroups).map(gauge_bits).collect()),
                _ => (
                    MetricValueType::U64,
                    (0..cgroups).map(|row| counter_value(row, index)).collect(),
                ),
            };
            let kind = match *kind {
                Kind_::Counter => MetricKind::Counter,
                Kind_::Gauge => MetricKind::Gauge,
            };
            let _ = unit;
            Column::new(
                u32::try_from(1 + cgroups + index).expect("metric index fits"),
                value_type,
                kind,
                values,
                vec![0u8; cgroups],
            )
            .expect("column")
        })
        .collect();
    Block {
        frame_type: hostobs_proto::frame::FrameType::Block,
        corr: 1,
        header: header(cgroups),
        rows,
        time: Vec::new(),
        columns,
        dictionary: if with_dictionary {
            Some(dictionary(cgroups))
        } else {
            None
        },
        skipped_sections: Vec::new(),
    }
}

// ---------------------------------------------------------------- the sampler's TLV

fn put_varint(out: &mut Vec<u8>, mut value: u64) {
    while value >= 0x80 {
        out.push((value as u8) | 0x80);
        value >>= 7;
    }
    out.push(value as u8);
}

fn zigzag(value: i64) -> u64 {
    ((value << 1) ^ (value >> 63)) as u64
}

fn put_counter_value(out: &mut Vec<u8>, metric: u32, delta: u64) {
    // Kind 0: an exact integer delta as a zigzag varint (the sampler's `Sv::V` with a
    // whole number). The sampler stores a DETLA per tick, not the raw counter.
    put_varint(out, u64::from(metric) << 2);
    put_varint(out, zigzag(delta as i64));
}

fn put_f64_value(out: &mut Vec<u8>, metric: u32, value: f64) {
    put_varint(out, (u64::from(metric) << 2) | 1);
    out.extend_from_slice(&value.to_bits().to_le_bytes());
}

fn sampler_frame(cgroups: usize) -> Vec<u8> {
    sampler_frame_values(cgroups, false)
}

/// The sampler's TLV carrying the **same information** the column codec carries: raw
/// absolute `u64` counters as varints instead of per-tick deltas. This is the like-for-like
/// byte comparison; the delta variant is what the sampler really ships today.
fn sampler_frame_values(cgroups: usize, raw_counters: bool) -> Vec<u8> {
    let mut out = Vec::new();
    out.extend_from_slice(&1_000u64.to_le_bytes()); // ts_ms
    out.extend_from_slice(&1_000u32.to_le_bytes()); // dt_ms
    put_varint(&mut out, cgroups as u64);
    for row in 0..cgroups {
        put_varint(&mut out, (row + 1) as u64); // entity id
        put_varint(&mut out, COLUMNS.len() as u64);
        for (index, (_, _, kind, _)) in COLUMNS.iter().enumerate() {
            let metric = u32::try_from(index).expect("metric index fits");
            if *kind == Kind_::Counter {
                let delta = 250 + (row as u64 % 71) + index as u64;
                if raw_counters {
                    // The raw cumulative reading the column codec stores, as a varint.
                    put_counter_value(&mut out, metric, counter_value(row, index));
                } else {
                    put_counter_value(&mut out, metric, delta);
                }
            } else {
                put_f64_value(&mut out, metric, 1000.0 + row as f64);
            }
        }
    }
    out
}

fn main() {
    println!("SP-4 wire-size measurement (deterministic, no clock, no host data)");
    println!();
    println!(
        "shape: one live tick, {} columns per cgroup, per-cgroup rows",
        COLUMNS.len()
    );
    println!(
        "sampler codec: the sampler's varint TLV, carrying per-tick DELTAS ({}-byte f64 for gauges)",
        8
    );
    println!("column codec: raw u64 counters + u8 quality per value, dictionary in the frame");
    println!();
    println!(
        "{:>8}  {:>10}  {:>10}  {:>10}  {:>10}  {:>10}",
        "cgroups", "sampler B", "columns B", "sampler/row", "columns/row", "ratio"
    );
    for cgroups in [100usize, 1_000] {
        let sampler = sampler_frame(cgroups);
        let columns = encode_block(&column_block(cgroups)).expect("encode the column block");
        println!(
            "{cgroups:>8}  {:>10}  {:>10}  {:>10.1}  {:>10.1}  {:>10.3}",
            sampler.len(),
            columns.len(),
            sampler.len() as f64 / cgroups as f64,
            columns.len() as f64 / cgroups as f64,
            columns.len() as f64 / sampler.len() as f64
        );
    }
    println!();
    // A live tick carries the dictionary only when the generation changed, so the steady
    // state is the same block without the dictionary section. Both numbers are published;
    // the sampler also keeps its dictionary beside its segments instead of in the frame.
    println!("steady-state live tick (no dictionary section in the frame):");
    println!(
        "{:>8}  {:>10}  {:>10}  {:>10}  {:>10}",
        "cgroups", "sampler B", "columns B", "columns/row", "ratio"
    );
    let mut steady = Vec::new();
    for cgroups in [100usize, 1_000] {
        let sampler = sampler_frame(cgroups);
        let columns = encode_block(&column_block_with_dictionary(cgroups, false)).expect("encode");
        steady.push((cgroups, sampler.len(), columns.len()));
        println!(
            "{cgroups:>8}  {:>10}  {:>10}  {:>10.1}  {:>10.3}",
            sampler.len(),
            columns.len(),
            columns.len() as f64 / cgroups as f64,
            columns.len() as f64 / sampler.len() as f64
        );
    }
    println!();
    println!("like-for-like: the sampler TLV carrying RAW u64 counters (the same information,");
    println!("without a quality column, which the sampler cannot represent at all):");
    println!(
        "{:>8}  {:>12}  {:>10}  {:>12}  {:>10}",
        "cgroups", "sampler raw B", "columns B", "sampler raw/row", "columns/row"
    );
    for cgroups in [100usize, 1_000] {
        let sampler = sampler_frame_values(cgroups, true);
        let columns = encode_block(&column_block_with_dictionary(cgroups, false)).expect("encode");
        println!(
            "{cgroups:>8}  {:>12}  {:>10}  {:>12.1}  {:>10.1}",
            sampler.len(),
            columns.len(),
            sampler.len() as f64 / cgroups as f64,
            columns.len() as f64 / cgroups as f64
        );
    }
    println!();
    let cgroups = 100usize;
    let columns = encode_block(&column_block(cgroups)).expect("encode");
    let dictionary_bytes = dictionary(cgroups).encode().expect("dictionary");
    println!(
        "at {cgroups} cgroups the dictionary-bearing frame is {} B, of which the dictionary is {} B ({} %)",
        columns.len(),
        dictionary_bytes.len(),
        100 * dictionary_bytes.len() / columns.len()
    );
    let (_, _, steady_100) = steady.first().copied().unwrap_or((100, 0, 0));
    println!(
        "per-row cost without the dictionary: {} B (1,000 cgroups: {} B)",
        steady_100 / cgroups,
        steady.last().map(|(_, _, len)| *len).unwrap_or(0) / 1_000
    );
}
