---
title: Molt and changesets
description: A feature-by-feature map of molt and JavaScript changesets, and the ecosystem reasons the two differ.
---

# Molt and changesets

Molt and [changesets](https://github.com/changesets/changesets) implement the same release model for
two different packaging ecosystems. If you know one, this page tells you what carries over and what
does not.

Most of the differences below trace to a single fact: npm and PyPI are not the same kind of index,
and SemVer and PEP 440 are not the same kind of version. A rule that is correct on npm is often
wrong on PyPI, and the reverse. Where that happens, each tool does the right thing for its own
ecosystem.

Molt follows changesets v3 semantics. Material written about v2, which is still most of what you
will find online, describes older behavior.

## Legend

| Mark | Meaning |
|---|---|
| ✅ | Supported |
| ⬜ | Not supported |
| ➖ | The concept does not exist in that ecosystem |
| 🚫 | Out of scope, by decision |

## The workflow

The core loop is the same in both tools, and the muscle memory transfers.

| Capability | molt | changesets | Notes |
|---|---|---|---|
| Changeset file: Markdown body, YAML front matter | ✅ | ✅ | Same [format](/config/changeset-format). Molt matches package names under PEP 503 normalization, so `Acme_Core` and `acme-core` are one package |
| Record intent now, consume it in a batch | ✅ | ✅ | The [two-clock model](/introduction/the-changeset-workflow) |
| Flatten to the highest bump, keep every summary | ✅ | ✅ | Three changesets become one release at the highest bump |
| Intent lives in a committed file, not in git history | ✅ | ✅ | Squash- and rebase-safe in both |
| Empty changeset to satisfy a CI gate | ✅ | ✅ | `molt add --empty` |
| Interactive prompt to write a changeset | ✅ | ✅ | |
| Write a changeset with no prompt | ✅ | ⬜ | `molt add --minor acme-core -m "..."`. What makes Dependabot, Renovate, and code generators able to open a complete pull request. See [molt add](/cli/add) |
| Seed changesets from commit history, once, at adoption | ✅ | ⬜ | A [migration aid](/guides/migrating-from-changesets), not an ongoing mode |

## Versions

| Capability | molt | changesets | Notes |
|---|---|---|---|
| Version and range math | PEP 440 + PEP 508 | SemVer + node-semver | Molt uses [`packaging`](/concepts/versioning-pep440), so its answer matches what `pip` and `uv` resolve |
| `major` / `minor` / `patch` bump types | ✅ | ✅ | Same three words |
| Prerelease versions | ✅ | ✅ | Molt: `1.0.0rc1`, `a1`, `b2`, `.dev3`. changesets: any tag, such as `1.0.0-next.0` |
| Arbitrary prerelease tag names | ➖ | ✅ | PEP 440 defines a closed vocabulary, so `1.0.0-canary.1` has no Python spelling |
| Prerelease as a persistent repository mode | 🚫 | ✅ | changesets uses `pre enter` / `pre exit` and a `pre.json`. Molt uses [`molt version --pre rc`](/concepts/prerelease) per run, with no state to enter, commit, or exit |
| Epochs, post-releases, local versions | ✅ | ➖ | PEP 440 shapes with no SemVer equivalent |
| Snapshot releases | ✅ | ✅ | Different mechanics: see the publishing table |

## Monorepos

Both tools compute a release plan across a workspace. This is the part that is the same idea and a
different implementation.

| Capability | molt | changesets | Notes |
|---|---|---|---|
| Dependent propagation to a fixpoint | ✅ | ✅ | The [three-pass loop](/concepts/release-plan). Pass order is observable and load-bearing in both |
| Rewrite a dependent's constraint on the bumped package | ✅ | ✅ | Molt splices the specifier inside a PEP 508 string; changesets edits a `package.json` field |
| Release a dependent only when the new version leaves its range | ✅ | ✅ | |
| Dependency bumps render as a patch, last in the section | ✅ | ✅ | Add a separate changeset if you want a louder entry |
| [`fixed` and `linked`](/concepts/linked-vs-fixed) groups | ✅ | ✅ | Same semantics, clearer names |
| [`ignore`](/config/options) list | ✅ | ✅ | |
| Single-package repository as a first-class path | ✅ | ✅ | Molt makes it the default path, since most Python projects ship one package |
| `peerDependencies` bump rules | ➖ | ✅ | Python has no peer dependencies. Extras (`foo[bar]`) behave like ordinary dependencies |
| Lockfile refreshed when versions change | ✅ | ⬜ | A stale `uv.lock` fails any `--frozen` or `--locked` install, so molt runs `uv lock` in the same commit. npm lockfiles do not carry workspace versions the same way |
| Comment- and format-preserving manifest edits | ✅ | ➖ | `pyproject.toml` is hand-maintained and holds comments. JSON has none to lose |
| Package discovery behind a swappable backend | ✅ (uv) | ⬜ | Python has five workspace conventions where JavaScript has essentially one, so the seam is worth its cost on one side and not the other. See [Ecosystems](/ecosystems/overview) |

## Publishing

| Capability | molt | changesets | Notes |
|---|---|---|---|
| Build artifacts, then upload | ✅ | ✅ | `molt build` then `molt publish` |
| Publish in dependency order | ✅ | ✅ | |
| Trusted publishing over OIDC | ✅ | ✅ | |
| Publish a subset | ✅ | ✅ | `molt publish --filter` |
| Snapshot to a separate index | ✅ | ➖ | PyPI versions are permanent, so molt sends `.devN` snapshots to a wheelhouse, TestPyPI, or a private index by default. See [Snapshots](/concepts/snapshots) |
| Snapshot hidden behind a dist-tag | ➖ | ✅ | PyPI has no dist-tags. `.devN` in the version string does the same job, and `pip` and `uv` skip dev versions by default |
| Unpublish a bad release | ➖ | ✅ | npm allows it inside a window. PyPI does not |
| [Yank a bad release](/cli/yank) | ✅ | ➖ | PEP 592. `molt yank` checks the version, reports whether it is already yanked, and prints the steps. PyPI publishes no yank API, so the last click is yours |
| Per-package publish access setting | ➖ | ✅ | PyPI has no per-package access flag. Choose an index with `molt publish --repository` |
| Annotated git tags per released package | ✅ | ✅ | `molt git-tag` |

## Changelogs

| Capability | molt | changesets | Notes |
|---|---|---|---|
| One `CHANGELOG.md` per package | ✅ | ✅ | |
| Pluggable generator | ✅ | ✅ | Molt resolves generators through Python [entry points](/extending/changelog-plugins) |
| Author and pull-request attribution | ✅ | ✅ | |
| [Templates](/guides/changelog-templates) for sections, dates, and layout | ✅ | ⬜ | Jinja2. changesets generators return strings, so layout is fixed |
| Markdown that needs no formatter pass afterwards | ✅ | ⬜ | Molt emits the blank lines correctly rather than repairing them later |

## Automation

| Capability | molt | changesets | Notes |
|---|---|---|---|
| A release pull request kept in sync with pending changesets | ✅ | ✅ | |
| Host releases, one per package | ✅ | ✅ | |
| Signed commits made through the host API | ✅ | ✅ | [`commit-mode: api`](/guides/ci-github-action#signed-commits) |
| GitHub | ✅ | ✅ | |
| GitLab, Gitea, Bitbucket, Azure DevOps | ⬜ | ⬜ | Both tools ship a GitHub backend. Molt keeps host calls behind a [protocol](/forges/overview), so a second host is a backend rather than a rewrite. The core loop already runs on any CI |
| A machine-readable plan on every mutating command | ✅ | ⬜ | `--dry-run` prints it and writes nothing. See [Dry runs and plans](/guides/dry-run-and-plans) |
| All-or-nothing `version` | ✅ | ⬜ | Molt buffers every write and flushes them together, so an interrupted run leaves the tree untouched and re-runs safely |
| Rate-limit handling, retry, and backoff on host calls | ✅ | ⬜ | Molt honors `Retry-After`, retries transient `5xx` with jittered backoff, and reports when a rate limit resets |
| Windows | ✅ | ✅ | Molt runs its test suite on Windows |

## Out of scope for molt

These are decisions, not gaps:

- **Versions derived from commit messages.** A commit is not a release intent, and a prefix
  convention cannot see a breaking change that crosses a package boundary. Molt reads commits once,
  at adoption, to [seed changesets](/guides/migrating-from-changesets).
- **Executable configuration.** Config is TOML or JSON so any tool can read it without running your
  code.
- **Shell hooks as the main extension point.** Extension goes through typed Python
  [entry points](/extending/changelog-plugins).
- **One pull request per package**, bespoke per-build-tool integrations, and chat bots.

## See also

- [Migrating from changesets](/guides/migrating-from-changesets) -- the practical port guide.
- [Design decisions](/reference/design-decisions) -- the reasoning behind each divergence.
- [Acknowledgements](/reference/acknowledgements) -- what molt is built on.
