---
title: Ecosystems overview
---

# Ecosystems overview

Molt talks to your project through an **ecosystem backend** -- a seam that hides how a given tool spells "workspace," "version," and "internal dependency," so the engine can stay the same whether you use uv, Poetry, Hatch, PDM, or setuptools.

## The reason for the seam

Python has no single workspace convention. Every packaging tool expresses the same three things differently:

- **where the version lives** -- `[project].version`, `[tool.poetry].version`, or a dynamic source,
- **how workspace members are discovered** -- `[tool.uv.workspace]`, Poetry path dependencies, PDM's layout,
- **how an intra-repo dependency is pinned** -- a PEP 508 string plus `[tool.uv.sources]`, a Poetry `{ path = ... }` entry, and so on.

Hard-wiring any one of those would make molt a uv tool rather than a Python tool. So molt puts a backend protocol behind all of them. The engine that computes the [release plan](/concepts/release-plan) never reads `pyproject.toml` directly; it asks the backend.

## Backend detection

Molt inspects the repository and picks the backend that matches. A `[tool.uv.workspace]` table, or a `uv.lock`, selects the [uv backend](/ecosystems/uv), which is the backend molt ships. Detection is deterministic, and you can pin the backend explicitly in [configuration](/config/options) if a repository is ambiguous.

[Poetry, Hatch, PDM, and setuptools](/ecosystems/poetry-hatch-pdm-setuptools) each have a place in the protocol. Naming one in `ecosystem` is an error that says so, rather than a silent fallback that would discover the wrong set of packages.

## What a backend provides

Every backend implements the same small contract. Given a repository, it can:

- **Discover packages** -- enumerate the workspace members, each with a name and directory. Names are matched using PEP 503 normalization, so `Foo_Bar` and `foo-bar` are the same package.
- **Read and write the version** -- read a package's current `[project].version` for the engine, and write the bumped value back. Writes go through a style-preserving TOML editor so your comments, spacing, and quoting survive.
- **Rewrite internal dependency pins** -- when `acme-core` bumps, edit the specifier in `acme-cli`'s dependency on it. Because Python dependencies are PEP 508 strings, molt splices the specifier substring in place rather than reformatting the whole array.
- **Update the lockfile** -- after mutating manifests, refresh the lockfile so the recorded versions are not stale. For uv this is one `uv lock` call. A `--frozen` or `--locked` install in CI fails against a stale lock, so this step is what keeps the release commit installable.

The engine calls these; it never assumes which tool is underneath.

## Dynamic versions

Some packages do not carry a static version at all -- `[project]` declares `dynamic = ["version"]` and the real version comes from `[tool.hatch.version]`, `setuptools-scm`, or a git tag. There is no `[project].version` field for molt to write.

Molt resolves a version from the file a `file` version source names, so a `__about__.py` or `_version.py` holding `__version__` versions normally. A version derived from a **git tag** (`setuptools-scm`, `hatch-vcs`, `versioningit`, or pdm's `scm`) is detected and refused by name, because writing a version back into a tag-derived source would not change what the build backend computes. See [Dynamic versions](/ecosystems/dynamic-versions) for the full matrix and for how to register your own source.

## See also

- [uv](/ecosystems/uv) -- the backend molt ships, in detail.
- [Poetry, Hatch, PDM & setuptools](/ecosystems/poetry-hatch-pdm-setuptools) -- where each tool's version, members, and pins live.
- [Dynamic versions](/ecosystems/dynamic-versions) -- packages whose version is not in `[project]`.
- [Options reference](/config/options) -- pinning or configuring the backend.
- [Dependency propagation](/guide/dependency-propagation) -- what the engine does with what the backend reports.
