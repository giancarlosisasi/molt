---
title: Publishing
---

# Publishing

Build and upload exactly the packages whose versions changed, in dependency order, over PyPI's Trusted Publishing -- with an exhaustive pre-flight check that runs before a single file is uploaded.

Publishing is the last step of the release loop. By the time you get here, [`molt version`](/cli/version) has already bumped the manifests, written the changelogs, and updated the lockfile; the new versions are committed. `molt publish` takes that committed state, works out what is not yet on PyPI, builds it, and uploads it.

```bash
# The whole thing, for a single package or a monorepo
molt publish
```

## The three-stage pipeline

Under the hood, `molt publish` is a pipeline you can also drive stage by stage:

1. **Plan** -- query PyPI for each package to decide what actually needs uploading, then order the work by the dependency graph. `molt publish --dry-run` prints this [plan](/guides/dry-run-and-plans) and uploads nothing.
2. **Build** -- build an sdist and a wheel per package. [`molt build`](/cli/pack) does this on its own and writes the artifacts (plus a checksum for each) into an out directory.
3. **Publish** -- upload the built artifacts, then git-tag each successfully published package.

Running `molt publish` on its own does all three. Splitting them matters in CI, where you want to build in an unprivileged job and upload from a separate job that holds the publish credential:

```bash
# Decide once, on any machine
molt publish-plan --output publish-plan.json

# Build once, in a low-privilege job
molt build --from-publish-plan publish-plan.json --out-dir dist/

# Upload the prebuilt artifacts from a job that has the OIDC grant
molt publish --from-pack-dir dist/
```

The build stage writes an enriched `publish-plan.json` into the out directory, which is what `--from-pack-dir` reads: the upload job never recomputes the plan, so it uploads exactly the artifacts that were reviewed.

The command is `molt build`, not `molt pack` -- `build` is what the Python packaging ecosystem calls this stage, and there is no `pack` alias. See [`molt publish`](/cli/publish) and [`molt build`](/cli/pack) for the full flag surface.

## Trusted Publishing over OIDC -- no long-lived token

Molt authenticates to PyPI with **Trusted Publishing (OIDC)**. In CI, the runner mints a short-lived identity token, exchanges it for a one-time PyPI upload token, and uploads with it. There is no API token to store as a secret, nothing long-lived to leak, and nothing to rotate.

This is also where a whole subsystem from changesets simply disappears. On npm, `changeset publish` has to thread 2FA through the upload: an `--otp` flag, interactive OTP prompts, web-auth challenges, and concurrency that drops to one while it waits for you to type a code. **PyPI has no 2FA-at-publish**, so molt has none of that machinery. There is nothing to prompt for mid-run -- a publish either has a valid credential before it starts, or it fails before it uploads anything.

A long-lived API token still works for local or non-CI publishes; OIDC is the default and the recommended path for automation. See [CI: GitHub Action](/guides/ci-github-action) for the `id-token: write` permission that enables it.

## Exhaustive pre-flight validation

PyPI is **immutable**: a version, once uploaded, can never be replaced or removed. In a monorepo that publishes several packages at once, this has a sharp edge -- if package three of five fails to upload, the two that already succeeded **cannot be rolled back**. You are left with a half-published release.

Changesets validates per package as it goes. Molt refuses to, precisely because PyPI cannot undo a partial publish. Instead, molt validates the **entire plan before the first upload**:

- every target package name is valid and resolvable,
- every version to be published is not already on the index,
- every artifact built cleanly and its checksum is intact,
- the publish credential is reachable and accepted.

Only when the whole plan passes does the first byte go up. This turns "the run failed halfway" from a data-loss event into a no-op you can safely retry.

## Publishing is idempotent

Even with pre-flight checks, two runs can race (a merge lands between plan and upload). Molt handles this the same way it handles a clean retry: if PyPI rejects an upload with **400 "File already exists"**, molt treats that package as **already published and skips it** -- it does not fail the run. Re-running `molt publish` after a partial failure uploads only what is still missing and no-ops on the rest.

If there is genuinely nothing to publish -- every planned version is already on the index -- `molt publish` exits cleanly and tags nothing.

## Topological publish order

Molt uploads in **dependency order**: a package is never published before the packages it depends on. Given `acme-cli` depending on `acme-core`, molt publishes `acme-core` first, then `acme-cli`. Packages with no dependency relationship between them are grouped so they can go up together; a dependency **cycle** is collapsed into a single group and published as a unit. This ordering is the same monorepo reasoning that powers the [release plan](/concepts/release-plan), reused for upload.

If an upload fails, molt **stops** rather than pressing on -- the pre-flight check is the primary defense, and stop-on-failure is the backstop that keeps a downstream package from publishing against a dependency that never made it.

## Publishing a subset with `--filter`

`molt publish --filter` restricts the run to a subset of packages by name or glob. Unlike changesets, whose publish flow is coupled to its GitHub Action, molt's `--filter` works anywhere -- your laptop, a cron job, a non-GitHub CI:

```bash
# Publish only the core package and anything under the acme- prefix
molt publish --filter acme-core --filter 'acme-*'
```

Molt still resolves the dependency order within whatever the filter selects.

## Tags and machine-readable output

After each successful upload molt creates an annotated git tag:

- monorepo package: `acme-core@1.2.0`
- single-package project: `v1.2.0`

Pass `--output` to write a newline-delimited JSON stream of `git-tag` events -- one line per tag -- for a downstream step to consume. Tag-only packages (private packages you version but do not upload) are tagged without being published.

## Snapshots go to a separate index

Do **not** publish snapshot builds to PyPI. Because every version is permanent, a throwaway snapshot would burn a public version number forever, and PyPI has no dist-tag to hide it behind.

Molt does not silently reroute a snapshot -- it cannot invent the URL of your private index -- so it **refuses** instead. A release whose version has the snapshot shape aborts the run before molt reads the index and before it writes any output file:

```
Refusing to publish a snapshot release to PyPI: acme-core 0.0.0.dev20211213000730.
```

Name an index and the guardrail clears:

```bash
molt publish --repository snapshots
```

See [Snapshot releases](/concepts/snapshots) for how snapshot versions are built and [`molt publish`](/cli/publish) for the exact trigger shape.

## When a release goes wrong

You cannot unpublish from PyPI, but you are not stuck with a bad release either. PEP 592 lets you **yank** a version: resolvers stop selecting it while existing pins keep working. Molt exposes this as a first-class verb -- see [Yanking a release](/guides/yank) and [`molt yank`](/cli/yank).

## See also

- [`molt publish`](/cli/publish) -- every flag, exit code, and the plan format.
- [`molt pack`](/cli/pack) -- building sdists and wheels on their own.
- [CI: GitHub Action](/guides/ci-github-action) -- the OIDC permissions and the publish-on-merge loop.
- [Snapshot releases](/concepts/snapshots) -- publishing throwaway versions safely.
- [Yanking a release](/guides/yank) -- the recovery path when a published version is bad.
