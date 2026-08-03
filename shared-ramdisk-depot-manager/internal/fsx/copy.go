package fsx

import (
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
)

// CopyTree copies src into dst, preserving directory structure, permission
// bits and symlinks.
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
		target := filepath.Join(dst, rel)
		info, err := d.Info()
		if err != nil {
			return err
		}

		switch {
		case info.Mode()&fs.ModeSymlink != 0:
			link, err := os.Readlink(path)
			if err != nil {
				return err
			}
			if err := os.Remove(target); err != nil && !os.IsNotExist(err) {
				return err
			}
			return os.Symlink(link, target)
		case d.IsDir():
			if err := os.MkdirAll(target, info.Mode().Perm()); err != nil {
				return err
			}
			// MkdirAll honours umask, so set the mode explicitly.
			return os.Chmod(target, info.Mode().Perm())
		case info.Mode().IsRegular():
			return copyFile(path, target, info.Mode().Perm())
		default:
			return fmt.Errorf("fsx: %q is neither a regular file, a directory nor a symlink", rel)
		}
	})
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
