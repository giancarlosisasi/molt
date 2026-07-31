---
title: GitHub
---

# GitHub

The GitHub backend is molt's first-class forge: it attributes changes to pull requests and authors with hand-written GraphQL over httpx, creates GitHub Releases, and does both with a cache and backoff that changesets' client lacks.

## What it does

The GitHub backend implements the [forge protocol](/forges/overview) against GitHub's API:

- **Attribution.** Resolves the pull request and author behind each commit, so [changelog templates](/guides/changelog-templates) can link the PR (`[#1613](...)`), credit the author (`Thanks [@author](...)!`), and link the commit.
- **Releases.** After a successful [publish](/guides/publishing), creates one GitHub Release per package, tagged with the package's tag and carrying its changelog entry as the body.
- **Release pull request.** Opens and updates the "Version Packages" PR that the [CI loop](/guides/ci-github-action) keeps in sync.

## Attribution via GraphQL

Molt asks GitHub for attribution with a small, hand-written GraphQL query over `httpx` -- not a heavyweight REST client. Each distinct commit or pull request costs exactly one request; a repeated lookup for the same commit or pull request is answered from the per-instance cache instead of asking GitHub again, so a changelog with several lines pointing at the same commit fetches it once. For each commit it fetches the associated pull requests and authors; the **PR author is preferred over the raw commit author**, and when several PRs are associated, the earliest-merged one wins -- so a change is credited to the human who proposed it.

Because this runs over plain `httpx`, GitHub Enterprise Server is supported by pointing molt at your instance (see configuration below) -- no separate code path.

## Authentication

The GitHub backend authenticates its API calls with a token from the `GITHUB_TOKEN` environment variable, sent as a GitHub `Authorization` token. In [GitHub Actions](/guides/ci-github-action) the built-in `secrets.GITHUB_TOKEN` is enough for attribution, the release PR, and creating Releases. Locally, a personal access token with read scopes covers attribution.

Note that this token is **only** for talking to GitHub. Uploading to PyPI is a completely separate credential and uses [OIDC Trusted Publishing](/guides/publishing#trusted-publishing-over-oidc----no-long-lived-token) -- there is no PyPI token in the mix.

## Configuration

The backend resolves its endpoints and repository from the environment, which CI already sets:

- `GITHUB_REPOSITORY` -- the `owner/name` slug the changelog and releases target. Repository names are validated against `owner/name` form.
- `GITHUB_TOKEN` -- the API token described above.
- `GITHUB_SERVER_URL` -- defaults to `https://github.com`; override for GitHub Enterprise Server.
- `GITHUB_GRAPHQL_URL` -- defaults to `https://api.github.com/graphql`; override for GHES.

For local runs, molt also reads these from a `.env` file if present. The GitHub-flavored [changelog generator](/guides/changelog-templates) can take an explicit `repo` in its options instead of relying on `GITHUB_REPOSITORY`.

## Caching and resilience

The GitHub backend keys its attribution cache by `(kind, repository, id)`, so a commit or PR referenced by many changelog lines is fetched exactly once per release. It honors `Retry-After`, retries transient `5xx` responses with jittered backoff, sets an explicit request timeout, and reports an actionable error (with the reset time) when the rate limit is exhausted. These are deliberate fixes over changesets' client, whose cache never hits and which has no retry logic at all -- see [Forges overview](/forges/overview#caching-and-backoff-done-right).

## See also

- [Forges overview](/forges/overview) -- the protocol this backend implements.
- [CI: GitHub Action](/guides/ci-github-action) -- the release loop and its permissions.
- [Changelog templates](/guides/changelog-templates) -- what attribution renders into.
- [GitLab, Gitea & others](/forges/gitlab-gitea-others) -- the planned backends behind the same seam.
