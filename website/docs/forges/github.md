---
title: GitHub
---

# GitHub

The GitHub backend attributes changes to pull requests and authors with hand-written GraphQL over httpx, creates GitHub Releases, and does both behind a request cache and a backoff policy.

## What it does

The GitHub backend implements the [forge protocol](/forges/overview) against GitHub's API:

- **Attribution.** Resolves the pull request and author behind each commit, so [changelog templates](/guides/changelog-templates) can link the PR (`[#1613](...)`), credit the author (`Thanks [@author](...)!`), and link the commit.
- **Releases.** Creates a GitHub Release for a package's tag, carrying its changelog entry as the body. The [CI action](/guides/ci-github-action) creates one per published package.
- **Release pull request.** Opens and updates the "Version Packages" PR that the [CI loop](/guides/ci-github-action) keeps in sync.
- **Commits.** Creates a commit on a branch through GitHub's API, which is what [`commit-mode: api`](/guides/ci-github-action#signed-commits) uses so that GitHub signs the release commit.

## Which transport each call uses

Molt uses **both** of GitHub's APIs, and the split is GitHub's rather than molt's:

| Call | Transport | Why it cannot be the other one |
|---|---|---|
| Attribution | GraphQL | One request answers commit, pull request and author together. |
| Creating a commit | GraphQL | `createCommitOnBranch` is the only GitHub API that authors a multi-file commit **server-side**, which is what makes it signed and marked *Verified*. The REST git-database path builds the commit object on the client, so there is nothing for GitHub to sign. |
| Creating a release | REST | GitHub's GraphQL schema has no release-creation mutation at all -- `POST {api}/repos/{owner}/{name}/releases`. |
| The release pull request | REST | Listing, opening and updating a pull request are all REST. |
| Moving the release branch's ref | REST | GraphQL has no ref mutations. |

None of them is a second, weaker client: the same token, the same timeout, the same bounded jittered retry and the same `Retry-After` handling apply to every one of them.

Two behaviors to expect from **release creation**. A tag that already has a release is reported as "nothing created" rather than as an error, so re-running a half-failed publish finishes the job instead of failing on what already succeeded. And the prerelease flag follows PEP 440 -- `1.0.0rc1`, `1.0.0a1` and every snapshot version are marked as prereleases, a post-release and a local version are not.

Two behaviors to expect from **commit creation**. A commit is never made on a branch that molt has momentarily emptied: replacing an existing release branch happens through a short-lived `molt/tmp/<branch>` ref, because a release branch that briefly holds no changes against its base is one GitHub may auto-close the pull request for -- and, with "automatically delete head branches" on, delete. And a run whose version command changed nothing sends no commit at all: the branch is reset onto the base and molt reports that no commit was needed, rather than writing an empty commit into a public diff.

## Attribution via GraphQL

Molt asks GitHub for attribution with a small, hand-written GraphQL query over `httpx` -- not a heavyweight REST client. Each distinct commit or pull request costs exactly one request; a repeated lookup for the same commit or pull request is answered from the per-instance cache instead of asking GitHub again, so a changelog with several lines pointing at the same commit fetches it once. For each commit it fetches the associated pull requests and authors; the **PR author is preferred over the raw commit author**, and when several PRs are associated, the earliest-merged one wins -- so a change is credited to the human who proposed it.

Because this runs over plain `httpx`, GitHub Enterprise Server is supported by pointing molt at your instance (see configuration below) -- no separate code path.

## Authentication

The GitHub backend authenticates its API calls with a token from the `GITHUB_TOKEN` environment variable, sent as a GitHub `Authorization` token. In [GitHub Actions](/guides/ci-github-action) the built-in `secrets.GITHUB_TOKEN` is enough for attribution, the release PR, and creating Releases. Locally, a personal access token with read scopes covers attribution.

Note that this token is **only** for talking to GitHub. Uploading to PyPI is a completely separate credential and uses [OIDC Trusted Publishing](/guides/publishing#trusted-publishing-over-oidc----no-long-lived-token) -- there is no PyPI token in the mix.

## Repository settings

Four settings on the repository decide whether the release loop can do its work. The first one is off by default and stops the version phase dead, so check it before the first run.

### Pull-request creation

**Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests.**

Turn it on. Without it the version phase fails when it opens the "Version Packages" pull request:

```text
GitHub Actions is not permitted to create or approve pull requests.
```

It is **off by default on personal-account repositories**. An organization repository inherits the organization's setting, which an owner sets under the same path in organization settings; a repository cannot turn it on if the organization has turned it off.

Granting `pull-requests: write` in the workflow does not substitute for it. The two are separate gates and the run needs both.

### Workflow permissions

The radio above the checkbox sets the *default* token permissions for workflows that do not state their own. molt's workflows state their own, per job, so the restricted default is the right choice: leave it on **Read repository contents and packages permissions** and let each job grant what it needs.

The release loop needs `contents: write` and `pull-requests: write` on the version phase, and `contents: write` plus `id-token: write` on the publish. See [CI: GitHub Action](/guides/ci-github-action#understanding-the-permissions).

### Branch protection

molt does not push to your base branch. It force-updates `changeset-release/<base>` and opens a pull request from it, so a protection rule on `main` is no obstacle. Two rules do matter:

- A rule covering `changeset-release/*` blocks the version phase. Exclude that pattern, or allow the token to force-push to it.
- A rule requiring **signed commits** rejects a commit made on the runner. Set `commit-mode: api` so GitHub authors and signs the commit instead. See [Signed commits](/guides/ci-github-action#signed-commits).

### Checks on the release pull request

A pull request opened with `GITHUB_TOKEN` does not start other workflows the way a human's push does. GitHub holds the `pull_request` run for approval rather than starting it, which is its protection against a workflow triggering itself in a loop. Expect to click **Approve and run** on the "Version Packages" pull request, or to see no checks on it at all.

This does not affect the release. Merging that pull request is a push by you, so `release.yml` runs normally and the publish phase goes ahead.

If you want checks to start on their own, open the release pull request with a personal access token instead of `GITHUB_TOKEN`: pass it as `github-token`. A pull request opened with a PAT triggers workflows normally. The cost is a long-lived credential in the repository, which is the thing the rest of this setup exists to avoid.

## Configuration

The backend resolves its endpoints and repository from the environment, which CI already sets:

- `GITHUB_REPOSITORY` -- the `owner/name` slug the changelog and releases target. Repository names are validated against `owner/name` form.
- `GITHUB_TOKEN` -- the API token described above.
- `GITHUB_SERVER_URL` -- defaults to `https://github.com`; override for GitHub Enterprise Server.
- `GITHUB_GRAPHQL_URL` -- defaults to `https://api.github.com/graphql`; override for GHES.
- `GITHUB_API_URL` -- the REST base used for releases, pull requests and refs; defaults to `GITHUB_GRAPHQL_URL` with the trailing `/graphql` removed. Every GitHub Actions runner sets it, GHES included, so it is normally already correct. Set it yourself for a self-hosted instance you run molt against from outside Actions: a GHES REST base is `https://your-host/api/v3`, which is not what stripping `/graphql` off its GraphQL URL produces.

For local runs, molt also reads these from a `.env` file if present. The GitHub-flavored [changelog generator](/guides/changelog-templates) can take an explicit `repo` in its options instead of relying on `GITHUB_REPOSITORY`.

## Caching and resilience

The GitHub backend keys its attribution cache by `(kind, repository, id)`, so a commit or PR referenced by many changelog lines is fetched exactly once per release. It honors `Retry-After`, retries transient `5xx` responses with jittered backoff, sets an explicit request timeout, and reports an actionable error (with the reset time) when the rate limit is exhausted. See [Forges overview](/forges/overview#caching-and-backoff).

## See also

- [Forges overview](/forges/overview) -- the protocol this backend implements.
- [CI: GitHub Action](/guides/ci-github-action) -- the release loop and its permissions.
- [Changelog templates](/guides/changelog-templates) -- what attribution renders into.
- [GitLab, Gitea & others](/forges/gitlab-gitea-others) -- releasing from another host.
