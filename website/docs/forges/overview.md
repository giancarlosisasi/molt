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
- **Release publication.** Given a tag and a changelog body, create a release on the host.
- **Pull request lifecycle.** Find the existing release PR for a branch and update it in place, or open a new one.

The engine, the changelog generators, and the CI loop call these; they never assume the host is GitHub.

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
