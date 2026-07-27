---
title: Yanking a release
---

# Yanking a release

Yanking marks a published version as bad so installers stop selecting it, without breaking the people already pinned to it -- the recovery path PyPI's immutability would otherwise deny you. `molt yank` verifies the version and walks you through it.

```bash
# Check the release and get the exact steps
molt yank acme-core 1.2.0 --reason "Corrupt wheel; use 1.2.1."
```

> **The yank itself is a manual step.** PyPI exposes no API for yanking -- it is a web-UI action, `twine` has no yank command, and an upload token would not authorize one. `molt yank` checks that the version exists, tells you whether it is already yanked, and prints the management URL and steps; you finish it in the browser. [`molt yank`](/cli/yank) explains the constraint in full.

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

Because a yank changes what every downstream resolver sees, get the target right before you touch the browser. That is what `molt yank` is for -- it queries the index and tells you what is actually there:

```bash
molt yank acme-core 1.2.0 --reason "Corrupt wheel; use 1.2.1."
```

It confirms the version exists, reports whether it is **already yanked** and with what reason, and prints the release-management URL plus the steps to complete. Getting the version number wrong is the realistic failure here -- yanking `1.2.0` when you meant `1.2.1` takes a *good* release out of circulation -- and a bad version number fails the check with exit 1 instead of loading a page where one wrong click does damage.

Two things to know before you click:

- **You must be an Owner** of the project, signed in with 2FA. A Maintainer cannot yank.
- **A yank is reversible.** An un-yank restores the version to normal selection, so this is far less destructive than the delete PyPI refuses to offer. Run `molt yank acme-core 1.2.0 --undo` for those steps. Still, treat it as a public, visible change to your package's history.

## See also

- [`molt yank`](/cli/yank) -- flags, confirmation behavior, and exit codes.
- [Publishing](/guides/publishing) -- why PyPI immutability shapes the whole publish flow.
- [Why Molt?](/introduction/why-molt) -- how PyPI's constraints become a feature changesets structurally cannot offer.
