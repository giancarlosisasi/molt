---
title: uv
---

# uv

The uv backend is molt's first-class, fully supported ecosystem: it discovers uv workspace members, reads and writes their versions, rewrites intra-workspace dependency pins, and refreshes `uv.lock` on every `molt version`.

If your monorepo is a uv workspace, molt needs no ecosystem configuration -- it detects uv from `[tool.uv.workspace]` (or the presence of `uv.lock`) and just works.

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

## Lockfile updates -- a real differentiator

After `molt version` rewrites the manifests, it runs the uv lock update so `uv.lock` reflects the new versions, and includes the lockfile in the files it stages for commit.

Changesets does not do this -- it never touches lockfiles at all. In npm that is cosmetic. In Python it is load-bearing: a consumer of your release PR that runs `uv sync --locked` (or any frozen-lockfile install) against a stale `uv.lock` **fails**. Molt keeping the lock in step means the release commit is internally consistent and installs cleanly in CI. See [Why Molt?](/introduction/why-molt) for where this sits among molt's additions.

## How pins drive dependent churn

The version constraint style you use decides how far a bump ripples. An **exact pin** maximizes churn: if `acme-cli` requires `acme-core==1.4.0` and `acme-core` bumps, the old constraint no longer admits the new version, so `acme-cli` must be re-released with an updated pin. A **range** like `>=1.4.0,<2` absorbs a compatible bump without forcing a release, because the new version still satisfies it.

This is exactly the reasoning in [Dependency propagation](/guides/dependency-propagation) -- the uv backend supplies the version and the constraints, and the [release plan](/concepts/release-plan) engine decides who needs to move. The uv source table (`{ workspace = true }`) tells uv where to resolve the package from; the specifier in `[project].dependencies` tells molt whether a dependent has fallen out of range.

## Single-package uv projects

You do not need a workspace to use the uv backend. A plain single-package project with one `pyproject.toml` is fully first-class -- `molt init`, `molt add`, `molt version`, and `molt publish` work end to end with no workspace table at all. Most Python projects are single-package, and molt treats that as the default path, not a degenerate case.

## See also

- [Ecosystems overview](/ecosystems/overview) -- the backend protocol uv implements.
- [Poetry, Hatch, PDM & setuptools](/ecosystems/poetry-hatch-pdm-setuptools) -- the other backends.
- [Dependency propagation](/guides/dependency-propagation) -- how constraints decide who gets re-released.
- [Options reference](/config/options) -- configuring the backend and the lock command.
