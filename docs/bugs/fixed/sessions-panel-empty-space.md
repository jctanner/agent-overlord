# Bug: Sessions Panel Contains a Large Empty Area

## Summary

The horizontal title container in the Agent Sessions panel used flexible height,
causing it to consume a large portion of the panel above the table.

## Expected

The title occupies one terminal row and the sessions table begins immediately
below it.

## Resolution

Constrained the sessions and wall panel header containers to one row and added a
Textual geometry regression test for the table's position.

## Status

Fixed

