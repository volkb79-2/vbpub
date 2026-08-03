package fsx

import "syscall"

// makeFifo creates a named pipe, for the "a release transaction may not
// contain one" case.
func makeFifo(path string) error { return syscall.Mkfifo(path, 0o644) }
