# TUI UX review

Pilot review of the real screens at 80×24, 100×30, 120×40, 160×50, 200×60, and 60×40, plus a resize while
Dashboard is open. The harness writes plain-text renders under pytest's temporary directory, outside the repository.
The top-level route order follows the current TrueNAS `NavigationService`: Dashboard, Storage, Datasets, Shares, Data
Protection, Credentials, Containers, Virtual Machines, Apps, Reporting, System. Network remains under System; tnt adds
Jobs/Alerts there and nests My API Keys under Credentials because it has no account menu. Containers, Virtual Machines,
and Apps are conditional in TrueNAS; tnt leaves the separate container/VM pages visible and labels the condition in text.
([upstream navigation source](https://raw.githubusercontent.com/truenas/webui/master/src/app/services/navigation/navigation.service.ts))

## Ranked findings

1. **High — no small-terminal recovery (40×10).** Resizing could leave the full tree and content competing for an
   unusable area. The app now shows a resize notice below 48 columns or 12 rows and restores the open page when space
   returns. Pilot covers 40×10 and 60×10.
2. **High — help and shortcuts were easy to miss (80×24 and larger).** `?` only made a transient notice and the
   command palette had no persistent on-screen cue. A compact key legend and `?` help dialog now expose Jump, menu,
   host, refresh, SSH, quit, table, mouse, and dialog controls.
3. **High — the current location was implicit (all sizes).** Opening a screen from the palette did not show a
   breadcrumb. A `TrueNAS > …` location line now follows the active screen and host picker.
4. **Medium — planned and conditional routes looked available (all sizes).** Tree entries now label planned,
   conditional, and tnt-only items in text, so state does not depend on color or opening each entry.
5. **Medium — host picker actions and keyboard focus were unclear (80×24, 60×40).** One long button row made later
   actions hard to reach, and the host table did not receive focus. Actions now occupy two compact rows, the table
   instructions explain column navigation, and long host names and the last-status column can be inspected by scrolling.
   The picker opens focused on its table (or Add when empty). Pilot checks each action label at 80×24.
6. **Medium — confirmation targets lived in input placeholders (80×24).** A long target could scroll out of view.
   Plan and host-removal dialogs now wrap the exact target above a neutral input prompt; the guarded typed comparison
   is unchanged.
7. **Medium — empty read views had no explicit empty message (80×24 through 200×60).** The status count was available
   but an empty table looked unfinished. Core read views now say when there are no rows or when a filter has no matches.
8. **Medium — Escape had no consistent return action (all sizes).** Escape now returns focus to the menu, closes the
   host picker to its previous page, or cancels a modal through its own cancel path.
9. **Medium — the fixed 30-column sidebar crowded narrow layouts (60×40 and 80×24).** Its width now scales with the
   terminal while keeping enough room for TrueNAS menu labels.
10. **Medium — the first-run host picker offered actions with no configured host (80×24, 160×50).** Host-only actions
    are now disabled until a row is selected, Add receives focus when the host list is empty, and the exit action is
    labeled Quit; Escape explains that a host is required.
11. **Remaining — external screen-reader semantics.** Visible text, named controls, keyboard focus, and a non-color
    status vocabulary help keyboard use, but the Textual interface does not expose a semantic screen-reader tree.

Busy and failure messages remain visible; empty Alerts, sanitized RPC failure, and a running job's percentage are
covered by the harness. Table state and job progress use words and numbers as well as color. The palette remains the
global screen/action search; per-view `/` filtering is limited to read-only table views.
