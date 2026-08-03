// Package wings reads the shape of the Pterodactyl node srdm shares a host
// with.
//
// srdm never configures Wings and never patches it. It reads two facts and
// refuses when they are not what the host-bind driver needs:
//
//  1. how the Wings container propagates the volume tree, because under
//     Docker's default rprivate every mount srdm makes is invisible to Wings
//     and every unmount leaves a ghost — the 2026-07-31 outage;
//  2. whether Wings will run its pre-boot chown walk, because that walk
//     calls chown on every entry unconditionally and a chown on a read-only
//     mount returns EROFS even when the owner already matches, which makes
//     the server unstartable.
//
// Both are read rather than assumed, and both refuse rather than warn. An
// operator who meets either as a start failure at 3am has been failed by
// this package.
package wings

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"strings"

	"srdm/internal/config"
	"srdm/internal/mountinfo"
)

// ChownWalk is what Wings will do to a server's files before starting it.
type ChownWalk struct {
	// Enabled is the value of system.check_permissions_on_boot.
	Enabled bool
	// Known is false when srdm could not determine the value. It is then
	// treated as enabled — the unsafe reading — so ambiguity refuses rather
	// than proceeds.
	Known bool
	// Source says where the answer came from, for the refusal message.
	Source string
}

// ReadChownWalk reads system.check_permissions_on_boot out of Wings' config.
//
// Wings' config is YAML and srdm has no YAML parser — a dependency D-005
// already declined once, for the same reason it declines here: srdm's gate
// is hermetic because its dependency set is empty. What is needed is one
// boolean at a known place in a document srdm does not otherwise care about,
// so this scans for it rather than parsing the file.
//
// A scanner can be defeated by YAML srdm does not understand: flow mappings,
// anchors, a second `system:` block, an unexpected indentation style. So it
// FAILS CLOSED. Anything it cannot read as an unambiguous `false` is
// reported as not Known, the caller treats that as the walk being enabled,
// and `access: ro` is refused unless the node asserts F1 instead. The
// failure mode of guessing the other way is a server that will not start.
func ReadChownWalk(path string) (ChownWalk, error) {
	f, err := os.Open(path)
	if err != nil {
		return ChownWalk{Enabled: true, Source: path}, fmt.Errorf(
			"wings: reading %s: %w; srdm cannot tell whether Wings will run its pre-boot "+
				"chown walk, so it assumes it will", path, err)
	}
	defer f.Close()

	const key = "check_permissions_on_boot:"
	var (
		found int
		value string
	)
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if i := strings.Index(line, "#"); i >= 0 {
			line = line[:i]
		}
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(trimmed, key) {
			continue
		}
		found++
		value = strings.TrimSpace(strings.TrimPrefix(trimmed, key))
	}
	if err := sc.Err(); err != nil {
		return ChownWalk{Enabled: true, Source: path}, fmt.Errorf("wings: reading %s: %w", path, err)
	}

	switch {
	case found == 0:
		// Wings' own default is true, so an absent key means the walk runs.
		return ChownWalk{Enabled: true, Known: true,
			Source: path + " (key absent; Wings defaults it to true)"}, nil
	case found > 1:
		// Two answers is no answer. Refusing beats picking one.
		return ChownWalk{Enabled: true, Source: path}, fmt.Errorf(
			"wings: %s sets check_permissions_on_boot %d times; srdm will not guess "+
				"which one Wings uses", path, found)
	}

	switch strings.ToLower(strings.Trim(value, `"'`)) {
	case "false", "no", "off":
		return ChownWalk{Enabled: false, Known: true, Source: path}, nil
	case "true", "yes", "on":
		return ChownWalk{Enabled: true, Known: true, Source: path}, nil
	default:
		return ChownWalk{Enabled: true, Source: path}, fmt.Errorf(
			"wings: %s sets check_permissions_on_boot to %q, which srdm does not "+
				"recognise as a boolean; it assumes the walk runs", path, value)
	}
}

// Propagation is how the volume tree travels between the host and the Wings
// container.
type Propagation struct {
	// HostPeerGroup is the propagation of the host-side mount that carries
	// BindRoot. It must be shared, or there is nothing for the container's
	// rslave to be a slave OF.
	HostPeerGroup string
	// HostMountPoint is the mount the above was read from — usually an
	// ancestor of BindRoot rather than BindRoot itself.
	HostMountPoint string
	// Container is what the Wings container declares for its bind of
	// BindRoot, as Docker reports it: rslave, rprivate, and so on. Empty
	// when srdm could not ask.
	Container string
	// ContainerName names the container the above came from.
	ContainerName string
	// Degraded explains why the container half is missing, when it is.
	Degraded string
}

// HostShared reports whether the host side can propagate at all.
func (p Propagation) HostShared() bool { return p.HostPeerGroup == mountinfo.PropagationShared }

// ContainerReceives reports whether the container's declared propagation
// carries host-side events into it.
//
// `rslave` and `slave` both do; `rshared`/`shared` do too, and more besides.
// Docker's default `rprivate` does not, and neither does `private`.
func (p Propagation) ContainerReceives() bool {
	switch p.Container {
	case "rslave", "slave", "rshared", "shared":
		return true
	default:
		return false
	}
}

// ContainerInspector reports how a container binds a host path. It is
// injected because the gate has no Docker daemon, and because the real
// implementation is one HTTP GET.
type ContainerInspector interface {
	// BindPropagation returns the propagation the container declares for its
	// bind of hostPath, and the container's name.
	BindPropagation(ctx context.Context, hostPath string) (propagation, container string, err error)
}

// ReadPropagation reads both halves.
//
// The host half is read from srdm's own mount table and is always available.
// The container half needs Docker; when it is missing the result is marked
// Degraded rather than assumed either way, because "rprivate" and "I could
// not ask" call for different things from an operator.
func ReadPropagation(ctx context.Context, cfg config.Wings, mountInfoPath string,
	inspector ContainerInspector) (Propagation, error) {

	entries, err := mountinfo.Read(mountInfoPath)
	if err != nil {
		return Propagation{}, fmt.Errorf("wings: reading the mount table: %w", err)
	}
	e, ok := enclosingMount(entries, cfg.BindRoot)
	if !ok {
		return Propagation{}, fmt.Errorf(
			"wings: no mount carries %s, so srdm cannot tell how it propagates", cfg.BindRoot)
	}
	p := Propagation{
		HostPeerGroup:  e.Propagation(),
		HostMountPoint: e.MountPoint,
	}
	if inspector == nil {
		p.Degraded = "no container inspector configured"
		return p, nil
	}
	prop, name, err := inspector.BindPropagation(ctx, cfg.BindRoot)
	if err != nil {
		p.Degraded = err.Error()
		return p, nil
	}
	p.Container, p.ContainerName = prop, name
	return p, nil
}

// enclosingMount returns the mount that actually carries path — the deepest
// mount point at or above it.
//
// A directory is almost never a mount point of its own: /var/lib/pterodactyl
// on a stock node is part of the root filesystem, and its propagation is the
// root's. Looking only for an exact match would report "no mount" on every
// normal host.
func enclosingMount(entries []mountinfo.Entry, path string) (mountinfo.Entry, bool) {
	var best mountinfo.Entry
	var found bool
	for _, e := range entries {
		if !withinPath(path, e.MountPoint) {
			continue
		}
		if !found || len(e.MountPoint) > len(best.MountPoint) ||
			(len(e.MountPoint) == len(best.MountPoint) && e.ID > best.ID) {
			best, found = e, true
		}
	}
	return best, found
}

func withinPath(path, root string) bool {
	path = strings.TrimSuffix(path, "/")
	root = strings.TrimSuffix(root, "/")
	return path == root || root == "/" || strings.HasPrefix(path, root+"/")
}
