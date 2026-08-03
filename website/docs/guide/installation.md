---
title: Installation
---

# Installation

Install `molt-release` once and you get a `molt` command on your `PATH`; then run `molt init` to set up a project.

## Requirements

- **Python 3.11 or newer.** molt reads TOML with the standard-library `tomllib` parser, which landed in 3.11. The floor stops there so molt runs in the CI you already have.
- **A workspace on any supported ecosystem.** uv is first-class; Poetry, Hatch, PDM, and setuptools are covered through a backend seam. See [Ecosystems](/ecosystems/overview).
- **Windows, macOS, or Linux.** molt is tested on Windows from the first commit and keeps its output ASCII-clean, so it behaves the same in every runner.

## Install as a tool

The recommended path is `uv tool install`, which puts molt in its own isolated environment and exposes the `molt` command globally:

```bash
uv tool install molt-release
```

Prefer not to install anything? Run it straight from the index with `uvx`:

```bash
uvx molt-release --help
```

Either way, the command you type afterward is always `molt`:

```bash
molt --help
```

Other installers work too, since `molt-release` is an ordinary PyPI distribution:

```bash
pipx install molt-release
# or, into the current environment
pip install molt-release
```

:::tip molt in GitHub Actions
In CI you do not install molt yourself. The [GitHub Action](/guide/ci-github-action) installs it
with uv on the runner, keeps a "Version Packages" pull request in sync with your changesets, and
publishes to PyPI over OIDC when that pull request merges. You add it with one `uses:` line:

```yaml
- uses: giancarlosisasi/molt-action@v1
  with:
    molt-version: "0.1.3"
    publish: molt publish
```
:::

## `molt-release` the package, `molt` the command

You install **`molt-release`** but you run **`molt`**. The distribution is named `molt-release` because `molt` on PyPI belongs to an unrelated project. A distribution name and the console script it installs are independent, so `molt-release` still gives you a `molt` binary. You never type `molt-release` again after installing. See the [Overview](/guide/what-is-molt).

## Verify the install

```bash
molt --version
```

This prints the bare version string (for example, `0.1.1`) and touches none of your project -- it is safe to run anywhere.

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

- **One package?** Follow the [single-package quickstart](/guide/quickstart-single-package) -- the majority Python case, and first-class in molt.
- **A workspace?** Follow the [monorepo quickstart](/guide/quickstart-monorepo) to see dependency propagation in action.
- **Want the mental model first?** Read [The release workflow](/guide/the-release-workflow).
- **Releasing from GitHub?** Set up the [GitHub Action](/guide/ci-github-action) and molt runs `molt version` and `molt publish` for you, on every push to your base branch.
- **Coming from JavaScript changesets?** [Molt and changesets](/reference/comparison-with-changesets) maps the commands, configuration keys, and changeset fields one to one, and [Migrating from changesets](/guide/migrating-from-changesets) converts an existing `.changeset/` directory.
