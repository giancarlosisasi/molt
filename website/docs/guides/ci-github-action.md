---
title: "CI: GitHub Action"
---

# CI: GitHub Action

Automate the whole release loop on GitHub: molt keeps a "Version Packages" pull request in sync with your accumulated changesets, and publishes to PyPI over OIDC the moment that PR merges.

Molt ships a composite GitHub Action that installs itself with [uv](/ecosystems/uv) and runs the release loop for you. Because it is the same `molt` you run locally, nothing about the automation is GitHub-specific magic -- the Action just wires molt's own commands into the two events that matter.

:::warning What ships today
The **release loop itself is implemented**: mode selection, the release pull request (opened, reused and force-updated), its body, and the GitHub Release per published package. The **`action.yml` wrapper is not published yet** -- `uses: molt/action@v1` in the workflow below does not resolve, and the `molt-version` / `publish` inputs are the shape it will take. Until it lands, drive the same loop yourself with plain `molt` commands (see [Molt is not tied to GitHub Actions](#molt-is-not-tied-to-github-actions)).
:::

## The two-phase loop

Every push to your base branch triggers one of two behaviors, and molt decides which:

- **Version phase** -- there are pending changesets. Molt opens (or updates) a **release pull request** titled "Version Packages". That PR contains exactly what [`molt version`](/cli/version) would do: bumped manifests, rewritten changelogs, an updated lockfile. As more changesets merge, the same PR is kept up to date -- it is reused, never re-created, so its number and review history survive.
- **Publish phase** -- there are no pending changesets but there *are* versions on disk that are not yet on PyPI. That is the state right after the release PR merges. Molt builds and [publishes](/guides/publishing) the changed packages, git-tags each one, and creates a GitHub Release per package with its changelog entry as the body.

So the human loop is simply: merge feature PRs (each carrying a changeset), watch the "Version Packages" PR accumulate, and merge it when you want to cut a release. Merging it is the release.

The release PR lives on a dedicated branch, `changeset-release/<base>` (for a `main` base, `changeset-release/main`). Molt owns that branch and force-updates it -- do not hand-edit it, your changes are replaced on the next push. The name is deliberately the one changesets uses, so a repository migrating from changesets keeps its existing release branch, its open release PR, and any branch protection rule written against that name.

The commit molt writes on that branch is titled "Version Packages" by default; the version loop takes the message as a parameter, so a workflow may set its own. The pull-request title and the commit message are **separate** settings: renaming one never renames the other, so a repository with a conventional-commit convention can rename the commit and keep the pull request its reviewers recognise.

Only one phase runs per push -- never both. A run with pending changesets versions; a run with none publishes if a publish command is configured. Pending changesets that release nothing (what `molt add --empty` writes) are reported as pending and open no pull request, because there is nothing to release.

### What the release pull request says

The body lists every package the version run bumped: one `## <name>@<version>` section per package, carrying that package's own changelog entry for that version. Sections are ordered **public packages first, then highest bump level first**, so the release a reader cares about is at the top of a body that may list dozens.

The list is never filtered -- a private package that was bumped is in the body too, because the pull request is the record of what merging it releases.

Very large releases degrade rather than fail. GitHub rejects a pull-request body over 65536 characters, so molt drops the changelog text first (keeping every heading, plus a note saying so) and, if that is still too long, replaces the package list with a single note. The explanatory header and the `# Releases` heading survive in every case.

### What the publish phase creates

After a successful publish, molt creates one **GitHub Release per published package**, named for that package's tag and carrying that package's changelog entry as the body, marked as a prerelease when the version is a PEP 440 prerelease. This is on by default and can be switched off; switching it off does not change what is published or what is reported.

A package with no `CHANGELOG.md` is skipped silently -- that is what "this project keeps no changelog" looks like on disk. A package whose `CHANGELOG.md` exists but carries no section for the version just published **fails the run**, naming the package and the version: the file is there, so release notes were meant to be written and are missing. A release the host already carries is not an error -- re-running a publish that half-failed completes the missing releases instead of failing on the finished ones.

### No pre mode

changesets marks the release pull request of a prerelease run with a title suffix and a warning banner, read out of `.changeset/pre.json`. **Molt has none of that**, and it is not an oversight: molt has no pre state at all. `molt pre` exists only to point you at [`molt version --pre {a,b,rc,dev}`](/cli/version), because changesets' `1.0.1-next.0` is not a legal PEP 440 version. A release pull request that carries prereleases therefore looks like any other one.

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
