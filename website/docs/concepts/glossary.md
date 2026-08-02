---
title: Glossary
---

# Glossary

Precise definitions for the terms molt uses. Where a term means something different in another release tool, this page says so.

## Bump type

The size of a version change a [changeset](#changeset) requests for a package: `major`, `minor`, `patch`, or `none`. `major` / `minor` / `patch` move the version per [PEP 440 arithmetic](/concepts/versioning-pep440). **`none` is a first-class value meaning "record a changelog entry but do not move this package's version by itself."**

## Changeset

A small, human-written Markdown file recording the **intent** of a change: which packages it affects, the [bump type](#bump-type) for each, and a prose summary for the changelog. Changesets accumulate in `.changeset/` and are consumed in a batch by [`molt version`](/cli/version); see [Changesets](/concepts/changesets) for the model and [Changeset format](/config/changeset-format) for the grammar.

## Dependency

A package that is *depended upon* -- the thing further **up** the graph. When `acme-cli` requires `acme-core`, `acme-core` is the dependency. molt reads the dependency's declared version constraint to decide whether a [dependent](#dependent) needs a release.

## Dependent

A package that *depends on* another -- the thing further **down** the graph. When `acme-core` gets a release, molt looks at its dependents to see whether any of them fall out of range and need a release too; that is the core job of the [release plan](#release-plan). "Dependency" and "dependent" differ by two letters and are used constantly, so molt's user-facing output leans on "requires" / "required-by" where it helps.

## Ecosystem backend

The pluggable adapter that teaches molt how a particular Python packaging tool expresses workspaces and intra-repo dependencies. uv, Poetry, Hatch, PDM, and setuptools all express these differently, so molt puts a backend protocol behind them rather than calling one of them directly. See [Ecosystems](/ecosystems/overview).

## Fixed packages

A configured group whose members **always release together at one shared version**, including members that nothing changed. Contrast with [linked packages](#linked-packages), which only align the members already releasing; the precise difference is in [Linked vs fixed packages](/concepts/linked-vs-fixed).

## Forge

The code-hosting platform molt integrates with for release automation: pull or merge requests, release notes, and tags. GitHub is the backend molt ships, behind a host-agnostic protocol. See [Forges](/forges/overview).

## Linked packages

A configured group whose **releasing members are aligned to a shared version**, while members with no reason to release are left untouched. Contrast with [fixed packages](#fixed-packages), which force-release every member; see [Linked vs fixed packages](/concepts/linked-vs-fixed).

## Member

A single package (a [project](/config/config-file) with its own `pyproject.toml` and version) inside a [workspace](#workspace). molt uses "member", "project", or "package" for the unit, and reserves "workspace" for the whole repository.

## Prerelease

A staged, non-final release on the way to a specific stable version -- `1.2.1rc0` heading toward `1.2.1`. molt cuts one with the stateless flag `molt version --pre {a,b,rc,dev}` (PEP 440 identifiers only; there is no persistent `pre.json` mode), and a plain `molt version` lands on the stable version. See [Prerelease mode](/concepts/prerelease).

## Release plan

The calculated object that describes everything a set of changesets will release -- each package's name, [bump type](#bump-type), old version, new version, and originating changesets -- including cross-package propagation and group alignment. It is a machine-readable value you can inspect with `--dry-run` before anything is written; see [The release plan](/concepts/release-plan).

## Snapshot

A disposable release built from an exact commit for testing, shaped as `0.0.0.dev<datetime>` so it sorts below every real release. Because PyPI versions are permanent and it has no dist-tags, molt targets a non-PyPI index for snapshots by default. See [Snapshot releases](/concepts/snapshots).

## updateInternalDependencies

The config option controlling **whether a released dependency's version constraint is rewritten** in a dependent's manifest, keyed on how big the dependency's bump was (`"patch"` -- the default -- rewrites the pin on any bump; `"minor"` only rewrites it for minor-or-larger bumps). molt's config spells this snake_case as `update_internal_dependencies`. It governs the *constraint*, not whether a dependent is released -- that is [updateInternalDependents](#updateinternaldependents), two letters away and a different job entirely.

## updateInternalDependents

The config option controlling **whether a dependent is itself given a release** when a dependency moves (`"out-of-range"` -- the default -- releases a dependent only when the dependency's new version leaves its declared range; `"always"` releases every non-dev dependent on any non-`none` bump). molt's config spells this snake_case as `update_internal_dependents`. It governs *releases*, not the constraint rewrite -- that is [updateInternalDependencies](#updateinternaldependencies).

## Workspace

The **whole repository** of related packages managed together -- matching uv, Cargo, and pnpm, and each package in it is a [member](#member).

> **If you are coming from changesets:** it uses "workspace" to mean *a single package*. In molt the word follows uv, Cargo, and pnpm instead: **workspace = the repository, member = the package.**

## Where to go next

- [Changesets](/concepts/changesets) and [The release plan](/concepts/release-plan) -- the two central concepts.
- [Linked vs fixed packages](/concepts/linked-vs-fixed) -- the grouping terms, distinguished.
- [Molt and changesets](/reference/comparison-with-changesets) -- what else the two tools name differently.
