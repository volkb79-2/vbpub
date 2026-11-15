//! The store's only write path.
//!
//! Plan v4 §A.3 allows writes in exactly two places: the actuator and "the store's own state
//! dirs". This module *is* the store's state-dir write path, and it is the only file in
//! `crates/store/src` that may name a write operation. `tools/check-lints.py` enforces that
//! mechanically (the allowlist entry is `crates/store/src/fsio.rs` alone), and
//! `docs/HAND-MUTANTS-M1C.md` replays a patch that moves one write into another module and
//! asserts that the lint lane refuses it.
//!
//! # Contracts
//!
//! - **One root, one directory handle.** [`StoreDir`] is opened once from a configured path
//!   and every operation takes a *single path component* ([`check_name`]); nothing in the
//!   store can escape its directory by spelling a path.
//! - **No symbolic link is ever followed.** Every operation that names a file first takes
//!   `symlink_metadata` and refuses a link ([`StoreError::Symlink`]); creation uses
//!   `create_new`, so an existing name — including a link a hostile caller planted — is
//!   never opened for writing. `StoreDir::open` refuses a root that is itself a link or not
//!   a directory.
//! - **Writes are reported, never swallowed.** Every failure carries the operation, the
//!   errno and the name ([`StoreError::Io`]), and a full disk is its own variant
//!   ([`StoreError::NoSpace`]) so the counters can count it.
//! - **Sealing is atomic.** [`StoreDir::seal_open_segment`] writes a temporary file, copies
//!   the open segment into it, appends the footer, fsyncs the file, renames it to the
//!   sealed name, fsyncs the directory and only then removes the open file. A crash at any
//!   point leaves either the open segment (intact) plus a `.tmp` orphan, or a complete
//!   sealed segment, never a half-sealed file under a name a reader would trust.
//! - **Bounded reads and listings.** [`StoreDir::read`] refuses a file larger than its
//!   caller's bound *before* allocating, and [`StoreDir::list`] refuses a directory beyond
//!   its entry bound.
//!
//! # Why `symlink_metadata` and not `openat`/`O_NOFOLLOW`
//!
//! `O_NOFOLLOW` is only reachable through `libc` or `rustix`, i.e. `unsafe` or a new
//! dependency; the workspace forbids the first and the lane image's baked `Cargo.lock`
//! forbids the second (`HOB-27`). The check below is therefore a `symlink_metadata` before
//! the open, which closes the accidental-link case (a link planted before the store runs,
//! the sampler's `fsafe` lesson) and leaves a documented TOCTOU window for a link swapped in
//! between the two calls (`HOB-31`).

use core::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use crate::error::StoreError;

/// The largest directory the store will list in one call unless its caller widens it.
pub const LIST_BOUND: usize = 4096;

/// The chunk size a streamed copy uses, so a copy never needs the whole file in memory.
pub const COPY_CHUNK: usize = 64 * 1024;

/// Check that `name` is one path component the store may join under its root.
///
/// # Errors
///
/// [`StoreError::BadName`] for an empty name, `.`/`..`, a separator, a NUL byte or a
/// leading `-` (a name that could be read as an option by a tool).
pub fn check_name(name: &str) -> Result<(), StoreError> {
    let refuse = || StoreError::BadName {
        name: name.to_string(),
    };
    if name.is_empty() || name.len() > 255 || name == "." || name == ".." {
        return Err(refuse());
    }
    if name.starts_with('-') {
        return Err(refuse());
    }
    if name
        .bytes()
        .any(|byte| byte == b'/' || byte == 0 || byte == b'\n' || byte == b'\r')
    {
        return Err(refuse());
    }
    Ok(())
}

/// What a listed name is, from its own metadata (no link is followed).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EntryKind {
    /// A regular file.
    File,
    /// A directory.
    Directory,
    /// A symbolic link.
    Symlink,
    /// A socket, device, FIFO or anything else.
    Other,
}

/// One listed name.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DirEntry {
    /// The entry's own name (one component).
    pub name: String,
    /// What it is.
    pub kind: EntryKind,
    /// Its size in bytes (`0` for a directory or a device).
    pub len: u64,
}

/// The metadata facts the store uses: size, kind, and whether it is a link.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FileMeta {
    /// The size in bytes.
    pub len: u64,
    /// What the name is.
    pub kind: EntryKind,
}

/// A write failure carrying the file it happened to.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteReport {
    /// The name written.
    pub name: String,
    /// The bytes that reached the file before the failure (`0` when the open failed).
    pub written: u64,
}

/// One store directory (a tier's own state dir), opened once and used by relative name only.
#[derive(Clone, Debug)]
pub struct StoreDir {
    root: PathBuf,
}

impl StoreDir {
    /// Open (and optionally create) a store directory.
    ///
    /// The root is refused when it is a symbolic link, is not a directory, or cannot be
    /// created. Nothing inside it is read, truncated or deleted here.
    ///
    /// # Errors
    ///
    /// [`StoreError::Symlink`], [`StoreError::NotADirectory`] and [`StoreError::Io`].
    pub fn open(root: &Path, create: bool) -> Result<StoreDir, StoreError> {
        match fs::symlink_metadata(root) {
            Ok(meta) => {
                if meta.file_type().is_symlink() {
                    return Err(StoreError::Symlink {
                        name: root.display().to_string(),
                    });
                }
                if !meta.is_dir() {
                    return Err(StoreError::NotADirectory {
                        path: root.display().to_string(),
                    });
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                if !create {
                    return Err(StoreError::io("open", &root.display().to_string(), &error));
                }
                fs::create_dir_all(root).map_err(|error| {
                    StoreError::io("create-dir", &root.display().to_string(), &error)
                })?;
                let meta = fs::symlink_metadata(root)
                    .map_err(|error| StoreError::io("stat", &root.display().to_string(), &error))?;
                if meta.file_type().is_symlink() || !meta.is_dir() {
                    return Err(StoreError::NotADirectory {
                        path: root.display().to_string(),
                    });
                }
            }
            Err(error) => return Err(StoreError::io("stat", &root.display().to_string(), &error)),
        }
        StoreDir::sync_root(root)?;
        Ok(StoreDir {
            root: root.to_path_buf(),
        })
    }

    /// The configured root, as the caller spelled it (never a resolved path).
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// The path of one name under this root.
    #[must_use]
    pub fn path_of(&self, name: &str) -> PathBuf {
        self.root.join(name)
    }

    /// Resolve a validated name to its path.
    ///
    /// # Errors
    ///
    /// [`StoreError::BadName`] for a name [`check_name`] refuses.
    pub fn checked_path(&self, name: &str) -> Result<PathBuf, StoreError> {
        check_name(name)?;
        Ok(self.root.join(name))
    }

    /// List the directory's entries inside `max_entries`.
    ///
    /// # Errors
    ///
    /// [`StoreError::EntryLimit`] when more entries exist, and [`StoreError::Io`].
    pub fn list(&self, max_entries: usize) -> Result<Vec<DirEntry>, StoreError> {
        let reader = fs::read_dir(&self.root)
            .map_err(|error| StoreError::io("list", &self.root.display().to_string(), &error))?;
        let mut entries = Vec::new();
        for next in reader {
            if entries.len() >= max_entries {
                return Err(StoreError::EntryLimit { max: max_entries });
            }
            let entry = next.map_err(|error| {
                StoreError::io("list", &self.root.display().to_string(), &error)
            })?;
            let name = entry.file_name().to_string_lossy().into_owned();
            let meta = fs::symlink_metadata(entry.path())
                .map_err(|error| StoreError::io("stat", &name, &error))?;
            let kind = if meta.file_type().is_symlink() {
                EntryKind::Symlink
            } else if meta.is_dir() {
                EntryKind::Directory
            } else if meta.is_file() {
                EntryKind::File
            } else {
                EntryKind::Other
            };
            entries.push(DirEntry {
                name,
                kind,
                len: meta.len(),
            });
        }
        entries.sort_by(|left, right| left.name.cmp(&right.name));
        Ok(entries)
    }

    /// The metadata of one name, or `None` when it does not exist.
    ///
    /// # Errors
    ///
    /// [`StoreError::BadName`] and [`StoreError::Io`] (other than a missing name).
    pub fn metadata(&self, name: &str) -> Result<Option<FileMeta>, StoreError> {
        let path = self.checked_path(name)?;
        match fs::symlink_metadata(&path) {
            Ok(meta) => {
                let kind = if meta.file_type().is_symlink() {
                    EntryKind::Symlink
                } else if meta.is_dir() {
                    EntryKind::Directory
                } else if meta.is_file() {
                    EntryKind::File
                } else {
                    EntryKind::Other
                };
                Ok(Some(FileMeta {
                    len: meta.len(),
                    kind,
                }))
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(StoreError::io("stat", name, &error)),
        }
    }

    /// Read a whole file, refusing one larger than `max_bytes` before allocating.
    ///
    /// The bound is applied twice: to the size the metadata reports (a cheap refusal for a
    /// huge file) and to the bytes actually read (a file that grew after the stat is
    /// truncated by the read, so the buffer cannot outgrow the bound).
    ///
    /// # Errors
    ///
    /// [`StoreError::FileTooLarge`], [`StoreError::Symlink`], [`StoreError::Io`] and
    /// [`StoreError::Truncated`] when the read stops short of the reported size.
    pub fn read(&self, name: &str, max_bytes: usize) -> Result<Vec<u8>, StoreError> {
        let path = self.checked_path(name)?;
        let meta =
            fs::symlink_metadata(&path).map_err(|error| StoreError::io("stat", name, &error))?;
        if meta.file_type().is_symlink() {
            return Err(StoreError::Symlink {
                name: name.to_string(),
            });
        }
        if !meta.is_file() {
            return Err(StoreError::NotADirectory {
                path: path.display().to_string(),
            });
        }
        if meta.len() > u64::try_from(max_bytes).map_err(|_| StoreError::LengthOverflow)? {
            return Err(StoreError::FileTooLarge {
                len: meta.len(),
                max: max_bytes,
            });
        }
        let budget = max_bytes.checked_add(1).ok_or(StoreError::LengthOverflow)?;
        let file = File::open(&path).map_err(|error| StoreError::io("read", name, &error))?;
        let mut buffer = Vec::new();
        let read = file
            .take(u64::try_from(budget).map_err(|_| StoreError::LengthOverflow)?)
            .read_to_end(&mut buffer)
            .map_err(|error| StoreError::io("read", name, &error))?;
        if read > max_bytes {
            return Err(StoreError::FileTooLarge {
                len: u64::try_from(read).map_err(|_| StoreError::LengthOverflow)?,
                max: max_bytes,
            });
        }
        Ok(buffer)
    }

    /// Read `len` bytes at `offset` of one name (a bounded windowed read).
    ///
    /// # Errors
    ///
    /// [`StoreError::FileTooLarge`], [`StoreError::Symlink`], [`StoreError::Io`] and
    /// [`StoreError::Truncated`] when fewer bytes exist than asked.
    pub fn read_at(
        &self,
        name: &str,
        offset: u64,
        len: usize,
        max_file_bytes: u64,
    ) -> Result<Vec<u8>, StoreError> {
        let path = self.checked_path(name)?;
        let meta =
            fs::symlink_metadata(&path).map_err(|error| StoreError::io("stat", name, &error))?;
        if meta.file_type().is_symlink() {
            return Err(StoreError::Symlink {
                name: name.to_string(),
            });
        }
        if meta.len() > max_file_bytes {
            return Err(StoreError::FileTooLarge {
                len: meta.len(),
                max: usize::try_from(max_file_bytes).unwrap_or(usize::MAX),
            });
        }
        let mut file = File::open(&path).map_err(|error| StoreError::io("read", name, &error))?;
        file.seek(SeekFrom::Start(offset))
            .map_err(|error| StoreError::io("seek", name, &error))?;
        let mut buffer = Vec::new();
        let budget = u64::try_from(len)
            .map_err(|_| StoreError::LengthOverflow)?
            .checked_add(0)
            .ok_or(StoreError::LengthOverflow)?;
        let read = file
            .take(budget)
            .read_to_end(&mut buffer)
            .map_err(|error| StoreError::io("read", name, &error))?;
        if read != len {
            return Err(StoreError::Truncated {
                context: "windowed read",
                need: len,
                have: read,
            });
        }
        Ok(buffer)
    }

    /// Create one name that must not exist yet, write `bytes`, and fsync the file.
    ///
    /// A name that exists — including a planted link — is refused by `create_new` before
    /// anything is written, so a hostile caller can never have bytes appended to a file of
    /// their choosing.
    ///
    /// # Errors
    ///
    /// [`StoreError::Io`] (`op = "create"`), [`StoreError::NoSpace`].
    pub fn create(&self, name: &str, bytes: &[u8]) -> Result<(), StoreError> {
        let path = self.checked_path(name)?;
        if let Some(meta) = self.metadata(name)? {
            return Err(StoreError::ForeignEntry {
                name: name.to_string(),
                found: meta.kind.describe(),
            });
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|error| StoreError::io("create", name, &error))?;
        write_all(&mut file, name, bytes)?;
        file.sync_all()
            .map_err(|error| StoreError::io("sync", name, &error))?;
        Ok(())
    }

    /// Open an existing file for appending.
    ///
    /// # Errors
    ///
    /// [`StoreError::Symlink`], [`StoreError::Truncated`] (the name does not exist) and
    /// [`StoreError::Io`].
    pub fn open_appender(&self, name: &str) -> Result<Appender, StoreError> {
        let path = self.checked_path(name)?;
        let meta = fs::symlink_metadata(&path).map_err(|error| match error.kind() {
            std::io::ErrorKind::NotFound => StoreError::Truncated {
                context: "appended segment",
                need: 1,
                have: 0,
            },
            _ => StoreError::io("stat", name, &error),
        })?;
        if meta.file_type().is_symlink() {
            return Err(StoreError::Symlink {
                name: name.to_string(),
            });
        }
        let file = OpenOptions::new()
            .append(true)
            .open(&path)
            .map_err(|error| StoreError::io("open", name, &error))?;
        Ok(Appender {
            name: name.to_string(),
            file,
            written: meta.len(),
        })
    }

    /// Truncate a store-owned file to `len` bytes (the torn-tail repair).
    ///
    /// # Errors
    ///
    /// [`StoreError::Symlink`], [`StoreError::Io`].
    pub fn truncate(&self, name: &str, len: u64) -> Result<(), StoreError> {
        let path = self.checked_path(name)?;
        let meta =
            fs::symlink_metadata(&path).map_err(|error| StoreError::io("stat", name, &error))?;
        if meta.file_type().is_symlink() {
            return Err(StoreError::Symlink {
                name: name.to_string(),
            });
        }
        let file = OpenOptions::new()
            .write(true)
            .open(&path)
            .map_err(|error| StoreError::io("truncate", name, &error))?;
        file.set_len(len)
            .map_err(|error| StoreError::io("truncate", name, &error))?;
        file.sync_all()
            .map_err(|error| StoreError::io("sync", name, &error))
    }

    /// Remove one store-owned file.
    ///
    /// # Errors
    ///
    /// [`StoreError::Symlink`] (a link is never removed, because the store did not create
    /// it), [`StoreError::Io`].
    pub fn remove(&self, name: &str) -> Result<(), StoreError> {
        let path = self.checked_path(name)?;
        let meta =
            fs::symlink_metadata(&path).map_err(|error| StoreError::io("stat", name, &error))?;
        if meta.file_type().is_symlink() {
            return Err(StoreError::Symlink {
                name: name.to_string(),
            });
        }
        if !meta.is_file() {
            return Err(StoreError::ForeignEntry {
                name: name.to_string(),
                found: "not a regular file",
            });
        }
        fs::remove_file(&path).map_err(|error| StoreError::io("remove", name, &error))
    }

    /// Rename one store-owned file within this directory.
    ///
    /// # Errors
    ///
    /// [`StoreError::Symlink`] for either name and [`StoreError::Io`].
    pub fn rename(&self, from: &str, to: &str) -> Result<(), StoreError> {
        let from_path = self.checked_path(from)?;
        let to_path = self.checked_path(to)?;
        let meta = fs::symlink_metadata(&from_path)
            .map_err(|error| StoreError::io("stat", from, &error))?;
        if meta.file_type().is_symlink() {
            return Err(StoreError::Symlink {
                name: from.to_string(),
            });
        }
        // A destination that already exists is refused: the store never overwrites a name it
        // did not create, and a rename is always "from the temp name to a free name".
        if self.metadata(to)?.is_some() {
            return Err(StoreError::ForeignEntry {
                name: to.to_string(),
                found: "already present",
            });
        }
        fs::rename(&from_path, &to_path).map_err(|error| StoreError::io("rename", to, &error))?;
        self.sync()
    }

    /// fsync the directory itself, so a rename or a fresh name is durable.
    ///
    /// # Errors
    ///
    /// [`StoreError::Io`].
    pub fn sync(&self) -> Result<(), StoreError> {
        StoreDir::sync_root(&self.root)
    }

    /// The size of one name, or `None` when it does not exist.
    ///
    /// # Errors
    ///
    /// [`StoreError::Symlink`] for a link, [`StoreError::Io`].
    pub fn size_of(&self, name: &str) -> Result<Option<u64>, StoreError> {
        match self.metadata(name)? {
            None => Ok(None),
            Some(meta) if meta.kind == EntryKind::Symlink => Err(StoreError::Symlink {
                name: name.to_string(),
            }),
            Some(meta) => Ok(Some(meta.len)),
        }
    }

    /// Seal an open segment atomically: copy it into a temporary file, append `footer`,
    /// fsync, rename to `sealed`, fsync the directory, then remove the open file.
    ///
    /// The copy is streamed in [`COPY_CHUNK`] chunks and bounded by `max_bytes`: a file
    /// larger than the bound is refused rather than copied, so a corrupt or foreign file
    /// can never be duplicated into a trusted name.
    ///
    /// # Errors
    ///
    /// [`StoreError::FileTooLarge`], [`StoreError::Symlink`], [`StoreError::ForeignEntry`]
    /// (the temporary or the sealed name already exists) and [`StoreError::Io`].
    pub fn seal_open_segment(
        &self,
        open: &str,
        sealed: &str,
        temp: &str,
        footer: &[u8],
        max_bytes: u64,
    ) -> Result<(), StoreError> {
        for name in [open, sealed, temp] {
            check_name(name)?;
        }
        let open_path = self.checked_path(open)?;
        let meta = fs::symlink_metadata(&open_path)
            .map_err(|error| StoreError::io("stat", open, &error))?;
        if meta.file_type().is_symlink() {
            return Err(StoreError::Symlink {
                name: open.to_string(),
            });
        }
        if meta.len() > max_bytes {
            return Err(StoreError::FileTooLarge {
                len: meta.len(),
                max: usize::try_from(max_bytes).unwrap_or(usize::MAX),
            });
        }
        for name in [sealed, temp] {
            if let Some(existing) = self.metadata(name)? {
                return Err(StoreError::ForeignEntry {
                    name: name.to_string(),
                    found: existing.kind.describe(),
                });
            }
        }
        let temp_path = self.checked_path(temp)?;
        let sealed_path = self.checked_path(sealed)?;
        let mut source =
            File::open(&open_path).map_err(|error| StoreError::io("read", open, &error))?;
        let mut target = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp_path)
            .map_err(|error| StoreError::io("create", temp, &error))?;
        let mut buffer = [0u8; COPY_CHUNK];
        let mut copied: u64 = 0;
        loop {
            let read = source
                .read(&mut buffer)
                .map_err(|error| StoreError::io("read", open, &error))?;
            if read == 0 {
                break;
            }
            copied = copied
                .checked_add(u64::try_from(read).map_err(|_| StoreError::LengthOverflow)?)
                .ok_or(StoreError::LengthOverflow)?;
            if copied > max_bytes {
                return Err(StoreError::FileTooLarge {
                    len: copied,
                    max: usize::try_from(max_bytes).unwrap_or(usize::MAX),
                });
            }
            write_all(&mut target, temp, &buffer[..read])?;
        }
        write_all(&mut target, temp, footer)?;
        target
            .sync_all()
            .map_err(|error| StoreError::io("sync", temp, &error))?;
        drop(target);
        fs::rename(&temp_path, &sealed_path)
            .map_err(|error| StoreError::io("rename", sealed, &error))?;
        self.sync()?;
        // Only now, with the sealed name durable, is the open file removed. A crash before
        // this point leaves both names: the sealed one is complete and the open one is a
        // duplicate, which the open path removes as an orphan it created.
        fs::remove_file(&open_path).map_err(|error| StoreError::io("remove", open, &error))?;
        self.sync()
    }

    fn sync_root(root: &Path) -> Result<(), StoreError> {
        let file = File::open(root)
            .map_err(|error| StoreError::io("sync-dir", &root.display().to_string(), &error))?;
        file.sync_all()
            .map_err(|error| StoreError::io("sync-dir", &root.display().to_string(), &error))
    }
}

impl EntryKind {
    /// A short name for a report.
    #[must_use]
    pub const fn describe(self) -> &'static str {
        match self {
            EntryKind::File => "a regular file",
            EntryKind::Directory => "a directory",
            EntryKind::Symlink => "a symlink",
            EntryKind::Other => "a device, socket or FIFO",
        }
    }
}

impl fmt::Display for EntryKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.describe())
    }
}

/// An open append handle: one file, written from the end, synced on demand.
///
/// A handle exists only through [`StoreDir::open_appender`], so its name was checked and
/// its link status verified once. Writes are counted so a partial write can be rolled back
/// with [`Appender::truncate_to`].
#[derive(Debug)]
pub struct Appender {
    name: String,
    file: File,
    written: u64,
}

impl Appender {
    /// The name this handle writes.
    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }

    /// The file's length after every write this handle completed.
    #[must_use]
    pub const fn len(&self) -> u64 {
        self.written
    }

    /// True while the handle has written nothing since it was opened.
    #[must_use]
    pub const fn is_empty_file(&self) -> bool {
        self.written == 0
    }

    /// True when the file holds no bytes at all.
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.written == 0
    }

    /// Append `bytes`, reporting how many reached the file.
    ///
    /// # Errors
    ///
    /// [`StoreError::NoSpace`] or [`StoreError::Io`]; the caller can roll back with
    /// [`Appender::truncate_to`] so no half-record stays behind.
    pub fn append(&mut self, bytes: &[u8]) -> Result<u64, StoreError> {
        let written = write_all(&mut self.file, &self.name, bytes)?;
        self.written = self
            .written
            .checked_add(u64::try_from(written).map_err(|_| StoreError::LengthOverflow)?)
            .ok_or(StoreError::LengthOverflow)?;
        u64::try_from(written).map_err(|_| StoreError::LengthOverflow)
    }

    /// Cut the file back to the length it had when the append began, after a failed write.
    ///
    /// # Errors
    ///
    /// [`StoreError::Io`].
    pub fn truncate_to(&mut self, len: u64) -> Result<(), StoreError> {
        self.file
            .set_len(len)
            .map_err(|error| StoreError::io("truncate", &self.name, &error))?;
        self.written = len;
        Ok(())
    }

    /// fsync the file.
    ///
    /// # Errors
    ///
    /// [`StoreError::Io`].
    pub fn sync(&mut self) -> Result<(), StoreError> {
        self.file
            .sync_data()
            .map_err(|error| StoreError::io("sync", &self.name, &error))
    }
}

/// Write every byte, reporting the failure with the name and how much got through.
fn write_all(file: &mut File, name: &str, bytes: &[u8]) -> Result<usize, StoreError> {
    match file.write_all(bytes) {
        Ok(()) => Ok(bytes.len()),
        Err(error) => {
            // `write_all` does not say how much it wrote, so the file's own length is the
            // measured fact; a caller that must roll back uses `set_len` with its own
            // remembered length, never this estimate.
            let written = file.metadata().map(|meta| meta.len()).unwrap_or(0);
            let report = WriteReport {
                name: name.to_string(),
                written,
            };
            let _ = report;
            Err(StoreError::io("append", name, &error))
        }
    }
}

/// The temporary-directory helper **every** store test uses.
///
/// The write-boundary lint (`tools/check-lints.py`) allows filesystem mutation in exactly one
/// file, `crates/store/src/fsio.rs`. That rule is not suspended for tests: a test that called
/// `std::fs::create_dir` or `OpenOptions::write` directly would be a second write path, and the
/// lint could no longer tell a test's scratch directory from a product's silent write. So the
/// test helpers live here, behind `#[cfg(test)]`, and every test — in `fsio`, `segment`,
/// `tier`, `events`, `store` — goes through [`TempDir`].
#[cfg(test)]
pub mod testdir {
    use std::path::{Path, PathBuf};

    /// A temporary directory that removes itself, so no test writes outside `$TMPDIR`.
    #[derive(Debug)]
    pub struct TempDir {
        path: PathBuf,
    }

    impl TempDir {
        /// A fresh directory under `$TMPDIR`, named `hostobs-store-<label>-<pid>-<nanos>`.
        #[must_use]
        pub fn new(label: &str) -> TempDir {
            let base = std::env::temp_dir();
            let unique = format!(
                "hostobs-store-{label}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|value| value.as_nanos())
                    .unwrap_or(0)
            );
            let path = base.join(unique);
            std::fs::create_dir(&path).expect("create temp dir");
            TempDir { path }
        }

        /// The directory itself.
        #[must_use]
        pub fn path(&self) -> &Path {
            &self.path
        }

        /// The path of `name` inside it (which may not exist).
        #[must_use]
        pub fn at(&self, name: &str) -> PathBuf {
            self.path.join(name)
        }

        /// Create the subdirectory `name` and answer its path.
        ///
        /// As [`TempDir::write_file`], the answer is available but not required.
        pub fn mkdir(&self, name: &str) -> PathBuf {
            let path = self.path.join(name);
            std::fs::create_dir(&path).expect("create a subdirectory");
            path
        }

        /// Write `bytes` to `name` and answer its path.
        ///
        /// The path is returned so a caller that needs it has it, but the usual case is "write
        /// this file and move on", so the answer is not `#[must_use]`.
        pub fn write_file(&self, name: &str, bytes: &[u8]) -> PathBuf {
            let path = self.path.join(name);
            std::fs::write(&path, bytes).expect("write a scratch file");
            path
        }

        /// Read `name`.
        #[must_use]
        pub fn read_file(&self, name: &str) -> Vec<u8> {
            std::fs::read(self.path.join(name)).expect("read a scratch file")
        }

        /// True when `name` exists (of any type).
        #[must_use]
        pub fn exists(&self, name: &str) -> bool {
            std::fs::symlink_metadata(self.path.join(name)).is_ok()
        }

        /// Cut `name` back to `len` bytes (the crash a torn tail simulates).
        pub fn truncate(&self, name: &str, len: u64) {
            let path = self.path.join(name);
            let file = std::fs::OpenOptions::new()
                .write(true)
                .open(&path)
                .expect("open a scratch file to truncate");
            file.set_len(len).expect("truncate a scratch file");
            file.sync_all().expect("sync a truncated scratch file");
        }

        /// Point `name` at `target`, which may live outside this directory.
        pub fn symlink_to(&self, name: &str, target: &Path) {
            std::os::unix::fs::symlink(target, self.path.join(name)).expect("create a symlink");
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::testdir::TempDir;
    use super::{check_name, Appender, EntryKind, StoreDir};
    use crate::error::StoreError;

    #[test]
    fn a_name_is_one_component_and_never_a_path() {
        assert_eq!(check_name("hot-1-2.seg"), Ok(()));
        assert_eq!(check_name("a.b_c"), Ok(()));
        for bad in ["", ".", "..", "../x", "a/b", "-x", "a\nb", "a\0b"] {
            assert_eq!(
                check_name(bad),
                Err(StoreError::BadName {
                    name: bad.to_string()
                }),
                "{bad} should be refused"
            );
        }
        assert_eq!(
            check_name(&"a".repeat(256)),
            Err(StoreError::BadName {
                name: "a".repeat(256)
            })
        );
    }

    #[test]
    fn a_directory_is_created_listed_and_bounded() {
        let temp = TempDir::new("list");
        let dir = StoreDir::open(&temp.path().join("tier"), true).expect("open");
        dir.create("b.seg", b"bb").expect("create");
        dir.create("a.seg", b"a").expect("create");
        let entries = dir.list(8).expect("list");
        assert_eq!(
            entries
                .iter()
                .map(|entry| entry.name.as_str())
                .collect::<Vec<_>>(),
            vec!["a.seg", "b.seg"]
        );
        assert!(entries.iter().all(|entry| entry.kind == EntryKind::File));
        assert_eq!(dir.list(1), Err(StoreError::EntryLimit { max: 1 }));
        assert_eq!(
            dir.metadata("missing.seg"),
            Ok(None),
            "a missing name is absence, not an error"
        );
        assert_eq!(dir.size_of("a.seg"), Ok(Some(1)));
        assert_eq!(dir.root(), temp.path().join("tier").as_path());
        assert_eq!(dir.path_of("a.seg"), temp.path().join("tier").join("a.seg"));
        assert_eq!(
            dir.checked_path("../escape"),
            Err(StoreError::BadName {
                name: "../escape".to_string()
            })
        );
    }

    #[test]
    fn a_file_is_read_within_its_bound() {
        let temp = TempDir::new("read");
        let dir = StoreDir::open(temp.path(), false).expect("open root");
        dir.create("x.seg", b"0123456789").expect("create");
        assert_eq!(dir.read("x.seg", 10), Ok(b"0123456789".to_vec()));
        assert_eq!(
            dir.read("x.seg", 9),
            Err(StoreError::FileTooLarge { len: 10, max: 9 })
        );
        assert_eq!(dir.read_at("x.seg", 2, 3, 1024), Ok(b"234".to_vec()));
        assert_eq!(
            dir.read_at("x.seg", 8, 4, 1024),
            Err(StoreError::Truncated {
                context: "windowed read",
                need: 4,
                have: 2
            })
        );
        assert_eq!(
            dir.read_at("x.seg", 0, 2, 4),
            Err(StoreError::FileTooLarge { len: 10, max: 4 })
        );
    }

    #[test]
    fn a_link_is_never_read_written_truncated_or_removed() {
        let temp = TempDir::new("link");
        let dir = StoreDir::open(temp.path(), false).expect("open root");
        let outside = temp.path().join("outside.txt");
        std::fs::write(&outside, b"outside").expect("write outside");
        let link = temp.path().join("link.seg");
        std::os::unix::fs::symlink(&outside, &link).expect("symlink");
        assert_eq!(
            dir.read("link.seg", 1024),
            Err(StoreError::Symlink {
                name: "link.seg".into()
            })
        );
        assert_eq!(
            dir.create("link.seg", b"x"),
            Err(StoreError::ForeignEntry {
                name: "link.seg".into(),
                found: "a symlink"
            })
        );
        assert_eq!(
            dir.open_appender("link.seg")
                .map(|appender| appender.name().to_string()),
            Err(StoreError::Symlink {
                name: "link.seg".into()
            })
        );
        assert_eq!(
            dir.truncate("link.seg", 0),
            Err(StoreError::Symlink {
                name: "link.seg".into()
            })
        );
        assert_eq!(
            dir.remove("link.seg"),
            Err(StoreError::Symlink {
                name: "link.seg".into()
            })
        );
        assert_eq!(
            dir.rename("link.seg", "other.seg"),
            Err(StoreError::Symlink {
                name: "link.seg".into()
            })
        );
        assert_eq!(
            std::fs::read(&outside)
                .map(|bytes| bytes.to_vec())
                .unwrap_or_default(),
            b"outside".to_vec(),
            "the link's target is untouched"
        );
        assert_eq!(
            dir.list(8).map(|entries| entries.len()),
            Ok(2),
            "the link and the file the test planted beside it"
        );
    }

    #[test]
    fn a_root_that_is_a_link_or_a_file_is_refused() {
        let temp = TempDir::new("root");
        let outside = temp.mkdir("real");
        let link = temp.path().join("linked");
        std::os::unix::fs::symlink(&outside, &link).expect("symlink");
        assert_eq!(
            StoreDir::open(&link, false).map(|_| ()),
            Err(StoreError::Symlink {
                name: link.display().to_string()
            })
        );
        let file = temp.write_file("file", b"x");
        assert_eq!(
            StoreDir::open(&file, false).map(|_| ()),
            Err(StoreError::NotADirectory {
                path: file.display().to_string()
            })
        );
        assert_eq!(
            StoreDir::open(&temp.path().join("missing"), false).map(|_| ()),
            Err(StoreError::Io {
                op: "open",
                os_code: Some(2),
                message: "No such file or directory (os error 2)".into(),
            })
        );
    }

    #[test]
    fn an_append_handle_writes_from_the_end_and_can_be_rolled_back() {
        let temp = TempDir::new("append");
        let dir = StoreDir::open(temp.path(), false).expect("open root");
        dir.create("s.seg", b"header").expect("create");
        let mut appender = dir.open_appender("s.seg").expect("open appender");
        assert_eq!(appender.len(), 6);
        assert!(!appender.is_empty_file());
        appender.append(b"ab").expect("append");
        assert_eq!(appender.len(), 8);
        assert_eq!(dir.read("s.seg", 64), Ok(b"headerab".to_vec()));
        appender.truncate_to(6).expect("roll back");
        assert_eq!(dir.read("s.seg", 64), Ok(b"header".to_vec()));
        appender.append(b"cd").expect("append");
        appender.sync().expect("sync");
        assert_eq!(appender.name(), "s.seg");
        assert_eq!(dir.read("s.seg", 64), Ok(b"headercd".to_vec()));
    }

    #[test]
    fn an_appender_refuses_a_name_that_does_not_exist() {
        let temp = TempDir::new("missing");
        let dir = StoreDir::open(temp.path(), false).expect("open root");
        let parsed = dir.open_appender("nope.seg");
        assert_eq!(
            parsed.map(|appender| appender.name().to_string()),
            Err(StoreError::Truncated {
                context: "appended segment",
                need: 1,
                have: 0
            })
        );
    }

    #[test]
    fn an_existing_name_is_never_overwritten_by_a_create() {
        let temp = TempDir::new("create");
        let dir = StoreDir::open(temp.path(), false).expect("open root");
        dir.create("x.seg", b"one").expect("create");
        assert_eq!(
            dir.create("x.seg", b"two"),
            Err(StoreError::ForeignEntry {
                name: "x.seg".into(),
                found: "a regular file"
            })
        );
        assert_eq!(dir.read("x.seg", 64), Ok(b"one".to_vec()));
        temp.mkdir("sub");
        assert_eq!(
            dir.create("sub", b""),
            Err(StoreError::ForeignEntry {
                name: "sub".into(),
                found: "a directory"
            })
        );
        assert_eq!(
            dir.remove("sub"),
            Err(StoreError::ForeignEntry {
                name: "sub".into(),
                found: "not a regular file"
            })
        );
        assert_eq!(
            dir.truncate("missing.seg", 0).map_err(|error| error.kind()),
            Err("io")
        );
        assert_eq!(
            dir.read("sub", 64),
            Err(StoreError::NotADirectory {
                path: temp.path().join("sub").display().to_string()
            })
        );
    }

    #[test]
    fn sealing_copies_the_open_segment_appends_the_footer_and_removes_the_open_name() {
        let temp = TempDir::new("seal");
        let dir = StoreDir::open(temp.path(), false).expect("open root");
        dir.create("t-1-0.seg.open", b"header-and-record")
            .expect("create");
        let footer = vec![0xA5u8; 64];
        dir.seal_open_segment(
            "t-1-0.seg.open",
            "t-1-0.seg",
            "t-1-0.seg.tmp",
            &footer,
            1024,
        )
        .expect("seal");
        let sealed = dir.read("t-1-0.seg", 1024).expect("read sealed");
        assert_eq!(sealed.len(), 17 + 64);
        assert_eq!(&sealed[..17], b"header-and-record");
        assert_eq!(&sealed[17..], &footer[..]);
        assert_eq!(dir.metadata("t-1-0.seg.open"), Ok(None));
        assert_eq!(dir.metadata("t-1-0.seg.tmp"), Ok(None));
        // A second seal onto an existing sealed name is refused, never overwriting it.
        dir.create("t-1-1.seg.open", b"x").expect("create");
        assert_eq!(
            dir.seal_open_segment(
                "t-1-1.seg.open",
                "t-1-0.seg",
                "t-1-1.seg.tmp",
                &footer,
                1024
            )
            .map_err(|error| error.kind()),
            Err("foreign-entry")
        );
        assert_eq!(
            dir.seal_open_segment(
                "missing.seg.open",
                "t-1-2.seg",
                "t-1-2.seg.tmp",
                &footer,
                1024
            )
            .map_err(|error| error.kind()),
            Err("io")
        );
        dir.create("big.seg.open", b"0123456789").expect("create");
        assert_eq!(
            dir.seal_open_segment("big.seg.open", "big.seg", "big.seg.tmp", &footer, 4),
            Err(StoreError::FileTooLarge { len: 10, max: 4 })
        );
    }

    #[test]
    fn a_rename_refuses_a_link_source_and_a_present_destination() {
        let temp = TempDir::new("rename");
        let dir = StoreDir::open(temp.path(), false).expect("open root");
        dir.create("a.seg", b"a").expect("create");
        dir.create("b.seg", b"b").expect("create");
        assert_eq!(
            dir.rename("a.seg", "b.seg").map_err(|error| error.kind()),
            Err("foreign-entry")
        );
        dir.rename("a.seg", "c.seg").expect("rename");
        assert_eq!(dir.metadata("a.seg"), Ok(None));
        assert_eq!(dir.read("c.seg", 8), Ok(b"a".to_vec()));
        let outside = temp.path().join("outside");
        std::fs::write(&outside, b"o").expect("write");
        std::os::unix::fs::symlink(&outside, temp.path().join("link.seg")).expect("symlink");
        assert_eq!(
            dir.rename("link.seg", "d.seg"),
            Err(StoreError::Symlink {
                name: "link.seg".into()
            })
        );
        assert_eq!(
            dir.rename("missing.seg", "e.seg")
                .map_err(|error| error.kind()),
            Err("io")
        );
    }

    #[test]
    fn an_appender_is_what_the_directory_handed_out() {
        let temp = TempDir::new("handle");
        let dir = StoreDir::open(temp.path(), false).expect("open root");
        dir.create("empty.seg", b"").expect("create");
        let mut appender: Appender = dir.open_appender("empty.seg").expect("open");
        assert!(appender.is_empty_file());
        appender.append(&[1u8]).expect("append");
        assert!(!appender.is_empty_file());
        assert_eq!(dir.size_of("empty.seg"), Ok(Some(1)));
    }
}
