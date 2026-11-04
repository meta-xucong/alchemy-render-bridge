# Codex home for image output

## Intended behavior

The Codex image tool saves its original under the user's Codex home. The Bridge
executor resolves that same home for the app-server child and collects the
original from the image-generation item's `savedPath`. The native path is
authoritative; the executor does not require a shell step to copy the image.

## Observed mismatch

The original VPS run exposed a missing `CODEX_HOME` in the Codex child. After
the environment fix was merged, a later real run saved two images under the
native `CODEX_HOME/generated_images/<thread>` location, but the turn still
timed out in `turn_wait` without a captured `turn/completed` event. Therefore
the environment fix was necessary but not sufficient.

## Owning layer and correction

The Bridge Codex executor owns the child process environment and output-path
resolution. When `CODEX_HOME` is unset, it uses `Path.home() / '.codex'`; when
configured, it resolves that value before passing it to the app-server child.
The prompt contract now asks for one native image generation, treats the
completed item's `savedPath` as the original, and ends the turn without shell,
steps to locate, copy, move, or convert the image. Lifecycle diagnostics retain
only allowlisted item type and kind labels and counts, never event payloads or
prompt text. Those counters will help distinguish a missing terminal event
from an unexpected item shape.

## Acceptance

Deterministic lifecycle tests must prove that unset and configured
`CODEX_HOME` values reach the app-server child consistently, relative values
resolve to one absolute path, and both `imageGeneration` and
`Extension:image_gen.generation` events return the original artifact from
`savedPath`. Final real-provider acceptance follows the complete focused test
suite and independent code audit.
