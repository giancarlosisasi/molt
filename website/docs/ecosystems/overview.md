---
title: Ecosystems overview
---

# Ecosystems overview

Molt talks to your project through an **ecosystem backend** -- a seam that hides how a given tool spells "workspace," "version," and "internal dependency," so the engine can stay the same whether you use uv, Poetry, Hatch, PDM, or setuptools.

## Why molt needs this at all

JavaScript has essentially one workspace convention, so changesets can hard-wire it. Python does not. Every packaging tool expresses the same three things differently:

- **where the version lives** -- `[project].version`, `[tool.poetry].version`, or a dynamic source,
- **how workspace members are discovered** -- `[tool.uv.workspace]`, Poetry path dependencies, PDM's layout,
- **how an intra-repo dependency is pinned** -- a PEP 508 string plus `[tool.uv.sources]`, a Poetry `{ path = ... }` entry, and so on.

Hard-wiring any one of those would make molt "a uv tool" instead of "the Python release tool." So molt puts a backend protocol behind all of them. The engine that computes the [release plan](/concepts/release-plan) never reads `pyproject.toml` directly -- it asks the backend.

This is not an incidental design choice. Pluggable ecosystem support is the single most-requested changesets feature, **formally rejected upstream in 2020** ([#310](https://github.com/changesets/changesets/issues/310)) and re-requested for six years since, including a working version-provider pull request from OpenAI. Molt exists in large part to build the thing changesets decided not to. See [Why Molt?](/introduction/why-molt) for the full story.

## How molt detects the backend

Molt inspects the repository and picks the backend that matches:

- a `[tool.uv.workspace]` table (or a `uv.lock`) selects the **uv** backend,
- a `[tool.poetry]` table selects **Poetry**,
- `[tool.hatch]`, `[tool.pdm]`, or a plain setuptools layout select those respectively.

Detection is deterministic, and you can pin the backend explicitly in [configuration](/config/options) if a repository is ambiguous. The [uv backend](/ecosystems/uv) is the first-class, fully supported path today; the [others](/ecosystems/poetry-hatch-pdm-setuptools) exist behind the same protocol at varying maturity.

## What a backend provides

Every backend implements the same small contract. Given a repository, it can:

- **Discover packages** -- enumerate the workspace members, each with a name and directory. Names are matched using PEP 503 normalization, so `Foo_Bar` and `foo-bar` are the same package.
- **Read and write the version** -- read a package's current `[project].version` for the engine, and write the bumped value back. Writes go through a style-preserving TOML editor so your comments, spacing, and quoting survive.
- **Rewrite internal dependency pins** -- when `acme-core` bumps, edit the specifier in `acme-cli`'s dependency on it. Because Python dependencies are PEP 508 strings, molt splices the specifier substring in place rather than reformatting the whole array.
- **Update the lockfile** -- after mutating manifests, refresh the lockfile so the recorded versions are not stale. For uv this is one `uv lock` call. This is a real fix over changesets, which never touches lockfiles -- cosmetic in npm, but load-bearing in Python, where a `--frozen`/`--locked` install in CI fails against a stale lock.

The engine calls these; it never assumes which tool is underneath.

## Dynamic versions

Some packages do not carry a static version at all -- `[project]` declares `dynamic = ["version"]` and the real version comes from `[tool.hatch.version]`, `setuptools-scm`, or a git tag. There is no `[project].version` field for molt to write.

For v1, molt **detects a dynamic version and refuses to run**, with a clear message explaining that it cannot bump a package whose version is computed elsewhere and pointing you at a static `[project].version`. Silently doing nothing would be worse -- you would think you cut a release when you did not. Pluggable version *writers* (for `__version__` in a module, a `_version.py`, and similar) are a planned extension of the backend seam; see the [roadmap](/reference/roadmap).

## See also

- [uv](/ecosystems/uv) -- the first-class backend, in detail.
- [Poetry, Hatch, PDM & setuptools](/ecosystems/poetry-hatch-pdm-setuptools) -- the other backends and their status.
- [Options reference](/config/options) -- pinning or configuring the backend.
- [Dependency propagation](/guides/dependency-propagation) -- what the engine does with what the backend reports.
