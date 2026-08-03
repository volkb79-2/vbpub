// Package fsx holds the durability primitives srdm's on-disk formats rely
// on. They live in one place because getting them subtly wrong in one
// caller is how a store that "wrote COMPLETE" comes back from a power cut
// without it.
package fsx

import (
	"os"
	"path/filepath"
)

// SyncDir fsyncs a directory so entries created, renamed or removed inside
// it are durable.
//
// Syncing a file makes its *contents* durable; the directory entry that
// gives those contents a name is a separate write. A rename that is not
// followed by a directory fsync can vanish across a power cut even though
// the file it pointed at survived.
func SyncDir(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

// WriteFileSync writes data to path atomically: a temporary file in the
// same directory, fsync'd and chmod'd, then renamed into place, then the
// directory fsync'd.
//
// A reader therefore sees either the previous content or the new content,
// never a partial write — and after this returns, that remains true across
// a crash.
func WriteFileSync(path string, data []byte, mode os.FileMode) (err error) {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() {
		if err != nil {
			tmp.Close()
			os.Remove(tmpName)
		}
	}()

	if _, err = tmp.Write(data); err != nil {
		return err
	}
	if err = tmp.Chmod(mode); err != nil {
		return err
	}
	if err = tmp.Sync(); err != nil {
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	if err = os.Rename(tmpName, path); err != nil {
		return err
	}
	return SyncDir(dir)
}
