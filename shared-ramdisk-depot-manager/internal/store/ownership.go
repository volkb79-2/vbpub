package store

import (
	"io/fs"
	"os"
	"path/filepath"
	"syscall"

	"srdm/internal/config"
)

// ownerOf returns the uid and gid recorded for an entry, and whether the
// platform reported them at all.
func ownerOf(info fs.FileInfo) (uid, gid int, ok bool) {
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, 0, false
	}
	return int(st.Uid), int(st.Gid), true
}

// needsChown reports whether an entry must be chowned to reach (uid, gid).
//
// A root chown with *identical* uid and gid is not a no-op: it strips
// setuid, setgid and file capabilities from non-directories. Measured, not
// assumed:
//
//	chmod 4755 f ; chown 1000:1000 f   (already 1000:1000)  ->  4755 becomes 755
//	setcap cap_net_raw+ep c ; chown 1000:1000 c             ->  capability gone
//	chmod 2755 d ; chown 1000:1000 d   (a directory)        ->  2755 unchanged
//
// So an unconditional walk incidentally strips those bits from every
// non-directory it touches, and a naive "skip when uid and gid already
// match" removes that hardening silently. The predicate is therefore
// narrower — it skips only when the ids already match AND the entry is a
// directory or carries neither bit. This is the same predicate the F1 Wings
// patch uses, for the same reason.
//
// Known limit: file capabilities live in an xattr that FileInfo does not
// expose, so an already-correctly-owned file carrying only fscaps (and
// neither setuid nor setgid) is skipped and keeps them. Zero fscaps were
// measured across the case-study node's shared tmpfs and every server
// volume, and the store's job here is to normalize ownership rather than to
// strip capabilities.
func needsChown(info fs.FileInfo, wantUID, wantGID int) bool {
	uid, gid, ok := ownerOf(info)
	if !ok {
		return true
	}
	if uid != wantUID || gid != wantGID {
		return true
	}
	if info.IsDir() {
		return false
	}
	return info.Mode()&(fs.ModeSetuid|fs.ModeSetgid) != 0
}

// chownFunc is the syscall the walk uses. Lchown, not Chown: a symlink must
// have its own ownership set rather than its target's.
type chownFunc func(name string, uid, gid int) error

// normalizeOwnership walks root and brings every entry to the configured
// owner and mode.
//
// Nothing happens when the owner is unset (uid or gid below zero), which is
// how an unprivileged run — a test, a CLI inspecting a store it does not
// own — stays honest instead of silently claiming it normalized anything.
func normalizeOwnership(root string, own config.Ownership, chown chownFunc) (changed int, err error) {
	setOwner := own.UID >= 0 && own.GID >= 0

	err = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := d.Info()
		if err != nil {
			return err
		}

		if setOwner && needsChown(info, own.UID, own.GID) {
			if err := chown(path, own.UID, own.GID); err != nil {
				return err
			}
			changed++
		}

		// A symlink has no meaningful permission bits of its own, and
		// chmod would follow it to the target.
		if info.Mode()&fs.ModeSymlink != 0 {
			return nil
		}
		want := own.FileMode
		if d.IsDir() {
			want = own.DirMode
		}
		if want == 0 {
			return nil
		}
		// Preserve setuid, setgid and sticky: the mode policy governs the
		// permission bits, and silently dropping the others here would undo
		// the care needsChown takes above.
		want |= info.Mode() & (fs.ModeSetuid | fs.ModeSetgid | fs.ModeSticky)
		if info.Mode().Perm() != want.Perm() {
			if err := os.Chmod(path, want); err != nil {
				return err
			}
			changed++
		}
		return nil
	})
	return changed, err
}
