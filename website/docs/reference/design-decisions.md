---
title: Design decisions
---

# Design decisions

The load-bearing choices behind molt, each stated as the decision, the alternative it beats, and the reason -- so the divergences from changesets read as intentional, not accidental.

changesets' own `decisions.md` is one of the reasons that tool is understandable. This page is molt's equivalent. It records the choices that shape the product, especially the ones where molt departs from changesets. For the feature-level view of those departures, see [Comparison with changesets](/reference/comparison-with-changesets).

## PEP 440, not SemVer

**Decision.** Version and specifier math is built entirely on the [`packaging`](https://packaging.pypa.io) library -- the PyPA reference implementation of PEP 440 and PEP 508. molt does not hand-roll version parsing and does not use a SemVer library.

**Alternative.** Port changesets' node-semver logic, or bolt a SemVer shim onto Python versions.

**Why.** Python versions are genuinely not SemVer: prereleases come from a fixed `a`/`b`/`rc`/`dev` vocabulary, there is no caret operator, and ordering includes epochs, post-releases, and normalization. A SemVer engine would mis-parse and mis-order real PyPI versions. Using the reference implementation also means molt's answer to "does this version satisfy this constraint?" matches exactly what `pip` and `uv` will do at install time. One consequence is deliberate: PEP 440 scopes prerelease opt-in across a whole specifier set, so a dependent that already opted into prereleases is not force-released as its dependency moves `rc0 -> rc1`. That is the correct answer for Python and is pinned by a test asserting the divergence from changesets. See [Versioning and PEP 440](/concepts/versioning-pep440).

## Prerelease is a flag; `pre.json` is gone

**Decision.** Prerelease is an invocation flag -- `molt version --pre rc` -- with no persistent state. There is no `pre.json`, no mode to enter or exit.

**Alternative.** Port changesets' `pre enter` / `pre exit` model, which writes a repo-global `pre.json` that becomes shared branch state.

**Why.** Two independent pressures point the same way. First, `pre.json` is the single most-disliked part of changesets -- roughly fifteen open issues trace back to its global state, merge conflicts, and whole-branch locking. Second, arbitrary prerelease tags are illegal under PEP 440 anyway, so the mechanism could not survive the port intact. Making prerelease a per-invocation flag satisfies PEP 440 and removes changesets' most-complained-about subsystem in one move. See [Prerelease mode](/concepts/prerelease) and [`molt pre`](/cli/pre).

## Snapshots target a non-PyPI index

**Decision.** Snapshot releases are PEP 440 `.devN` versions built for a **separate index** (a local wheelhouse, TestPyPI, or a private index) by default. Publishing snapshots to PyPI requires an explicit, noisy opt-in.

**Alternative.** Port changesets' `0.0.0-<tag>-<datetime>` scheme with a dist-tag to hide the snapshot from default installs.

**Why.** PyPI has immutable versions, no unpublish, and no dist-tags. changesets' approach depends on all three npm affordances that PyPI lacks: every snapshot would permanently burn a public version number and appear on the project's PyPI page forever, and there is no `latest`-vs-`next` tag to keep it out of a plain `pip install`. Encoding "this is a prerelease" in the version string itself (`.devN`) is strictly safer -- `pip` and `uv` exclude dev versions from resolution by default, so the mechanism cannot be clobbered by a single flagless publish. See [Snapshot releases](/concepts/snapshots).

## Ecosystem and forge seams from day one

**Decision.** Package discovery and version writing sit behind an **ecosystem backend** protocol (uv first; Poetry, Hatch, PDM, setuptools to follow). Forge integration sits behind a **forge** protocol (GitHub first; GitLab, Gitea, and others as additions).

**Alternative.** Hard-wire uv and GitHub, the way changesets hard-wires `package.json` and GitHub.

**Why.** Python has no single workspace standard -- uv, Poetry, Hatch, PDM, and setuptools each express workspaces and intra-repo dependencies differently -- so hard-wiring one would exclude most of the ecosystem. The seam is the very feature changesets rejected in 2020 and has deferred ever since; it is cheap to design in and expensive to retrofit, which is exactly why upstream never added it. Shipping GitHub and uv first keeps scope sane while leaving GitLab and Poetry as additions rather than rewrites. See [Ecosystems](/ecosystems/overview) and [Forges](/forges/overview).

## One distribution, monorepo-ready internals

**Decision.** molt ships as a single distribution, `molt-cli` (the command is `molt`), with internal module boundaries that mirror changesets' package split.

**Alternative.** Replicate changesets' 21-package layout, or split `molt-core` from the CLI immediately.

**Why.** changesets' many-packages split is a JavaScript-cultural artifact -- bundle-size pressure and the npm micro-package norm -- and Python has none of those forces. Twenty-one distributions would mean twenty-one PyPI names, changelogs, and version matrices for a project with one maintainer. A single distribution with clean internal seams keeps the option open: extracting an importable `molt-core` later is a mechanical split, as long as CLI types never leak into the engine (enforced by an import-boundary lint rule). The name `molt-cli` is used because `molt` is squatted on PyPI by a dead 2012 project; the distribution name and the console script are independent, so users still type `molt`. See [What is molt](/introduction/what-is-molt).

## Python floor of 3.11

**Decision.** `requires-python = ">=3.11"`.

**Alternative.** A higher floor (one abandoned Python port required 3.13) or a lower one (3.10 and earlier).

**Why.** 3.11 is where `tomllib` landed in the standard library, so molt reads TOML on the hot path with no extra dependency; it also brings `ExceptionGroup`, `Self`, and faster startup. A release tool has to run inside the CI a project already has, which is frequently pinned to an older interpreter -- so a high floor is a pure adoption tax with no engineering payoff, and is the likely reason the 3.13-floored prior attempt saw no uptake. 3.10 and earlier buy nothing and are EOL-adjacent.

## Config lives in one place; both is an error

**Decision.** Configuration lives in `[tool.molt]` in the root `pyproject.toml`, with a `.molt/config.json` fallback for projects that prefer JSON. If **both** are present, molt errors and asks you to pick one.

**Alternative.** Merge the two sources, or let one silently win.

**Why.** Merging two config sources means defining precedence, and every precedence rule is a subtle footgun -- changesets hit exactly this with its `--ignore`-flag-versus-config-`ignore` conflict, which it also resolved by refusing to combine them. Erroring on two sources keeps configuration unambiguous: there is always exactly one answer to "where does this setting come from." Config being static TOML/JSON (never executable) also means any tool can read it without running your code. See [The config file](/config/config-file).

## Upstream bugs molt does not port

**Decision.** A faithful port keeps faithful behavior -- but molt deliberately does **not** reproduce a set of known changesets bugs. Things that look like divergences here are corrections, made on purpose.

**Alternative.** Port changesets bug-for-bug in the name of fidelity.

**Why.** These are defects the changesets source exhibits today; copying them would import known-wrong behavior into a tool whose entire job is correctness.

| Upstream bug | molt's behavior |
|---|---|
| Nothing is atomic; a mid-run failure leaves packages bumped with changesets still on disk, so re-running `version` **double-bumps** | Buffer-then-flush: all mutations are staged and flushed together, so a failed run leaves the tree untouched and is safely resumable |
| Summary post-processing **strips every line starting with `#`**, destroying Markdown headings in changeset descriptions | Summaries are treated as literal prose; `#` headings survive |
| Special replacement patterns (`$1`, `$&`) in a summary corrupt changelog output | Summaries are never passed through a template engine |
| A `none` release replaced during dependent propagation **loses its changelog summary** | The summary is preserved |
| A skipped/ignored package referenced by a changeset is **silently dropped** | molt emits a warning/diagnostic instead of silently no-op'ing |
| No lockfile update anywhere (cosmetic in npm, load-bearing in Python) | `uv.lock` is updated during `version`, in the same commit |
| A failed `git commit` is only logged; **exit code stays 0** | Explicit git failures fail with a non-zero exit code |
| Changelog Markdown emitted with broken blank lines, repaired by a later formatter pass | Correct Markdown emitted directly, no formatter step |

## A note on git

**Decision.** Implicit git use is best-effort and only ever warns; explicit git use hard-fails. Detecting changed packages for the `add` prompt never errors if git is unavailable; `molt status --since main` does.

**Why.** This crisp rule is one of changesets' better decisions, ported verbatim. The core flow -- add, version, changelog -- works without git at all; git only enters when you ask for it, and only then is its failure your problem.

## Where to go next

- [Comparison with changesets](/reference/comparison-with-changesets) -- these decisions as a feature table.
- [Why molt](/introduction/why-molt) -- the strategic framing.
- [Roadmap](/reference/roadmap) -- how these decisions sequence into a build order.
- [Troubleshooting](/reference/troubleshooting) -- what these decisions look like when they stop you.
