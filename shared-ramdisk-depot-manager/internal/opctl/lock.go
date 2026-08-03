package opctl

import (
	"fmt"
	"os"
	"syscall"

	"srdm/internal/config"
)

// Lock is mutual exclusion between operations that change state.
//
// Every operation here is a sequence of steps that are individually safe and
// jointly not: two activations interleaving would publish two generations,
// expose a server to one and tear down the other, and leave an assignment
// naming a release nothing has mounted. Each step would succeed.
//
// flock, and the choice is not incidental. A lock FILE containing a pid is a
// lock that survives the process that took it, so the first thing a crash
// leaves behind is a system that refuses to be repaired; every implementation
// then grows a staleness heuristic, and the heuristic is where the bugs are.
// flock is released by the kernel when the fd closes, which includes the
// process dying however it dies.
type Lock struct{ f *os.File }

// Acquire takes the operation lock, or refuses.
//
// Non-blocking, deliberately. Waiting would hide a stuck operation behind
// what looks like a slow one, and the operator holding the terminal is the
// one best placed to decide whether to wait — an operation that refuses says
// so immediately and can be retried, while one that blocks has to be
// interrupted before anybody learns anything.
func Acquire(cfg config.Config) (*Lock, error) {
	if err := os.MkdirAll(cfg.RunDir, 0o755); err != nil {
		return nil, fmt.Errorf("opctl: create %s: %w", cfg.RunDir, err)
	}
	f, err := os.OpenFile(cfg.LockPath(), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, fmt.Errorf("opctl: open %s: %w", cfg.LockPath(), err)
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = f.Close()
		if err == syscall.EWOULDBLOCK {
			return nil, &BusyError{Path: cfg.LockPath()}
		}
		return nil, fmt.Errorf("opctl: locking %s: %w", cfg.LockPath(), err)
	}
	return &Lock{f: f}, nil
}

// Release drops the lock. Closing the fd is what releases it, so this cannot
// leave the lock held even if the unlock itself fails.
func (l *Lock) Release() error {
	if l == nil || l.f == nil {
		return nil
	}
	err := syscall.Flock(int(l.f.Fd()), syscall.LOCK_UN)
	closeErr := l.f.Close()
	l.f = nil
	if err != nil {
		return err
	}
	return closeErr
}

// BusyError is another srdm operation already holding the lock.
type BusyError struct{ Path string }

func (e *BusyError) Error() string {
	return fmt.Sprintf("opctl: another srdm operation is in progress (%s is locked); "+
		"operations change mounts, units and durable records together, and two running "+
		"at once would each succeed at steps that contradict the other's", e.Path)
}
