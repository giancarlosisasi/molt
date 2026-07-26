---
title: Installation
---

# Installation

Install `molt-cli` once and you get a `molt` command on your `PATH`; then run `molt init` to set up a project.

## Requirements

- **Python 3.11 or newer.** molt uses the standard-library `tomllib` parser, which landed in 3.11. There is no lower floor on purpose -- a release tool has to run in the CI you already have, and pinning a high interpreter version is an adoption tax with no payoff.
- **A workspace on any supported ecosystem.** uv is first-class; Poetry, Hatch, PDM, and setuptools are covered through a backend seam. See [Ecosystems](/ecosystems/overview).
- **Windows, macOS, or Linux.** molt is tested on Windows from the first commit and keeps its output ASCII-clean, so it behaves the same in every runner.

## Install as a tool

The recommended path is `uv tool install`, which puts molt in its own isolated environment and exposes the `molt` command globally:

```bash
uv tool install molt-cli
```

Prefer not to install anything? Run it straight from the index with `uvx`:

```bash
uvx molt-cli --help
```

Either way, the command you type afterward is always `molt`:

```bash
molt --help
```

Other installers work too, since `molt-cli` is an ordinary PyPI distribution:

```bash
pipx install molt-cli
# or, into the current environment
pip install molt-cli
```

## `molt-cli` the package, `molt` the command

You install **`molt-cli`** but you run **`molt`**. The distribution is named `molt-cli` because `molt` is already claimed on PyPI by an unrelated, long-abandoned project. A distribution name and the console script it installs are independent, so `molt-cli` still gives you a `molt` binary. You never type `molt-cli` again after installing. The full story is in [What is Molt?](/introduction/what-is-molt).

## Verify the install

```bash
molt --version
```

This prints the bare version string (for example, `0.1.0`) and touches none of your project -- it is safe to run anywhere.

## Scaffold a project

From the root of the repository you want to manage, run:

```bash
molt init
```

`molt init` creates a `.changeset/` directory -- the home for the changeset files you will write -- and adds a `[tool.molt]` configuration block. It is safe to re-run: if molt is already set up, it reports that and changes nothing.

```text
.changeset/
  README.md          # a short guide, committed alongside your changesets
pyproject.toml       # now contains a [tool.molt] section
```

See [`molt init`](/cli/init) for the flags and prompts, and [The config file](/config/config-file) for where configuration lives and every option it accepts.

## Where to go next

- **One package?** Follow the [single-package quickstart](/getting-started/quickstart-single-package) -- the majority Python case, and first-class in molt.
- **A workspace?** Follow the [monorepo quickstart](/getting-started/quickstart-monorepo) to see dependency propagation in action.
- **Want the mental model first?** Read [The release workflow](/getting-started/the-release-workflow).
