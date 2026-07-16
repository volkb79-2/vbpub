// Package sysd is the real systemd implementation of manager.SystemdClient,
// using the D-Bus API (the daemon-reload-safe channel: properties set through
// systemd are re-applied by systemd, unlike raw cgroupfs writes).
package sysd

import (
	"context"
	"fmt"
	"log/slog"
	"strings"

	sdbus "github.com/coreos/go-systemd/v22/dbus"
	godbus "github.com/godbus/dbus/v5"

	"wings-slice-manager/internal/spec"
)

// Client talks to the systemd manager over D-Bus.
type Client struct {
	conn *sdbus.Conn
	log  *slog.Logger
}

// New connects to systemd: first via the system message bus, falling back to
// systemd's private socket (/run/systemd/private, root only) so the daemon
// also works on hosts without a D-Bus broker in the mount namespace.
func New(ctx context.Context, log *slog.Logger) (*Client, error) {
	conn, err := sdbus.NewWithContext(ctx)
	if err != nil {
		var errPriv error
		conn, errPriv = sdbus.NewSystemdConnectionContext(ctx)
		if errPriv != nil {
			return nil, fmt.Errorf("connecting to systemd (system bus: %v): %w", err, errPriv)
		}
		log.Debug("connected to systemd via private socket")
	}
	return &Client{conn: conn, log: log}, nil
}

// Close releases the D-Bus connection.
func (c *Client) Close() {
	c.conn.Close()
}

func propsToDBus(p spec.Props) []sdbus.Property {
	var out []sdbus.Property
	add := func(name string, v *uint64) {
		if v != nil {
			out = append(out, sdbus.Property{Name: name, Value: godbus.MakeVariant(*v)})
		}
	}
	add("MemoryMin", p.MemoryMin)
	add("MemoryLow", p.MemoryLow)
	add("MemoryHigh", p.MemoryHigh)
	add("MemoryMax", p.MemoryMax)
	add("CPUWeight", p.CPUWeight)
	add("IOWeight", p.IOWeight)
	return out
}

// EnsureSlice creates the transient slice with the given properties, or, if
// the unit is already loaded, updates its runtime properties in place.
// Transient-unit properties are systemd-owned: they survive daemon-reload and
// are re-applied by systemd, which is the entire point of this channel.
func (c *Client) EnsureSlice(ctx context.Context, name string, props spec.Props) error {
	dprops := propsToDBus(props)

	loaded := false
	if units, err := c.conn.ListUnitsByNamesContext(ctx, []string{name}); err == nil {
		for _, u := range units {
			if u.Name == name && u.LoadState == "loaded" {
				loaded = true
			}
		}
	}
	if loaded {
		if len(dprops) == 0 {
			return nil
		}
		return c.conn.SetUnitPropertiesContext(ctx, name, true, dprops...)
	}

	full := append([]sdbus.Property{
		{Name: "Description", Value: godbus.MakeVariant("wings-slice-manager managed slice")},
	}, dprops...)
	ch := make(chan string, 1)
	if _, err := c.conn.StartTransientUnitContext(ctx, name, "replace", full, ch); err != nil {
		// Lost a race with systemd creating the slice (e.g. Docker placing a
		// scope under it concurrently): fall back to property update.
		if strings.Contains(err.Error(), "already exists") {
			if len(dprops) == 0 {
				return nil
			}
			return c.conn.SetUnitPropertiesContext(ctx, name, true, dprops...)
		}
		return err
	}
	select {
	case result := <-ch:
		if result != "done" {
			return fmt.Errorf("starting transient slice %s: job result %q", name, result)
		}
	case <-ctx.Done():
		return ctx.Err()
	}
	return nil
}

// StopSlice stops (and thereby removes) a transient slice.
func (c *Client) StopSlice(ctx context.Context, name string) error {
	ch := make(chan string, 1)
	if _, err := c.conn.StopUnitContext(ctx, name, "replace", ch); err != nil {
		return err
	}
	select {
	case result := <-ch:
		if result != "done" {
			return fmt.Errorf("stopping slice %s: job result %q", name, result)
		}
	case <-ctx.Done():
		return ctx.Err()
	}
	return nil
}

// ListSlices returns the names of loaded slice units matching prefix*.slice.
func (c *Client) ListSlices(ctx context.Context, prefix string) ([]string, error) {
	units, err := c.conn.ListUnitsByPatternsContext(ctx, nil, []string{prefix + "*.slice"})
	if err != nil {
		return nil, err
	}
	var out []string
	for _, u := range units {
		if u.LoadState == "loaded" {
			out = append(out, u.Name)
		}
	}
	return out, nil
}

// GetMemoryMin reads the MemoryMin property of a slice unit.
func (c *Client) GetMemoryMin(ctx context.Context, unit string) (uint64, bool, error) {
	prop, err := c.conn.GetUnitTypePropertyContext(ctx, unit, "Slice", "MemoryMin")
	if err != nil {
		return 0, false, err
	}
	v, ok := prop.Value.Value().(uint64)
	if !ok {
		return 0, false, fmt.Errorf("unexpected MemoryMin type %T on %s", prop.Value.Value(), unit)
	}
	return v, true, nil
}
