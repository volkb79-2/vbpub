// Package config holds srdm's on-disk layout and daemon configuration.
//
// Naming (master plan decision 9): every identifier is the single token
// "srdm". systemd's "-" is the slice hierarchy separator, so a hyphenated
// root would nest under auto-created ancestors with MemoryMin=0 and every
// class floor beneath it would be arithmetically dead.
package config

import (
	"fmt"
	"io/fs"
	"path/filepath"
	"strings"
)

// Default roots. Both are deliberately single-token "srdm" paths.
const (
	DefaultStateDir = "/var/lib/srdm"
	DefaultRunDir   = "/run/srdm"
	DefaultSlice    = "srdm.slice"
)

// Ownership is the normalization applied to release content before a
// transaction may be promoted.
//
// The master plan fixes the store at srdm:srdm, 0755/0644 world-readable:
// managed content carries no secrets, and every consumer reads it.
type Ownership struct {
	UID      int
	GID      int
	DirMode  fs.FileMode
	FileMode fs.FileMode
}

// DefaultOwnership returns the mode policy with an unset uid/gid. Callers
// that leave UID/GID at -1 get mode normalization only; see store.Promote.
func DefaultOwnership() Ownership {
	return Ownership{UID: -1, GID: -1, DirMode: 0o755, FileMode: 0o644}
}

// Config is the resolved daemon configuration.
type Config struct {
	// StateDir is the persistent root: the release store and the journal.
	StateDir string
	// RunDir is the volatile root: operation tmpfs mounts and sockets.
	// P01 only creates it; publication topology arrives with P02.
	RunDir string
	// Owner is the ownership normalization applied at promotion.
	Owner Ownership
	// Slice is the admin-owned parent slice whose MemoryMin backs the class
	// floors. srdm verifies it rather than writing it (decision D-003).
	Slice string
}

// Default returns the shipped defaults.
func Default() Config {
	return Config{
		StateDir: DefaultStateDir,
		RunDir:   DefaultRunDir,
		Owner:    DefaultOwnership(),
		Slice:    DefaultSlice,
	}
}

// Validate reports whether the configuration is usable.
func (c Config) Validate() error {
	for _, f := range []struct {
		name, val string
	}{
		{"state_dir", c.StateDir},
		{"run_dir", c.RunDir},
	} {
		if f.val == "" {
			return fmt.Errorf("config: %s is empty", f.name)
		}
		if !filepath.IsAbs(f.val) {
			return fmt.Errorf("config: %s must be an absolute path, got %q", f.name, f.val)
		}
	}
	if c.Slice == "" {
		return fmt.Errorf("config: slice is empty")
	}
	if !strings.HasSuffix(c.Slice, ".slice") {
		return fmt.Errorf("config: slice %q must name a systemd slice unit", c.Slice)
	}
	// A hyphen before ".slice" makes systemd nest the unit under auto-created
	// ancestors that carry MemoryMin=0, which silently kills every class floor
	// below it. Refuse rather than ship a dead protection budget.
	if strings.Contains(strings.TrimSuffix(c.Slice, ".slice"), "-") {
		return fmt.Errorf("config: slice %q contains %q, which systemd reads as a hierarchy "+
			"separator; the auto-created ancestors carry MemoryMin=0 and would make every "+
			"class floor beneath it arithmetically dead", c.Slice, "-")
	}
	if c.Owner.DirMode&fs.ModeType != 0 || c.Owner.FileMode&fs.ModeType != 0 {
		return fmt.Errorf("config: ownership modes must be permission bits only")
	}
	return nil
}

// StoreDir is the release store root.
func (c Config) StoreDir() string { return filepath.Join(c.StateDir, "store") }

// ReleasesDir holds one directory per promoted, verified release.
func (c Config) ReleasesDir() string { return filepath.Join(c.StoreDir(), "releases") }

// ChannelsDir holds <profile>/<channel> symlinks into ReleasesDir.
func (c Config) ChannelsDir() string { return filepath.Join(c.StoreDir(), "channels") }

// TxDir holds in-flight transaction directories, one per operation.
func (c Config) TxDir() string { return filepath.Join(c.StoreDir(), "tx") }

// QuarantineDir holds transactions recovery could not adopt.
func (c Config) QuarantineDir() string { return filepath.Join(c.StoreDir(), "quarantine") }

// JournalDir holds durable per-operation records and the events stream.
func (c Config) JournalDir() string { return filepath.Join(c.StateDir, "journal") }

// ReleaseDir is the path of a promoted release.
func (c Config) ReleaseDir(releaseID string) string {
	return filepath.Join(c.ReleasesDir(), releaseID)
}

// ChannelLink is the path of a channel symlink.
func (c Config) ChannelLink(profileID, channel string) string {
	return filepath.Join(c.ChannelsDir(), profileID, channel)
}
