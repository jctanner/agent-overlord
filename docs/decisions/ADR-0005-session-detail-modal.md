# ADR-0005: Replace Session Detail Drawer with Modal Overlay

## Status

Accepted

## Context

Clicking a session row in the sessions table opens a `WorkerInspector` side panel
that slides in from the right edge of the viewport. The panel is position-fixed
and does not overlay the rest of the page, which makes it easy to miss on wide
displays and inconsistent with the modal pattern already used by the Memories and
Approval Center views.

## Decision

Replace the right-anchored drawer with a centered modal overlay that uses the
existing `.modal-backdrop` pattern. The modal will support backdrop click-to-close
and retain all current inspector content and inventory actions.

## Consequences

Positive:

- Consistent interaction model with Memories and Approval Center.
- Session detail is visually prominent and clearly modal.
- Backdrop click-to-close provides a familiar dismiss affordance.

Negative:

- The page beneath is no longer visible while inspecting a worker. Users who
  preferred side-by-side viewing lose that layout.
