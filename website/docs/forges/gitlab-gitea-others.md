---
title: "GitLab, Gitea & others"
---

# GitLab, Gitea & others

GitLab, Gitea, Bitbucket, and Azure DevOps are planned forge backends -- the seam for them exists today, and this page is honest about what that means and what is not shipped yet.

## Where things stand

Molt ships [GitHub](/forges/github) as the first-class forge backend. GitLab, Gitea, Bitbucket, and Azure DevOps are **planned**, not shipped. What exists now is the [forge protocol](/forges/overview) they will implement -- the seam that keeps pull-request creation, release notes, and commit attribution out of the engine and behind a backend.

For exactly which backends are available, in progress, or on the shelf in the version you are running, the [roadmap](/reference/roadmap) is the source of truth. This page describes the shape of the work, not a ship date.

## Why the seam is already there

Building the abstraction on day one is the whole point. In changesets, forge support is [hard-wired to GitHub](/forges/overview#why-the-forge-is-a-seam): the request for GitLab support ([#879](https://github.com/changesets/changesets/issues/879)) drew zero maintainer replies over four years, and "GitLab support?" was the one and only open issue on the abandoned Python port, pychangeset. Both stalled because retrofitting a forge abstraction into a GitHub-shaped codebase is a rewrite.

Molt avoids that trap by designing the seam in from the start. Adding GitLab is implementing a backend against a protocol GitHub already satisfies -- attribution, release publication, and the release-PR (or merge-request) lifecycle -- not re-architecting the release engine. A new forge is additive.

## What a new backend has to answer

Any forge backend implements the same host-agnostic contract described in the [forges overview](/forges/overview): resolve the change request and author behind a commit (GitLab merge requests, Gitea/Bitbucket pull requests), create a release from a tag and a changelog body, and open or update the release change-request for a branch. The [CI loop](/guides/ci-github-action) and the [changelog generators](/guides/changelog-templates) call that contract without caring which host is underneath.

The generic caching and backoff behavior from the [overview](/forges/overview#caching-and-backoff-done-right) is part of the shared client, so a new backend inherits real request-deduplication and retry rather than reinventing them.

## Using molt on an unsupported host today

You do not need a native forge backend to release with molt. The core loop -- [`molt version`](/cli/version), [`molt publish`](/guides/publishing), and the tags it pushes -- is forge-independent and runs anywhere uv is installed, on any CI. A native backend is what adds host-specific niceties (the release change-request, host releases, rich attribution); without one, you still get correct versioning, changelogs, and PyPI publishing, and you can wire the change-request step into your own CI. See [CI: GitHub Action](/guides/ci-github-action#molt-is-not-tied-to-github-actions) for the portable, forge-agnostic path.

## See also

- [Forges overview](/forges/overview) -- the protocol these backends will implement.
- [GitHub](/forges/github) -- the first-class backend and the reference implementation.
- [Roadmap](/reference/roadmap) -- the authoritative, current status of each planned forge.
