---
title: "Poetry, Hatch, PDM & setuptools"
description: Where each packaging tool keeps its version, its workspace members, and its internal dependency pins.
---

# Poetry, Hatch, PDM & setuptools

Molt reads a project through an [ecosystem backend](/ecosystems/overview). A backend answers four
questions: which packages does this repository hold, where is each one's version, how is an
intra-repo dependency pinned, and how is the lockfile refreshed. What differs between packaging
tools is only *where* each of those things lives.

The backend molt ships is [uv](/ecosystems/uv). Setting `ecosystem` to any of the tools below is an
error that names the tool, rather than a silent fallback that would discover the wrong set of
packages. This page is the map of where each tool keeps what a backend needs.

## Poetry

- **Version.** Poetry 2 projects use the standard `[project].version`; older Poetry projects keep it
  in `[tool.poetry].version`. A backend reads whichever the project uses and writes back to the same
  place.
- **Workspace members.** Poetry has no monorepo primitive. Multi-package Poetry repositories wire
  members together with path dependencies.
- **Internal dependencies.** Expressed as path dependencies with the published constraint alongside.
  The constraint is the part a release rewrites; the path wiring stays as it is.

```toml
[tool.poetry.dependencies]
acme-core = { path = "../acme-core", develop = true }
```

## Hatch

- **Version.** Hatch commonly declares `dynamic = ["version"]` in `[project]` and points
  `[tool.hatch.version]` at the real source, often `path = "src/acme_core/__init__.py"`.
- **Where molt finds it.** A `__version__` assignment in a Python file is a
  [`file` version source](/ecosystems/dynamic-versions), which molt reads and writes. A package with
  a static `[project].version` under Hatch's build backend versions normally either way.

## PDM

- **Version.** Static `[project].version`, read and written directly.
- **Workspace members and internal dependencies.** PDM expresses local packages through its own
  path-dependency and layout conventions, which map onto the same discover, read-version, and
  rewrite-pin contract.

## setuptools

- **Version.** A static `[project].version` versions normally.
- **`setuptools-scm`.** When the version comes from a git tag, there is nothing in a manifest to
  write. Molt detects this and names the package rather than pretending to bump a computed version.
  See [Dynamic versions](/ecosystems/dynamic-versions).

## One protocol, many tools

The [release plan](/concepts/release-plan) that computes bumps and propagation asks the backend for
facts and never reads `pyproject.toml` itself. Adopting a new packaging tool is therefore writing a
backend against a protocol that already has an implementation, not changing the engine.

## See also

- [Ecosystems overview](/ecosystems/overview) -- the backend contract every tool here implements.
- [uv](/ecosystems/uv) -- the backend molt ships.
- [Dynamic versions](/ecosystems/dynamic-versions) -- version sources outside `[project].version`.
- [Options reference](/config/options) -- selecting a backend explicitly.
