---
"molt-release": patch
---

Move the composite GitHub Action to its own repository, `giancarlosisasi/molt-action`. Workflows
reference it as `uses: giancarlosisasi/molt-action@v1` instead of `uses: giancarlosisasi/molt@v1`;
the inputs, the outputs and their defaults are unchanged, so the only edit a workflow needs is that
line. The old `giancarlosisasi/molt@v1` reference no longer resolves.

An action is versioned by a moving major ref and a Python distribution by immutable PEP 440 tags.
Sharing one tag namespace meant molt's release workflow force-pushed `v1` into the same tag list
that carries `v0.1.2`, and a reader of that list could not tell a release from a pointer.

The action's conformance test moved with it and gained CI of its own: it resolves `action.yml`'s
inputs against an installed `molt-release` rather than a local source tree, and runs on every push,
weekly on a schedule, and again before `v1` moves.
