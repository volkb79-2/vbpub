package hold

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"runtime/debug"
	"sort"
	"syscall"

	"srdm/internal/store"
)

// WorkerArgs is what a hold unit's worker is told. It is deliberately a
// value type with an explicit Argv: the argv the daemon builds and the flags
// the worker parses are one contract, and splitting them across two files is
// how they drift.
type WorkerArgs struct {
	// ReleaseDir is the store directory of the release being published.
	ReleaseDir string
	// ReleaseID names the release, so the worker validates that the
	// directory it was pointed at is the release it was told to publish.
	ReleaseID string
	// Class is the publication class this unit holds.
	Class string
	// Target is the content root INSIDE the class's already-mounted op
	// tmpfs. The worker creates it; the daemon only mounts.
	Target string
}

// WorkerSubcommand is the argv[1] under which srdm runs as a hold worker.
const WorkerSubcommand = "hold-worker"

// Argv renders the worker invocation.
func (a WorkerArgs) Argv() []string {
	return []string{
		WorkerSubcommand,
		"--release-dir", a.ReleaseDir,
		"--release-id", a.ReleaseID,
		"--class", a.Class,
		"--target", a.Target,
	}
}

func (a WorkerArgs) validate() error {
	for name, val := range map[string]string{
		"--release-dir": a.ReleaseDir, "--release-id": a.ReleaseID,
		"--class": a.Class, "--target": a.Target,
	} {
		if val == "" {
			return fmt.Errorf("hold: worker argument %s is empty", name)
		}
	}
	for name, val := range map[string]string{"--release-dir": a.ReleaseDir, "--target": a.Target} {
		if !filepath.IsAbs(val) {
			return fmt.Errorf("hold: worker argument %s must be an absolute path, got %q", name, val)
		}
	}
	return nil
}

// RunWorker is the populate-and-hold worker: the process a hold unit runs,
// and the only thing in srdm that ever writes a class tree.
//
// It populates, verifies and seals, signals READY, and then parks. Every part
// of that order is load-bearing:
//
//   - it runs INSIDE the unit, so the pages it faults are charged to the
//     cgroup that carries the class's memory policy, and the charge and the
//     policy can never separate;
//   - it signals ready only when the tree is finished, so a caller that waits
//     for the unit and then acts on the content is not racing it (D-013);
//   - it parks rather than exits, because systemd reaps an active-but-empty
//     unit's cgroup and the charge would reparent to somewhere the class
//     floor does not apply (D-011).
//
// The return value is the process exit status, and it is the daemon's
// refusal channel: ExitNoSpace means the class cannot hold its content and
// the generation is quarantined rather than half-published (D-017).
func RunWorker(args []string, stderr io.Writer) int {
	if code := prepare(args, stderr); code != ExitOK {
		return code
	}

	// Everything the worker allocated to do the copy goes back to the kernel
	// before it parks. The parked process is charged to the class cgroup
	// alongside the content it is holding, so its own footprint comes
	// straight off the class floor (D-011).
	debug.FreeOSMemory()

	if err := notifyReady(); err != nil {
		fmt.Fprintf(stderr, "hold-worker: signalling readiness: %v\n", err)
		return ExitFault
	}

	park()
	return ExitOK
}

// prepare is everything the worker does BEFORE it becomes a hold: populate,
// verify, seal. Split out because the two steps that follow it are
// process-level and irreversible — a readiness signal to systemd and a park
// that never returns — and a unit test that triggered either would be
// testing the harness rather than the work.
func prepare(args []string, stderr io.Writer) int {
	fs := flag.NewFlagSet(WorkerSubcommand, flag.ContinueOnError)
	fs.SetOutput(stderr)
	var wa WorkerArgs
	fs.StringVar(&wa.ReleaseDir, "release-dir", "", "store directory of the release")
	fs.StringVar(&wa.ReleaseID, "release-id", "", "release id")
	fs.StringVar(&wa.Class, "class", "", "publication class to hold")
	fs.StringVar(&wa.Target, "target", "", "content root inside the class op tmpfs")
	if err := fs.Parse(args); err != nil {
		return ExitUsage
	}
	if err := wa.validate(); err != nil {
		fmt.Fprintf(stderr, "%v\n", err)
		return ExitUsage
	}

	rel, err := store.LoadReleaseDir(wa.ReleaseID, wa.ReleaseDir)
	if err != nil {
		fmt.Fprintf(stderr, "hold-worker: loading release %s from %s: %v\n",
			wa.ReleaseID, wa.ReleaseDir, err)
		return ExitFault
	}

	if err := Populate(rel.RootDir(), wa.Target, rel.Manifest, wa.Class); err != nil {
		if errors.Is(err, syscall.ENOSPC) {
			fmt.Fprintf(stderr, "hold-worker: class %q ran out of space in its tmpfs: %v\n",
				wa.Class, err)
			return ExitNoSpace
		}
		fmt.Fprintf(stderr, "hold-worker: populating class %q: %v\n", wa.Class, err)
		return ExitFault
	}

	// Verified BEFORE it is sealed and long before anything binds it. A class
	// that does not match its manifest never becomes an exposure.
	if err := rel.Manifest.VerifyClass(wa.Target, wa.Class); err != nil {
		fmt.Fprintf(stderr, "hold-worker: class %q does not match the manifest: %v\n", wa.Class, err)
		return ExitVerify
	}

	if err := Seal(wa.Target); err != nil {
		fmt.Fprintf(stderr, "hold-worker: sealing class %q: %v\n", wa.Class, err)
		return ExitFault
	}
	return ExitOK
}

// park blocks until systemd asks the unit to stop.
//
// Without this the worker would exit, systemd would reap its cgroup, and
// every page it faulted would reparent to the parent slice where the class
// floor does not apply — measured, D-011.
func park() {
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	<-sig
}

// notifyReady performs sd_notify(READY=1) with no library.
//
// The protocol is a single datagram to $NOTIFY_SOCKET, which is why srdm can
// speak it and stay dependency-free. A leading "@" means the abstract
// namespace; Go's syscall layer translates that to the leading NUL byte the
// kernel wants, so the value is usable verbatim.
func notifyReady() error {
	addr := os.Getenv("NOTIFY_SOCKET")
	if addr == "" {
		return errors.New("NOTIFY_SOCKET is unset: the unit is not Type=notify, so " +
			"\"active\" would mean \"exec'd\" rather than \"populated\" (D-013)")
	}
	conn, err := net.Dial("unixgram", addr)
	if err != nil {
		return fmt.Errorf("dialling %s: %w", addr, err)
	}
	defer conn.Close()
	if _, err := conn.Write([]byte("READY=1\n")); err != nil {
		return fmt.Errorf("writing to %s: %w", addr, err)
	}
	return nil
}

// Populate copies one class's entries out of the release into the class
// tree, in manifest order so parents exist before their children.
func Populate(releaseRoot, target string, m *store.Manifest, class string) error {
	entries := m.ClassEntries(class)
	if len(entries) == 0 {
		return fmt.Errorf("hold: manifest has no entries for class %q", class)
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Path < entries[j].Path })

	if err := os.MkdirAll(target, 0o755); err != nil {
		return err
	}
	for _, e := range entries {
		src := filepath.Join(releaseRoot, filepath.FromSlash(e.Path))
		dst := filepath.Join(target, filepath.FromSlash(e.Path))
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return err
		}
		switch e.Type {
		case store.EntryDir:
			if err := os.MkdirAll(dst, 0o755); err != nil {
				return err
			}
		case store.EntrySymlink:
			if err := os.Remove(dst); err != nil && !os.IsNotExist(err) {
				return err
			}
			if err := os.Symlink(e.Target, dst); err != nil {
				return err
			}
		case store.EntryFile:
			if err := copyRegular(src, dst); err != nil {
				return err
			}
		default:
			return fmt.Errorf("hold: manifest entry %q has unknown type %q", e.Path, e.Type)
		}
	}
	return nil
}

func copyRegular(src, dst string) (err error) {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	// 0644 now; Seal removes the write bits once the tree verifies.
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return err
	}
	defer func() {
		if cerr := out.Close(); err == nil {
			err = cerr
		}
	}()
	_, err = io.Copy(out, in)
	return err
}

// Seal removes every write bit, deepest entry first.
//
// Deepest-first because removing write from a directory is harmless to chmod
// but the ordering makes the walk independent of whether it is: no step
// depends on a permission an earlier step removed.
func Seal(root string) error {
	var paths []string
	err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Type()&fs.ModeSymlink != 0 {
			// A symlink has no permission bits of its own, and chmod would
			// follow it to the target.
			return nil
		}
		paths = append(paths, p)
		return nil
	})
	if err != nil {
		return err
	}
	sort.Sort(sort.Reverse(sort.StringSlice(paths)))
	for _, p := range paths {
		info, err := os.Lstat(p)
		if err != nil {
			return err
		}
		if err := os.Chmod(p, info.Mode().Perm()&^0o222); err != nil {
			return err
		}
	}
	return nil
}
