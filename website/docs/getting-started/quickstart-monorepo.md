---
title: "Quickstart: monorepo"
---

# Quickstart: monorepo

Version a uv workspace where one package depends on another, and watch a single changeset cascade to its dependents through the release plan.

This is where molt earns its keep. When you change one package, molt works out which other packages depend on it, decides whether they need a release too, and rewrites their dependency constraints to match. That cross-package reasoning -- the [release plan](/concepts/release-plan) -- is the hard part of the problem, and the reason molt exists.

If you have not read the [single-package quickstart](/getting-started/quickstart-single-package), start there; this page assumes the `init` / `add` / `version` / `publish` loop.

## The workspace

A uv workspace with two members and one internal dependency: `acme-cli` depends on `acme-core`.

```toml
# ./pyproject.toml  (workspace root)
[tool.uv.workspace]
members = ["packages/*"]
```

```toml
# ./packages/acme-core/pyproject.toml
[project]
name = "acme-core"
version = "1.2.0"
```

```toml
# ./packages/acme-cli/pyproject.toml
[project]
name = "acme-cli"
version = "0.5.0"
dependencies = ["acme-core>=1.2.0,<2.0.0"]

[tool.uv.sources]
acme-core = { workspace = true }
```

Run `molt init` once at the workspace root. molt discovers both members automatically.

## 1. Add a changeset for the changed package

Suppose you make a **breaking** change to `acme-core` -- you rename `export()` and drop the old positional API. You only wrote code in `acme-core`, so you only describe `acme-core`:

```bash
molt add
```

```text
? Which packages were affected by the changes you made?
> [x] acme-core
  [ ] acme-cli
? Which packages should have a major bump?
> [x] acme-core
? Please enter a summary for this change (this will be in the changelogs).
> Rename export() to export_all() and drop the legacy positional API.
```

That writes one changeset -- note that it says nothing about `acme-cli`:

```md
# .changeset/proud-taxis-drum.md
---
"acme-core": major
---

Rename export() to export_all() and drop the legacy positional API.
```

You do not have to reason about the ripple effects yourself. That is the plan's job.

## 2. Preview the release plan

Before you version anything, see exactly what the release will contain:

```bash
molt status
```

```text
Packages to be bumped:
- major
  - acme-core   1.2.0 -> 2.0.0
- patch
  - acme-cli    0.5.0 -> 0.5.1   (dependency bump)
```

`acme-cli` has **no changeset of its own**, yet it appears in the plan. Here is the reasoning molt did:

- `acme-core` is going to `2.0.0` (a major bump).
- `acme-cli` requires `acme-core>=1.2.0,<2.0.0`. The new `2.0.0` falls **outside** that range -- the constraint would no longer resolve.
- So `acme-cli` needs a release. molt gives it the smallest bump that does the job, a **patch**, and will rewrite its constraint to admit the new version.

Had the change to `acme-core` been a *minor* bump (to `1.3.0`), `acme-cli` would **not** have been released -- `1.3.0` still satisfies `>=1.2.0,<2.0.0`, so nothing about `acme-cli` needs to change. Which bumps cascade and which do not is governed entirely by the constraints your packages declare. That is the whole topic of [Dependency propagation](/guides/dependency-propagation).

You can also print the plan as JSON (`molt status --output json`) or preview the file writes with `molt version --dry-run`. See [Checking status](/guides/status) and [Dry runs and plans](/guides/dry-run-and-plans).

## 3. Version the workspace

```bash
molt version
```

molt applies the whole plan at once. In `acme-core`:

```diff
 [project]
 name = "acme-core"
-version = "1.2.0"
+version = "2.0.0"
```

In `acme-cli`, **two** things change -- its own version *and* its constraint on `acme-core`, rewritten to admit `2.0.0` while preserving the range style:

```diff
 [project]
 name = "acme-cli"
-version = "0.5.0"
-dependencies = ["acme-core>=1.2.0,<2.0.0"]
+version = "0.5.1"
+dependencies = ["acme-core>=2.0.0,<3.0.0"]
```

Each package gets its changelog entry. `acme-core` records the breaking change you described; `acme-cli` records that it moved because a dependency did (dependency bumps always land in the Patch section):

```md
# packages/acme-cli/CHANGELOG.md

## 0.5.1

### Patch Changes

- Updated dependencies:
  - acme-core@2.0.0
```

The lockfile (`uv.lock`) is refreshed, and the consumed changeset is deleted. All of this is buffered and flushed together, so a mid-run failure never leaves you half-versioned.

## 4. Publish what changed

```bash
molt publish
```

molt publishes exactly the packages whose versions changed -- here, both -- in dependency order, and tags each one. In a workspace the tags are per package: `acme-core@2.0.0` and `acme-cli@0.5.1`. See [Publishing](/guides/publishing).

## Where to go next

- [Dependency propagation](/guides/dependency-propagation) -- the full rules for how bumps cascade, including exact pins, ranges, and transitive dependents. This is the moat.
- [The release plan](/concepts/release-plan) -- the engine that computes all of the above.
- [Linked vs fixed packages](/concepts/linked-vs-fixed) -- when you want packages to move together on purpose.
- [uv workspaces](/ecosystems/uv) -- how molt discovers members and reads internal dependencies.
