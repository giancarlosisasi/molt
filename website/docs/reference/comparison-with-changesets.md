---
title: Comparison with changesets
---

# Comparison with changesets

A precise, non-marketing map of where molt matches JS changesets, where it deliberately diverges, what it adds, and what it refuses to build.

molt is a faithful port of changesets with documented divergences. This page is the technical reference for exactly which is which. It tracks changesets at `3.0.0-next.9` (the v3 line) and follows v3 semantics, not the older v2 behavior that most online material and both abandoned Python ports describe. If you are coming from changesets, pair this with [Migrating from changesets](/guides/migrating-from-changesets).

## The v3 baseline

molt ports **v3 semantics**. The behaviors below changed between changesets v2 and v3; molt matches the v3 column:

| Behavior | v2 | v3 (what molt follows) |
|---|---|---|
| Peer-dependent propagation | major cascade | patch only -- N/A for molt anyway (no peerDependencies) |
| `version` with no changesets | exit 0 | **exit 1** |
| `tag` command | `tag` | `git-tag` |
| Default base branch | `master` | `main` |
| Legacy v1 changeset format | supported | removed -- molt reads the v2/v3 Markdown format only |
| Publish pipeline | one `publish` step | `pack` -> `publish` split (build artifacts, then upload) |

## Ported faithfully

These behaviors are the reason changesets works, and molt keeps them intact. Getting any of them wrong produces silently incorrect version numbers, so they are validated against ported test fixtures.

| Behavior | Notes |
|---|---|
| Changeset file = Markdown + YAML front matter | Same [format](/config/changeset-format); names matched with PEP 503 normalization |
| Two-phase [add -> version](/introduction/the-changesets-model) | Record intent while fresh; consume in a batch |
| Flatten to the **max** bump; changelog keeps the **union** | Three changesets -> one release at the highest bump, every summary preserved |
| Files on disk -> squash- and rebase-safe | Intent lives in a committed file, not in git history |
| The 3-pass fixpoint [release plan](/concepts/release-plan) | `determineDependents` -> `matchFixedConstraint` -> `applyLinks`, repeated to a fixpoint; order is observable and load-bearing |
| Dependent propagation + range rewriting | Over PEP 508 requirements; a dependent is released when the dependency leaves its declared range |
| Dependency bumps are always a **patch**, rendered **last** in the patch section | Same rule; add a separate changeset for a louder dependent entry |
| [`fixed` and `linked`](/concepts/linked-vs-fixed) groups | Same semantics (with clearer names) |
| `status`, `--since`, JSON output | Reports pending releases without mutating |
| Changelog generator plugins | Two functions per generator; molt owns section layout ([Changelog plugins](/extending/changelog-plugins)) |
| Topological publish ordering | Publish in dependency order |
| Trusted publishing via OIDC | 1:1 with changesets' v3 GitHub workflow |
| Git policy: implicit git = warn, explicit git = fail | Detecting changed packages never hard-fails; `--since main` does |

## Deliberately different

These diverge because Python's packaging rules force a different answer than JavaScript's. Each is intentional and documented in [Design decisions](/reference/design-decisions).

| Area | changesets | molt |
|---|---|---|
| Version + range math | SemVer / node-semver | [PEP 440 + PEP 508](/concepts/versioning-pep440) via `packaging` |
| Prerelease identifiers | arbitrary tags (`-next.0`) | fixed vocabulary (`aN`/`bN`/`rcN`/`.devN`) |
| Prerelease range opt-in | scoped to the same release tuple | scoped across the whole specifier set (PEP 440); dependents that opted in are not force-bumped `rc0 -> rc1` |
| Prerelease mode | `pre.json` repo-global state | [`--pre` invocation flag](/concepts/prerelease); no persistent state |
| Snapshots | throwaway version + npm dist-tag | `.devN` on a [separate index](/concepts/snapshots); never PyPI by default |
| Bad release recovery | unpublish / deprecate | [`molt yank`](/cli/yank) (PEP 592, guided -- PyPI has no yank API) |
| peerDependencies | first-class subsystem | dropped -- no Python analogue |
| dist-tags / `--tag` | central to prerelease + snapshot flows | gone -- PyPI has no dist-tags |
| Dependencies | a map in `package.json` | a list of PEP 508 strings in `pyproject.toml`; range rewriting splices the string |
| Manifest editing | JSON reserialize (no comments to lose) | comment- and format-preserving TOML via `tomlkit` |
| Lockfile | left stale | [`uv.lock` updated](/ecosystems/uv) during `version` |
| Workspace model | one `package.json` universe | [ecosystem backend seam](/ecosystems/overview) (uv, Poetry, Hatch, PDM, setuptools) |
| Forge | hard-wired to GitHub | [forge seam](/forges/overview); GitHub first |
| Vocabulary | "workspace" = one package; `linked`/`fixed` nearly identical | "workspace" = the repo; clearer group names ([Glossary](/concepts/glossary)) |

## What molt adds

Capabilities changesets lacks -- most cheap to build fresh, expensive to retrofit, which is why they were never added upstream. Several are the community's longest-standing unmet requests (see the [Roadmap](/reference/roadmap) for the demand history).

| Addition | changesets status |
|---|---|
| Ecosystem / version-source abstraction | Formally rejected in 2020, re-requested for six years |
| [Lockfile updates during `version`](/ecosystems/uv) | Absent entirely |
| Uniform machine-readable plan object on **every** mutating command; `--dry-run` = plan + print | Partial (`publish-plan` only, v3) |
| [Non-interactive `molt add`](/cli/add) | Not possible today -- unlocks Dependabot/Renovate/codegen |
| [Real changelog templating](/guides/changelog-templates) (Jinja2, dates, sections) | Open 7 years (#109) |
| No `pre.json` -- [prerelease as a flag](/concepts/prerelease) | ~15 open issues trace to `pre.json` |
| [Forge-agnostic](/forges/overview) integration | Open 4 years, zero maintainer comments |
| Single-package + root-workspace as a first-class path | Degenerate special case upstream |
| [`molt yank`](/cli/yank) (guided; the yank itself is a browser step) | Impossible on npm |
| Atomic, resumable `version` (buffer-then-flush) | Double-bumps on a retry after mid-run failure |
| Correct changelog Markdown, [emitted directly](/guides/changelog-templates) | Needs a formatter pass to repair blank lines |
| Windows-correct from day one | Windows CI added only in 2026-07 |

## What molt refuses

A sharp tool says no. These are deliberate non-goals, not backlog items:

- **Full commit-derived versioning.** It destroys the intent-based model; a commit is not a release intent, and conventional-commit prefixes cannot see breaking changes across package boundaries. molt offers one-time [changeset seeding](/guides/migrating-from-changesets) from commits as a migration aid only.
- **Executable config.** Config is TOML/JSON so other tools can read it without running your code.
- **Arbitrary shell hooks as the primary extension point.** Extension happens through Python [entry points](/extending/changelog-plugins).
- **Per-tool bespoke integrations** (Nx/Turbo-style), one-PR-per-package, chat bots, and config-option creep. Being the best Python release tool beats being a mediocre everything-tool.

## Where to go next

- [Migrating from changesets](/guides/migrating-from-changesets) -- the practical port guide.
- [Design decisions](/reference/design-decisions) -- the rationale behind each divergence.
- [Roadmap](/reference/roadmap) -- what is shipped, in progress, and planned.
- [Why molt](/introduction/why-molt) -- the strategic case.
