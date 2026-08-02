---
title: Dependency propagation
---

# Dependency propagation

When you bump a package, molt works out which other packages in your workspace depend on it, decides whether each one needs a release too, and rewrites its dependency constraint to match.

A changeset on one package can ripple across the whole workspace, and the rules below are what decide how far. This guide is the practical version; [The release plan](/concepts/release-plan) is the conceptual one.

## The core rule

> A dependent is released when its dependency's **new** version falls **outside** the constraint the dependent declares on it. The dependent gets a **patch** bump, and its constraint is rewritten to admit the new version.

Everything below follows from that one sentence. Two consequences up front:

- **The propagated bump is always a patch.** It does not matter whether the dependency had a minor or a major change -- a dependent that goes out of range gets a patch. The dependent's own code did not change; it is being re-released only so its pin is valid.
- **A `none` change never propagates.** A changeset that bumps a package by `none` moves nothing downstream.

## What "outside the range" means

Whether a bump escapes a constraint depends entirely on the constraint. Three common shapes, mapped to PEP 440, with a dependency starting at `1.0.0`:

| Dependency bump | `>=1.0.0,<2.0.0` (caret-style) | `~=1.0.0` (compatible release) | `==1.0.0` (exact pin) |
|---|---|---|---|
| `none` | -- | -- | -- |
| `patch` -> `1.0.1` | in range | in range | **out -> patch** |
| `minor` -> `1.1.0` | in range | **out -> patch** | **out -> patch** |
| `major` -> `2.0.0` | **out -> patch** | **out -> patch** | **out -> patch** |

Read down a column: the tighter the constraint, the more often the dependent has to move.

- **`==1.0.0` (exact pin)** breaks on *any* real bump. Maximum churn, maximum safety -- the dependent is rebuilt on every release of the dependency.
- **`~=1.0.0` (compatible release)** allows patches (`>=1.0.0,<1.1.0`), so it breaks on minor and major.
- **`>=1.0.0,<2.0.0` (caret-style)** allows anything below the next major, so it breaks only on major.

This is pure range satisfaction -- the same PEP 440 math pip uses to decide whether a version is installable. molt is not inventing a policy here; it is asking "would this dependent's requirement still resolve?" and releasing it only when the answer is no.

## Exact pins and workspace sources

The exact-pin column is where workspaces spend most of their time. If you want two packages to move in near-lockstep -- a library and its companion CLI, say -- pin the dependency exactly, and every release of the library drags the CLI along.

In changesets this is spelled `workspace:*`, which resolves to the dependency's exact current version. In a uv workspace the equivalent is a member pinned to its exact version, pointed at the local package through `[tool.uv.sources]`:

```toml
[project]
name = "acme-cli"
version = "0.5.0"
dependencies = ["acme-core==1.2.0"]

[tool.uv.sources]
acme-core = { workspace = true }
```

molt reads the *effective constraint* on the internal dependency and applies the rules above. A bare, unconstrained dependency (the equivalent of `*`) is satisfied by every version and so never triggers a bump; an exact pin triggers on everything. How molt interprets `[tool.uv.sources]` and workspace members is covered in [uv workspaces](/ecosystems/uv).

## Worked examples

Take a workspace where `acme-cli` depends on `acme-core`.

### In range: no cascade

`acme-cli` requires `acme-core>=1.2.0,<2.0.0`. You ship a **minor** change to `acme-core` (`1.2.0 -> 1.3.0`). `1.3.0` still satisfies the constraint, so `acme-cli` is **not** released. Nothing about it needs to change.

### Out of range: cascade + rewrite

Same constraint, but now a **major** change to `acme-core` (`1.2.0 -> 2.0.0`). `2.0.0` falls outside `<2.0.0`, so `acme-cli` gets a **patch** (`0.5.0 -> 0.5.1`) and its pin is rewritten, preserving the caret style:

```diff
 [project]
 name = "acme-cli"
-version = "0.5.0"
-dependencies = ["acme-core>=1.2.0,<2.0.0"]
+version = "0.5.1"
+dependencies = ["acme-core>=2.0.0,<3.0.0"]
```

Its changelog records why it moved -- dependency bumps always land in the Patch section, last:

```md
## 0.5.1

### Patch Changes

- Updated dependencies:
  - acme-core@2.0.0
```

### Transitive: all the way down

Propagation is transitive. Suppose `acme-cli` depends on `acme-api`, which depends on `acme-core`, and each pins the one below it exactly. A patch to `acme-core` bumps `acme-api` (patch), which in turn bumps `acme-cli` (patch):

```text
acme-core   patch  1.0.0 -> 1.0.1   (you wrote this changeset)
acme-api    patch  2.3.0 -> 2.3.1   (dependency out of range)
acme-cli    patch  0.5.0 -> 0.5.1   (dependency out of range)
```

molt walks the dependency graph until no further bumps are needed, so a single changeset at the bottom of a chain releases the whole chain above it. None of the upper packages need a changeset of their own; in the [plan](/guides/dry-run-and-plans) they appear as releases with an empty `changesets` array.

### `none` stops the ripple

A changeset that bumps `acme-core` by `none` -- recorded, but not a release -- propagates nothing, even to exact-pinned dependents. Use this when a change is internal enough that consumers genuinely do not need to move.

## Forcing in-range dependents: `update_internal_dependents`

By default molt is conservative: it releases a dependent only when it has to (out of range). Sometimes you want the opposite -- every dependent re-released whenever a dependency moves, even if the pin would still resolve. Set the config option:

```toml
[tool.molt]
update_internal_dependents = "always"
```

With `"always"`, an in-range `minor` or `patch` to a dependency forces its dependents to a patch too. The default is `"out-of-range"`, which is the behavior described above. One thing does not change under `"always"`: a `none` bump still propagates nothing. See [Configuration options](/config/options).

## Where to go next

- [The release plan](/concepts/release-plan) -- the engine that computes propagation, including linked and fixed groups.
- [Linked vs fixed packages](/concepts/linked-vs-fixed) -- make packages move together by policy, not just by dependency.
- [Versioning and PEP 440](/concepts/versioning-pep440) -- the range math behind "in range" and "out of range".
- [Configuration options](/config/options) -- `update_internal_dependents` and related knobs.
