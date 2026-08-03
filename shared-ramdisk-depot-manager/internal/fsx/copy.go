package fsx

import (
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
)

// CopyTree copies src into dst, preserving directory structure, modes and
// symlinks — with one documented exception, in CopyEntry: a directory always
// comes out at least owner-writable, because a tree cannot be built inside
// its own read-only directories.
//
// Symlinks are recreated as links rather than followed: following them would
// silently duplicate content, and — worse — a link pointing outside the
// source would drag unrelated files into a release.
//
// Ownership is deliberately not copied. The store normalizes ownership as a
// named phase of promotion, and a copy that guessed at it would either need
// privilege it should not have or leave a half-normalized tree behind.
func CopyTree(src, dst string) error {
	srcInfo, err := os.Lstat(src)
	if err != nil {
		return err
	}
	if !srcInfo.IsDir() {
		return fmt.Errorf("fsx: %q is not a directory", src)
	}

	return filepath.WalkDir(src, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if err := CopyEntry(path, filepath.Join(dst, rel), info); err != nil {
			return fmt.Errorf("fsx: copying %q: %w", rel, err)
		}
		return nil
	})
}

// CopyEntry copies one path — a directory, a symlink or a regular file — and
// gives the copy the source's mode.
//
// Exported because a caller assembling a tree out of several sources needs
// the per-entry step rather than the whole walk: it has its own decisions to
// make about each path before the bytes move (harvest classifies every entry
// and refuses a collision between two class trees). Sharing the step keeps
// those trees mode-identical to a staged one, which is what makes a harvested
// release comparable with a from-scratch stage at all.
//
// info is passed in rather than re-stat'd so a caller that already walked the
// tree does not pay for a second lstat per entry, and so the entry that is
// copied is the one the caller decided about.
func CopyEntry(src, dst string, info fs.FileInfo) error {
	switch {
	case info.Mode()&fs.ModeSymlink != 0:
		link, err := os.Readlink(src)
		if err != nil {
			return err
		}
		if err := os.Remove(dst); err != nil && !os.IsNotExist(err) {
			return err
		}
		return os.Symlink(link, dst)
	case info.IsDir():
		// A directory gets the owner's write and search bits whatever the
		// source's are, because a tree cannot be built inside its own sealed
		// directories: publication leaves a class tree 0555, and copying that
		// mode across would make the very next entry fail with EACCES for
		// anyone but root. The permission bits of a release are set by the
		// store's ownership phase, as a named step of promotion, so this loses
		// nothing that survives — while setuid, setgid and sticky, which the
		// store DOES carry through, are preserved here exactly.
		if err := os.MkdirAll(dst, info.Mode().Perm()|0o700); err != nil {
			return err
		}
		// MkdirAll honours umask, and mkdir(2) does not always carry setgid
		// through, so the mode is set explicitly afterwards.
		return os.Chmod(dst, fullMode(info.Mode())|0o700)
	case info.Mode().IsRegular():
		return copyFile(src, dst, fullMode(info.Mode()))
	default:
		return fmt.Errorf("fsx: %q is neither a regular file, a directory nor a symlink", src)
	}
}

// fullMode is the permission bits plus setuid, setgid and sticky.
//
// Perm() alone is blind to exactly the three bits that matter most — the same
// blindness store.unixMode exists to avoid. A copy keyed on Perm() silently
// drops a setgid directory's bit, so a release staged through this function
// would differ from the tree it was staged from, in a way no test of the
// content would ever show.
func fullMode(m fs.FileMode) fs.FileMode {
	return m.Perm() | (m & (fs.ModeSetuid | fs.ModeSetgid | fs.ModeSticky))
}

func copyFile(src, dst string, mode fs.FileMode) (err error) {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
	if err != nil {
		return err
	}
	defer func() {
		if cerr := out.Close(); err == nil {
			err = cerr
		}
	}()

	if _, err = io.Copy(out, in); err != nil {
		return err
	}
	return out.Chmod(mode)
}
