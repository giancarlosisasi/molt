---
title: "CI: GitHub Action"
---

# CI: GitHub Action

Automate the whole release loop on GitHub: molt keeps a "Version Packages" pull request in sync with your accumulated changesets, and publishes to PyPI over OIDC the moment that PR merges.

Molt ships a composite GitHub Action that installs itself with [uv](/ecosystems/uv) and runs the release loop for you. Because it is the same `molt` you run locally, nothing about the automation is GitHub-specific magic -- the Action just wires molt's own commands into the two events that matter.

The Action lives at the root of the molt repository, so you reference it as `uses: giancarlosisasi/molt@v1` -- there is no separate action repository to keep in step with the CLI.

`v1` is a **moving** reference: it tracks the newest 1.x action, and molt's own release moves it after each successful publish. Pin a commit in production (`uses: giancarlosisasi/molt@<sha>`) if you would rather decide when the action changes under you. Dependabot updates a commit pin; it cannot tell you a moving tag moved.

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

Molt has no prerelease state, so a release pull request that carries prereleases looks like any other one. There is no title suffix and no banner. `molt pre` exists to point you at [`molt version --pre {a,b,rc,dev}`](/cli/version). If you are migrating a workflow from changesets, the title suffix and banner it reads out of `.changeset/pre.json` have no molt equivalent.

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

      - uses: giancarlosisasi/molt@v1
        with:
          molt-version: "0.1.0"   # pin molt; keeps the Action and the tool in lockstep
          publish: molt publish   # the command to run in the publish phase
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

That is the entire release automation for a single package or a monorepo. There is **no separate `astral-sh/setup-uv` step**: the Action installs uv itself, from a commit-pinned copy it owns. In production, pin `actions/checkout` and `giancarlosisasi/molt` to full commit SHAs.

## Inputs

Every input is optional. Supplying only a token runs the version phase with the defaults below.

| Input | Default | What it does |
|---|---|---|
| `molt-version` | latest release | The exact `molt-release` version to install, for example `"0.1.0"`. Empty installs the latest release. See [Pinning molt](#pinning-molt). |
| `publish` | *(empty)* | The command that publishes the release, for example `molt publish`. Leave it empty to run the version phase only -- supplying it is also what makes the publish phase reachable at all. |
| `version-command` | *(empty)* | The command that versions the release, for example `molt version --snapshot canary`. Empty runs molt's own `molt version`. Split with POSIX shell rules, so a quoted argument stays one argument. |
| `title` | `Version Packages` | The title of the release pull request. |
| `commit` | `Version Packages` | The commit message molt writes on the release branch. Separate from the title. |
| `create-releases` | `true` | Create one GitHub Release per published package. `true` or `false`; anything else fails the run rather than being read as `false`. |
| `base-branch` | the branch the workflow runs on | The branch being released. The release branch is `changeset-release/<base-branch>`. |
| `commit-mode` | `git-cli` | How the version commit reaches the remote. `git-cli` commits and force-pushes from the runner; `api` sends the changes to GitHub instead, so GitHub authors and **signs** the commit. See [Signed commits](#signed-commits). Anything else fails the run. |
| `github-token` | `${{ github.token }}` | The token molt uses to open the pull request and create releases. A `GITHUB_TOKEN` already in the step's environment wins over this input. |

You also configure PyPI Trusted Publishing once, on PyPI itself, to trust this repository and workflow. After that there is no API token anywhere in the workflow -- see [Publishing](/guides/publishing#trusted-publishing-over-oidc----no-long-lived-token).

## Understanding the permissions

The three grants map one-to-one onto the three things molt does:

- **`id-token: write`** is the important one. It lets the job mint the short-lived OIDC token that PyPI exchanges for a one-time upload token. This is what replaces a stored `PYPI_API_TOKEN` secret entirely.
- **`contents: write`** lets molt push the release branch, push the annotated tags after a successful publish, and create the GitHub Releases.
- **`pull-requests: write`** lets molt open and update the "Version Packages" PR.

Starting from `permissions: {}` at the workflow level and granting per job is the recommended posture: nothing has ambient write access, and the OIDC grant exists only where a publish can happen. For a stricter setup, split the version phase and the publish phase into separate jobs so `id-token: write` is scoped to the publish job alone.

## Pinning molt

`molt-version` pins the exact molt the Action runs. Left empty, it installs the **latest release** -- it does not read the Action's own ref, because turning a ref like `v1` (or a branch, or a SHA) into a PyPI specifier is guesswork, and guessing a version is the opposite of pinning one.

The Action pins the one third-party action it bundles (`astral-sh/setup-uv`) to a commit SHA, and molt's own repository runs Dependabot weekly against that pin -- so an audit of the third-party actions you inherit has a named owner for the refresh.

Pin it. A pinned version is what stops your releases from changing behavior on a day you did not touch anything, and because the Action installs molt with `uvx` from a warm uv cache, a pinned version resolves in seconds. `uvx` also puts that same install on `PATH` for the commands molt spawns, so `publish: molt publish` runs the version you pinned and not some other one.

## Molt is not tied to GitHub Actions

The Action is a convenience, not a dependency. Every phase above is a plain molt command, and `molt publish` -- including [`--filter`](/guides/publishing#publishing-a-subset-with---filter) -- runs the same everywhere. You can drive releases from GitLab CI, a Jenkins box, a cron job, or your laptop with nothing more than uv installed:

```bash
# Anywhere uv is available -- no GitHub, no Action
uvx --from molt-release molt version
git commit -am "Version Packages"
uvx --from molt-release molt publish
```

This is a deliberate contrast with changesets, whose publish flow is coupled to its own GitHub Action. Molt keeps the orchestration and the CLI as the same code path, so "run it in CI" and "run it by hand" are the same tool.

## Forge-agnostic by design

Opening the release PR, attributing authors, and writing release notes all go through molt's **forge** protocol rather than calling GitHub directly. See [Forges](/forges/overview) for what a forge backend answers, and [GitLab, Gitea & others](/forges/gitlab-gitea-others) for releasing from another host.

## Windows and cross-platform notes

Molt is tested on Windows, macOS, and Linux from the first commit, so the CLI behaves identically wherever your CI runs -- paths resolve correctly and output stays ASCII-clean under Windows' cp1252 console. The release job itself typically runs on Linux, but the tool you are automating does not care which runner you pick.

## Action outputs

The Action exposes four outputs so later steps can react to a release:

| Output | Value |
|---|---|
| `published` | `"true"` when at least one package was published this run. |
| `published_packages` | A JSON array of `{ "name", "version" }` objects for what went out. `[]` when nothing was published. |
| `has_changesets` | `"true"` when pending changesets exist. |
| `pull_request_number` | The number of the created or updated "Version Packages" PR. **Empty** when the run opened none, which is every publish run. |

```yaml
      - uses: giancarlosisasi/molt@v1
        id: molt
        with:
          publish: molt publish
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - if: steps.molt.outputs.published == 'true'
        run: echo "released ${{ steps.molt.outputs.published_packages }}"
```

Every value is written to `GITHUB_OUTPUT` in the delimiter (heredoc) form, so a value containing a newline or an equals sign survives intact.

### A failing run still writes its outputs

A publish that uploads three packages out of five fails the step -- and still reports the three. That is the run where reading `published_packages` matters most, so guard the reader with `if: always()` rather than letting it be skipped along with the rest of the job:

```yaml
      - uses: giancarlosisasi/molt@v1
        id: molt
        with:
          publish: molt publish
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - if: always() && steps.molt.outputs.published == 'true'
        run: echo "these went out: ${{ steps.molt.outputs.published_packages }}"
```

**`published` is not a success signal.** It says at least one package reached the index, which is exactly as true after a half-publish as after a clean one. A workflow that treats it as "the release worked" must also check the molt step's own outcome.

One case writes nothing, deliberately: a run that fails before it observes anything -- a protected release branch, an unusable configuration -- has no result to report, and reporting `published = false` about a repository molt never finished reading would be worse than reporting nothing.

Molt also fails the run when a package published but its GitHub Release could not be created. It creates every remaining release first and then fails naming each one it could not create, with the reason -- the packages are already on the index by then, and re-running is safe because a release that already exists is left alone.

## Signed commits

If your release branch is protected by a rule that **requires signed commits**, set `commit-mode: api`:

```yaml
      - uses: giancarlosisasi/molt@v1
        with:
          commit-mode: api
          publish: molt publish
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

In that mode molt does not commit locally at all. It runs the version command on the branch the workflow checked out, works out what changed, and sends those changes to GitHub. **GitHub authors the commit**, so GitHub signs it with its own key, marks it *Verified*, and attributes it to whoever owns the token. A branch protection rule that requires signed commits accepts it. The default, `git-cli`, commits and force-pushes from the runner exactly as before -- and an unsigned commit is what such a rule rejects.

**What the mode buys is the signature, and only the signature.** Three things it does not change, and a reader who sees "signed commits" will assume all three:

- It does **not** bypass a review requirement or a required status check. An API commit is subject to branch protection exactly as a push is.
- It does **not** make the release branch trigger other workflows. A commit authored with the workflow's own `GITHUB_TOKEN` never starts another workflow run, over the API just as over a push.
- It does **not** sign tags. Molt pushes tags with git in both modes; a tag ref carries no signature either way.

Using a **GitHub App token or a PAT** instead of `${{ github.token }}` changes two of those: the commit is attributed to the app or the user rather than to `github-actions[bot]`, and the release branch *does* trigger workflows.

Two observable differences from the default mode, both harmless on their own and worth knowing if you add steps after molt:

- **The checkout stays on your base branch.** No local `changeset-release/<base>` branch is created or checked out, so a later step cannot inspect one locally. It exists on the remote, which is where the pull request reads it from.
- **No committer identity is configured on the runner.** The Action's identity step writes a *global* git identity, and in `api` mode there is nothing local to attribute, so the step is skipped. A later step doing its own `git commit` will need to configure one.

## What molt's Action does not do

Deliberate omissions, each with the reason -- a changesets user migrating looks for exactly these.

| changesets has | molt does not | Why |
|---|---|---|
| Pre mode (`pre.json`, a title suffix and a banner on the release PR) | -- | Molt has no pre state at all: `1.0.1-next.0` is not a legal PEP 440 version, so `--pre` is a stateless flag on [`molt version`](/cli/version). |
| Draft pull requests (`prDraft`) | -- | Not implemented; the release PR is always opened ready for review. |
| Signed **tags** in `api` mode (`commitMode` also routes `pushTag`) | -- | Molt pushes tags with git in both modes. A tag ref carries no signature either way -- `git push origin <tag>` and the API create the same object -- so signing the commit solves the problem on its own. |
| A `cwd` input | -- | The Action runs in the checkout root, so a repository whose Python workspace lives in a subdirectory cannot use it yet. |

## Migrating from `changesets/action`

The release branch name is unchanged -- `changeset-release/<base>` -- so an open release pull request, its number and any branch protection rule written against that name all survive the switch.

| `changesets/action@v1` | molt | Note |
|---|---|---|
| `publish` | `publish` | Same meaning: the command run in the publish phase. |
| `version` | `version-command` | Renamed. `molt-version` already names a *distribution* pin, so two inputs differing only by a prefix would be a copy-paste waiting to go wrong. |
| `title` | `title` | Same spelling. |
| `commit` | `commit` | Same spelling. |
| `createGithubReleases` | `create-releases` | Renamed: kebab-case, and host-neutral -- molt's forge seam means the host is not necessarily GitHub. |
| `branch` | `base-branch` | Renamed for clarity: it is the branch being released, not the release branch. |
| `cwd` | -- | Not supported. |
| `setupGitUser` | -- | Absorbed: the Action configures a `github-actions[bot]` identity when the runner has none, and leaves one you configured yourself alone. |
| `commitMode` | `commit-mode` | Renamed, and **one value changes**: `git-cli` is spelled the same, `github-api` becomes `api`. Copying `github-api` across fails the run naming both accepted spellings rather than quietly running the wrong mode. |
| `prDraft` | -- | Not supported. |
| `publishedPackages` (output) | `published_packages` | Outputs are snake_case, like every other machine-readable payload molt emits. |

## See also

- [Publishing](/guides/publishing) -- the OIDC upload flow the publish phase runs.
- [`molt version`](/cli/version) -- what the "Version Packages" PR contains.
- [Forges](/forges/overview) -- the seam behind PR creation and release notes.
- [The changeset workflow](/introduction/the-changeset-workflow) -- the add / version / publish loop this automates.
