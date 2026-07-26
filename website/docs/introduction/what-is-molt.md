---
title: What is Molt?
---

# What is Molt?

Molt is a command-line tool that manages **versioning, changelogs, and publishing** for Python packages using small, human-written files called *changesets*.

Between "I merged a pull request" and "a user can `pip install` the fix" sits a pile of manual, error-prone bookkeeping: deciding the next version number, editing `pyproject.toml`, writing a changelog entry, bumping every internal package that depended on what you changed, tagging the commit, building a wheel, and uploading it. Molt collapses that work into a simple loop:

1. **Record intent while you work.** As part of a change, you run `molt add` and answer two questions: which packages did this affect, and how much (a patch, a minor, or a major change)? That writes a changeset file you commit alongside your code.
2. **Turn intent into releases.** At release time, `molt version` reads every accumulated changeset, computes the correct new version for each package (and for anything that depends on them), rewrites the manifests, and updates the changelogs.
3. **Ship.** `molt publish` builds and uploads exactly the packages that changed.

Molt is a Python-native port of the JavaScript [`changesets`](https://github.com/changesets/changesets) tool, rebuilt on Python's own packaging standards -- PEP 440 versions and PEP 508 requirements -- instead of SemVer and npm. If you have used changesets, the model on [The changesets model](/introduction/the-changesets-model) will feel familiar; the details that differ are deliberate and documented in [Why Molt?](/introduction/why-molt).

## `molt-cli` the package, `molt` the command

There are two names, and the distinction matters when you install:

- The **command you type is `molt`.**
- The **package you install is `molt-cli`.**

The distribution is named `molt-cli` because the name `molt` is already claimed on PyPI by an unrelated, abandoned project (two releases, last touched in 2012). A distribution name and the console script it installs are independent, so `molt-cli` still gives you a `molt` binary on your `PATH`.

```bash
# Install as a persistent tool -- this puts a `molt` command on your PATH
uv tool install molt-cli

# Or run it one-off, without installing anything
uvx molt-cli --help

# Either way, the command is always `molt`
molt --help
```

You never type `molt-cli` again after installing. See [Installation](/getting-started/installation) for the full setup, including running molt inside CI.

## Who it is for

Molt is built for two audiences, and treats both as first-class:

- **Single-package projects** -- one library, one `pyproject.toml`, one version number. This is the common case in Python, so it is the default path in molt, not a stripped-down special case. `molt init`, `molt add`, `molt version`, `molt publish` work end to end with zero workspace configuration.
- **Monorepos and workspaces** -- many interdependent packages released from one repository. This is where molt earns its keep: when you bump `acme-core`, molt works out that `acme-cli` depends on it, decides whether `acme-cli` needs a release too, and rewrites its dependency constraint to match. That cross-package reasoning -- the [release plan](/concepts/release-plan) -- is the hard part of the problem and the reason molt exists.

Most Python projects ship a single package, and molt is designed so that the single-package experience never feels like an afterthought bolted onto a monorepo tool. If your project later grows into a workspace, the same commands scale up without a rewrite.

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

Every mutating command can be run with `--dry-run` first, which prints the exact [plan](/concepts/release-plan) it would execute and writes nothing.

## Where to go next

- **New to this model?** Read [The changesets model](/introduction/the-changesets-model) to understand why intent-based versioning beats guessing version numbers from commit history.
- **Want the pitch?** [Why Molt?](/introduction/why-molt) covers what molt adds beyond a straight port and where it deliberately diverges from changesets.
- **Ready to try it?** Jump to [Installation](/getting-started/installation) and the [single-package quickstart](/getting-started/quickstart-single-package).
