# Codex home for image output

## Intended behavior

The Codex image tool saves its original under the user's Codex home. The Bridge
app-server and any shell tool launched by that Codex turn must resolve the same
home so the turn can finish and the existing event-bound output can be
collected into the active Bridge attempt.

## Observed mismatch

In the VPS acceptance run, `image_gen.generation` completed. The following
Codex shell step tried to locate `generated_images` through `$env:CODEX_HOME`,
but that variable was empty. The shell step failed, the Codex turn never
completed, and Bridge eventually reported its existing 600-second timeout.

## Owning layer and correction

The Bridge Codex executor owns the child process environment and output-path
resolution. When `CODEX_HOME` is unset, both now use `Path.home() / '.codex'`;
when it is configured, both use that configured path, resolving relative
values in the Bridge process before passing them to the app-server child. This
keeps both processes pointed at the same image output location used by Bridge's
existing collection path.

## Acceptance

Deterministic lifecycle tests must prove that unset and configured
`CODEX_HOME` values reach the app-server child consistently, relative values
resolve to one absolute path, and the normal image event lifecycle returns one
artifact. Real provider generation is reserved for final acceptance after the
focused regression suite passes.
