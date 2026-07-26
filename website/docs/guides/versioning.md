---
title: Versioning
---

# Versioning

`molt version` consumes every pending changeset in one atomic step -- bumping versions, propagating to dependents, rewriting internal pins, writing changelogs, updating the lockfile, and deleting the changesets it applied.

This is the release clock ticking. Where [`molt add`](/guides/adding-a-changeset) records intent continuously, `molt version` drains the whole `.changeset/` buffer at once.

```bash
molt version
```

## What it does

`molt version` builds the [release plan](/concepts/release-plan) and applies it. In order:

1. **Reads every pending changeset** and computes the final bump for each directly-affected package -- the highest bump among the changesets touching it wins.
2. **Propagates to dependents.** Any package that depends on something being bumped is examined; if the new version falls outside its declared constraint, it gets a release too. See [Dependency propagation](/guides/dependency-propagation).
3. **Bumps `[project].version`** for every package in the plan, in its `pyproject.toml`, preserving your formatting and comments.
4. **Rewrites internal dependency pins.** When a dependent is released because a dependency moved out of range, molt splices its constraint so it admits the new version, keeping the leading operator style (`>=1.2.0,<2.0.0` becomes `>=2.0.0,<3.0.0`).
5. **Writes changelogs.** Each package gets a `## <version>` heading with `### Major / Minor / Patch Changes` sections. Your summaries appear under the section matching their bump; dependency bumps are recorded last, always in the Patch section.
6. **Updates the lockfile.** For uv, molt runs the lock step so `uv.lock` matches the new versions. changesets skips this -- cosmetic in npm, load-bearing in Python.
7. **Deletes the consumed changesets.** They have become concrete releases, so they leave `.changeset/`.

Commit the result and you are ready to [publish](/guides/publishing):

```bash
git commit -am "Release"
```

## Atomic by design: no double-bumps

molt buffers every mutation and flushes them together at the end. Changelog generation runs first as a transactional guard -- if anything fails there, nothing is written. This matters because of a specific failure mode molt deliberately fixes: in changesets, a run that fails partway can leave packages bumped *with their changesets still on disk*, so the next run bumps them a second time.

In molt, either the whole plan lands or none of it does. A consumed changeset is deleted as part of the same flush that bumps the version, so re-running `molt version` never double-applies. If there is nothing left to consume, it tells you and exits.

## Exit code when there is nothing to do

Run `molt version` with an empty `.changeset/` and it exits with **code 1**:

```text
No pending changesets found.
```

This is intentional (and matches changesets v3, not the older v2 behavior): "you asked me to release but there is nothing to release" is treated as a failure so a misconfigured pipeline cannot silently pass. Your CI release job should tolerate exit 1 here as "nothing to release yet" rather than a hard error -- the [GitHub Action](/guides/ci-github-action) handles this for you.

## Preview before you apply

`molt version` writes to disk. To see the plan without touching anything:

```bash
molt version --dry-run
```

This prints every version change and file it would write, and executes nothing. Add `--output json` for a machine-readable [plan object](/guides/dry-run-and-plans). The read-only [`molt status`](/guides/status) answers the same question and is the form to run as a CI gate.

## Prereleases

To cut release candidates instead of normal releases, pass `--pre` with a PEP 440 phase (`a`, `b`, `rc`, or `dev`):

```bash
molt version --pre rc      # 1.2.1rc0, then 1.2.1rc1 on the next run
molt version               # back to normal: drops the suffix -> 1.2.1
```

Prerelease in molt is an **invocation flag**, not a persistent mode -- there is no `pre.json` branch state to leak or clean up. See [Prerelease mode](/concepts/prerelease).

## Snapshots

For a throwaway build tied to a branch or PR -- to install and test before a real release -- use a snapshot:

```bash
molt version --snapshot
```

Snapshots produce `.devN`-style PEP 440 versions and, because PyPI versions are immutable, target a separate index by default rather than burning a public version number. See [Snapshot releases](/concepts/snapshots).

## Where to go next

- [Dependency propagation](/guides/dependency-propagation) -- how bumps cascade across a workspace.
- [Dry runs and plans](/guides/dry-run-and-plans) -- preview and machine-readable output.
- [Publishing](/guides/publishing) -- ship what you just versioned.
- [`molt version`](/cli/version) -- every flag, including `--pre` and `--snapshot`.
