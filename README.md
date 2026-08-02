# molt

Changeset-driven versioning, changelogs, and publishing for Python packages and monorepos. A
Python-native port of [`changesets`](https://github.com/changesets/changesets), built on PEP 440
versions and PEP 508 requirements instead of SemVer and npm.

**[Documentation](https://molt.dev)**

## Install

The distribution is `molt-cli`. The command it installs is `molt`.

```bash
uv tool install molt-cli
molt --help
```

`pipx install molt-cli` and `pip install molt-cli` work too, and `uvx molt-cli --help` runs it
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

See [Dependency propagation](https://molt.dev/guides/dependency-propagation).

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

Full reference: [CLI](https://molt.dev/cli/overview).

## Python specifics

Some behavior differs from changesets because Python's packaging rules force a different answer.

- **PEP 440 version math**, through `packaging`. Prereleases are `1.0.0rc1`, `1.0.0a2`, or
  `1.0.0.dev3` from a fixed set of spellings, and package names compare under PEP 503
  normalization.
- **Prerelease is a flag**, not a persistent mode: `molt version --pre rc`. There is no `pre.json`
  branch state to merge or forget.
- **`uv.lock` is refreshed** when `molt version` bumps a package. uv workspaces are first-class;
  other backends sit behind an [ecosystem seam](https://molt.dev/ecosystems/overview).
- **Snapshots target a separate index**, because PyPI versions are immutable and every snapshot
  would otherwise burn a public version number.
- **A bad release is yanked, not unpublished.** `molt yank` verifies the version, reports whether it
  is already yanked, and prints the exact steps. PyPI exposes no yank API, so the final click is
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

See [CI: GitHub Action](https://molt.dev/guides/ci-github-action) for the full workflow, its inputs,
and its outputs. Nothing in the loop is GitHub-specific: the same `molt` commands drive it anywhere.

## Documentation

- [What is Molt?](https://molt.dev/introduction/what-is-molt) and
  [Why Molt?](https://molt.dev/introduction/why-molt)
- [Installation](https://molt.dev/getting-started/installation),
  [single-package quickstart](https://molt.dev/getting-started/quickstart-single-package),
  [monorepo quickstart](https://molt.dev/getting-started/quickstart-monorepo)
- [The release plan](https://molt.dev/concepts/release-plan) and
  [Versioning and PEP 440](https://molt.dev/concepts/versioning-pep440)
- [Configuration](https://molt.dev/config/config-file) and
  [every option](https://molt.dev/config/options)
- [Migrating from changesets](https://molt.dev/guides/migrating-from-changesets)

## License

MIT.
