---
title: "Poetry, Hatch, PDM & setuptools"
---

# Poetry, Hatch, PDM & setuptools

The same ecosystem backend protocol that powers uv is what lets molt reach Poetry, Hatch, PDM, and setuptools projects -- so molt stays "the Python release tool," not "a uv tool."

Every backend implements the one contract from the [ecosystems overview](/ecosystems/overview): discover packages, read and write the version, rewrite internal dependency pins, and update the lockfile. What differs between tools is only *where* each of those things lives in the project's files. This page maps that out and is honest about what ships today versus what is planned.

## Support status

Molt ships **uv as the first-class backend** -- fully supported and the recommended path. The backends below exist behind the same seam and are maturing at different rates. Treat this page as the map of the seam, and the [roadmap](/reference/roadmap) as the source of truth for exactly what is production-ready in the version you are running.

## Poetry

- **Version.** Poetry 2 projects use the standard `[project].version`; older Poetry projects keep it in `[tool.poetry].version`. The backend reads whichever the project uses and writes back to the same place.
- **Workspace members.** Poetry has no first-class monorepo primitive; multi-package Poetry repos wire members together with path dependencies.
- **Internal dependencies.** Expressed as path dependencies, for example `acme-core = { path = "../acme-core", develop = true }`, with the published constraint alongside. Molt rewrites the version constraint while leaving the path wiring intact.

```toml
[tool.poetry.dependencies]
acme-core = { path = "../acme-core", develop = true }
```

## Hatch

- **Version.** Hatch commonly uses a **dynamic version**: `[project]` declares `dynamic = ["version"]` and `[tool.hatch.version]` points at the real source (often `path = "src/acme_core/__init__.py"`).
- **Dynamic-version policy.** Because there is no static `[project].version` to write, molt v1 **detects this and refuses** with guidance rather than silently doing nothing -- see [dynamic versions](/ecosystems/overview#dynamic-versions). A package with a static `[project].version` under Hatch's build backend versions normally. Pluggable version *writers* (for `__init__.py` / `_version.py` sources) are a planned extension.

## PDM

- **Version.** Static `[project].version`, read and written directly.
- **Workspace members and internal dependencies.** PDM expresses local packages through its own path-dependency and layout conventions; the backend maps those onto the same discover / read-version / rewrite-pin contract.

## setuptools

- **Version.** A static `[project].version` versions normally.
- **`setuptools-scm`.** When the version is derived from git tags (via `setuptools-scm` or `uv-dynamic-versioning`), **there is nothing in a manifest to write** -- the version comes from the tag. This is the same dynamic-version situation as Hatch: molt v1 detects it and refuses with guidance rather than pretend to bump a computed version.

## Why one protocol, many tools

Building this seam from day one -- instead of hard-wiring uv -- is what keeps molt honest to its own pitch. Python genuinely has no single workspace standard, and a release tool that only served one packaging tool would just be that tool's plugin. By putting version-source, workspace discovery, and dependency editing behind a backend protocol, molt can adopt a new ecosystem by adding a backend, not by rewriting the engine. The [release plan](/concepts/release-plan) that computes bumps and propagation does not change no matter which backend answers its questions.

## See also

- [Ecosystems overview](/ecosystems/overview) -- the backend contract every tool here implements.
- [uv](/ecosystems/uv) -- the first-class backend.
- [Roadmap](/reference/roadmap) -- the current, authoritative support status per backend.
- [Options reference](/config/options) -- selecting a backend explicitly.
