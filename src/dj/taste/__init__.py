"""The taste layer — the personal signal that makes this *my* DJ (ADR 0003).

CLAP says what a track sounds like (general); this package captures what *I*
think of it (mine). Free-text notes embed into a taste vector (`embed`), sparse
labels spread across the library via CLAP neighbors (`propagate`), and ranking
blends the two signals (`score`). `tag` is the CLI that collects the labels.

Kept import-light: submodules lazy-import their heavy deps, so importing the
package (and the fast tests) needs nothing but numpy. See docs/taste.md.
"""
