---
title: Acknowledgements
description: The projects and standards molt is built from, and the prior work it learned from.
---

# Acknowledgements

Molt is an independent open-source project. It is built on standards, libraries, and ideas that
other people designed and maintain, and this page names them.

## changesets

Molt's workflow comes from [changesets](https://github.com/changesets/changesets), the release tool
for JavaScript monorepos. The shape of a changeset file, the split between recording intent and
consuming it, the max-bump flattening rule, and the fixpoint pass that propagates a version bump to
dependents are all ideas molt learned there.

Reading the changesets source is what made molt's engine possible. The three-pass release plan in
particular is a subtle piece of work, and having a reference implementation to study and to test
against removed most of the risk from building the Python equivalent. Molt's engine is validated
against test fixtures derived from that project.

Molt is a separate tool for a separate ecosystem. It targets PEP 440 versions, PEP 508
requirements, and PyPI, so many of its rules resolve differently. [Molt and
changesets](/reference/comparison-with-changesets) sets the two side by side. Where molt keeps a
changesets behavior, it keeps it because it is right, not because it is inherited.

changesets is MIT-licensed, and thanks are due to its maintainers and contributors.

## Python packaging standards

Molt implements behavior specified by the Python packaging community:

| Standard | What molt uses it for |
|---|---|
| [PEP 440](https://peps.python.org/pep-0440/) | Version identifiers, ordering, prerelease and development spellings |
| [PEP 508](https://peps.python.org/pep-0508/) | Dependency specifiers and the constraints molt rewrites |
| [PEP 503](https://peps.python.org/pep-0503/) | Distribution name normalization |
| [PEP 517](https://peps.python.org/pep-0517/) / [PEP 621](https://peps.python.org/pep-0621/) | Build backends and `[project]` metadata |
| [PEP 592](https://peps.python.org/pep-0592/) | Yanking a released version |
| [PEP 621](https://peps.python.org/pep-0621/) and [PEP 639](https://peps.python.org/pep-0639/) | Manifest and license metadata molt reads and writes |

The [Python Packaging Authority](https://www.pypa.io/) writes and maintains these, along with
[`packaging`](https://packaging.pypa.io), the reference implementation molt does all of its version
and specifier math with. Molt hand-rolls none of it, which is what makes its answer to "does this
version satisfy this constraint?" the same answer `pip` and `uv` give.

## Libraries

| Library | Role in molt |
|---|---|
| [`packaging`](https://packaging.pypa.io) | PEP 440 and PEP 508 versions, specifiers, and ordering |
| [`uv`](https://github.com/astral-sh/uv) | The workspace model molt discovers, and the lockfile it refreshes |
| [Typer](https://typer.tiangolo.com) and [Click](https://click.palletsprojects.com) | The command surface and the external-editor primitive |
| [Rich](https://rich.readthedocs.io) | Terminal rendering |
| [questionary](https://github.com/tmbo/questionary) | Interactive prompts |
| [Pydantic](https://docs.pydantic.dev) | Configuration validation and the published JSON schema |
| [tomlkit](https://tomlkit.readthedocs.io) | Comment- and format-preserving `pyproject.toml` edits |
| [ruamel.yaml](https://yaml.readthedocs.io) | Changeset front matter, on YAML 1.2 |
| [Jinja2](https://jinja.palletsprojects.com) | Changelog templating |
| [httpx](https://www.python-httpx.org) | The forge and PyPI HTTP client |

The documentation site runs on [Rspress](https://rspress.dev).

## Prior work in Python

[`the-roaring/pychangeset`](https://github.com/the-roaring/pychangeset) brought the changeset model
to Python before molt did, and reading it was useful. It is a clear statement of the problem, and
the questions its issue tracker raised shaped two of molt's early decisions: keeping the supported
Python floor low, and putting the forge behind a seam from the first commit.

[changepacks](https://github.com/changepacks/changepacks) takes a polyglot approach to the same
model from Rust, and is worth a look if you release across more than one language ecosystem.

## Related tools

Molt is one answer to Python release automation. These are others, and one of them may fit your
project better:

- [`hatch`](https://hatch.pypa.io) and [`uv version`](https://docs.astral.sh/uv/) -- set and bump a
  single package's version directly.
- [`setuptools-scm`](https://setuptools-scm.readthedocs.io) and
  [`hatch-vcs`](https://github.com/ofek/hatch-vcs) -- derive a version from git tags.
- [`python-semantic-release`](https://python-semantic-release.readthedocs.io) -- derive versions and
  changelogs from conventional commit messages.
- [`towncrier`](https://towncrier.readthedocs.io) -- news fragments assembled into a changelog, a
  model close to molt's for the changelog half of the problem.

## Trademarks

Molt is not affiliated with, endorsed by, or sponsored by the changesets project, the Python
Packaging Authority, Astral, PyPI, GitHub, or any other project named on this page. Product names
belong to their respective owners.
