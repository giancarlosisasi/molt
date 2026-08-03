---
title: uv
---

# uv

The uv backend discovers uv workspace members, reads and writes their versions, rewrites intra-workspace dependency pins, and refreshes `uv.lock` on every `molt version`.

If your monorepo is a uv workspace, molt needs no ecosystem configuration. It detects uv from `[tool.uv.workspace]`, or from the presence of `uv.lock`.

## uv workspaces

A uv workspace is declared in the root `pyproject.toml`:

```toml
[tool.uv.workspace]
members = ["packages/*"]
```

Molt reads that glob to enumerate the workspace members. Each member is a package with a name from its own `[project].name` and a version from its own `[project].version`:

```toml
# packages/acme-core/pyproject.toml
[project]
name = "acme-core"
version = "1.4.0"
```

These are the packages [`molt add`](/cli/add) offers you, that [`molt version`](/cli/version) bumps, and that [`molt publish`](/cli/publish) uploads.

A member does not have to write its version in `[project].version`. One that declares
`dynamic = ["version"]` and keeps the value in a file -- hatch's `__about__.py` idiom and its
setuptools and pdm equivalents -- is discovered, planned, released and tagged exactly like the
member above; molt reads the version from that file and writes the new one back into it. See
[Dynamic versions](/ecosystems/dynamic-versions) for what molt detects, how to tell it directly, and
why a version derived from a git tag is not releasable yet.

## Intra-repo dependencies via `[tool.uv.sources]`

Within a workspace, one package depends on another the uv way: a normal dependency requirement in `[project].dependencies`, plus a `[tool.uv.sources]` entry marking it as a workspace source.

```toml
# packages/acme-cli/pyproject.toml
[project]
name = "acme-cli"
version = "2.1.0"
dependencies = ["acme-core>=1.4.0"]

[tool.uv.sources]
acme-core = { workspace = true }
```

The `{ workspace = true }` source tells uv to resolve `acme-core` from inside the workspace during development. The actual **version constraint still lives in the PEP 508 string** (`acme-core>=1.4.0`) -- that is the part molt reasons about and rewrites, not the source table.

This split matters for how bumps propagate. When `acme-core` moves to `1.5.0`, molt updates the specifier in `acme-cli`'s dependency string in place, preserving the surrounding formatting. A workspace source with **no** version constraint (just `"acme-core"` in `dependencies`) is the uv analogue of an "always current" pin -- there is nothing to rewrite, so molt leaves it alone.

## Lockfile updates

After `molt version` rewrites the manifests, it runs the uv lock update so `uv.lock` reflects the new versions, and includes the lockfile in the files it stages for commit.

This is what keeps the release commit installable. Anyone who runs `uv sync --locked`, or any other frozen-lockfile install, against a `uv.lock` that still records the old versions gets a failure. Refreshing the lock in the same commit means the manifests and the lock agree at every point in history.

## How pins drive dependent churn

The version constraint style you use decides how far a bump ripples. An **exact pin** maximizes churn: if `acme-cli` requires `acme-core==1.4.0` and `acme-core` bumps, the old constraint no longer admits the new version, so `acme-cli` must be re-released with an updated pin. A **range** like `>=1.4.0,<2` absorbs a compatible bump without forcing a release, because the new version still satisfies it.

This is exactly the reasoning in [Dependency propagation](/guide/dependency-propagation) -- the uv backend supplies the version and the constraints, and the [release plan](/concepts/release-plan) engine decides who needs to move. The uv source table (`{ workspace = true }`) tells uv where to resolve the package from; the specifier in `[project].dependencies` tells molt whether a dependent has fallen out of range.

## Single-package uv projects

You do not need a workspace to use the uv backend. A plain single-package project with one `pyproject.toml` runs the whole loop: `molt init`, `molt add`, `molt version`, and `molt publish` work end to end with no workspace table at all. Most Python projects ship one package, so this is molt's default path.

## See also

- [Ecosystems overview](/ecosystems/overview) -- the backend protocol uv implements.
- [Poetry, Hatch, PDM & setuptools](/ecosystems/poetry-hatch-pdm-setuptools) -- where those tools keep the same information.
- [Dependency propagation](/guide/dependency-propagation) -- how constraints decide who gets re-released.
- [Options reference](/config/options) -- configuring the backend and the lock command.
