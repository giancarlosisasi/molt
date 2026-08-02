---
title: molt version
---

# molt version

Consume every pending changeset in one batch: bump versions, propagate to dependents, rewrite internal pins and changelogs, and update the lockfile.

## Synopsis

```bash
molt version [OPTIONS]
```

## Description

`molt version` drains the `.changeset/` buffer. It reads all pending changesets, builds the [release plan](/concepts/release-plan), and applies it:

- **Bumps each package** to its computed version, taking the highest bump among the changesets that touch it.
- **Propagates through the dependency graph** -- a dependent whose constraint no longer accepts the new version gets released too, and its pin is rewritten. See [Dependency propagation](/guides/dependency-propagation).
- **Rewrites internal dependency pins** in every manifest section where a bumped package appears.
- **Writes changelogs** from the changeset summaries, correct Markdown emitted directly.
- **Updates the lockfile** (for uv, one `uv lock` call) so `uv.lock` never goes stale. Molt **never deletes a lockfile it cannot read**: if `uv.lock` will not parse, molt leaves the file exactly as it found it, warns, skips the refresh, and finishes the release -- your manifests and changelogs are still correct. Run `uv lock` yourself once the file is readable again. A lockfile that will not parse is as often a half-finished merge as it is corruption, and the bytes are not molt's to throw away.
- **Deletes the consumed changesets.**

Running `molt version` with **no pending changesets exits 1** with `No unreleased changesets found.` This is v3 semantics (v2 exited 0); CI pipelines must tolerate that code. See [Versioning](/guides/versioning).

### Atomicity

All mutations are **buffered and flushed together**. Changelog generation runs first and acts as the transactional guard: if it fails, nothing is written to disk. A mid-run failure therefore never leaves you half-bumped with changesets still on disk, so re-running cannot double-bump. This fixes a known changesets footgun.

### Where the new version is written

A released package's new version goes **where its version source says**, which is
`[project].version` only for a statically versioned package. A package that declares
`dynamic = ["version"]` and keeps the value in a file -- hatch's `__about__.py`, setuptools'
`{ attr = ... }` or `{ file = ... }`, pdm's file source -- has that file rewritten instead, and its
`pyproject.toml` is left completely alone. Only the version text moves: comments, imports, other
assignments and the file's own line endings all survive byte-for-byte.

A package whose version comes from a **git tag** (setuptools-scm, hatch-vcs, versioningit) is named
in a warning and skipped -- molt cannot yet create the tag that would realize a new version. A
package that declares its version dynamic and gives molt no way to find it is named the same way.
Either way the rest of the release goes ahead, and a changeset that *names* such a package still
fails the run. See [Dynamic versions](/ecosystems/dynamic-versions).

### Prereleases and snapshots

- `--pre {a,b,rc,dev}` produces a PEP 440 prerelease (`1.1.0rc0`, `1.1.0a1`, `1.1.0.dev3`). It is **stateless** -- there is no pre-mode and no `pre.json`. Run it again and the counter increments (`rc0` to `rc1`); run plain `molt version` to finalize to the stable version. See [Prerelease control](/cli/pre) and [Prerelease mode](/concepts/prerelease).
- `--snapshot` produces a throwaway snapshot version for a branch or PR build, defaulting to a separate index rather than PyPI. See [Snapshot releases](/concepts/snapshots).

## Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--pre {a,b,rc,dev}` | string | -- | Cut a PEP 440 prerelease this run (alpha, beta, release candidate, or dev). Stateless; see [molt pre](/cli/pre). |
| `--snapshot` | flag | off | Cut an unnamed snapshot release. Optional-value flag -- see the note below. |
| `--snapshot-name <name>` | string | -- | Cut a snapshot release with the given name. Numeric-looking names stay strings. |
| `--ignore <name>` | list | -- | Skip a package this run. Repeatable. Mutually exclusive with the config `ignore` list. |
| `--dry-run` | flag | off | Print the release plan; write nothing. See [Dry runs and plans](/guides/dry-run-and-plans). |
| `--cwd <path>` | path | current directory | Directory to run in; root discovery starts here. |
| `-h`, `--help` | flag | -- | Show help and exit. |

### The `--snapshot` optional value

Because the parser cannot bind a space-separated optional value, molt accepts three forms:

```bash
molt version --snapshot              # unnamed snapshot
molt version --snapshot=pr-123       # named snapshot
molt version --snapshot-name pr-123  # named snapshot
```

The space form `molt version --snapshot pr-123` is **rejected** with a message suggesting `--snapshot=pr-123` or `--snapshot-name pr-123`.

`--snapshot` forces `commit: false` for the run and skips prerelease bookkeeping. Snapshot and `--pre` cannot be combined.

## Validation

`molt version` fails before writing when:

- `--ignore` is passed **and** `ignore` is set in the config (`you can only use one of them at a time`).
- An `--ignore` name is **not in the project**.
- A **published dependent of a skipped package is not itself skipped** -- molt names the dependent and asks you to add it to `--ignore`. Dev-dependency-only dependents and unversioned private packages are exempt.

## Exit codes

| Situation | Exit code |
|---|---|
| Versions applied (or `--dry-run` printed) | 0 |
| No unreleased changesets | 1 |
| `--ignore` conflicts with config, unknown `--ignore` name, or an unskipped dependent of a skipped package | 1 |
| `--snapshot` combined with `--pre` | 1 |

## Examples

Cut a normal release from all pending changesets:

```bash
molt version
```

Preview the plan without touching disk:

```bash
molt version --dry-run
```

Cut a release candidate, then iterate:

```bash
molt version --pre rc        # 1.1.0rc0
molt version --pre rc        # 1.1.0rc1
molt version                 # 1.1.0  (finalizes)
```

Cut a snapshot for a PR build:

```bash
molt version --snapshot=pr-123
```

Skip a package for this run only:

```bash
molt version --ignore internal-scratch
```

## See also

- [Versioning](/guides/versioning) -- the release-time guide.
- [The release plan](/concepts/release-plan) and [Dependency propagation](/guides/dependency-propagation).
- [Versioning and PEP 440](/concepts/versioning-pep440), [Prerelease mode](/concepts/prerelease), [Snapshot releases](/concepts/snapshots).
- [molt publish](/cli/publish) -- ship what `version` bumped.
