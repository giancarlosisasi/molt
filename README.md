# molt

Changeset-driven versioning, changelogs, and publishing for Python packages and monorepos. Built on
PEP 440 versions and PEP 508 requirements, and on the way `pip` and `uv` actually resolve them.

**[Documentation](https://molt.gio-labs.com)**

## Install

The distribution is `molt-release`. The command it installs is `molt`.

```bash
uv tool install molt-release
molt --help
```

`pipx install molt-release` and `pip install molt-release` work too, and `uvx molt-release --help` runs it
without installing anything. Requires Python 3.11 or newer, on Windows, macOS, or Linux.

## The release loop

You record the intent of a change while you write it. A separate step turns accumulated intent into
releases.

```bash
molt init                 # one time: create .changeset/ and a [tool.molt] block
# ... make a change ...
molt add                  # record which packages changed, and how much
molt version              # bump versions, write changelogs, update the lockfile
git commit -am "Release"
molt publish              # build, upload to PyPI, tag
```

`molt add` writes a small Markdown file you commit alongside your code:

```md
---
"acme-core": minor
---

Add a --stream flag to the export API for large datasets.
```

`molt version` consumes every pending changeset in one pass, takes the highest bump per package,
folds the summaries into each `CHANGELOG.md`, and deletes the files it consumed. All mutations are
buffered and flushed together, so a mid-run failure never leaves a repository half-versioned.

## Monorepo propagation

In a workspace, a changeset for one package cascades to the packages that depend on it. Given
`acme-cli` requiring `acme-core>=1.2.0,<2.0.0`, a major bump to `acme-core` plans this:

```text
$ molt status
Packages to be bumped:
- major
  - acme-core   1.2.0 -> 2.0.0
- patch
  - acme-cli    0.5.0 -> 0.5.1   (dependency bump)
```

`acme-cli` has no changeset of its own. Version `2.0.0` falls outside its constraint, so it needs a
release. Molt gives it the smallest bump that does the job and rewrites the constraint to
`>=2.0.0,<3.0.0`. A minor bump to `1.3.0` would have released nothing extra, because `1.3.0` still
satisfies the range.

See [Dependency propagation](https://molt.gio-labs.com/guides/dependency-propagation).

## Commands

| Command | What it does |
|---|---|
| `molt init` | Scaffold config and `.changeset/`; detect the ecosystem backend and workspace layout. |
| `molt add` | Record a changeset. Bare `molt` runs this. |
| `molt version` | Consume changesets: bump versions, propagate to dependents, rewrite pins, write changelogs, update the lockfile. |
| `molt status` | Report pending changesets and the projected release, as text or JSON. Doubles as a CI gate. |
| `molt publish` | Build and upload changed packages to PyPI over OIDC Trusted Publishing, in dependency order. |
| `molt build` | Build sdists and wheels for the packages a plan will publish. |
| `molt yank` | Check a released version and print the steps to yank it on PyPI (PEP 592). |
| `molt git-tag` | Create annotated git tags for released packages. |

`--dry-run` prints the plan for any mutating command and writes nothing. `--non-interactive`
resolves every prompt to its documented default, which is what Dependabot, Renovate, and codegen
need. Human-facing output goes to stderr and stdout carries machine-readable payloads only, so
`molt status --output json | jq '.releases[].name'` needs no filtering.

Full reference: [CLI](https://molt.gio-labs.com/cli/overview).

## Python specifics

Python's packaging rules shape several of molt's behaviors.

- **PEP 440 version math**, through `packaging`. Prereleases are `1.0.0rc1`, `1.0.0a2`, or
  `1.0.0.dev3` from a fixed set of spellings, and package names compare under PEP 503
  normalization.
- **Prerelease is a flag**, not a persistent mode: `molt version --pre rc`. There is no state file
  to enter, commit, or exit.
- **`uv.lock` is refreshed** when `molt version` bumps a package, so a `--frozen` or `--locked`
  install in CI still resolves against the release commit. uv workspaces are first-class; other
  backends sit behind an [ecosystem seam](https://molt.gio-labs.com/ecosystems/overview).
- **Snapshots target a separate index.** PyPI versions are permanent, so a snapshot on the public
  index would burn a version number for good.
- **A bad release is yanked, not unpublished.** `molt yank` verifies the version, reports whether it
  is already yanked, and prints the exact steps. PyPI publishes no yank API, so the final click is
  yours.

## Continuous integration

The repository root carries a composite GitHub Action that runs the whole loop. It keeps a "Version
Packages" pull request in sync with your pending changesets, then builds, publishes, tags, and
creates a GitHub Release per package once that pull request merges.

```yaml
- uses: giancarlosisasi/molt@<sha>
  with:
    molt-version: "0.1.0"
    publish: molt publish
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

See [CI: GitHub Action](https://molt.gio-labs.com/guides/ci-github-action) for the full workflow,
its inputs, and its outputs. Nothing in the loop is GitHub-specific: the same `molt` commands drive
it anywhere.

## Documentation

- [Overview](https://molt.gio-labs.com/introduction/what-is-molt) and
  [The changeset workflow](https://molt.gio-labs.com/introduction/the-changeset-workflow)
- [Installation](https://molt.gio-labs.com/getting-started/installation),
  [single-package quickstart](https://molt.gio-labs.com/getting-started/quickstart-single-package),
  [monorepo quickstart](https://molt.gio-labs.com/getting-started/quickstart-monorepo)
- [The release plan](https://molt.gio-labs.com/concepts/release-plan) and
  [Versioning and PEP 440](https://molt.gio-labs.com/concepts/versioning-pep440)
- [Configuration](https://molt.gio-labs.com/config/config-file) and
  [every option](https://molt.gio-labs.com/config/options)
- [Migrating from changesets](https://molt.gio-labs.com/guides/migrating-from-changesets)

## Acknowledgements

Molt's workflow comes from [changesets](https://github.com/changesets/changesets), the release tool
for JavaScript monorepos. Its version math comes from
[`packaging`](https://packaging.pypa.io), the PyPA reference implementation of PEP 440 and PEP 508.
Full credits, including the prior work in Python and the related tools worth comparing molt against,
are on the [Acknowledgements](https://molt.gio-labs.com/reference/acknowledgements) page.

## License

MIT.
