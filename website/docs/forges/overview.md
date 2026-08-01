---
title: Forges overview
---

# Forges overview

Molt talks to your code host through a **forge backend** -- a seam that hides how a given host creates pull requests, publishes release notes, and attributes commits, so release automation is not welded to any one platform.

## Why the forge is a seam

Changesets is hard-wired to GitHub. Its release automation, its author attribution, and its release notes all assume the GitHub API. Asking for another host has been an open request for years -- ["GitLab support?"](https://github.com/changesets/changesets/issues/879) sat with zero maintainer replies for four years, and it was also the single open issue on the abandoned Python port, pychangeset.

Molt abstracts the forge from day one. The parts of the release loop that touch a code host go through a backend protocol rather than calling GitHub directly:

- **Pull request creation** -- opening and updating the "Version Packages" release PR.
- **Release notes** -- turning a package's changelog entry into a host-native release.
- **Commit and author attribution** -- resolving which PR and which author a change came from, so changelogs can credit contributors.

GitHub ships first and is the most polished backend. But because the seam exists, adding another host is writing a backend, not rewriting the engine. See [Why Molt?](/introduction/why-molt) for where this sits among molt's deliberate additions.

## What a forge backend provides

A forge backend answers a small, host-agnostic set of questions:

- **Attribution.** Given a commit, which pull request introduced it, and who authored it? Given a pull request number, its author and merge commit? This is what lets [changelog templates](/guides/changelog-templates) render `Thanks @author!` and link back to the PR.
- **Release publication.** Given an existing tag, a name and a changelog body, create a release on the host -- one per released package, with that package's changelog entry as the body. This is implemented today.
- **Pull request lifecycle.** Find the existing release PR for a branch and update it in place, or open a new one. Not implemented yet.

The engine, the changelog generators, and the CI loop call these; they never assume the host is GitHub.

### Two details of release publication worth knowing

**A release the host already carries is not a failure.** Creating a release for a tag that already has one reports "nothing created" instead of raising, so re-running a publish that half-failed completes the releases that are missing rather than failing on the ones that already went out.

**The prerelease flag follows PEP 440, not npm semver.** `1.0.0rc1`, `1.0.0a1`, `1.0.0b2`, `1.0.0.dev1` and every `molt version --snapshot` version are published as prereleases; `1.0.0` and a post-release such as `1.0.0.post1` are not, and neither is a local version such as `1.0.0+local.build`. If you are migrating from changesets, this is a deliberate difference: it marks a release as a prerelease when the version string contains a hyphen, and no PEP 440 prerelease contains one.

Note that molt does not yet *call* this itself. A backend can create a release today; the release loop that will drive it -- one release per package, after the tags are pushed -- is part of the [CI action](/guides/ci-github-action) still being built.

## Caching and backoff, done right

The forge backend is also where molt fixes two real bugs in changesets' GitHub client.

- **A cache that actually caches.** Changesets' attribution client is documented as caching results but never does -- its cache is keyed by object identity, so every lookup misses and the same commit is refetched every time. Molt keys its cache by `(host, kind, repository, id)`, so a commit or PR is fetched once per release no matter how many changelog lines reference it.
- **Real resilience.** Changesets has no rate-limit handling, no retry, and no backoff -- a throttled request just surfaces as a confusing "missing data" error. Molt honors `Retry-After`, retries transient `5xx` responses with jittered backoff, sets an explicit timeout, and fails with an actionable message when the rate limit is exhausted (including when it resets).

On a large release that generates many changelog lines, these are the difference between one clean batch of API calls and a storm of throttled refetches.

## See also

- [GitHub](/forges/github) -- the first-class forge backend, in detail.
- [GitLab, Gitea & others](/forges/gitlab-gitea-others) -- planned forges and their status.
- [CI: GitHub Action](/guides/ci-github-action) -- the release loop the forge backend drives.
- [Changelog templates](/guides/changelog-templates) -- what author and PR attribution feed into.
