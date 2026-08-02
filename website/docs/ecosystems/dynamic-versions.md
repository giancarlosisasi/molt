---
title: Dynamic versions
---

# Dynamic versions

Not every Python package writes its version in `[project].version`. A very common modern shape puts
it in a file instead:

```toml
# packages/acme-core/pyproject.toml
[project]
name = "acme-core"
dynamic = ["version"]

[tool.hatch.version]
path = "src/acme_core/__about__.py"
```

Molt reads that. `acme-core` appears in `molt status`, is offered by `molt add`, is planned by the
engine, gets its `CHANGELOG.md` written, has the new version spliced back into `__about__.py` by
`molt version`, and is tagged and published like any other package. There is no extra command and
no extra step.

## What molt detects

Molt reads the tables your build backend already wrote. It never runs your build.

| What your manifest says | Molt reads the version from |
| --- | --- |
| `[tool.hatch.version] path = "..."` | that file |
| `[tool.hatch.version] source = "vcs"` | a git tag -- **not releasable yet**, see below |
| `[tool.setuptools.dynamic] version = { attr = "pkg.__version__" }` | the file holding that attribute |
| `[tool.setuptools.dynamic] version = { file = "VERSION" }` | that file |
| `[tool.pdm.version] source = "file"` + `path` | that file |
| `[tool.pdm.version] source = "scm"` | a git tag -- **not releasable yet** |
| `setuptools-scm`, `hatch-vcs` or `versioningit` in `[build-system].requires` | a git tag -- **not releasable yet** |

`{ attr = "pkg.__version__" }` is turned into a file path by the ordinary import-path convention --
`pkg/__init__.py`, then `src/pkg/__init__.py`, then `pkg.py`. Molt does **not** import your package
to read its version: importing runs your top-level code, and molt reads versions on every
`molt status`.

## Telling molt directly

Detection is a heuristic over other tools' tables. When it is wrong, or when your project uses
something molt does not recognise, say so:

```toml
# packages/acme-core/pyproject.toml
[project]
name = "acme-core"
dynamic = ["version"]

[tool.molt.version_source]
kind = "file"
path = "src/acme_core/__about__.py"
# pattern = '__version__\s*=\s*"(?P<version>[^"]+)"'   # optional
```

An explicit declaration always wins over detection.

The table lives in the **package's own** `pyproject.toml`, not the workspace root's: where a version
lives is a property of a package. See [`version_source`](/config/options) for each member.

## How the version is found inside the file

By default molt looks for `__version__`, `version` or `VERSION` assigned to a quoted string, at any
indentation:

```python
# src/acme_core/__about__.py
__version__ = "1.4.0"
```

Two rules are worth knowing:

- **Only the version text is replaced.** Comments, imports, other assignments and the file's own
  line endings come back byte-for-byte. Molt never reformats a file you wrote.
- **Two version literals in one file is an error**, not a first-wins guess. Keep one, or write a
  `pattern` that matches exactly one.

If the pattern finds nothing but the whole file is a version -- setuptools' `{ file = "VERSION" }`
idiom, a `VERSION` file containing `1.4.0` -- molt reads the whole file and rewrites it, keeping the
trailing newline.

A `pattern` must capture a group named `version`. That group is the text molt replaces, so a pattern
without it is refused when your configuration loads, with a message saying so.

## The three answers a package can get

After molt has looked, every package is in exactly one of three situations:

| Situation | What molt does |
| --- | --- |
| Molt found the version | Released, changelogged, tagged and published normally |
| No version and no `dynamic = ["version"]` -- an application, a docs site, the workspace root | Skipped, **silently**. Its pins on released dependencies still move |
| `dynamic = ["version"]` but molt cannot find the source | Skipped, and **named once** with the reason and the fix. Its pins still move |

The middle row is silent on purpose: warning about a package you never intend to release is noise.
The last row is loud on purpose: a package that declares `dynamic = ["version"]` is a distribution
somebody intends to build, and saying nothing about it is the failure this whole feature exists to
fix.

In every skipped case, a changeset that **names** the package still fails the run:

```
Changeset quiet-lions-give asks to release "acme-core", which molt has no version for ...
```

Skipping a package nobody asked to release is a convenience. Silently dropping one somebody did ask
to release is not.

## Versions from a git tag are not releasable yet

If your version comes from setuptools-scm, hatch-vcs, versioningit or pdm's `scm` source, molt
recognises it and says so:

```
"acme-core" takes its version from a git tag (setuptools-scm, hatch-vcs or versioningit). molt
cannot yet create the tag that would realize a new version, so it cannot release this package.
Give it a static [project].version, or point [tool.molt.version_source] at a file that holds the
version.
```

The reason is structural rather than a missing feature. For a tag source there is nothing on disk to
write -- the tag *is* the version -- so `molt version` would have to create the tag on its own
version commit. That works for a local release and is wrong for the release-pull-request flow molt
documents: there the version commit lives on `changeset-release/<base>` and is merged, often
squashed, days later, so the tag would point at a commit that never lands.

The answer molt is going to take instead is build-time version injection
(`SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>` and its pdm equivalent), which is correct in both
flows. It needs the planned version to survive from `molt version` to `molt build`, and that is its
own piece of design.

Until then: give the package a static `[project].version`, or point `[tool.molt.version_source]` at
a file.

## Out of scope

- **Run your build to ask for the version.** A PEP 517 metadata build is correct for every project
  and needs an isolated environment, network access and running arbitrary code out of the repository
  being released -- for a value molt reads on the cheapest command in the tool.
- **Import your package.** Same objection, smaller.
- **Write the version into more than one file.** One source, one write target. A multi-file source
  is a plugin.

## `molt build` needs no change

The build backend reads the file molt just wrote, so `molt build` and `molt publish` need to know
nothing about version sources at all.

## Writing your own version source

Version sources resolve through the `molt.version_source` entry-point group, and molt's own three
(`static`, `file`, `tag`) are registered through exactly that mechanism -- no privileged path. A
distribution that registers a fourth becomes usable by naming its `kind`:

```toml
[project.entry-points."molt.version_source"]
mytool = "mypackage.molt_source"
```

The module offers `build(*, name, directory, version, options)` returning an object with `kind`,
`read()`, `plan_write(new_version)` and `describe()`. Naming a source that is not installed fails
with a message listing the ones that are.
