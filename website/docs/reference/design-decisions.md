---
title: Design decisions
description: The load-bearing choices behind molt, each with the alternative it was chosen over.
---

# Design decisions

Each entry below states a decision, the alternative it was chosen over, and the reason. Together
they are why molt behaves the way the rest of this site describes.

For a feature-by-feature view of where molt and changesets land differently, see
[Molt and changesets](/reference/comparison-with-changesets).

## PEP 440 rather than SemVer

**Decision.** Version and specifier math is built entirely on the [`packaging`](https://packaging.pypa.io) library, the PyPA reference implementation of PEP 440 and PEP 508. Molt hand-rolls no version parsing and uses no SemVer library.

**Alternative.** Reimplement node-semver's logic, or bolt a SemVer shim onto Python versions.

**Why.** Python versions are not SemVer: prereleases come from a fixed `a`/`b`/`rc`/`dev` vocabulary, there is no caret operator, and ordering includes epochs, post-releases, and normalization. A SemVer engine would mis-parse and mis-order real PyPI versions. Using the reference implementation also means molt's answer to "does this version satisfy this constraint?" is exactly what `pip` and `uv` will do at install time. One consequence follows from the standard: PEP 440 scopes prerelease opt-in across a whole specifier set, so a dependent that already opted into prereleases is not re-released as its dependency moves `rc0 -> rc1`. See [Versioning and PEP 440](/concepts/versioning-pep440).

## Prerelease as a flag

**Decision.** Prerelease is an invocation flag, `molt version --pre rc`, with no persistent state. There is no mode to enter or exit.

**Alternative.** An `enter` / `exit` pair writing a repository-global state file, the way changesets does it.

**Why.** A named prerelease channel is what a persistent mode carries, and PEP 440 defines a closed vocabulary with no way to spell one. That alone settles the mechanism. It also removes a class of failure that has nothing to do with Python: a state file that lives on a branch is state everyone on that branch shares, has to merge, and has to remember to clear. A per-run flag keeps the whole prerelease state in the version number on disk. See [Prerelease mode](/concepts/prerelease) and [`molt pre`](/cli/pre).

## Snapshots target a non-PyPI index

**Decision.** Snapshot releases are PEP 440 `.devN` versions built for a **separate index** (a local wheelhouse, TestPyPI, or a private index) by default. Publishing snapshots to PyPI requires an explicit, noisy opt-in.

**Alternative.** A throwaway `0.0.0-<tag>-<datetime>` version on the public index, hidden from default installs by a dist-tag, which is how npm handles it.

**Why.** That approach rests on three npm affordances PyPI does not have: versions you can unpublish, versions that do not permanently occupy a number, and a `latest`-versus-`next` tag to keep a build out of a plain `pip install`. On PyPI every snapshot would burn a public version number and stay on the project page forever. Encoding "this is a development build" in the version string itself, as `.devN`, does the same job with the grain of the ecosystem: `pip` and `uv` exclude dev versions from resolution by default, so one flagless publish cannot expose it. See [Snapshot releases](/concepts/snapshots).

## Ecosystem and forge seams from the first commit

**Decision.** Package discovery and version writing sit behind an **ecosystem backend** protocol. Host integration sits behind a **forge** protocol. uv and GitHub are the implementations molt ships.

**Alternative.** Call uv and the GitHub API directly from the engine.

**Why.** Python has no single workspace standard. uv, Poetry, Hatch, PDM, and setuptools each express workspaces and intra-repo dependencies differently, so calling one of them directly from the engine would tie molt to that tool permanently. A protocol is cheap to design in and expensive to retrofit, so it goes in first even though there is one implementation behind it today. The same reasoning applies to the forge: the core release loop already runs on any CI, and a second host is a backend rather than a rewrite. See [Ecosystems](/ecosystems/overview) and [Forges](/forges/overview).

## One distribution

**Decision.** Molt ships as a single distribution, `molt-release`, with internal module boundaries between the engine, the CLI, and the backends.

**Alternative.** Publish the engine, the CLI, the changelog generators, and each backend as separate distributions.

**Why.** Splitting a tool into many small distributions answers pressures Python does not have, and it would mean a separate PyPI name, changelog, and version matrix for each one. A single distribution with clean internal seams keeps the option open: extracting an importable `molt-core` later is a mechanical split, as long as CLI types never leak into the engine, which an import-boundary lint rule enforces. The distribution is named `molt-release` because `molt` on PyPI belongs to an unrelated project; the distribution name and the console script are independent, so users still type `molt`. See [Overview](/guide/what-is-molt).

## Python floor of 3.11

**Decision.** `requires-python = ">=3.11"`.

**Alternative.** A higher floor, such as 3.13, or a lower one.

**Why.** 3.11 is where `tomllib` landed in the standard library, so molt reads TOML on the hot path with no extra dependency, and it brings `ExceptionGroup`, `Self`, and faster startup. A release tool has to run inside the CI a project already has, which is often pinned to an older interpreter, so every version above 3.11 in the floor costs adoption and buys nothing here. 3.10 and earlier are EOL-adjacent and would cost a `tomli` dependency.

## Config lives in one place; both is an error

**Decision.** Configuration lives in `[tool.molt]` in the root `pyproject.toml`, with a `.molt/config.json` fallback for projects that prefer JSON. If **both** are present, molt errors and asks you to pick one.

**Alternative.** Merge the two sources, or let one silently win.

**Why.** Merging two config sources means defining precedence, and every precedence rule is a place where a setting comes from somewhere the reader did not look. Erroring on two sources keeps configuration unambiguous: there is always exactly one answer to "where does this setting come from." Config being static TOML or JSON, never executable, also means any tool can read it without running your code. See [The config file](/config/config-file).

## Guarantees around failure and text

**Decision.** Eight behaviors are promised outright rather than left to chance. Each is covered by a test.

| Situation | What molt does |
|---|---|
| A run fails part-way through `version` | Every mutation is buffered and flushed together, so a failed run leaves the tree untouched. Re-running is safe and cannot double-bump |
| A changeset summary starts a line with `#` | The heading survives. Summaries are literal prose, never post-processed |
| A summary contains `$1` or `$&` | It appears verbatim. A summary is never passed through a replacement engine |
| A `none` release is replaced during dependent propagation | Its changelog summary is preserved |
| A changeset names a package that is ignored or private | Molt reports it by name rather than silently doing nothing |
| Versions change | `uv.lock` is refreshed in the same commit |
| An explicit git operation fails | The command exits non-zero. A failure you asked for is never logged and swallowed |
| A changelog entry is written | The Markdown is correct as emitted, including blank lines, with no formatter pass afterwards |

## Git is optional until you ask for it

**Decision.** Implicit git use is best-effort and only warns. Explicit git use fails hard. Detecting changed packages for the `add` prompt never errors if git is unavailable; `molt status --since main` does.

**Why.** The core flow of add, version, and changelog works with no git at all, which keeps molt usable in a container, a tarball, or a checkout with no history. Git enters when you name it, and only then is its failure yours to handle.

## Where to go next

- [Molt and changesets](/reference/comparison-with-changesets) -- these decisions as a feature table.
- [Acknowledgements](/reference/acknowledgements) -- what molt is built on.
- [Troubleshooting](/reference/troubleshooting) -- what these decisions look like when they stop you.
