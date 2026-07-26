---
title: Prerelease control
---

# Prerelease control

How molt cuts prereleases -- and why there is no `molt pre` mode to enter or exit.

## Molt has no prerelease mode

changesets models prereleases as a **persistent state**: you run `changeset pre enter next`, which writes a `pre.json` file that becomes shared branch state, cut some prereleases, and later run `changeset pre exit`. That `pre.json` is the single most-disliked part of changesets, and its arbitrary tags (`1.0.1-next.0`) are not even legal under PEP 440.

Molt does neither. Prerelease is a **stateless flag on [`molt version`](/cli/version)**:

```bash
molt version --pre rc      # cut release candidates this run
molt version               # back to a normal release -- nothing to clean up
```

There is no `pre.json`, no mode to enter, no mode to exit, and no leftover state on the branch. The "state" is simply the version string already on disk. This page is the reference for how `--pre` behaves.

## Synopsis

```bash
molt version --pre {a,b,rc,dev} [OPTIONS]
```

## The `--pre` values

`--pre` takes one PEP 440 prerelease identifier. Each maps to a fixed spelling -- arbitrary tags are not allowed:

| Value | Meaning | Example version |
|---|---|---|
| `a` | alpha | `1.1.0a0` |
| `b` | beta | `1.1.0b0` |
| `rc` | release candidate | `1.1.0rc0` |
| `dev` | developmental release | `1.1.0.dev0` |

See [Versioning and PEP 440](/concepts/versioning-pep440) for the version grammar.

## How the counter moves

Because the counter lives in the version string, not in a state file, iteration is just running the command again:

- **First `--pre` run** consumes the pending changesets, computes the target bump, and appends the prerelease identifier at counter `0`. A pending minor on `1.0.0` with `--pre rc` yields `1.1.0rc0`.
- **Each subsequent `--pre` run** increments the counter: `1.1.0rc0` to `1.1.0rc1` to `1.1.0rc2`. The highest bump type across the accumulated changesets is retained, so a major that landed earlier is not lost when a later minor arrives.
- **Exiting prerelease** is running plain `molt version` (no `--pre`). It finalizes to the stable version, dropping the prerelease identifier: `1.1.0rc1` becomes `1.1.0`.

Dependents that already opted into prereleases through their own constraints are **not** force-released as a dependency moves `rc0` to `rc1` -- this follows PEP 440's opt-in scoping and is a deliberate divergence from changesets. See [Dependency propagation](/guides/dependency-propagation) and [Versioning and PEP 440](/concepts/versioning-pep440).

`--pre` cannot be combined with `--snapshot`.

## Migrating from `changeset pre enter` / `pre exit`

For users coming from changesets, the mapping is direct:

| changesets | molt |
|---|---|
| `changeset pre enter next` | `molt version --pre rc` (choose an `a`/`b`/`rc`/`dev` identifier -- arbitrary tags like `next` are not valid PEP 440) |
| repeated `changeset version` while in pre mode | repeated `molt version --pre <id>` (counter increments each run) |
| `changeset pre exit` then `changeset version` | `molt version` (finalizes to the stable version) |
| the `.changeset/pre.json` file | nothing -- no state file exists |

If you have a `pre.json` from a previous tool, delete it; molt does not read it.

## Examples

Cut a candidate, iterate, then release:

```bash
molt version --pre rc      # 1.1.0rc0
# fix something, add a changeset
molt version --pre rc      # 1.1.0rc1
molt version               # 1.1.0
```

Cut an alpha for early testing:

```bash
molt version --pre a       # 1.1.0a0
```

Preview a prerelease without writing:

```bash
molt version --pre rc --dry-run
```

## See also

- [molt version](/cli/version) -- the command `--pre` lives on.
- [Prerelease mode](/concepts/prerelease) -- the concept in depth.
- [Migrating from changesets](/guides/migrating-from-changesets).
- [Snapshot releases](/concepts/snapshots) -- the other throwaway-version path.
