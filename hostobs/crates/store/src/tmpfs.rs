//! Which filesystem a store directory lives on, read from the mount table.
//!
//! Plan v4 §A.4 puts hot and warm on tmpfs and requires a `require_tmpfs` check. `statfs`'s
//! `f_type == TMPFS_MAGIC` is only reachable through `libc` or `rustix` — `unsafe` or a new
//! dependency, both forbidden here (`HOB-27`) — so the check reads the kernel's own
//! `/proc/self/mounts` instead and matches the directory against the mount whose prefix covers
//! it. That is a *read*, and it is the same fact `statfs` reports one layer up: the mount
//! table states the filesystem type of every mount in the calling process's mount namespace.
//!
//! # Contracts
//!
//! * **The longest matching prefix wins.** `/run/hostobs/tiers` is covered by the mount at
//!   `/run` when a more specific mount does not exist, so the answer is the resolver's rule,
//!   not "the first line that matches".
//! * **No entry means unknown, and unknown is a refusal**, never "assume tmpfs". A path
//!   outside every listed mount is [`StoreError::MountNotFound`], and a mount table that
//!   cannot be read is [`StoreError::MountTableUnreadable`] — three distinct states, not one
//!   boolean.
//! * **The table is parsed, not guessed.** An escape (`\040` for a space and the other
//!   three-digit octal forms) is decoded, because a mount point may contain one; a malformed
//!   line is skipped rather than failing the whole table (the kernel can add fields this
//!   parser does not use).
//! * **A test never reads the host's table** unless it asks for it: [`MountTable::parse`] is
//!   pure and takes text, and [`read_mounts`] is the one function that touches the host.

use std::collections::BTreeMap;
use std::path::Path;

use crate::error::StoreError;

/// The filesystem type name tmpfs reports in the mount table.
pub const TMPFS: &str = "tmpfs";

/// The largest mount table this crate will parse, so an enormous table cannot grow memory
/// without a limit.
pub const MAX_TABLE_BYTES: usize = 4 << 20;

/// The largest number of mounts one table may list.
pub const MAX_MOUNTS: usize = 65_536;

/// One parsed mount.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Mount {
    /// The mount point, with escapes decoded.
    pub point: String,
    /// The filesystem type (`tmpfs`, `ext4`, `btrfs`, …).
    pub fstype: String,
    /// The mount's options, unparsed.
    pub options: String,
}

/// A parsed mount table with a longest-prefix matcher.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MountTable {
    mounts: Vec<Mount>,
}

impl MountTable {
    /// Parse `/proc/self/mounts`-shaped text.
    ///
    /// Each line is `device mountpoint fstype options dump pass`; the two trailing numbers are
    /// not used. A line with fewer than four fields is skipped, and so is a line whose mount
    /// point cannot be decoded, because the fields this crate uses would then be guesses.
    ///
    /// # Errors
    ///
    /// [`StoreError::MountTableUnreadable`] for more than [`MAX_MOUNTS`] entries.
    pub fn parse(text: &str) -> Result<MountTable, StoreError> {
        let mut mounts = Vec::new();
        for line in text.lines() {
            if line.trim().is_empty() || line.starts_with('#') {
                continue;
            }
            let mut fields = line.split_whitespace();
            // `/proc/self/mounts` always carries `device point fstype options`; a line with
            // fewer fields is not a mount line, and guessing which field a two-token line
            // meant would make the filesystem type a guess as well.
            let (Some(_device), Some(point), Some(fstype), Some(options)) =
                (fields.next(), fields.next(), fields.next(), fields.next())
            else {
                continue;
            };
            let Some(point) = unescape(point) else {
                continue;
            };
            if mounts.len() >= MAX_MOUNTS {
                return Err(StoreError::MountTableUnreadable {
                    reason: "the mount table lists more mounts than the bound".to_string(),
                });
            }
            mounts.push(Mount {
                point,
                fstype: fstype.to_string(),
                options: options.to_string(),
            });
        }
        Ok(MountTable::from_mounts(mounts))
    }

    /// Build a table from mount entries (test and fixture convenience).
    ///
    /// The entries are sorted by mount point so the longest-prefix walk is easy to read; the
    /// **stability** of that sort is what preserves the table's own line order, which is what
    /// [`MountTable::covering`] needs for two mounts at the same point: the kernel exposes the
    /// last one, so the last line wins.
    #[must_use]
    pub fn from_mounts(mut mounts: Vec<Mount>) -> MountTable {
        mounts.sort_by(|left, right| left.point.cmp(&right.point));
        MountTable { mounts }
    }

    /// The number of mounts in the table.
    #[must_use]
    pub fn len(&self) -> usize {
        self.mounts.len()
    }

    /// True when the table lists nothing.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.mounts.is_empty()
    }

    /// The mounts in the table.
    pub fn mounts(&self) -> impl Iterator<Item = &Mount> {
        self.mounts.iter()
    }

    /// The mount whose point covers `path`, by longest prefix.
    ///
    /// The comparison is on whole components, so `/run/hostobs` is not covered by a mount at
    /// `/run/host`: a prefix that ends inside a component is not a mount for this path.
    ///
    /// Two mounts at the same point are a real state (`mount` over an existing mount shadows
    /// it), and the kernel exposes the **last** one. The table keeps its line order for equal
    /// points and the later line wins here — taking the first would report the shadowed
    /// filesystem, which is exactly the overmount that a `require_tmpfs` check must not be
    /// fooled by.
    ///
    /// The path is compared as the caller spelled it: [`MountTable::check_tmpfs`] is what
    /// resolves it first, because a lexical comparison alone accepts `..` and an intermediate
    /// link that the kernel resolves elsewhere.
    ///
    /// # Errors
    ///
    /// [`StoreError::MountNotFound`] when no entry covers the path.
    pub fn covering(&self, path: &Path) -> Result<&Mount, StoreError> {
        let wanted = path.to_string_lossy().into_owned();
        let mut best: Option<&Mount> = None;
        for mount in &self.mounts {
            if !covers(&mount.point, &wanted) {
                continue;
            }
            let better = match best {
                None => true,
                // `>=`, not `>`: an equal point is the overmount, and its line is the later one.
                Some(current) => mount.point.len() >= current.point.len(),
            };
            if better {
                best = Some(mount);
            }
        }
        best.ok_or(StoreError::MountNotFound { path: wanted })
    }

    /// The filesystem type of `path`.
    ///
    /// # Errors
    ///
    /// [`StoreError::MountNotFound`].
    pub fn fstype_of(&self, path: &Path) -> Result<String, StoreError> {
        Ok(self.covering(path)?.fstype.clone())
    }

    /// Check the `require_tmpfs` policy for one directory.
    ///
    /// The configured path is **resolved first** ([`MountTable::resolve`]): the mount table
    /// lists the paths the kernel resolved, so matching a path that still carries `..` or an
    /// intermediate link compares the caller's spelling with the kernel's fact. That is how
    /// an overmount is missed — `/run/x` linked to `/var/disk` would be checked as `/run/x`
    /// and pass a tmpfs test while its bytes land on a disk.
    ///
    /// # Errors
    ///
    /// [`StoreError::NotTmpfs`] when `required` and the filesystem is something else,
    /// [`StoreError::MountNotFound`] when no mount covers the resolved path — an unknown
    /// filesystem never passes a required check — and [`StoreError::Config`]/
    /// [`StoreError::Symlink`] for a path that cannot be resolved as a directory *inside* the
    /// configured tree.
    pub fn check_tmpfs(&self, path: &Path, required: bool) -> Result<bool, StoreError> {
        let resolved = self.resolve(path)?;
        let mount = self.covering(&resolved)?;
        let is_tmpfs = mount.fstype == TMPFS;
        if required && !is_tmpfs {
            return Err(StoreError::NotTmpfs {
                tier: path.display().to_string(),
                fstype: mount.fstype.clone(),
            });
        }
        Ok(is_tmpfs)
    }

    /// Resolve one configured path to the spelling the kernel's mount table uses.
    ///
    /// Three refusals, each its own fact:
    ///
    /// * a relative path ([`StoreError::Config`]): a mount point is absolute, so a relative
    ///   configured path can never be compared with one;
    /// * a `..` component ([`StoreError::Config`]): it names a directory outside the configured
    ///   tree, which is the opposite of what a tier's own state directory is;
    /// * a component that is a symbolic link ([`StoreError::Symlink`]): the kernel would
    ///   resolve it elsewhere, so the mount this table finds for the link's own path is not the
    ///   mount the store's bytes land on.
    ///
    /// `.` components are dropped (they name nothing new). A component that does not exist yet
    /// is not a link and cannot be one, so the walk stops there: the store creates its
    /// directories after this check, and a path that does not exist is exactly that state.
    ///
    /// # Errors
    ///
    /// As above, plus [`StoreError::Io`] when a component cannot be inspected at all —
    /// "could not determine" is a refusal, never a pass.
    pub fn resolve(&self, path: &Path) -> Result<std::path::PathBuf, StoreError> {
        use std::path::Component;
        let mut resolved = std::path::PathBuf::new();
        for component in path.components() {
            match component {
                Component::RootDir => resolved.push("/"),
                Component::CurDir => {}
                Component::ParentDir => {
                    return Err(StoreError::Config {
                        key: "tmpfs_dir",
                        reason: "must not contain a `..` component",
                    });
                }
                Component::Prefix(_) => {
                    return Err(StoreError::Config {
                        key: "tmpfs_dir",
                        reason: "must be an absolute Unix path",
                    });
                }
                Component::Normal(part) => resolved.push(part),
            }
        }
        if !resolved.is_absolute() {
            return Err(StoreError::Config {
                key: "tmpfs_dir",
                reason: "must be an absolute path",
            });
        }
        // Walk the resolved path one component at a time: the link that matters is an
        // *intermediate* one, which a check of the last component alone never sees.
        let mut walked = std::path::PathBuf::from("/");
        let mut components = resolved.components();
        let _ = components.next();
        for part in components {
            walked.push(part);
            match std::fs::symlink_metadata(&walked) {
                Ok(meta) if meta.file_type().is_symlink() => {
                    return Err(StoreError::Symlink {
                        name: walked.display().to_string(),
                    });
                }
                Ok(_) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    // The store creates its directories after this check, so a missing
                    // component is the ordinary case; nothing below it can be a link either.
                    break;
                }
                Err(error) => {
                    return Err(StoreError::io("stat", &walked.display().to_string(), &error))
                }
            }
        }
        Ok(resolved)
    }
}

/// True when `mount_point` is a whole-component prefix of `path` (or is the root).
#[must_use]
pub fn covers(mount_point: &str, path: &str) -> bool {
    if mount_point == "/" {
        return path.starts_with('/');
    }
    let trimmed = mount_point.trim_end_matches('/');
    if !path.starts_with(trimmed) {
        return false;
    }
    match path.as_bytes().get(trimmed.len()) {
        None => true,
        Some(b'/') => true,
        Some(_) => false,
    }
}

/// Decode the mount table's octal escapes (`\040` etc), or `None` for a malformed escape.
#[must_use]
pub fn unescape(text: &str) -> Option<String> {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars();
    while let Some(character) = chars.next() {
        if character != '\\' {
            out.push(character);
            continue;
        }
        let digit_one = chars.next()?.to_digit(8)?;
        let digit_two = chars.next()?.to_digit(8)?;
        let digit_three = chars.next()?.to_digit(8)?;
        let value = (digit_one * 64)
            .checked_add(digit_two * 8)?
            .checked_add(digit_three)?;
        out.push(char::from(u8::try_from(value).ok()?));
    }
    Some(out)
}

/// Read the calling process's own mount table (`/proc/self/mounts`).
///
/// # Errors
///
/// [`StoreError::MountTableUnreadable`].
pub fn read_self() -> Result<MountTable, StoreError> {
    MountTable::parse(&read_mounts(Path::new("/proc/self/mounts"))?)
}

/// Read one mount table file under the byte bound.
///
/// # Errors
///
/// [`StoreError::MountTableUnreadable`].
pub fn read_mounts(path: &Path) -> Result<String, StoreError> {
    let metadata = std::fs::metadata(path).map_err(|error| StoreError::MountTableUnreadable {
        reason: format!("{}: {error}", path.display()),
    })?;
    if metadata.len() > u64::try_from(MAX_TABLE_BYTES).unwrap_or(u64::MAX) {
        return Err(StoreError::MountTableUnreadable {
            reason: format!(
                "{} is {} bytes, past the {MAX_TABLE_BYTES}-byte bound",
                path.display(),
                metadata.len()
            ),
        });
    }
    let text = std::fs::read_to_string(path).map_err(|error| StoreError::MountTableUnreadable {
        reason: format!("{}: {error}", path.display()),
    })?;
    if text.len() > MAX_TABLE_BYTES {
        return Err(StoreError::MountTableUnreadable {
            reason: format!("{} grew past the bound", path.display()),
        });
    }
    Ok(text)
}

/// A deterministic table for a test: one tmpfs at `/`, never the host's.
///
/// Test-only by construction (`#[cfg(test)]`): a production caller that could build its own
/// table would have a bypass for the `require_tmpfs` policy, and the honest production table
/// is the kernel's own (`read_self`).
#[cfg(test)]
#[must_use]
pub fn tmpfs_only() -> MountTable {
    MountTable::from_mounts(vec![Mount {
        point: "/".to_string(),
        fstype: TMPFS.to_string(),
        options: "rw".to_string(),
    }])
}

/// A deterministic table naming every path as `fstype`, mounted at `/` (test-only, as
/// [`tmpfs_only`]).
#[cfg(test)]
#[must_use]
pub fn all_of(fstype: &str) -> MountTable {
    MountTable::from_mounts(vec![Mount {
        point: "/".to_string(),
        fstype: fstype.to_string(),
        options: "rw".to_string(),
    }])
}

/// Look up a path's filesystem in a table of keyed entries, by longest covering prefix.
#[must_use]
pub fn lookup<'a>(table: &'a BTreeMap<String, String>, path: &Path) -> Option<&'a str> {
    let wanted = path.to_string_lossy().into_owned();
    table
        .iter()
        .filter(|(point, _)| covers(point, &wanted))
        .max_by_key(|(point, _)| point.len())
        .map(|(_, fstype)| fstype.as_str())
}

#[cfg(test)]
mod tests {
    use super::{
        all_of, covers, lookup, read_mounts, unescape, Mount, MountTable, MAX_MOUNTS,
        MAX_TABLE_BYTES, TMPFS,
    };
    use crate::error::StoreError;
    use crate::fsio::testdir::TempDir;
    use std::collections::BTreeMap;
    use std::path::Path;

    #[test]
    fn a_table_parses_lines_and_skips_what_it_cannot_read() {
        let text = "/dev/sda1 / ext4 rw,relatime 0 0
tmpfs /run tmpfs rw,nosuid,nodev 0 0
tmpfs /run/hostobs/tiers tmpfs rw,size=64m 0 0
/dev/mapper/x /var ext4 rw 0 0
# a comment
short line
one two three
";
        let table = MountTable::parse(text).expect("parse");
        assert_eq!(table.len(), 4);
        assert!(!table.is_empty());
        assert_eq!(
            table
                .mounts()
                .map(|mount| mount.point.as_str())
                .collect::<Vec<_>>(),
            vec!["/", "/run", "/run/hostobs/tiers", "/var"]
        );
        assert_eq!(table.fstype_of(Path::new("/x")), Ok("ext4".to_string()));
        assert_eq!(
            table.fstype_of(Path::new("/run/hostobs/tiers/hot")),
            Ok(TMPFS.to_string())
        );
        assert_eq!(
            table.fstype_of(Path::new("/run/hostobs")),
            Ok(TMPFS.to_string()),
            "the longest covering mount wins, not the first line"
        );
        assert_eq!(
            table.fstype_of(Path::new("/var/lib/x")),
            Ok("ext4".to_string())
        );
    }

    #[test]
    fn a_path_outside_every_mount_is_unknown_and_never_assumed() {
        let table = MountTable::from_mounts(vec![Mount {
            point: "/srv".to_string(),
            fstype: TMPFS.to_string(),
            options: String::new(),
        }]);
        assert_eq!(
            table.fstype_of(Path::new("/run/hostobs")),
            Err(StoreError::MountNotFound {
                path: "/run/hostobs".to_string()
            })
        );
        assert_eq!(
            table
                .check_tmpfs(Path::new("/run/hostobs"), true)
                .map(|_| ()),
            Err(StoreError::MountNotFound {
                path: "/run/hostobs".to_string()
            }),
            "an unknown filesystem never passes a required check"
        );
        assert_eq!(
            table
                .covering(Path::new("/srv/a"))
                .map(|mount| mount.point.as_str()),
            Ok("/srv")
        );
    }

    #[test]
    fn tmpfs_is_the_mounts_own_type_and_a_required_check_refuses_anything_else() {
        let on_tmpfs = super::tmpfs_only();
        assert_eq!(
            on_tmpfs.check_tmpfs(Path::new("/run/hostobs/tiers/hot"), true),
            Ok(true)
        );
        assert_eq!(
            on_tmpfs.check_tmpfs(Path::new("/run/hostobs/tiers/hot"), false),
            Ok(true)
        );
        let on_disk = all_of("ext4");
        assert_eq!(
            on_disk.check_tmpfs(Path::new("/var/lib/hostobs"), true),
            Err(StoreError::NotTmpfs {
                tier: "/var/lib/hostobs".to_string(),
                fstype: "ext4".to_string()
            })
        );
        assert_eq!(
            on_disk.check_tmpfs(Path::new("/var/lib/hostobs"), false),
            Ok(false)
        );
    }

    #[test]
    fn the_last_mount_at_a_point_is_the_one_the_kernel_exposes() {
        // Two mounts at the same point are a real state — `mount` over an existing mount — and
        // the kernel exposes the **last** one. Reporting the first would pass a `require_tmpfs`
        // check on a tmpfs that a disk filesystem shadows, so the later line must win
        // (review, M4).
        let table = super::MountTable::from_mounts(vec![
            super::Mount {
                point: "/run".to_string(),
                fstype: TMPFS.to_string(),
                options: "rw".to_string(),
            },
            super::Mount {
                point: "/run".to_string(),
                fstype: "ext4".to_string(),
                options: "rw".to_string(),
            },
        ]);
        assert_eq!(
            table.fstype_of(Path::new("/run/hostobs/tiers")),
            Ok("ext4".to_string()),
            "the overmount shadows the tmpfs"
        );
        assert_eq!(
            table.check_tmpfs(Path::new("/run/hostobs/tiers"), true),
            Err(StoreError::NotTmpfs {
                tier: "/run/hostobs/tiers".to_string(),
                fstype: "ext4".to_string()
            })
        );
        // A *longer* point still beats a shorter later one: only equal points are ordered by
        // line.
        let nested = super::MountTable::from_mounts(vec![
            super::Mount {
                point: "/run/hostobs/tiers".to_string(),
                fstype: TMPFS.to_string(),
                options: "rw".to_string(),
            },
            super::Mount {
                point: "/run".to_string(),
                fstype: "ext4".to_string(),
                options: "rw".to_string(),
            },
        ]);
        assert_eq!(
            nested.fstype_of(Path::new("/run/hostobs/tiers/hot")),
            Ok(TMPFS.to_string())
        );
    }

    #[test]
    fn a_path_that_is_relative_contains_a_parent_or_crosses_a_link_is_refused() {
        // A lexical comparison accepts `..` and an intermediate link, both of which the kernel
        // resolves elsewhere: the check would then be about a path that is not the one the
        // bytes land on (review, M4).
        let table = super::tmpfs_only();
        assert_eq!(
            table
                .check_tmpfs(Path::new("relative/tiers"), true)
                .map(|_| ()),
            Err(StoreError::Config {
                key: "tmpfs_dir",
                reason: "must be an absolute path"
            })
        );
        assert_eq!(
            table
                .check_tmpfs(Path::new("/run/../var/lib/hostobs"), true)
                .map(|_| ()),
            Err(StoreError::Config {
                key: "tmpfs_dir",
                reason: "must not contain a `..` component"
            })
        );
        // The intermediate link: `/run/x` is a link to a disk directory outside `/run`.
        let outside = TempDir::new("tmpfs-link-target");
        let base = TempDir::new("tmpfs-link");
        base.mkdir("run");
        base.symlink_to("run/x", &outside.path());
        let linked = base.path().join("run").join("x");
        assert_eq!(
            table.check_tmpfs(&linked, true).map(|_| ()),
            Err(StoreError::Symlink {
                name: linked.display().to_string()
            }),
            "an intermediate link is refused, never resolved into a pass"
        );
        // An ordinary absolute path is accepted, and `.` names nothing new.
        assert_eq!(
            table.check_tmpfs(Path::new("/run/./hostobs/tiers"), true),
            Ok(true)
        );
    }

    #[test]
    fn a_covering_mount_must_end_on_a_component_boundary() {
        assert!(covers("/", "/a"));
        assert!(covers("/run", "/run"));
        assert!(covers("/run/hostobs/", "/run/hostobs/x"));
        assert!(!covers("/run/host", "/run/hostobs"));
        assert!(!covers("/run", "/runner"));
        assert!(covers("/run", "/run/x/y"));
    }

    #[test]
    fn an_escape_is_decoded_and_a_malformed_one_is_skipped() {
        assert_eq!(unescape("/a\\040b").as_deref(), Some("/a b"));
        assert_eq!(unescape("/a\\134b").as_deref(), Some("/a\\b"));
        assert_eq!(unescape("/plain"), Some("/plain".to_string()));
        assert_eq!(unescape("/a\\04"), None);
        assert_eq!(unescape("/a\\0x9"), None);
        assert_eq!(unescape("/a\\999"), None);
        let text = "/dev/x /a\\040b ext4 rw 0 0\n/dev/y /c\\04d ext4 rw 0 0\n";
        let table = MountTable::parse(text).expect("parse");
        assert_eq!(table.len(), 1, "the undecodable line is skipped");
        assert_eq!(table.fstype_of(Path::new("/a b/x")), Ok("ext4".to_string()));
    }

    #[test]
    fn a_table_with_too_many_mounts_is_a_refusal() {
        let mut text = String::new();
        for index in 0..=MAX_MOUNTS {
            text.push_str(&format!("/dev/x /p{index} ext4 rw 0 0\n"));
        }
        assert!(matches!(
            MountTable::parse(&text),
            Err(StoreError::MountTableUnreadable { .. })
        ));
        let text = "/dev/x / ext4 rw 0 0\n";
        assert_eq!(
            MountTable::parse(text),
            Ok(MountTable::from_mounts(vec![Mount {
                point: "/".to_string(),
                fstype: "ext4".to_string(),
                options: "rw".to_string()
            }]))
        );
    }

    #[test]
    fn reading_a_missing_table_is_its_own_refusal() {
        assert_eq!(
            read_mounts(Path::new("/proc/does-not-exist/mounts")),
            Err(StoreError::MountTableUnreadable {
                reason: "/proc/does-not-exist/mounts: No such file or directory (os error 2)"
                    .to_string()
            })
        );
        let empty = MountTable::default();
        assert!(empty.is_empty());
        assert_eq!(
            empty.fstype_of(Path::new("/x")),
            Err(StoreError::MountNotFound {
                path: "/x".to_string()
            })
        );
        assert_eq!(MAX_TABLE_BYTES, 4 << 20);
    }

    #[test]
    fn a_keyed_lookup_uses_the_longest_covering_entry() {
        let table: BTreeMap<String, String> = [
            ("/".to_string(), "ext4".to_string()),
            ("/run".to_string(), TMPFS.to_string()),
        ]
        .into_iter()
        .collect();
        assert_eq!(lookup(&table, Path::new("/x")), Some("ext4"));
        assert_eq!(lookup(&table, Path::new("/run/x")), Some(TMPFS));
        assert_eq!(lookup(&BTreeMap::new(), Path::new("/x")), None);
    }
}
