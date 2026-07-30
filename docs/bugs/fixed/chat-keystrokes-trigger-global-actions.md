# Bug: Chat Keystrokes Trigger Global Actions

## Summary

Single-letter global application bindings intercepted ordinary prose typed into
the council composer. In particular, typing `r` launched a complete local and
remote inventory refresh, making keyboard input appear severely delayed.

## Reproduction

1. Focus the council composer.
2. Type a sentence containing `r`, `p`, `l`, `f`, or `q`.
3. Observe global refresh, wall, filter, or quit behavior instead of normal input.

## Expected

All printable characters are inserted immediately while the composer has focus.
Global actions require unambiguous control-key combinations.

## Resolution

- Replaced single-letter shortcuts with priority `Ctrl` bindings.
- Moved council focus from `/` to `Ctrl+G` so slash can be typed normally.
- Avoided rebuilding the sessions table when its displayed state is unchanged.
- Batched each inventory cycle's worker persistence into one SQLite transaction.
- Added regression coverage that types ordinary prose containing every formerly
  intercepted shortcut character.

## Status

Fixed

