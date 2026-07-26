---
title: "CI: GitHub Action"
---

# CI: GitHub Action

Automate the whole release loop on GitHub: molt keeps a "Version Packages" pull request in sync with your accumulated changesets, and publishes to PyPI over OIDC the moment that PR merges.

Molt ships a composite GitHub Action that installs itself with [uv](/ecosystems/uv) and runs the release loop for you. Because it is the same `molt` you run locally, nothing about the automation is GitHub-specific magic -- the Action just wires molt's own commands into the two events that matter.

## The two-phase loop

Every push to your base branch triggers one of two behaviors, and molt decides which:

- **Version phase** -- there are pending changesets. Molt opens (or updates) a **release pull request** titled "Version Packages". That PR contains exactly what [`molt version`](/cli/version) would do: bumped manifests, rewritten changelogs, an updated lockfile. As more changesets merge, the same PR is kept up to date -- it is reused, never re-created, so its number and review history survive.
- **Publish phase** -- there are no pending changesets but there *are* versions on disk that are not yet on PyPI. That is the state right after the release PR merges. Molt builds and [publishes](/guides/publishing) the changed packages, git-tags each one, and creates a GitHub Release per package with its changelog entry as the body.

So the human loop is simply: merge feature PRs (each carrying a changeset), watch the "Version Packages" PR accumulate, and merge it when you want to cut a release. Merging it is the release.

The release PR lives on a dedicated branch, `molt/release/<base>` (for a `main` base, `molt/release/main`). Molt owns that branch and force-updates it -- do not hand-edit it, your changes are replaced on the next push.

## A copy-pasteable workflow

```yaml
name: Release

on:
  push:
    branches: [main]

# No ambient permissions; the job grants only what it needs.
permissions: {}

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write        # push tags, push the release branch, create Releases
      pull-requests: write   # open and update the "Version Packages" PR
      id-token: write        # OIDC token for PyPI Trusted Publishing
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
          persist-credentials: false

      - uses: astral-sh/setup-uv@v6

      - uses: molt/action@v1
        with:
          molt-version: "0.1.x"   # pin molt; keeps the Action and the tool in lockstep
          publish: molt publish   # the command to run in the publish phase
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

That is the entire release automation for a single package or a monorepo. In production, pin `actions/checkout`, `astral-sh/setup-uv`, and `molt/action` to full commit SHAs.

You also configure PyPI Trusted Publishing once, on PyPI itself, to trust this repository and workflow. After that there is no API token anywhere in the workflow -- see [Publishing](/guides/publishing#trusted-publishing-over-oidc----no-long-lived-token).

## Understanding the permissions

The three grants map one-to-one onto the three things molt does:

- **`id-token: write`** is the important one. It lets the job mint the short-lived OIDC token that PyPI exchanges for a one-time upload token. This is what replaces a stored `PYPI_API_TOKEN` secret entirely.
- **`contents: write`** lets molt push the release branch, push the annotated tags after a successful publish, and create the GitHub Releases.
- **`pull-requests: write`** lets molt open and update the "Version Packages" PR.

Starting from `permissions: {}` at the workflow level and granting per job is the recommended posture: nothing has ambient write access, and the OIDC grant exists only where a publish can happen. For a stricter setup, split the version phase and the publish phase into separate jobs so `id-token: write` is scoped to the publish job alone.

## Pinning molt

`molt-version` pins the exact molt the Action runs, and it defaults to the Action's own version. Pinning it is what stops the Action and the CLI from drifting apart between releases -- a failure mode worth designing out from the start. Because the Action installs molt with `uvx` from a warm uv cache, a pinned version resolves in seconds.

## Molt is not tied to GitHub Actions

The Action is a convenience, not a dependency. Every phase above is a plain molt command, and `molt publish` -- including [`--filter`](/guides/publishing#publishing-a-subset-with---filter) -- runs the same everywhere. You can drive releases from GitLab CI, a Jenkins box, a cron job, or your laptop with nothing more than uv installed:

```bash
# Anywhere uv is available -- no GitHub, no Action
uvx molt-cli molt version
git commit -am "Version Packages"
uvx molt-cli molt publish
```

This is a deliberate contrast with changesets, whose publish flow is coupled to its own GitHub Action. Molt keeps the orchestration and the CLI as the same code path, so "run it in CI" and "run it by hand" are the same tool.

## Forge-agnostic by design

Opening the release PR, attributing authors, and writing release notes all go through molt's **forge** seam rather than being hard-wired to GitHub. GitHub ships first and is the most polished, but the abstraction is there from day one, so other forges are additions rather than rewrites. See [Forges](/forges/overview) for what a forge backend does and [GitLab, Gitea & others](/forges/gitlab-gitea-others) for what is planned.

## Windows and cross-platform notes

Molt is tested on Windows, macOS, and Linux from the first commit, so the CLI behaves identically wherever your CI runs -- paths resolve correctly and output stays ASCII-clean under Windows' cp1252 console. The release job itself typically runs on Linux, but the tool you are automating does not care which runner you pick.

## Action outputs

The Action exposes outputs so later steps can react to a release:

- `published` -- `"true"` when at least one package was published this run.
- `published_packages` -- a JSON array of `{ "name", "version" }` for what went out.
- `has_changesets` -- `"true"` when pending changesets exist (the version phase ran).
- `pull_request_number` -- the number of the created or updated "Version Packages" PR.

## See also

- [Publishing](/guides/publishing) -- the OIDC upload flow the publish phase runs.
- [`molt version`](/cli/version) -- what the "Version Packages" PR contains.
- [Forges](/forges/overview) -- the seam behind PR creation and release notes.
- [The changesets model](/introduction/the-changesets-model) -- the add / version / publish loop this automates.
