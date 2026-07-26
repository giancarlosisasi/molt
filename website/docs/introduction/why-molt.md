---
title: Why Molt?
---

# Why Molt?

Molt exists because the best release-automation model in the JavaScript world -- changesets -- has never had a credible Python-native equivalent, and the demand for one is proven, named, and unmet. This page explains the gap molt fills, what it adds beyond a straight port, and where it deliberately diverges from changesets.

## The demand is real, and it has been ignored for six years

The single most-requested feature on the changesets project, for six years running, is **support for languages and ecosystems other than JavaScript.** The history is a matter of public record:

- The request to make changesets pluggable across ecosystems was **formally rejected in 2020** -- the maintainers said it "creates so many questions that we don't want to have to answer."
- It keeps coming back anyway, from named organizations: Gradio, Shopify, The Guardian, SolarWinds, and even a working version-provider pull request from OpenAI. Python is named explicitly in those threads.
- As recently as mid-2026, the maintainers reaffirmed that ecosystem support is deferred to an unscheduled "maybe v4" rewrite.

The project is alive but stretched thin: the median open issue is close to four years old, and nearly half of open issues never received a maintainer reply. The conclusion is not that changesets is bad -- it is excellent -- but that **the Python-shaped hole in it is never going to be filled upstream.**

Two prior Python attempts tried and stalled at exactly the same place: they shipped single-package versioning and a changelog, then never reached credible **monorepo dependency propagation**. That propagation engine is the hard 80% of the problem, and finishing it properly is molt's reason to exist. See [The release plan](/concepts/release-plan) for what that engine actually does.

## What molt adds beyond a port

Porting changesets faithfully is the baseline. These are the things molt does that changesets does not -- most of them cheap to build from scratch, expensive to retrofit, which is exactly why they were never added upstream.

- **An ecosystem abstraction, designed in from day one.** Python has no single workspace standard -- uv, Poetry, Hatch, PDM, and setuptools all express workspaces and intra-repo dependencies differently. Molt puts a backend seam behind all of them instead of hard-wiring one. This is the very feature changesets rejected in 2020. See [Ecosystems](/ecosystems/overview).
- **Lockfile updates during `version`.** When molt bumps a package it also runs the lockfile update (for uv, one subprocess call). Upstream leaves lockfiles stale; in Python that is load-bearing, not cosmetic.
- **A machine-readable plan on every mutating command.** `add`, `version`, `publish`, and `yank` all produce the same shape of plan object. `--dry-run` prints the plan and executes nothing, on every one of them -- not just publish. See [Dry runs and plans](/guides/dry-run-and-plans).
- **Non-interactive `molt add`.** You can create a changeset without a prompt, which unlocks Dependabot, Renovate, and code-generation workflows that cannot answer interactive questions.
- **Real changelog templating.** Sections, dates, and custom formats via templates, rather than a single hard-coded layout. See [Changelog templates](/guides/changelog-templates).
- **Forge-agnostic release automation.** GitHub ships first, but the forge is a seam from day one, so GitLab, Gitea, and others are additions rather than rewrites. See [Forges](/forges/overview).
- **`molt yank` as a first-class verb.** A real recovery path for a bad release (more on this below).
- **Atomic, resumable `version`.** Molt buffers all mutations and flushes them together, so a mid-run failure does not leave you half-bumped with a double-bump waiting on the next run.
- **Correct changelog Markdown, emitted directly** -- no formatter pass to clean up broken blank lines afterward.
- **Windows-correct from day one.** Tested on Windows from the first commit, output stays ASCII-clean, and paths behave. Your release tool has to run in the CI you already have.

## Where molt deliberately diverges from changesets

Some differences are not enhancements -- they are places where Python's rules force a different answer than JavaScript's. These are intentional, and molt documents them rather than hiding them.

### Prereleases follow PEP 440, not node-semver

Version and specifier math is built on the `packaging` library -- the PyPA reference implementation of PEP 440 and PEP 508 -- not on SemVer. The vocabularies genuinely differ: a Python prerelease is `1.0.0rc0`, `1.0.0a1`, or `1.0.0.dev3` from a **fixed** set of spellings, not an arbitrary `1.0.0-next.0` tag.

This has a subtle, deliberate consequence for dependency propagation. PEP 440 scopes prerelease opt-in across a whole specifier set, so a constraint like `>=1.0.0rc0,<2.0.0` genuinely accepts `1.0.1rc0` -- and pip will install it. Molt honors that: a dependent that already opted into prereleases is not force-released as its dependency moves `rc0 -> rc1`. Reproducing node-semver's stricter rule would import a JavaScript-ism that has no meaning in Python and would over-release on every prerelease bump. See [Versioning and PEP 440](/concepts/versioning-pep440).

### Prerelease is a flag, and `pre.json` is gone

In changesets, entering prerelease mode writes a persistent `pre.json` file that becomes shared branch state -- the single loudest complaint in the tool, responsible for a long tail of open issues. Two pressures kill it for molt: arbitrary prerelease tags are illegal under PEP 440 anyway, and the branch-state design is the thing users hate most.

So in molt, prerelease is an **invocation flag**, not a mode you persist:

```bash
molt version --pre rc      # cut release candidates this run
molt version               # back to normal, no leftover state to clean up
```

One change satisfies PEP 440 and removes changesets' most-disliked subsystem. See [Prerelease mode](/concepts/prerelease).

### PyPI is immutable, so snapshots and yank work differently

PyPI has no unpublish, no dist-tags, immutable versions, and rejects local-version suffixes. That reshapes two areas:

- **Snapshots** cannot use changesets' throwaway-version-plus-dist-tag trick, because every snapshot would permanently burn a public version number. Molt targets a **separate index** for snapshots by default. See [Snapshot releases](/concepts/snapshots).
- **A bad release cannot be unpublished** -- but PEP 592 lets it be *yanked*, so resolvers stop selecting it while existing pins keep working. Molt turns that into a first-class [`molt yank`](/cli/yank) verb. The platform's harshest constraint becomes a feature changesets structurally cannot offer.

### Vocabulary that molt refuses to inherit

changesets' own glossary has a "we can't explain this yet" section. Molt fixes the confusing terms instead of porting them. Most importantly, changesets uses **"workspace" to mean a single package** -- the exact opposite of uv, Cargo, and pnpm. Molt never reuses that word for that meaning. See the [Glossary](/concepts/glossary) for the terms molt uses and why.

## What molt deliberately will not do

A sharp tool says no. Molt refuses, on purpose:

- **Full commit-derived versioning.** It kills the intent-based model. Molt offers changeset seeding from commits only as a one-time migration aid.
- **Executable config.** Configuration is TOML/JSON so other tools can read it without running your code.
- **Arbitrary shell hooks as the primary extension point.** Extension happens through Python entry points instead. See [Extending](/extending/changelog-plugins).
- **Per-tool bespoke integrations, chat bots, and config-option creep.** Being the best Python release tool beats being a mediocre everything-tool.

## Where to go next

- [The changesets model](/introduction/the-changesets-model) -- the workflow molt is built on.
- [The release plan](/concepts/release-plan) -- the dependency-propagation engine that is molt's core.
- [Comparison with changesets](/reference/comparison-with-changesets) -- a feature-by-feature table.
- [Design decisions](/reference/design-decisions) -- the rationale behind the divergences above.
- [Installation](/getting-started/installation) -- get started.
