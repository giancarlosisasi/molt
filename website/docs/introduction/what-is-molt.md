---
title: Overview
description: What molt is, the loop it runs, and who it is for.
---

# Overview

Molt is a command-line tool that manages **versioning, changelogs, and publishing** for Python
packages using small, human-written files called *changesets*.

Between "I merged a pull request" and "a user can `pip install` the fix" sits a pile of manual
bookkeeping: deciding the next version number, editing `pyproject.toml`, writing a changelog entry,
bumping every internal package that depended on what you changed, tagging the commit, building a
wheel, and uploading it. Molt collapses that work into one loop:

1. **Record intent while you work.** As part of a change, you run `molt add` and answer two
   questions: which packages did this affect, and how much (a patch, a minor, or a major change)?
   That writes a changeset file you commit alongside your code.
2. **Turn intent into releases.** At release time, `molt version` reads every accumulated changeset,
   computes the correct new version for each package and for anything that depends on them, rewrites
   the manifests, and updates the changelogs.
3. **Ship.** `molt publish` builds and uploads exactly the packages that changed.

Molt is built on Python's own packaging standards, PEP 440 versions and PEP 508 requirements, and
targets PyPI. The workflow itself comes from
[changesets](https://github.com/changesets/changesets), the release tool for JavaScript monorepos;
see [Acknowledgements](/reference/acknowledgements).

## `molt-release` the package, `molt` the command

There are two names, and the distinction matters when you install:

- The **command you type is `molt`.**
- The **package you install is `molt-release`.**

The distribution is named `molt-release` because `molt` on PyPI belongs to an unrelated project. A
distribution name and the console script it installs are independent, so `molt-release` still gives
you a `molt` binary on your `PATH`.

```bash
# Install as a persistent tool. This puts a `molt` command on your PATH.
uv tool install molt-release

# Or run it one-off, without installing anything
uvx molt-release --help

# Either way, the command is always `molt`
molt --help
```

You never type `molt-release` again after installing. See
[Installation](/getting-started/installation) for the full setup, including running molt inside CI.

## Who it is for

Molt treats two kinds of project as first-class:

- **Single-package projects.** One library, one `pyproject.toml`, one version number. This is the
  common case in Python, so it is molt's default path. `molt init`, `molt add`, `molt version`, and
  `molt publish` work end to end with no workspace configuration at all.
- **Monorepos and workspaces.** Many interdependent packages released from one repository. When you
  bump `acme-core`, molt works out that `acme-cli` depends on it, decides whether `acme-cli` needs a
  release too, and rewrites its dependency constraint to match. That cross-package reasoning is the
  [release plan](/concepts/release-plan).

The single-package path is not a stripped-down version of the monorepo path. If your project later
grows into a workspace, the same commands scale up without a rewrite.

## The shape of a workflow

Here is the whole loop, end to end, for a single package:

```bash
# One-time setup
molt init

# During development, as you make changes
molt add                 # record a changeset describing this change

# When you are ready to cut a release
molt version             # apply changesets: bump versions, write changelogs
git commit -am "Release"
molt publish             # build and upload to PyPI
```

Every mutating command accepts `--dry-run`, which prints the exact [plan](/concepts/release-plan) it
would execute and writes nothing.

## Where to go next

- [The changeset workflow](/introduction/the-changeset-workflow) -- how intent turns into version
  numbers.
- [Installation](/getting-started/installation) and the
  [single-package quickstart](/getting-started/quickstart-single-package) -- get molt running.
- [Molt and changesets](/reference/comparison-with-changesets) -- a feature-by-feature map, if you
  are coming from the JavaScript tool.
