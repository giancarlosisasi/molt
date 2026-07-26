---
title: Linked vs fixed packages
---

# Linked vs fixed packages

`fixed` and `linked` are molt's two mechanisms for making a set of packages share versions. They sound the same and are constantly confused -- so here is the one-line difference: **fixed always releases every member together; linked only aligns the members that were already going to release.**

Both are configured as groups of package names in [`[tool.molt]`](/config/options), and both are resolved inside the [release plan](/concepts/release-plan)'s fixpoint loop. A package can belong to a `fixed` group or a `linked` group, but never both.

> **Note.** changesets ships these two features with near-identical dictionary definitions ("share a semver categorisation..."), which is why nobody can remember which is which. molt states the distinction sharply and keeps `fixed` and `linked` as genuinely different tools -- see [Why Molt?](/introduction/why-molt) on the vocabulary molt refuses to inherit.

## fixed: move as one, always

A **fixed** group behaves like a single versioned unit. When *any* member releases, *every* member releases -- to one shared version -- even members that nothing changed.

```toml
[tool.molt]
fixed = [["acme-core", "acme-http"]]
```

Use `fixed` when the packages are, for practical purposes, one product with one version number that you always publish in lockstep (think a framework and its official runtime). The cost is churn: a changeset touching only `acme-core` still forces a fresh `acme-http` release at the new shared version.

## linked: align on release, never force one

A **linked** group keeps versions *consistent among the packages that are releasing anyway*, but it never drags in a member that had no reason to release.

```toml
[tool.molt]
linked = [["acme-core", "acme-http"]]
```

Use `linked` when you want releasing packages to stay on matching version numbers -- so a user never sees `acme-core 2.1.0` next to `acme-http 2.0.4` -- without publishing untouched packages just to keep the numbers tidy.

## The precise difference

Both mechanisms compute the same two things for the group:

- the **highest current version** among all members, read from disk, and
- the **highest bump** among the members that are releasing.

They differ only in *who* the resulting version is applied to:

| | **fixed** | **linked** |
|---|---|---|
| Who is released | **every non-skipped member of the group** | **only members already releasing** (from a changeset or from dependent propagation) |
| Untouched members | force-released at the group version | left exactly as they are |
| Adds packages to the plan | yes | no -- it only realigns existing releases |

## The contrast in one setup

The cleanest way to see the difference is to run the *identical* scenario through each mechanism.

**Setup.** Three packages, grouped together. `acme-http` gets a `minor` changeset and `acme-core` gets a `patch` changeset. `acme-billing` has **no** changeset -- but it already sits at a higher version than the others:

```toml
# acme-core     version = "1.0.0"   <- patch changeset
# acme-http     version = "1.0.0"   <- minor changeset
# acme-billing  version = "2.0.0"   <- NO changeset, but highest current version
```

The group's highest current version is `2.0.0` (from `acme-billing`), and the highest bump among releasing members is `minor` (from `acme-http`). So the shared target is `inc(2.0.0, minor)` = **`2.1.0`**.

**With `fixed = [["acme-core", "acme-http", "acme-billing"]]`** -- three releases:

```json
{
  "releases": [
    { "name": "acme-core",    "oldVersion": "2.0.0", "newVersion": "2.1.0", "type": "minor" },
    { "name": "acme-http",    "oldVersion": "2.0.0", "newVersion": "2.1.0", "type": "minor" },
    { "name": "acme-billing", "oldVersion": "2.0.0", "newVersion": "2.1.0", "type": "minor" }
  ]
}
```

`acme-billing` is force-released to `2.1.0` even though nothing changed it -- that is `fixed`.

**With `linked = [["acme-core", "acme-http", "acme-billing"]]`** -- two releases:

```json
{
  "releases": [
    { "name": "acme-core", "oldVersion": "2.0.0", "newVersion": "2.1.0", "type": "minor" },
    { "name": "acme-http", "oldVersion": "2.0.0", "newVersion": "2.1.0", "type": "minor" }
  ]
}
```

`acme-core` and `acme-http` are pulled up to `2.1.0` (note their `oldVersion` is realigned to the group max `2.0.0` before the bump), while `acme-billing` -- which had no reason to release -- **stays at `2.0.0`** and never enters the plan. That is `linked`.

Same three packages, same changesets: `fixed` produces three releases, `linked` produces two. That single row is the whole distinction.

## How they interact with the engine

Both passes run *after* dependent propagation within each iteration of the [fixpoint loop](/concepts/release-plan#the-engine-three-passes-to-a-fixpoint), and both can re-trigger the loop:

- Raising a group to a higher shared version can push a member out of *its* dependents' ranges, forcing more propagation on the next iteration.
- Because grouping runs after propagation, a group can absorb a propagated bump: if a dependent picks up a `patch` from propagation but its group is moving by `minor`, the group's `minor` overwrites the `patch`, and the member lands on the group version rather than a stray patch.

Chained groups (where one group depends on another) resolve through the same loop, one pass feeding the next until everything converges.

## Choosing between them

- Reach for **fixed** when the packages are conceptually one release with one version number, and you accept publishing all of them whenever any of them changes.
- Reach for **linked** when you want matching version numbers *among whatever is releasing*, without minting releases for untouched packages.
- If neither fits, plain [dependent propagation](/guides/dependency-propagation) already keeps a workspace correct -- most workspaces need no groups at all.

## Where to go next

- [The release plan](/concepts/release-plan) -- the fixpoint loop that resolves both mechanisms.
- [Versioning](/guides/versioning) -- running `molt version` and reading the result.
- [Options](/config/options) -- the `fixed` and `linked` config keys.
- [Glossary](/concepts/glossary) -- fixed, linked, dependent, and the rest.
