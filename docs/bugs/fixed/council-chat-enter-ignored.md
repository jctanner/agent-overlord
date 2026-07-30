# Bug: Council Chat Enter Appears Ignored

## Summary

The council composer submitted correctly only when its `Input` widget already had
focus. Clicking elsewhere in the Control Council panel did not reliably move focus
to the composer, so Enter appeared to do nothing.

## Reproduction

1. Start the Textual application.
2. Click the council panel or chat history rather than directly inside the input.
3. Attempt to submit a council message with Enter.

## Expected

Clicking within the council panel focuses the composer, and Enter submits the
message. A visible Send control provides the same operation.

## Actual

Focus could remain on the session table, leaving Enter bound to the wrong widget.

## Resolution

- Bound `Input.Submitted` directly to `#chat-input`.
- Made clicks within the council panel focus the composer.
- Added a visible Send button that uses the same submission path.
- Restored composer focus after every response.
- Rendered processing errors in chat and on the wall instead of failing silently.
- Added regression coverage for typing and pressing Enter, clicking Send, and
  clicking chat history to focus the composer.

## Status

Fixed

