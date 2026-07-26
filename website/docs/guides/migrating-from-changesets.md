---
title: Migrating from changesets
---

# Migrating from changesets

If you already run JS changesets, the model transfers directly -- molt keeps the changeset file, the two-phase workflow, and most config keys, and changes only what Python's packaging rules force it to.

molt is a faithful port, so the muscle memory carries over: you still `add` a changeset per change and `version` in a batch at release time. What differs are the places where PyPI and PEP 440 genuinely work differently from npm and SemVer, plus a handful of subsystems molt deletes because they have no Python meaning. This guide maps the concepts and lists the concrete differences. For a line-by-line feature table, see [Comparison with changesets](/reference/comparison-with-changesets).

## What transfers unchanged

- **Changeset files.** molt reads the same `.changeset/*.md` format -- YAML front matter mapping package names to bump types, then a Markdown summary. Your existing changesets are compatible in spirit; the only substantive change is that names are matched using [PEP 503 normalization](/config/changeset-format) (`Acme_Core` matches `acme-core`), which changesets never needed.
- **The workflow.** `add` -> `version` -> `publish` is the same loop, with the same "add intent while it is fresh, consume it in a batch" split.
- **Config keys, in camelCase.** molt's config lives in `[tool.molt]` in `pyproject.toml`, but it accepts your changesets keys via aliasing. `baseBranch`, `updateInternalDependencies`, `fixed`, `linked`, `ignore` are all understood alongside their snake_case spellings (`base_branch`, and so on), so you can port a config table mostly by pasting it. See [The config file](/config/config-file).
- **`fixed` and `linked` groups, `status`, `--since`, dry runs.** All present, with the same semantics.

## What changed, and why

### Versions and prereleases follow PEP 440

molt does version math with the [`packaging`](https://packaging.pypa.io) library -- the PyPA reference implementation of PEP 440 and PEP 508 -- not SemVer. Bump *types* are still `major`/`minor`/`patch`, but:

- A prerelease is `1.0.0rc0`, `1.0.0a1`, or `1.0.0.dev3` from a **fixed** vocabulary, not an arbitrary `1.0.0-next.0` tag.
- Range matching uses PEP 440 prerelease scoping, so a dependent that already opted into prereleases is not force-released as its dependency moves `rc0 -> rc1`. This is a deliberate divergence from node-semver; see [Versioning and PEP 440](/concepts/versioning-pep440).

### No `pre.json` -- prerelease is a flag

changesets' single most-disliked subsystem is `pre.json`: entering prerelease mode writes persistent, repo-global branch state that everyone in the repo then trips over. molt has no such file. Prerelease is an **invocation flag**:

```bash
# changesets
changeset pre enter next
changeset version
# ... later ...
changeset pre exit
changeset version

# molt
molt version --pre rc     # cut release candidates this run
molt version              # back to normal -- no state to exit, nothing to clean up
```

There is no mode to enter, no file to commit, and no branch to unblock. See [Prerelease mode](/concepts/prerelease) and [`molt pre`](/cli/pre).

### Snapshots target a non-PyPI index

changesets leans on npm's throwaway-version-plus-dist-tag trick for snapshots. PyPI has immutable versions and no dist-tags, so every snapshot would permanently burn a public version number. molt therefore builds snapshots as PEP 440 `.devN` versions and targets a **separate index** (a wheelhouse, TestPyPI, or a private index) by default -- never PyPI unless you loudly opt in. See [Snapshot releases](/concepts/snapshots).

### `molt yank` replaces "unpublish"

PyPI has no unpublish. The sanctioned recovery for a bad release is [PEP 592 yanking](https://peps.python.org/pep-0592/): the release stays installable for anyone already pinned to it, but resolvers stop selecting it. molt makes this a first-class verb, [`molt yank`](/cli/yank) -- a recovery path changesets structurally cannot offer.

### `tag` is now `git-tag`

changesets renamed `tag` to `git-tag` in v3 because "tag" collided with npm dist-tags. molt uses `git-tag` from the start. See [`molt git-tag`](/cli/git-tag).

### peerDependencies are gone

Python has no `peerDependencies`, so molt drops the concept entirely -- along with the experimental `onlyUpdatePeerDependentsWhenOutOfRange` flag. Extras (`foo[bar]`) are the nearest analogue and behave like ordinary dependencies. One whole class of changesets complexity disappears.

## What molt does differently, at a glance

| changesets | molt | Why |
|---|---|---|
| SemVer + node-semver | PEP 440 + PEP 508 via `packaging` | Python is not SemVer |
| `1.0.0-next.0` prerelease tags | `1.0.0rc0` / `a1` / `.dev3` (fixed vocabulary) | Arbitrary tags are illegal under PEP 440 |
| `pre.json` mode | `molt version --pre <kind>` flag | Removes the most-disliked subsystem |
| `--snapshot` -> npm dist-tag | `.devN` on a separate index | PyPI is immutable, has no dist-tags |
| unpublish / deprecate | [`molt yank`](/cli/yank) | PyPI has no unpublish |
| `tag` command | `git-tag` command | Name collided with dist-tags |
| `peerDependencies` bump rules | dropped | No Python analogue |
| `package.json` (JSON, no comments) | `pyproject.toml` (TOML, comment-preserving) | molt round-trips your comments and formatting |
| lockfile left stale | `uv.lock` updated during `version` | Stale Python lockfiles break `--frozen` CI |
| formatter pass (Prettier/dprint) | correct Markdown emitted directly | No formatter toolchain required |
| GitHub-only automation | forge is a seam ([GitHub](/forges/github) first) | GitLab/Gitea are additions, not rewrites |

## Seeding changesets from your history

Adopting molt mid-project leaves you with commits that predate any changesets. molt offers a one-time **seeding** step that proposes changeset files from your recent commit history, which you then edit and commit:

```bash
molt add --from-commits <since-ref>
```

This is a migration aid and nothing more. molt does **not** derive versions from commit messages on an ongoing basis -- that is a deliberately [refused anti-feature](/introduction/why-molt), because a commit is not a release intent and conventional-commit prefixes cannot see breaking changes that cross package boundaries. Seeding gets you a starting pile of changesets; from there, the normal `molt add` flow takes over.

## A suggested migration order

1. Move `.changeset/config.json` settings into `[tool.molt]` (camelCase keys are accepted, so this is mostly a paste).
2. Confirm your [ecosystem backend](/ecosystems/overview) -- uv, Poetry, Hatch, PDM, or setuptools -- discovers your workspace members.
3. Keep any pending `.changeset/*.md` files; molt reads them as-is.
4. Replace `pre enter`/`pre exit` habits with `molt version --pre`.
5. Repoint snapshot publishing at a non-PyPI index.
6. Optionally seed changesets for in-flight work with `molt add --from-commits`.

## Where to go next

- [Comparison with changesets](/reference/comparison-with-changesets) -- the full parity and divergence table.
- [Prerelease mode](/concepts/prerelease) and [`molt pre`](/cli/pre) -- the flag that replaces `pre.json`.
- [Snapshot releases](/concepts/snapshots) -- why they target a separate index.
- [Design decisions](/reference/design-decisions) -- the rationale behind each divergence.
