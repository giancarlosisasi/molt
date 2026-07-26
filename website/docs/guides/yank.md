---
title: Yanking a release
---

# Yanking a release

`molt yank` marks a published version as bad so installers stop selecting it, without breaking the people already pinned to it -- the recovery path PyPI's immutability would otherwise deny you.

```bash
# Pull a broken release out of circulation
molt yank acme-core 1.2.0
```

## Why yank exists

PyPI has **no unpublish**. Once `1.2.0` is uploaded it is there forever -- you cannot replace it, and you cannot delete it. On npm, changesets can lean on `npm unpublish` and dist-tags to walk back a mistake. Molt has neither, so it uses the mechanism PyPI does provide: **yanking**, standardized in PEP 592.

Yanking is the honest answer to "the release is broken and I can't take it back." It is not a workaround bolted on after the fact -- it is a first-class verb, because on PyPI it is the only real recovery tool there is.

## What yanking actually does

A yanked version stays on the index and stays installable, but resolvers treat it as a last resort:

- **New installs skip it.** `pip install acme-core` will not pick a yanked version when any non-yanked version satisfies the request. `1.2.0` yanked means fresh installs resolve to `1.1.x` or `1.2.1` instead.
- **Exact pins still work.** Anyone with `acme-core==1.2.0` in a lockfile or requirements file continues to install `1.2.0`. Yanking does not break reproducible builds that already depend on the bad version -- it only stops *new* resolutions from drifting onto it.

That asymmetry is the whole point. You take a bad release out of the default path for everyone going forward, while the environments that already trusted it keep resolving.

## Yank or re-release?

Yanking removes a version from consideration; it does not ship a fix. In almost every case you do **both**:

1. **Yank the bad version** so nobody new lands on it.
2. **Release a fix** -- add a changeset, run [`molt version`](/cli/version) to cut a higher version (`1.2.1`), and [publish](/guides/publishing) it as the new default.

Reach for yank alone when a version is actively harmful and there is no fix ready yet -- for example a release that leaks a secret, corrupts data, or is fundamentally broken on install. Yank plus a patch is the normal flow; yank alone is the emergency brake.

Do not yank simply to "hide" a version you dislike. If the version installs and works, cutting a higher release is cleaner than yanking a functional one.

## Running a yank safely

Because a yank changes what every downstream resolver sees, `molt yank` **confirms before it acts** -- it shows the exact package and version and asks you to approve. As with every mutating command, `--dry-run` prints the [plan](/guides/dry-run-and-plans) and changes nothing:

```bash
# See what would happen, touch nothing
molt yank acme-core 1.2.0 --dry-run
```

A yank can be reversed on PyPI (an un-yank restores the version to normal selection), so it is a far less destructive action than the delete PyPI refuses to offer -- but treat it as a public, visible change to your package's history and confirm it deliberately.

## See also

- [`molt yank`](/cli/yank) -- flags, confirmation behavior, and exit codes.
- [Publishing](/guides/publishing) -- why PyPI immutability shapes the whole publish flow.
- [Why Molt?](/introduction/why-molt) -- how PyPI's constraints become a feature changesets structurally cannot offer.
