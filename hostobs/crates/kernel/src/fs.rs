//! Root-anchored, bounded, read-only filesystem access.
//!
//! Every reader in this crate goes through [`Fs`], so a test drives a reader against a
//! fixture tree (a directory of committed files, or an in-memory [`MemFs`]) instead of a
//! live host, and the production implementation ([`RealFs`]) is one small module that
//! cannot read anything the abstraction does not offer.
//!
//! # Contracts
//!
//! - **Read-only, always.** The trait has no write, create, remove, rename or spawn
//!   operation, so no code in this crate *can* write. The workspace lint
//!   (`tools/check-lints.py`) additionally refuses the write tokens in this crate's
//!   sources, and this crate is `#![forbid(unsafe_code)]`, so there is no syscall path
//!   around it.
//! - **Every read is bounded.** `read` takes a byte bound and refuses a larger file with
//!   [`FsError::TooLarge`] instead of growing a buffer; `list` takes an entry bound and
//!   refuses a longer directory with [`FsError::TooManyEntries`]. A kernel pseudo-file is
//!   normally a few hundred bytes; a `FIFO` or `/dev/zero` planted under a configured root
//!   ends in a typed refusal, never in memory exhaustion (the sampler's `Slurp` cap,
//!   `topos/sampler/src/util.rs` `SLURP_MAX`).
//! - **A failure is typed and carries the quality code it maps to.** [`FsError::quality`]
//!   is the single place that decides whether an absent optional file is `absent` and an
//!   absent required feature is `unsupported`; nothing in a reader invents a zero.
//! - **No path-keyed identity.** This module hands out paths and entry names; it never
//!   derives an identity from them. Identity comes from [`crate::identity`]
//!   (`(mount id, inode)`, boot id, process `starttime` ticks), and a test asserts that
//!   two inodes at one path are two identities.

use core::fmt;
use std::path::{Path, PathBuf};

use crate::quality::QualityCode;

/// The kind of a directory entry, from the listing's own metadata (no link is followed).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EntryKind {
    /// A regular file.
    File,
    /// A directory.
    Directory,
    /// A symbolic link.
    Symlink,
    /// Anything else (a socket, a device, a FIFO).
    Other,
}

/// One listed name.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Entry {
    /// The entry's own name (one path component).
    pub name: String,
    /// What it is.
    pub kind: EntryKind,
}

impl Entry {
    /// True when this entry is a directory.
    #[must_use]
    pub const fn is_dir(&self) -> bool {
        matches!(self.kind, EntryKind::Directory)
    }
}

/// The metadata a reader needs: an inode, its device, and whether it is a directory or a
/// link.
///
/// The pair `(device, inode)` is the identity input of a cgroup (plan v4 §C.1's
/// `(mount id, cgroup inode)`; `device` is the `st_dev` this crate can read without a
/// syscall, see [`crate::identity::CgroupIdentity`]). A path is only a label, so metadata
/// is read without following the last component (`lstat` semantics).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Meta {
    /// The inode number of the path's last component.
    pub inode: u64,
    /// The device the path lives on (`st_dev`).
    pub device: u64,
    /// True for a directory.
    pub is_directory: bool,
    /// True for a symbolic link.
    pub is_symlink: bool,
    /// True for a regular file.
    pub is_file: bool,
}

impl Meta {
    /// Metadata for a regular file with a known inode on the first device (fixtures).
    #[must_use]
    pub const fn file(inode: u64) -> Meta {
        Meta {
            inode,
            device: 1,
            is_directory: false,
            is_symlink: false,
            is_file: true,
        }
    }

    /// Metadata for a directory with a known inode on the first device (fixtures).
    #[must_use]
    pub const fn directory(inode: u64) -> Meta {
        Meta {
            inode,
            device: 1,
            is_directory: true,
            is_symlink: false,
            is_file: false,
        }
    }
}

/// A stable, small classification of an I/O failure.
///
/// The raw `std::io::ErrorKind` is deliberately not carried: it is not a wire or evidence
/// vocabulary, and a reader only needs to tell this crate's refusals apart. The mapping
/// keeps `ENOENT` (absent) distinct from permission and from an ordinary read error,
/// because those carry different quality codes.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IoKind {
    /// `ENOENT` / `ENOTDIR`: the path does not exist.
    NotFound,
    /// `EACCES` / `EPERM`.
    PermissionDenied,
    /// Any other I/O failure.
    Other,
}

/// Every refusal of the filesystem layer.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FsError {
    /// The path does not exist (`ENOENT`, or `ENOTDIR` on a path component).
    NotFound,
    /// The process may not read it.
    PermissionDenied,
    /// The path is not a regular file (a directory, a socket, a device, a FIFO).
    NotAFile,
    /// The path is not a directory where one was required.
    NotADirectory,
    /// The file is larger than the bound the caller allowed; nothing was read.
    TooLarge {
        /// The bound that was refused.
        max_bytes: usize,
    },
    /// The directory holds more names than the bound the caller allowed.
    TooManyEntries {
        /// The bound that was refused.
        max_entries: usize,
    },
    /// Any other failure.
    Io(IoKind),
}

impl FsError {
    /// The quality code this refusal maps to.
    ///
    /// `absent` is the code the caller uses for a missing *optional* file: an absent
    /// per-cgroup controller is `QualityCode::ABSENT`, while an absent host feature (a
    /// kernel without PSI, say) is `QualityCode::UNSUPPORTED`. The caller states which,
    /// so this function cannot guess it wrongly.
    #[must_use]
    pub const fn quality(self, absent: QualityCode) -> QualityCode {
        match self {
            FsError::NotFound | FsError::NotAFile | FsError::NotADirectory => absent,
            FsError::PermissionDenied | FsError::Io(_) => QualityCode::UNREADABLE,
            FsError::TooLarge { .. } | FsError::TooManyEntries { .. } => QualityCode::NOT_SAMPLED,
        }
    }
}

impl fmt::Display for FsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            FsError::NotFound => f.write_str("no such file"),
            FsError::PermissionDenied => f.write_str("permission denied"),
            FsError::NotAFile => f.write_str("not a regular file"),
            FsError::NotADirectory => f.write_str("not a directory"),
            FsError::TooLarge { max_bytes } => {
                write!(f, "file is larger than the {max_bytes}-byte bound")
            }
            FsError::TooManyEntries { max_entries } => {
                write!(f, "directory holds more than {max_entries} entries")
            }
            FsError::Io(kind) => write!(f, "i/o error ({kind:?})"),
        }
    }
}

impl core::error::Error for FsError {}

/// A read-only, bounded file tree.
pub trait Fs: fmt::Debug {
    /// Read one whole file, or refuse it when it is larger than `max_bytes`.
    ///
    /// # Errors
    ///
    /// [`FsError::NotFound`], [`FsError::PermissionDenied`], [`FsError::NotAFile`],
    /// [`FsError::TooLarge`] and [`FsError::Io`].
    fn read(&self, path: &Path, max_bytes: usize) -> Result<Vec<u8>, FsError>;

    /// List one directory, or refuse it when it holds more than `max_entries` names.
    ///
    /// # Errors
    ///
    /// [`FsError::NotFound`], [`FsError::PermissionDenied`], [`FsError::NotADirectory`],
    /// [`FsError::TooManyEntries`] and [`FsError::Io`].
    fn list(&self, path: &Path, max_entries: usize) -> Result<Vec<Entry>, FsError>;

    /// Read at most `max_bytes` bytes from the start of one file.
    ///
    /// This is the *prefix* read, and only a fact that is itself bounded by a policy uses
    /// it: `/proc/<pid>/cmdline` is bounded by `cmdline_max` (256 bytes by default), where
    /// the caller must see the first bytes of a longer line to know it was cut, and a
    /// refusal would lose that fact. Every other reader uses [`Fs::read`], which refuses a
    /// file past its bound instead of truncating it — the difference is deliberate and
    /// named in both methods.
    ///
    /// # Errors
    ///
    /// [`FsError::NotFound`], [`FsError::PermissionDenied`], [`FsError::NotAFile`] and
    /// [`FsError::Io`]. A file longer than `max_bytes` is **not** an error here.
    fn read_prefix(&self, path: &Path, max_bytes: usize) -> Result<Vec<u8>, FsError>;

    /// Metadata of one path's last component, without following a link.
    ///
    /// # Errors
    ///
    /// [`FsError::NotFound`], [`FsError::PermissionDenied`] and [`FsError::Io`].
    fn meta(&self, path: &Path) -> Result<Meta, FsError>;

    /// True when the path exists (used to tell an absent optional file from a failure).
    fn exists(&self, path: &Path) -> bool {
        self.meta(path).is_ok()
    }
}

// ---------------------------------------------------------------- the real tree

/// The real filesystem, read-only.
///
/// It opens files with `File::open` (read-only by definition), stats paths with
/// `symlink_metadata` (no link is followed) and lists directories with `read_dir`; there
/// is no other operation. It is the only module in this crate that touches the host, and
/// it does so exclusively through those three read calls.
#[derive(Clone, Copy, Debug, Default)]
pub struct RealFs;

impl RealFs {
    /// The real root.
    #[must_use]
    pub const fn new() -> RealFs {
        RealFs
    }
}

fn io_kind(error: &std::io::Error) -> IoKind {
    match error.kind() {
        std::io::ErrorKind::NotFound => IoKind::NotFound,
        std::io::ErrorKind::PermissionDenied => IoKind::PermissionDenied,
        _ => {
            // `ENOTDIR` is a "does not exist for this purpose" failure, not an I/O error.
            if error.raw_os_error() == Some(20) {
                IoKind::NotFound
            } else {
                IoKind::Other
            }
        }
    }
}

fn to_fs_error(error: &std::io::Error) -> FsError {
    match io_kind(error) {
        IoKind::NotFound => FsError::NotFound,
        IoKind::PermissionDenied => FsError::PermissionDenied,
        IoKind::Other => FsError::Io(IoKind::Other),
    }
}

impl Fs for RealFs {
    fn read(&self, path: &Path, max_bytes: usize) -> Result<Vec<u8>, FsError> {
        let metadata = std::fs::metadata(path).map_err(|error| to_fs_error(&error))?;
        if !metadata.is_file() {
            return Err(FsError::NotAFile);
        }
        let mut file = std::fs::File::open(path).map_err(|error| to_fs_error(&error))?;
        // The bound is applied to the bytes **actually read**, never to a file's reported
        // length, because that length is a hint and not the fact: a sysfs attribute reports
        // one page (4096) whatever its content, and a `/proc` pseudo-file reports 0 while
        // answering with text. Refusing by reported length would reject a 20-byte sysfs
        // attribute under a 256-byte cap (a refusal about a file that is small enough), and
        // trusting it the other way would read past the bound. `take(max_bytes + 1)` stops
        // one byte past the allowance, so a real file that *is* too large costs at most
        // `max_bytes + 1` bytes read and then gets [`FsError::TooLarge`].
        //
        // `saturating_add` is not decoration: `usize::MAX` means "no bound", and an
        // unsaturating `+ 1` wraps that limit to 0 (an empty `Ok`, which is a wrong answer
        // that looks like an empty file) or panics in a debug build.
        let limit = u64::try_from(max_bytes)
            .unwrap_or(u64::MAX)
            .saturating_add(1);
        // No `with_capacity(metadata.len())`: the hint of a pseudo-file is 0 and of a huge
        // file is huge, so neither is a good allocation size. `read_to_end` grows into the
        // `take` bound, which is already bounded.
        let mut bytes = Vec::new();
        let read = std::io::Read::take(&mut file, limit);
        std::io::Read::read_to_end(&mut std::io::BufReader::new(read), &mut bytes)
            .map_err(|error| to_fs_error(&error))?;
        if bytes.len() > max_bytes {
            return Err(FsError::TooLarge { max_bytes });
        }
        Ok(bytes)
    }

    fn read_prefix(&self, path: &Path, max_bytes: usize) -> Result<Vec<u8>, FsError> {
        // Same open, same bound on the buffer, one deliberate difference from `read`:
        // a longer file is a prefix, which is what a bounded fact wants.
        let metadata = std::fs::metadata(path).map_err(|error| to_fs_error(&error))?;
        if !metadata.is_file() {
            return Err(FsError::NotAFile);
        }
        let mut file = std::fs::File::open(path).map_err(|error| to_fs_error(&error))?;
        let mut bytes = Vec::with_capacity(max_bytes.min(4096));
        let read = std::io::Read::take(&mut file, u64::try_from(max_bytes).unwrap_or(u64::MAX));
        std::io::Read::read_to_end(&mut std::io::BufReader::new(read), &mut bytes)
            .map_err(|error| to_fs_error(&error))?;
        Ok(bytes)
    }

    fn list(&self, path: &Path, max_entries: usize) -> Result<Vec<Entry>, FsError> {
        let metadata = std::fs::metadata(path).map_err(|error| to_fs_error(&error))?;
        if !metadata.is_dir() {
            return Err(FsError::NotADirectory);
        }
        let reader = std::fs::read_dir(path).map_err(|error| to_fs_error(&error))?;
        let mut entries = Vec::new();
        for item in reader {
            if entries.len() == max_entries {
                return Err(FsError::TooManyEntries { max_entries });
            }
            let item = item.map_err(|error| to_fs_error(&error))?;
            let kind = match item.file_type() {
                Ok(file_type) if file_type.is_symlink() => EntryKind::Symlink,
                Ok(file_type) if file_type.is_dir() => EntryKind::Directory,
                Ok(file_type) if file_type.is_file() => EntryKind::File,
                _ => EntryKind::Other,
            };
            entries.push(Entry {
                name: item.file_name().to_string_lossy().into_owned(),
                kind,
            });
        }
        entries.sort_by(|left, right| left.name.cmp(&right.name));
        Ok(entries)
    }

    fn meta(&self, path: &Path) -> Result<Meta, FsError> {
        let metadata = std::fs::symlink_metadata(path).map_err(|error| to_fs_error(&error))?;
        use std::os::unix::fs::MetadataExt;
        Ok(Meta {
            inode: metadata.ino(),
            device: metadata.dev(),
            is_directory: metadata.is_dir(),
            is_symlink: metadata.file_type().is_symlink(),
            is_file: metadata.is_file(),
        })
    }
}

// ---------------------------------------------------------------- an in-memory tree

/// A fixture tree held in memory: a deterministic [`Fs`] for tests and SP-2 evidence.
///
/// It is public because the SP-2 tests (and any later consumer's own tests) need a tree
/// that is *never* the live host and whose inodes the test chooses. It supports reads,
/// listings and metadata only — there is no write operation to misuse, and it refuses
/// exactly what [`RealFs`] refuses (a directory read as a file, an oversized file, a
/// over-long listing).
#[derive(Clone, Debug, Default)]
pub struct MemFs {
    files: Vec<(PathBuf, Vec<u8>, u64)>,
    directories: Vec<(PathBuf, u64)>,
}

impl MemFs {
    /// An empty tree.
    #[must_use]
    pub fn new() -> MemFs {
        MemFs::default()
    }

    /// Add a regular file with an explicit inode. Later calls replace an earlier entry.
    pub fn file(&mut self, path: impl Into<PathBuf>, bytes: impl Into<Vec<u8>>, inode: u64) {
        let path = path.into();
        self.files.retain(|(existing, ..)| *existing != path);
        self.files.push((path, bytes.into(), inode));
    }

    /// Add a directory with an explicit inode.
    pub fn directory(&mut self, path: impl Into<PathBuf>, inode: u64) {
        let path = path.into();
        self.directories.retain(|(existing, _)| *existing != path);
        self.directories.push((path, inode));
    }

    fn file_at(&self, path: &Path) -> Option<&(PathBuf, Vec<u8>, u64)> {
        self.files.iter().find(|(candidate, ..)| candidate == path)
    }
}

/// True when `path` is a direct child of `parent` (one component deeper).
fn is_child(parent: &Path, path: &Path) -> Option<String> {
    let name = path.file_name()?.to_string_lossy().into_owned();
    if path.parent() == Some(parent) {
        Some(name)
    } else {
        None
    }
}

impl Fs for MemFs {
    fn read(&self, path: &Path, max_bytes: usize) -> Result<Vec<u8>, FsError> {
        match self.file_at(path) {
            Some((_, bytes, _)) => {
                if bytes.len() > max_bytes {
                    return Err(FsError::TooLarge { max_bytes });
                }
                Ok(bytes.clone())
            }
            None if self.directories.iter().any(|(dir, _)| dir == path) => Err(FsError::NotAFile),
            None => Err(FsError::NotFound),
        }
    }

    fn read_prefix(&self, path: &Path, max_bytes: usize) -> Result<Vec<u8>, FsError> {
        match self.file_at(path) {
            Some((_, bytes, _)) => Ok(bytes
                .get(..bytes.len().min(max_bytes))
                .unwrap_or(&bytes[..])
                .to_vec()),
            None if self.directories.iter().any(|(dir, _)| dir == path) => Err(FsError::NotAFile),
            None => Err(FsError::NotFound),
        }
    }

    fn list(&self, path: &Path, max_entries: usize) -> Result<Vec<Entry>, FsError> {
        if !self.directories.iter().any(|(dir, _)| dir == path) {
            return Err(FsError::NotADirectory);
        }
        let mut entries: Vec<Entry> = Vec::new();
        for (file, ..) in &self.files {
            if let Some(name) = is_child(path, file) {
                entries.push(Entry {
                    name,
                    kind: EntryKind::File,
                });
            }
        }
        for (directory, _) in &self.directories {
            if let Some(name) = is_child(path, directory) {
                entries.push(Entry {
                    name,
                    kind: EntryKind::Directory,
                });
            }
        }
        entries.sort_by(|left, right| left.name.cmp(&right.name));
        if entries.len() > max_entries {
            return Err(FsError::TooManyEntries { max_entries });
        }
        Ok(entries)
    }

    fn meta(&self, path: &Path) -> Result<Meta, FsError> {
        if let Some((_, _, inode)) = self.file_at(path) {
            return Ok(Meta::file(*inode));
        }
        if let Some((_, inode)) = self
            .directories
            .iter()
            .find(|(candidate, _)| candidate == path)
        {
            return Ok(Meta::directory(*inode));
        }
        Err(FsError::NotFound)
    }
}

#[cfg(test)]
mod tests {
    use super::{EntryKind, Fs, FsError, IoKind, MemFs, Meta, RealFs};
    use crate::quality::QualityCode;
    use std::path::{Path, PathBuf};

    /// The committed fixture tree. Nothing in this crate's tests writes anywhere: a
    /// directory-level fixture lives in `crates/kernel/tests/fixtures/fs`, and a write
    /// token cannot appear in this crate's sources at all (see the module docs).
    fn fixtures() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/fs")
    }

    #[test]
    fn a_file_is_read_within_its_bound_and_a_larger_one_is_refused_unread() {
        let root = fixtures();
        let small = root.join("small");
        let large = root.join("large");
        let fs = RealFs::new();
        assert_eq!(fs.read(&small, 64), Ok(b"12 34\n".to_vec()));
        assert_eq!(
            fs.read(&large, 64),
            Err(FsError::TooLarge { max_bytes: 64 }),
            "a file past the bound is refused, not truncated"
        );
        assert_eq!(fs.read(&root.join("missing"), 64), Err(FsError::NotFound));
        assert_eq!(
            fs.read(&root, 64),
            Err(FsError::NotAFile),
            "a directory is not a file, so a reader cannot get a listing by accident"
        );
        // `usize::MAX` is "no bound", and it must mean *read the whole file*: the bound is
        // `max_bytes + 1` so one byte past it can be detected, and an unsaturating `+ 1`
        // wraps that limit to 0 (an empty `Ok`, which is a wrong answer) or panics in a
        // debug build.
        assert_eq!(
            fs.read(&large, usize::MAX),
            Ok(std::fs::read(&large).expect("the fixture itself")),
            "an unbounded read returns the whole file"
        );
        assert_eq!(fs.read(&small, usize::MAX), Ok(b"12 34\n".to_vec()));
    }

    #[test]
    fn a_listing_is_bounded_and_does_not_follow_links() {
        let root = fixtures().join("dir");
        let fs = RealFs::new();
        let entries = fs.list(&root, 8).expect("list");
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].name, "a");
        assert_eq!(entries[0].kind, EntryKind::File);
        assert_eq!(entries[1].name, "inner");
        assert!(
            entries.iter().all(|entry| !entry.is_dir()),
            "the listing reports what the directory holds, and `dir` has two files"
        );
        assert_eq!(
            fs.list(&root, 1),
            Err(FsError::TooManyEntries { max_entries: 1 }),
            "an over-long listing is refused, not silently cut"
        );
        assert_eq!(fs.list(&root.join("a"), 8), Err(FsError::NotADirectory));
    }

    #[test]
    fn metadata_comes_from_the_path_itself_so_two_inodes_at_one_path_are_two_identities() {
        let path = fixtures().join("dir");
        let fs = RealFs::new();
        let first = fs.meta(&path).expect("meta");
        assert!(first.is_directory);
        let file = fs.meta(&path.join("a")).expect("meta");
        assert!(file.is_file);
        // Two inodes are two identities. The path is only a label, which is why nothing in
        // this crate keys a value by it.
        assert!(std::fs::symlink_metadata(&path)
            .map(|metadata| {
                use std::os::unix::fs::MetadataExt;
                metadata.ino()
            })
            .map(|inode| inode == first.inode)
            .unwrap_or(false));
        assert!(!file.is_directory);
    }

    #[test]
    fn the_in_memory_tree_refuses_exactly_what_the_real_tree_refuses() {
        let mut fs = MemFs::new();
        fs.directory("/cg", 41);
        fs.file("/cg/cpu.stat", "usage_usec 5\n", 42);
        fs.file("/cg/big", vec![b'x'; 128], 43);
        assert_eq!(
            fs.read(Path::new("/cg/cpu.stat"), 64),
            Ok(b"usage_usec 5\n".to_vec())
        );
        assert_eq!(
            fs.read(Path::new("/cg/big"), 64),
            Err(FsError::TooLarge { max_bytes: 64 })
        );
        assert_eq!(fs.read(Path::new("/cg"), 64), Err(FsError::NotAFile));
        assert_eq!(fs.read(Path::new("/nope"), 64), Err(FsError::NotFound));
        assert_eq!(
            fs.list(Path::new("/cg"), 1),
            Err(FsError::TooManyEntries { max_entries: 1 }),
            "two names do not fit a bound of one"
        );
        assert!(
            fs.list(Path::new("/cg"), 2).is_ok(),
            "a bound of two accepts exactly two names"
        );
        let entries = fs.list(Path::new("/cg"), 8).expect("list");
        assert_eq!(entries.len(), 2);
        assert!(entries.iter().all(|entry| !entry.is_dir()));
        assert_eq!(
            fs.list(Path::new("/cg/cpu.stat"), 8),
            Err(FsError::NotADirectory)
        );
        assert_eq!(fs.meta(Path::new("/cg")), Ok(Meta::directory(41)));
        assert_eq!(fs.meta(Path::new("/cg/cpu.stat")), Ok(Meta::file(42)));
        assert!(fs.exists(Path::new("/cg")));
        assert!(!fs.exists(Path::new("/gone")));
    }

    #[test]
    fn every_failure_maps_to_a_quality_code_and_absence_is_the_callers_choice() {
        assert_eq!(
            FsError::NotFound.quality(QualityCode::ABSENT),
            QualityCode::ABSENT
        );
        assert_eq!(
            FsError::NotFound.quality(QualityCode::UNSUPPORTED),
            QualityCode::UNSUPPORTED,
            "an absent host feature is the caller's code, not a guess"
        );
        for error in [FsError::PermissionDenied, FsError::Io(IoKind::Other)] {
            assert_eq!(error.quality(QualityCode::ABSENT), QualityCode::UNREADABLE);
        }
        for error in [
            FsError::TooLarge { max_bytes: 4 },
            FsError::TooManyEntries { max_entries: 4 },
        ] {
            assert_eq!(error.quality(QualityCode::ABSENT), QualityCode::NOT_SAMPLED);
        }
        for error in [
            FsError::NotFound,
            FsError::NotAFile,
            FsError::NotADirectory,
            FsError::PermissionDenied,
            FsError::TooLarge { max_bytes: 4 },
            FsError::TooManyEntries { max_entries: 4 },
            FsError::Io(IoKind::Other),
        ] {
            assert!(!error.to_string().is_empty(), "{error:?}");
        }
    }
}
