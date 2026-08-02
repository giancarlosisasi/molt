---
title: "GitLab, Gitea & others"
description: Running the molt release loop on a host other than GitHub, and what a native backend adds.
---

# GitLab, Gitea & others

Molt releases from any CI, on any host. A native [forge backend](/forges/overview) is what adds
host-specific behavior on top of that: the release change-request, host releases, and rich author
attribution. [GitHub](/forges/github) is the backend molt ships.

## Releasing without a native backend

The core loop is host-independent. [`molt version`](/cli/version),
[`molt publish`](/guides/publishing), and [`molt git-tag`](/cli/git-tag) read your repository, write
your manifests and changelogs, upload to PyPI, and push tags. None of that calls a code host. It
runs anywhere uv is installed: GitLab CI, Gitea Actions, Jenkins, Buildkite, a cron job, or your
laptop.

```bash
uv tool install molt-release
molt version
git commit -am "Release"
molt publish
```

What you write yourself on a host without a backend is the release change-request step: opening or
updating the merge request that carries the version commit. That is a few lines of your host's own
CLI around `molt version`, and [CI: GitHub Action](/guides/ci-github-action#molt-is-not-tied-to-github-actions)
shows the portable shape.

## What a native backend adds

A backend implements the host-agnostic contract from the [forges overview](/forges/overview):

| Question the backend answers | What it enables |
|---|---|
| Which change request introduced this commit, and who wrote it? | `Thanks @author!` lines and pull-request links in [changelogs](/guides/changelog-templates) |
| Create a release from a tag and a changelog body | One host release per released package |
| Find or open the release change request for a branch | The "Version Packages" merge request, kept in sync |
| Commit a set of files onto a branch through the host API | Commits the host signs, without a signing key in CI |

GitLab merge requests, Gitea and Bitbucket pull requests, and Azure DevOps pull requests all fit
this shape. The generic caching, retry, and backoff behavior from the
[overview](/forges/overview#caching-and-backoff) lives in the shared client, so a backend inherits
it rather than reimplementing it.

## See also

- [Forges overview](/forges/overview) -- the protocol and what each member does.
- [GitHub](/forges/github) -- the reference implementation.
- [CI: GitHub Action](/guides/ci-github-action) -- the release loop, and the portable form of it.
